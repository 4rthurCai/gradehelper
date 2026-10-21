"""The per-team feedback issue in each team repo.

After the group run: open "hN feedback" with the auto-grader result at the group deadline.
After the final run: reply in the same issue with the result 24 hours later; teams without a
release by the group deadline get only the late note. TAs then answer in the issue by hand.

All reads (existing issues and replies, collaborators) use the reader client (the TA's
token); the writer client (the course bot) only creates issues and comments, so write:issue
is enough for it. What is already on Gitea is never posted twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .clients.gitea import GiteaClient
from .config import STAGE_FINAL, STAGE_GROUP, FeedbackSettings
from .grading import fmt_points
from .models import Team
from .report import Row

log = logging.getLogger(__name__)

OPEN, REPLY = "open", "reply"  # actions that post something
EXISTS, REPLIED, NO_ISSUE = "exists", "replied", "no issue"  # skipped


@dataclass(frozen=True)
class FeedbackItem:
    team: Team
    action: str
    title: str
    body: str
    issue: int | None = None
    assignees: tuple[str, ...] = ()

    @property
    def todo(self) -> bool:
        return self.action in (OPEN, REPLY)


def _heading(stage: str, hw: str, settings: FeedbackSettings) -> str:
    text = settings.group_heading if stage == STAGE_GROUP else settings.final_heading
    return f"## {text.format(hw=hw)}"


def _problem_table(rows: list[Row]) -> list[str]:
    """One line per deduction; 'whole team' when every member has it."""
    members = len(rows)
    who: dict[tuple[str, int, float, str], list[str]] = {}
    for row in rows:
        for d in row.grade.deductions:
            who.setdefault((d.key, d.count, d.points, d.description), []).append(row.student.display_name)
    lines = ["| Problem | Who | Deduction |", "| --- | --- | --- |"]
    for (_, count, points, description), names in who.items():
        problem = description + (f" (x{count})" if count > 1 else "")
        target = "whole team" if len(names) == members else ", ".join(names)
        lines.append(f"| {problem} | {target} | {fmt_points(points)} |")
    return lines


def render_message(stage: str, rows: list[Row], hw: str, settings: FeedbackSettings) -> str:
    """The issue body (group run) or the reply (final run) for one team's rows."""
    if stage == STAGE_FINAL and any(r.meta.late for r in rows):
        return settings.late
    parts = [_heading(stage, hw, settings), ""]
    if not any(r.grade.deductions for r in rows):
        parts.append(settings.group_no_problem if stage == STAGE_GROUP else settings.final_no_problem)
        return "\n".join(parts) + "\n"
    parts += _problem_table(rows)
    details = list(dict.fromkeys(d for r in rows for d in r.grade.details))
    notes = list(dict.fromkeys(f"Note ({r.student.display_name}): {n}" for r in rows for n in r.grade.notes))
    if details or notes:
        parts += ["", "Details:", *(f"- {d}" for d in details), *(f"- {n}" for n in notes)]
    parts += ["", settings.group_problems if stage == STAGE_GROUP else settings.final_problems]
    return "\n".join(parts) + "\n"


def _is_our_reply(body: str, hw: str, settings: FeedbackSettings) -> bool:
    text = (body or "").strip()
    return text.startswith(_heading(STAGE_FINAL, hw, settings)) or text == settings.late.strip()


def plan_feedback(
    reader: GiteaClient, stage: str, rows: list[Row], teams: tuple[Team, ...], hw: str, settings: FeedbackSettings
) -> list[FeedbackItem]:
    """What `post_feedback` would do per team. rows: that run's own deductions (stage_only)."""
    if stage not in (STAGE_GROUP, STAGE_FINAL):
        raise ValueError(f"feedback is posted after the group or final run, not {stage}")
    title = settings.title.format(hw=hw)
    plan = []
    for team in teams:
        team_rows = [r for r in rows if r.student.team == team.key]
        if not team_rows:
            continue  # not checked in this run (e.g. a partial run)
        body = render_message(stage, team_rows, hw, settings)
        found = min((i for i in reader.repo_issues(team.repo) if i.title == title), key=lambda i: i.number,
                    default=None)
        issue = found.number if found else None
        if stage == STAGE_GROUP:
            if found is not None:
                plan.append(FeedbackItem(team, EXISTS, title, body, issue))
            else:
                plan.append(FeedbackItem(team, OPEN, title, body, assignees=tuple(reader.collaborators(team.repo))))
        elif found is None:
            plan.append(FeedbackItem(team, NO_ISSUE, title, body))
        else:
            # The old late-issues command put the late note in the issue body itself.
            replied = _is_our_reply(getattr(found, "body", ""), hw, settings) or any(
                _is_our_reply(c.body, hw, settings) for c in reader.issue_comments(team.repo, issue)
            )
            plan.append(FeedbackItem(team, REPLIED if replied else REPLY, title, body, issue))
    return plan


def post_feedback(writer: GiteaClient, plan: list[FeedbackItem]) -> tuple[list[str], list[str]]:
    """Returns (posted as repo#issue, failures). Items that need nothing are skipped."""
    posted, failures = [], []
    for item in (p for p in plan if p.todo):
        repo = item.team.repo
        try:
            if item.action == OPEN:
                number = writer.create_issue(repo, item.title, item.body, list(item.assignees))
            else:
                writer.create_comment(repo, item.issue, item.body)
                number = item.issue
        except Exception as e:
            log.error("cannot post feedback in %s: %s", repo, e)
            failures.append(f"{repo}: {e}")
            continue
        posted.append(f"{repo}#{number}")
        log.info("%s %s#%s: %s", item.action, repo, number, item.title)
    return posted, failures
