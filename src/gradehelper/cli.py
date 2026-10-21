"""Command line: `gradehelper run h3`, `gradehelper upload 3`, `gradehelper ui`, ..."""

from __future__ import annotations

import functools
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.table import Table

from .config import (
    GROUP_RUNS,
    RUNS,
    STAGE_FINAL,
    STAGE_GROUP,
    STAGE_INDIVIDUAL,
    STAGE_INDIVIDUAL_FINAL,
    ConfigError,
    Workspace,
)
from .feedback import EXISTS, NO_ISSUE, OPEN, REPLIED, REPLY, plan_feedback, post_feedback
from .grading import fmt_points
from .notify import build_warnings, send_warnings
from .pipeline import Grader, suggest_stage
from .report import HandEditedError, Row, read_final_csv
from .roster import CHANGE_ORDER, diff_rosters, load_roster, roster_from_canvas, save_roster
from .upload import apply_upload, ledger_path, plan_upload

HELP = {"help_option_names": ["-h", "--help"]}
app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    context_settings=HELP,
    help="ENGR1510J grade helper. Per homework: individual (i) -> individual-final -> group (g) -> feedback "
    "-> final (f) -> feedback -> upload.",
)
roster_app = typer.Typer(no_args_is_help=True, context_settings=HELP, help="Student roster (hteams.csv)")
app.add_typer(roster_app, name="roster")
console = Console()

Hw = Annotated[str, typer.Argument(help="homework, e.g. 3 or h3")]
Teams = Annotated[str, typer.Option("--teams", "-t", help='only these teams, e.g. "1,5" or "hteam-01"')]
Deadline = Annotated[Optional[str], typer.Option("--deadline", "-d", help="override the cutoff, RFC 3339")]
Yes = Annotated[bool, typer.Option("--yes", "-y", help="do not ask for confirmation")]
Warn = Annotated[bool, typer.Option("--warn", "-w", help="send Mattermost warnings afterwards")]
Overwrite = Annotated[bool, typer.Option("--overwrite", help="regenerate CSVs even if they were edited by hand")]


def _workspace() -> Workspace:
    ws = Workspace(Path.cwd())
    handlers = [
        logging.FileHandler(ws.log_path, encoding="utf-8"),
        RichHandler(console=console, level=logging.WARNING, show_path=False),
    ]
    handlers[0].setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][%(name)s] %(message)s"))
    handlers[1].setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    return ws


def _hw(value: str) -> int:
    digits = value.lower().removeprefix("h")
    if not digits.isdigit():
        raise typer.BadParameter(f"not a homework number: {value}")
    return int(digits)


