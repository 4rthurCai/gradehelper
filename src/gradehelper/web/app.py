"""FastAPI app behind `gradehelper ui`. Bound to 127.0.0.1; every POST needs the session token."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import RUNS, STAGE_FINAL, STAGE_GROUP, STAGE_INDIVIDUAL, Workspace
from ..grading import fmt_points
from ..late_issues import late_teams, open_late_issues, plan_late_issues
from ..notify import build_warnings, send_warnings
from ..overrides import Override, load_overrides, overrides_path, save_overrides
from ..pipeline import Grader, suggest_stage
from ..report import read_final_csv
from ..upload import apply_upload, ledger_path, plan_upload
from .jobs import JobRunner

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.filters["pts"] = fmt_points


def _plan_digest(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:16]


def create_app(ws: Workspace) -> FastAPI:
    app = FastAPI(title="gradehelper", docs_url=None, redoc_url=None)
    token = secrets.token_urlsafe(24)
    jobs = JobRunner()

    def grader(hw: int) -> Grader:
        return Grader(ws, hw)

    def render(request: Request, name: str, **context) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(request, name, {"token": token, "jobs": jobs, **context})

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.method != "GET":
            # Pages send the token as a header (htmx hx-headers); other sites cannot read it.
            origin = request.headers.get("origin", "")
            sent = request.headers.get("x-token", "")
            if not secrets.compare_digest(sent, token) or (
                origin and not origin.startswith(("http://127.0.0.1", "http://localhost"))
            ):
                return HTMLResponse("forbidden: missing or wrong session token", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        homeworks = []
        for n in ws.homework_numbers():
            g = grader(n)
            results = g.results()
            stage, reason = suggest_stage(g.hw, g.run_times(), ws.course.final_grace_hours)
            homeworks.append({"hw": g.hw, "results": results, "suggested": stage, "reason": reason})
        return render(request, "index.html", homeworks=homeworks)

    @app.get("/hw/{hw}", response_class=HTMLResponse)
    def homework(request: Request, hw: int, stage: str = "", issues: bool = False):
        g = grader(hw)
        results = g.results()
        stage = stage if stage in RUNS else (g.latest_group_stage() or STAGE_INDIVIDUAL)
        problems = g.write_reports()
        rows = g.rows(stage)
        if issues:
            rows = [r for r in rows if r.grade.deductions or r.grade.notes]
        overrides = load_overrides(overrides_path(ws.output_dir, hw), set(ws.course.rubric))
        by_student = {o.student: o for o in overrides if o.student}
        return render(
            request, "homework.html", g=g, stage=stage, rows=rows, results=results, issues=issues, problems=problems,
            rubric=ws.course.rubric, overrides=by_student, team_overrides=[o for o in overrides if o.team],
        )

    @app.post("/hw/{hw}/run/{stage}")
    def run(hw: int, stage: str, teams: str = Form(""), deadline: str = Form("")):
        if stage not in RUNS:
            raise HTTPException(404)
        from datetime import datetime

        when = datetime.fromisoformat(deadline) if deadline.strip() else None

        def work(job):
            g = Grader(ws, hw, teams, progress=job.say)
            {STAGE_INDIVIDUAL: g.run_individual, STAGE_GROUP: g.run_group, STAGE_FINAL: g.run_final}[stage](when)

        try:
            jobs.start(f"h{hw} {stage}" + (f" (teams {teams})" if teams else ""), work)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return RedirectResponse(f"/hw/{hw}?stage={stage}", status_code=303)

    @app.get("/job", response_class=HTMLResponse)
    def job(request: Request):
        return render(request, "_job.html")

    @app.post("/hw/{hw}/override")
    def save_override(
        hw: int,
        student: str = Form(...),
        remove: list[str] = Form([]),
        score: str = Form(""),
        note: str = Form(""),
        stage: str = Form(""),
    ):
        path = overrides_path(ws.output_dir, hw)
        keys = set(ws.course.rubric)
        current = [o for o in load_overrides(path, keys) if o.student != student]
        unknown = set(remove) - keys
        if unknown:
            raise HTTPException(400, f"unknown rubric keys {sorted(unknown)}")
        if remove or score.strip() or note.strip():
            current.append(Override(
                student=student,
                remove=tuple(remove),
                score=float(score) if score.strip() else None,
                note=note.strip(),
            ))
        save_overrides(path, current)
        grader(hw).write_reports()  # a hand-edited CSV is kept; the page shows why
        return RedirectResponse(f"/hw/{hw}?stage={stage}#s{student}", status_code=303)

    @app.get("/hw/{hw}/warnings", response_class=HTMLResponse)
    def warnings_preview(request: Request, hw: int, stage: str = STAGE_INDIVIDUAL):
        g = grader(hw)
        warnings = build_warnings(g.rows(stage, stage_only=True), g.hw.name)
        return render(request, "warnings.html", g=g, stage=stage, warnings=warnings, sent=None)

    @app.post("/hw/{hw}/warnings", response_class=HTMLResponse)
    def warnings_send(request: Request, hw: int, stage: str = Form(...), count: int = Form(...)):
        g = grader(hw)
        warnings = build_warnings(g.rows(stage, stage_only=True), g.hw.name)
        if len(warnings) != count:
            raise HTTPException(409, "results changed since the preview; reload and check again")
        sent, failures = send_warnings(g.mattermost(), warnings)
        return render(request, "warnings.html", g=g, stage=stage, warnings=warnings, sent=(sent, failures))

    def _plan(hw: int, force: bool):
        g = grader(hw)
        rows = read_final_csv(ws.output_dir / f"h{hw}.csv")
        canvas = g.canvas()
        assignment = canvas.find_assignment(hw, g.hw.canvas_assignment_id)
        plan = plan_upload(rows, assignment, canvas.students(), ledger_path(ws.output_dir, hw), force)
        digest = _plan_digest([{"id": p.row.id, "score": p.row.score, "comment": p.row.comment} for p in plan.to_send])
        return g, assignment, plan, digest

    @app.get("/hw/{hw}/upload", response_class=HTMLResponse)
    def upload_preview(request: Request, hw: int, force: bool = False):
        try:
            g, assignment, plan, digest = _plan(hw, force)
        except (FileNotFoundError, LookupError, ValueError) as e:
            return render(request, "upload.html", g=grader(hw), error=str(e))
        return render(request, "upload.html", g=g, assignment=assignment, plan=plan, digest=digest,
                      force=force, result=None, error="")

    @app.post("/hw/{hw}/upload", response_class=HTMLResponse)
    def upload_apply(request: Request, hw: int, digest: str = Form(...), force: bool = Form(False)):
        g, assignment, plan, current = _plan(hw, force)
        if current != digest:
            raise HTTPException(409, "the CSV changed since the preview; reload and check again")
        result = apply_upload(plan, ledger_path(ws.output_dir, hw))
        return render(request, "upload.html", g=g, assignment=assignment, plan=plan, digest=digest,
                      force=force, result=result, error="")

    def _late_plan(hw: int):
        g = grader(hw)
        results = g.results()
        source = next((results[s] for s in (STAGE_GROUP, STAGE_FINAL) if s in results), None)
        teams = late_teams(g.full_roster, source) if source else []
        return g, plan_late_issues(g.gitea, teams, g.hw.name, ws.course.late_issue) if teams else []

    @app.get("/hw/{hw}/late-issues", response_class=HTMLResponse)
    def late_preview(request: Request, hw: int):
        g, plan = _late_plan(hw)
        return render(request, "late_issues.html", g=g, plan=plan, result=None)

    @app.post("/hw/{hw}/late-issues", response_class=HTMLResponse)
    def late_open(request: Request, hw: int, count: int = Form(...)):
        g, plan = _late_plan(hw)
        if sum(1 for p in plan if not p.exists) != count:
            raise HTTPException(409, "late teams changed since the preview; reload and check again")
        return render(request, "late_issues.html", g=g, plan=plan, result=open_late_issues(g.gitea, plan))

    return app