def _deadline(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise typer.BadParameter("deadline needs a UTC offset, e.g. 2026-10-02T23:59:59+08:00")
    return parsed


def _grader(hw: str, teams: str = "", overwrite: bool = False) -> Grader:
    ws = _workspace()
    def progress(msg: str) -> None:
        console.print(f"[dim]• {escape(msg)}[/dim]")

    return Grader(ws, _hw(hw), teams, progress=progress, overwrite=overwrite)


NEXT_STEP = {
    STAGE_INDIVIDUAL: "next: `gradehelper warn {n}` (Mattermost reminders) if not sent yet; "
    "the individual score comes from `gradehelper individual-final {n}` one day later",
    STAGE_INDIVIDUAL_FINAL: "next: group run at the group deadline",
    STAGE_GROUP: "next: `gradehelper feedback {n}` opens the feedback issue in each team repo. Not uploaded to Canvas.",
    STAGE_FINAL: "next: `gradehelper feedback {n}` replies in the feedback issues; push hws/{hw}.csv to "
    "hw-scoreboard for TA review; after the review, pull and `gradehelper upload {n}`.",
}


def _print_rows(rows: list[Row], title: str, only_issues: bool = False) -> None:
    table = Table(title=title, show_lines=False)
    for col in ("Team", "Name", "ID", "Score", "Deductions", "Late"):
        table.add_column(col)
    shown = 0
    for r in rows:
        if only_issues and not r.grade.deductions and not r.grade.notes:
            continue
        shown += 1
        keys = ", ".join(d.key + (f"x{d.count}" if d.count > 1 else "") for d in r.grade.deductions)
        style = "red" if r.grade.score <= -2.5 else ("yellow" if r.grade.score < 0 else "")
        table.add_row(r.student.team, escape(r.student.display_name), r.student.id,
                      fmt_points(r.grade.score), keys, "yes" if r.meta.late else "", style=style)
    console.print(table)
    clean = sum(1 for r in rows if not r.grade.deductions)
    console.print(f"{len(rows)} students, {clean} without deductions" + (f", {shown} shown" if only_issues else ""))


def _warn(grader: Grader, stage: str, yes: bool) -> None:
    warnings = build_warnings(grader.rows(stage, stage_only=True), grader.hw.name, grader.ws.course.joj)
    if not warnings:
        console.print("[green]no warnings to send[/green]")
        return
    for w in warnings[:3]:
        console.rule(escape(f"{w.name} ({w.student_id})"))
        console.print(w.text, markup=False, highlight=False)  # exactly what is sent
    console.rule()
    console.print(f"{len(warnings)} warnings ready ({stage} stage), first {min(3, len(warnings))} shown above")
    if not yes and not typer.confirm("send them on Mattermost?"):
        console.print("not sent")
        return
    sent, failures = send_warnings(grader.mattermost(), warnings)
    console.print(f"[green]sent {sent}[/green]" + (f", [red]{len(failures)} failed[/red]" if failures else ""))
    for f in failures:
        console.print(f"  [red]{escape(f)}[/red]")


def _run_stage(grader: Grader, stage: str, deadline: datetime | None, warn: bool, yes: bool) -> None:
    runner = {
        STAGE_INDIVIDUAL: grader.run_individual,
        STAGE_INDIVIDUAL_FINAL: grader.run_individual_final,
        STAGE_GROUP: grader.run_group,
        STAGE_FINAL: grader.run_final,
    }[stage]
    runner(deadline)
    _print_rows(grader.rows(stage), f"{grader.hw.name} {stage}", only_issues=True)
    out = grader.ws.output_dir
    report = out / (f"{grader.hw.name}.csv" if stage in GROUP_RUNS else f"{grader.hw.name}indv.csv")
    console.print(f"report: {report}")
    console.print(f"[bold]{NEXT_STEP[stage].format(n=grader.hw_number, hw=grader.hw.name)}[/bold]")
    if warn:
        _warn(grader, stage, yes)


def _guard(fn):
    """Turn expected failures into a one-line error instead of a traceback."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConfigError, FileNotFoundError, LookupError, ValueError, HandEditedError) as e:
            console.print(f"[red]error:[/red] {escape(str(e))}")
            raise typer.Exit(1) from e

    return wrapper


@app.command()
@_guard
def run(hw: Hw, teams: Teams = "", warn: Warn = False, yes: Yes = False, overwrite: Overwrite = False) -> None:
    """One click: pick individual / individual-final / group / final from the deadlines and run it."""
    grader = _grader(hw, teams, overwrite)
    course = grader.ws.course
    stage, reason = suggest_stage(grader.scheduled, grader.run_times(), course.final_grace_hours,
                                  individual_grace_hours=course.individual_grace_hours)
    console.print(f"[dim]deadlines from {escape(grader.deadline_source)}[/dim]")
    console.print(f"[bold]{grader.hw.name}: {stage} stage[/bold] ({escape(reason)})")
    if not yes and not typer.confirm("continue?", default=True):
        raise typer.Exit()
    _run_stage(grader, stage, None, warn, yes)


@app.command()
@_guard
def individual(
    hw: Hw, teams: Teams = "", deadline: Deadline = None, warn: Warn = False, yes: Yes = False,
    overwrite: Overwrite = False,
) -> None:
    """1. Individual deadline: branches, individual PRs, PR descriptions, JOJ; reminders only. Aliases: indv, i."""
    _run_stage(_grader(hw, teams, overwrite), STAGE_INDIVIDUAL, _deadline(deadline), warn, yes)


@app.command("individual-final")
@_guard
def individual_final(
    hw: Hw, teams: Teams = "", deadline: Deadline = None, warn: Warn = False, yes: Yes = False,
    overwrite: Overwrite = False,
) -> None:
    """2. Individual deadline + 1 day: the same checks again; this is the individual score. Alias: indv-final."""
    _run_stage(_grader(hw, teams, overwrite), STAGE_INDIVIDUAL_FINAL, _deadline(deadline), warn, yes)


@app.command()
@_guard
def group(
    hw: Hw, teams: Teams = "", deadline: Deadline = None, warn: Warn = False, yes: Yes = False,
    overwrite: Overwrite = False,
) -> None:
    """3. Group deadline: tag, reviews, release, lateness; then `feedback` opens the issues. Alias: g."""
    _run_stage(_grader(hw, teams, overwrite), STAGE_GROUP, _deadline(deadline), warn, yes)


@app.command()
@_guard
def final(
    hw: Hw, teams: Teams = "", deadline: Deadline = None, warn: Warn = False, yes: Yes = False,
    overwrite: Overwrite = False,
) -> None:
    """4. Group deadline + 1 day: full grading for TA review; then `feedback` replies. Alias: f."""
    _run_stage(_grader(hw, teams, overwrite), STAGE_FINAL, _deadline(deadline), warn, yes)


FEEDBACK_STATE = {
    OPEN: "[yellow]will open[/yellow]",
    EXISTS: "[dim]issue #{issue} exists, skipped[/dim]",
    REPLY: "[yellow]will reply in #{issue}[/yellow]",
    REPLIED: "[dim]already answered in #{issue}, skipped[/dim]",
    NO_ISSUE: "[red]no {title!r} issue; open it first with --stage group[/red]",
}


@app.command()
@_guard
def feedback(
    hw: Hw,
    stage: Annotated[Optional[str], typer.Option(help="group (open the issues) or final (reply in them); "
                                                 "default: the latest of the two runs")] = None,
    teams: Teams = "",
    yes: Yes = False,
) -> None:
    """Feedback issue in each team repo: opened after the group run, answered after the final run (as the bot)."""
    grader = _grader(hw, teams)
    stage = stage or grader.latest_group_stage()
    if stage is None:
        raise ValueError(f"run `gradehelper group {grader.hw_number}` first")
    if stage not in GROUP_RUNS:
        raise typer.BadParameter(f"--stage must be one of {GROUP_RUNS}")
    if stage not in grader.results():
        raise ValueError(f"no {stage} result yet; run `gradehelper {stage} {grader.hw_number}` first")
    bot = grader.bot_gitea  # checked before the preview: a missing GITEA_BOT_ACCESS_TOKEN stops here
    plan = plan_feedback(grader.gitea, stage, grader.rows(stage, stage_only=True), grader.roster.teams,
                         grader.hw.name, grader.ws.course.feedback)
    for item in plan:
        state = FEEDBACK_STATE[item.action].format(issue=item.issue, title=item.title)
        console.print(f"{item.team.repo}: {state}")
    todo = [p for p in plan if p.todo]
    if not todo:
        console.print("[green]nothing to post[/green]")
        return
    for item in todo[:3]:
        console.rule(escape(f"{item.team.repo}: {item.title}"))
        console.print(item.body, markup=False, highlight=False)  # exactly what is posted
    console.rule()
    what = f"open {len(todo)} issue(s)" if stage == STAGE_GROUP else f"post {len(todo)} reply(ies)"
    console.print(f"first {min(3, len(todo))} shown above; issues are assigned to all collaborators")
    if not yes and not typer.confirm(f"{what} on Gitea as the bot?"):
        console.print("not posted")
        return
    posted, failures = post_feedback(bot, plan)
    console.print(f"[green]posted {', '.join(posted) or 'none'}[/green]")
    for f in failures:
        console.print(f"  [red]{escape(f)}[/red]")


@app.command()
@_guard
def warn(
    hw: Hw,
    stage: Annotated[str, typer.Option(help="individual, individual_final, group or final")] = STAGE_INDIVIDUAL,
    yes: Yes = False,
) -> None:
    """Preview and send Mattermost warnings from the saved results."""
    if stage not in RUNS:
        raise typer.BadParameter(f"stage must be one of {RUNS}")
    _warn(_grader(hw), stage, yes)


@app.command()
@_guard
def show(hw: Hw, all_rows: Annotated[bool, typer.Option("--all")] = False, overwrite: Overwrite = False) -> None:
    """Show the latest saved results and refresh the CSVs (overrides applied)."""
    grader = _grader(hw, overwrite=overwrite)
    stage = grader.latest_stage()
    for problem in grader.write_reports():
        console.print(f"[yellow]{escape(problem)}[/yellow]")
    _print_rows(grader.rows(stage), f"{grader.hw.name} {stage}", only_issues=not all_rows)


@app.command()
@_guard
def upload(
    hw: Hw,
    yes: Yes = False,
    force: Annotated[bool, typer.Option(help="re-post grades that were already uploaded unchanged")] = False,
) -> None:
    """Upload hws/hN.csv to Canvas after the final run and the TA review (hand edits included)."""
    grader = _grader(hw)
    csv_path = grader.upload_source()
    rows = read_final_csv(csv_path)
    canvas = grader.canvas()
    assignment = canvas.find_assignment(grader.hw_number, grader.hw.canvas_assignment_id)
    ledger = ledger_path(grader.ws.output_dir, grader.hw_number)
    plan = plan_upload(rows, assignment, canvas.students(), ledger, force)

    console.print(f"assignment: [bold]{escape(assignment.name)}[/bold] (id {assignment.id}); source {csv_path}")
    table = Table(title="grades to post")
    for col in ("ID", "Name", "Canvas now", "New", "Comment"):
        table.add_column(col)
    for g in plan.to_send:
        current = "-" if g.current_score is None else fmt_points(g.current_score)
        table.add_row(g.row.id, escape(g.row.name), current, fmt_points(g.row.score), escape(g.row.comment[:60]))
    console.print(table)
    console.print(f"{len(plan.to_send)} to post, {len(plan.grades) - len(plan.to_send)} unchanged since last upload")
    for r in plan.unmatched_rows:
        console.print(f"[yellow]CSV row with no Canvas submission:[/yellow] {r.id} {escape(r.name)}")
    for u in plan.unmatched_users:
        console.print(f"[dim]Canvas student not in CSV: {escape(u)}[/dim]")
    if not plan.to_send:
        return
    if not yes and not typer.confirm(f"post {len(plan.to_send)} grades and comments to Canvas?"):
        console.print("not uploaded")
        return
    sent, failures = apply_upload(plan, ledger)
    console.print(f"[green]uploaded {sent}[/green]" + (f", [red]{len(failures)} failed[/red]" if failures else ""))
    for f in failures:
        console.print(f"  [red]{escape(f)}[/red]")


@app.command()
def ui(
    port: Annotated[int, typer.Option()] = 8151,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Local web page for reviewing, adjusting, warning and uploading."""
    import threading
    import webbrowser

    import uvicorn

    from .web.app import create_app

    ws = _workspace()
    url = f"http://127.0.0.1:{port}"
    console.print(f"gradehelper UI on {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(ws), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def doctor() -> None:
    """Check configuration, credentials and tools before a grading run."""
    ws = _workspace()
    ok = True

    def report(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        console.print(("[green]✓[/green] " if good else "[red]✗[/red] ") + escape(text))

    try:
        course = ws.course
        report(True, f"config/course.toml: {len(course.rubric)} rubric items")
    except ConfigError as e:
        report(False, str(e))
        raise typer.Exit(1) from e
    for n in ws.homework_numbers():
        try:
            g = Grader(ws, n)
            h, dl = g.hw, g.deadlines
            report(True, f"h{n}: {h.language}, {len(h.mandatory)} mandatory files; deadlines from "
                         f"{g.deadline_source}: individual {dl.individual:%m-%d %H:%M}, "
                         f"individual +1 day {dl.individual_final:%m-%d %H:%M}, "
                         f"group {dl.group:%m-%d %H:%M}, final {dl.final:%m-%d %H:%M}")
        except ConfigError as e:
            report(False, str(e))
    s = ws.secrets
    for group_name, fields in {
        "Canvas": ("canvas_access_token", "canvas_course_id"),
        "Gitea": ("gitea_access_token", "gitea_org_name"),
        "Gitea bot (feedback issues)": ("gitea_bot_access_token",),
        "Mattermost": ("mattermost_access_token", "mattermost_team"),
    }.items():
        missing = s.missing(*fields)
        report(not missing, f"{group_name} credentials" + (f" missing {', '.join(missing)}" if missing else ""))
    try:
        roster = load_roster(ws.roster_path)
        report(True, f"{ws.roster_path.name}: {len(roster.graded)} students in {len(roster.teams)} teams")
    except FileNotFoundError as e:
        report(False, str(e))
    report(shutil.which("ctags") is not None, "ctags installed (needed for C/C++ code quality)")
    report(shutil.which("git") is not None, "git installed")
    raise typer.Exit(0 if ok else 1)


@roster_app.command("sync")
@_guard
def roster_sync(yes: Yes = False) -> None:
    """Re-fetch students and hteam groups from Canvas into hteams.csv (shows changes, asks first)."""
    ws = _workspace()
    from .clients.canvas import CanvasClient

    roster, emails = roster_from_canvas(CanvasClient(ws.secrets).course)
    old = load_roster(ws.roster_path) if ws.roster_path.exists() else None
    if old is not None:
        changes = diff_rosters(old, roster)
        if not changes:
            console.print("no team changes on Canvas")
        for kind in CHANGE_ORDER:
            group = [c for c in changes if c.kind == kind]
            if group:
                console.print(f"[bold]{len(group)} {kind}[/bold]")
            for c in group:
                move = f"{c.before or '(no team)'} -> {c.after or '(no team)'}"
                console.print(f"  {escape(c.student.display_name)} ({c.student.id}): {move}")
        if changes and not yes and not typer.confirm(f"overwrite {ws.roster_path.name}?"):
            console.print("not saved")
            raise typer.Exit()
    save_roster(ws.roster_path, roster, emails)
    console.print(f"saved {len(roster.students)} students, {len(roster.teams)} teams to {ws.roster_path}")


@roster_app.command("show")
@_guard
def roster_show() -> None:
    ws = _workspace()
    roster = load_roster(ws.roster_path)
    for team in roster.teams:
        members = ", ".join(f"{s.display_name} ({s.id})" for s in team.members)
        console.print(f"[bold]{team.key}[/bold] {escape(members)}")
    console.print(f"{len(roster.graded)} students in {len(roster.teams)} teams")


# Short aliases (hidden from the command list; mentioned in each command's help).
for _alias, _command in (
    ("indv", individual), ("i", individual), ("indv-final", individual_final), ("g", group), ("f", final),
):
    app.command(_alias, hidden=True)(_command)
