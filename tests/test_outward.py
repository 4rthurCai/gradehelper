"""Warnings, Canvas upload, roster sync, CLI and web UI with fake remote services."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from gradehelper import cli
from gradehelper.config import STAGE_GROUP, STAGE_INDIVIDUAL
from gradehelper.models import Finding, StageResult, StudentMeta
from gradehelper.notify import build_warnings, send_warnings
from gradehelper.pipeline import Grader
from gradehelper.report import read_final_csv, result_path, save_result
from gradehelper.roster import load_roster, roster_from_canvas, save_roster
from gradehelper.upload import apply_upload, ledger_path, plan_upload
from gradehelper.web.app import create_app

from .conftest import ALICE, BOB, CAROL, DAVE, LONER, ns


def seed_results(ws) -> None:
    students = (ALICE.id, BOB.id, CAROL.id, DAVE.id)
    save_result(result_path(ws.output_dir, 1, STAGE_INDIVIDUAL), StageResult(
        1, STAGE_INDIVIDUAL, "2026-10-02T00:00:00+00:00", students=students,
        findings=(Finding(BOB.id, "indvFailSubmit", 1, ("individual branch missing",)),
                  Finding(CAROL.id, "noIndividualPR")),
    ))
    save_result(result_path(ws.output_dir, 1, STAGE_GROUP), StageResult(
        1, STAGE_GROUP, "2026-10-04T00:00:00+00:00", students=students,
        findings=(Finding(CAROL.id, "noReview"), Finding(DAVE.id, "noReview")),
        meta={s: StudentMeta(False, "2026-10-03T20:00:00+08:00") for s in students},
    ))
    Grader(ws, 1).write_reports()


class FakeMattermost:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    def post_to_student_channel(self, student_id, message):
        if student_id in self.fail_for:
            raise RuntimeError("channel not found")
        self.sent.append((student_id, message))


class FakeSubmission:
    def __init__(self, user_id, score=None):
        self.user_id, self.score, self.edits = user_id, score, []

    def edit(self, **data):
        self.edits.append(data)


class FakeAssignment:
    id, name = 42, "h1 Homework 1"

    def __init__(self, submissions):
        self.submissions = submissions

    def get_submissions(self):
        return self.submissions


def canvas_user(i, student, **extra):
    return ns(id=i, name=student.display_name, login_id=student.id,
              email=f"{student.jaccount}@sjtu.edu.cn", **extra)


# ---------- warnings ----------

def test_warnings_are_per_stage_and_combined(workspace):
    seed_results(workspace)
    g = Grader(workspace, 1)
    individual = {w.student_id: w for w in build_warnings(g.rows(STAGE_INDIVIDUAL, stage_only=True), "h1")}
    group = {w.student_id for w in build_warnings(g.rows(STAGE_GROUP, stage_only=True), "h1")}
    assert set(individual) == {BOB.id, CAROL.id}
    assert "individual branch missing" in individual[BOB.id].text
    assert individual[BOB.id].text.endswith(f"@{BOB.jaccount}")
    assert group == {CAROL.id, DAVE.id}  # individual issues are not repeated at the group stage

    mm = FakeMattermost(fail_for={CAROL.id})
    sent, failures = send_warnings(mm, list(individual.values()))
    assert sent == 1 and len(failures) == 1 and mm.sent[0][0] == BOB.id


# ---------- upload ----------

def test_upload_plan_matching_ledger_and_force(workspace):
    seed_results(workspace)
    rows = read_final_csv(workspace.output_dir / "h1.csv")
    students = [
        canvas_user(1, ALICE),
        canvas_user(2, BOB, ),
        ns(id=3, name="Li Si", login_id="lisi", email="lisi@sjtu.edu.cn"),  # matched by jaccount
        ns(id=9, name="Auditor", login_id="auditor", email="auditor@sjtu.edu.cn"),
    ]
    subs = [FakeSubmission(1), FakeSubmission(2, score=0), FakeSubmission(3), FakeSubmission(9)]
    ledger = ledger_path(workspace.output_dir, 1)

    plan = plan_upload(rows, FakeAssignment(subs), students, ledger, force=False)
    assert {g.row.id for g in plan.to_send} == {ALICE.id, BOB.id, CAROL.id}
    assert [r.id for r in plan.unmatched_rows] == [DAVE.id]
    assert plan.unmatched_users == ("Auditor (auditor)",)

    sent, failures = apply_upload(plan, ledger)
    assert (sent, failures) == (3, [])
    assert subs[1].edits[0]["submission"] == {"posted_grade": -1.0}
    assert "individual branch missing" in subs[1].edits[0]["comment"]["text_comment"]

    again = plan_upload(rows, FakeAssignment(subs), students, ledger, force=False)
    assert again.to_send == ()
    assert len(plan_upload(rows, FakeAssignment(subs), students, ledger, force=True).to_send) == 3


def test_final_csv_rejects_bad_scores(tmp_path):
    path = tmp_path / "h1.csv"
    path.write_text("Name,ID,Jaccount,Team,Score,Late,Release Time,Comments\nA,1,a,t,minus one,No,,x\n")
    with pytest.raises(ValueError, match="not a number"):
        read_final_csv(path)


# ---------- roster ----------

def test_roster_from_canvas_and_roundtrip(tmp_path):
    users = [canvas_user(1, ALICE, sis_user_id=ALICE.id), canvas_user(2, BOB), canvas_user(5, CAROL)]
    groups = [
        ns(name="hteam 1", get_memberships=lambda: [ns(user_id=1), ns(user_id=2)]),
        ns(name="Project Team 3", get_memberships=lambda: [ns(user_id=5)]),
    ]
    course = ns(get_users=lambda enrollment_type: users, get_groups=lambda: groups)
    roster, emails = roster_from_canvas(course)
    assert {s.id: s.team for s in roster.students} == {ALICE.id: "hteam-01", BOB.id: "hteam-01", CAROL.id: ""}
    path = tmp_path / "hteams.csv"
    save_roster(path, roster, emails)
    assert load_roster(path) == roster


# ---------- CLI ----------

def test_cli_show_and_doctor(workspace, monkeypatch):
    seed_results(workspace)
    monkeypatch.chdir(workspace.root)
    runner = CliRunner()
    shown = runner.invoke(cli.app, ["show", "h1", "--all"])
    assert shown.exit_code == 0, shown.output
    assert "Li Si" in shown.output and "noReview" in shown.output
    doctor = runner.invoke(cli.app, ["doctor"])
    assert "4 students in 2 teams" in doctor.output
    assert doctor.exit_code == 1  # no Canvas/Mattermost credentials in the test workspace


def test_cli_reports_config_errors_without_traceback(workspace, monkeypatch):
    monkeypatch.chdir(workspace.root)
    result = CliRunner().invoke(cli.app, ["show", "h99"])
    assert result.exit_code == 1 and "config file not found" in result.output


def test_cli_warn_needs_confirmation(workspace, monkeypatch):
    seed_results(workspace)
    monkeypatch.chdir(workspace.root)
    mm = FakeMattermost()
    monkeypatch.setattr(Grader, "mattermost", lambda self: mm)
    declined = CliRunner().invoke(cli.app, ["warn", "1"], input="n\n")
    assert "not sent" in declined.output and mm.sent == []
    accepted = CliRunner().invoke(cli.app, ["warn", "1", "--yes"])
    assert "sent 2" in accepted.output and len(mm.sent) == 2


# ---------- web ----------

@pytest.fixture
def web(workspace):
    seed_results(workspace)
    client = TestClient(create_app(workspace))
    page = client.get("/hw/1").text
    token = re.search(r'"X-Token": "([^"]+)"', page).group(1)
    return client, token


def test_web_pages_render(web):
    client, _ = web
    assert client.get("/").status_code == 200
    page = client.get("/hw/1?stage=group").text
    assert "Li Si" in page and "noReview" in page
    assert client.get("/hw/1/warnings?stage=individual").status_code == 200


def test_web_post_requires_token_and_local_origin(web):
    client, token = web
    form = {"student": DAVE.id, "remove": "noReview", "stage": "group"}
    assert client.post("/hw/1/override", data=form).status_code == 403
    evil = {"X-Token": token, "Origin": "https://evil.example"}
    assert client.post("/hw/1/override", data=form, headers=evil).status_code == 403


def test_web_override_updates_reports(web, workspace):
    client, token = web
    response = client.post(
        "/hw/1/override",
        data={"student": DAVE.id, "remove": ["noReview"], "note": "reviewed in person", "stage": "group"},
        headers={"X-Token": token},
    )
    assert response.status_code == 200
    row = {r.id: r for r in read_final_csv(workspace.output_dir / "h1.csv")}[DAVE.id]
    assert row.score == 0 and "reviewed in person" in row.comment
    client.post("/hw/1/override", data={"student": DAVE.id, "stage": "group"}, headers={"X-Token": token})
    assert {r.id: r for r in read_final_csv(workspace.output_dir / "h1.csv")}[DAVE.id].score == -1


def test_web_send_warnings_checks_preview_count(web, monkeypatch):
    client, token = web
    mm = FakeMattermost()
    monkeypatch.setattr(Grader, "mattermost", lambda self: mm)
    stale = client.post("/hw/1/warnings", data={"stage": "individual", "count": 5}, headers={"X-Token": token})
    assert stale.status_code == 409 and mm.sent == []
    ok = client.post("/hw/1/warnings", data={"stage": "individual", "count": 2}, headers={"X-Token": token})
    assert ok.status_code == 200 and len(mm.sent) == 2


# ---------- late issues ----------

class FakeIssueGitea:
    def __init__(self, existing):
        self.existing, self.created = existing, []

    def issue_titles(self, repo):
        return self.existing.get(repo, set())

    def collaborators(self, repo):
        return ["bot", "member"]

    def create_issue(self, repo, title, body, assignees):
        self.created.append((repo, title, body, tuple(assignees)))
        return 7


def test_late_issues_only_for_late_teams_and_skip_existing(workspace):
    from gradehelper.config import LateIssueSettings
    from gradehelper.late_issues import late_teams, open_late_issues, plan_late_issues
    from gradehelper.models import Roster

    roster = Roster((ALICE, BOB, CAROL, DAVE))
    result = StageResult(1, STAGE_GROUP, "t", students=(ALICE.id, BOB.id, CAROL.id, DAVE.id),
                         meta={ALICE.id: StudentMeta(False), BOB.id: StudentMeta(False),
                               CAROL.id: StudentMeta(True), DAVE.id: StudentMeta(True)})
    teams = late_teams(roster, result)
    assert [t.key for t in teams] == ["hteam-02"]

    gitea = FakeIssueGitea({})
    plan = plan_late_issues(gitea, teams, "h1", LateIssueSettings())
    opened, failures = open_late_issues(gitea, plan)
    assert opened == ["hteam02#7"] and failures == []
    assert gitea.created == [("hteam02", "h1 feedback", "Late submission, no feedback provided.", ("bot", "member"))]

    again = FakeIssueGitea({"hteam02": {"h1 feedback"}})
    assert open_late_issues(again, plan_late_issues(again, teams, "h1", LateIssueSettings())) == ([], [])
    assert again.created == []


def test_web_shows_three_runs_and_late_issue_page(web):
    client, _ = web
    page = client.get("/hw/1").text
    assert "1 · Individual" in page and "3 · Group +1 day" in page
    assert "do not upload yet" in client.get("/hw/1?stage=final").text
    assert "No late teams." in client.get("/hw/1/late-issues").text


def test_web_reports_hand_edited_csv(web, workspace):
    client, _ = web
    sheet = workspace.output_dir / "h1.csv"
    sheet.write_text(sheet.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
    page = client.get("/hw/1?stage=group").text
    assert "was edited or not written by gradehelper" in page
    assert sheet.read_text(encoding="utf-8").endswith("edited\n")


def test_cli_warning_preview_shows_brackets_verbatim(workspace, monkeypatch):
    seed_results(workspace)
    monkeypatch.chdir(workspace.root)
    out = CliRunner().invoke(cli.app, ["warn", "1"], input="n\n").output
    assert "[h1] Auto-grader warning:" in out


@pytest.mark.parametrize("alias", ["individual", "indv", "i"])
def test_individual_aliases_and_short_help(alias):
    result = CliRunner().invoke(cli.app, [alias, "-h"])
    assert result.exit_code == 0 and "Aliases: indv, i" in result.output and "-t" in result.output


def test_short_aliases_for_group_and_final():
    assert "Alias: g" in CliRunner().invoke(cli.app, ["g", "-h"]).output
    assert "Alias: f" in CliRunner().invoke(cli.app, ["f", "-h"]).output


def test_roster_diff_categories():
    from gradehelper.models import Roster, Student
    from gradehelper.roster import JOINED_COURSE, JOINED_TEAM, LEFT_COURSE, LEFT_TEAM, MOVED, diff_rosters

    newcomer = Student("520000000006", "Sun Qi", "sunqi", "hteam-02")
    old = Roster((ALICE, BOB, CAROL, DAVE, LONER))
    new = Roster((
        ALICE,  # unchanged
        Student(BOB.id, BOB.name, BOB.jaccount, ""),  # removed from his team
        Student(CAROL.id, CAROL.name, CAROL.jaccount, "hteam-01"),  # moved
        Student(LONER.id, LONER.name, LONER.jaccount, "hteam-02"),  # got a team
        newcomer,  # joined the course; DAVE dropped it
    ))
    changes = diff_rosters(old, new)
    assert {(c.kind, c.student.id, c.before, c.after) for c in changes} == {
        (LEFT_COURSE, DAVE.id, "hteam-02", ""),
        (LEFT_TEAM, BOB.id, "hteam-01", ""),
        (MOVED, CAROL.id, "hteam-02", "hteam-01"),
        (JOINED_TEAM, LONER.id, "", "hteam-02"),
        (JOINED_COURSE, newcomer.id, "", "hteam-02"),
    }
    assert changes[0].kind == LEFT_COURSE
    assert diff_rosters(old, old) == []


def test_cli_roster_sync_shows_changes_and_can_decline(workspace, monkeypatch):
    from gradehelper.clients import canvas as canvas_client

    monkeypatch.chdir(workspace.root)
    users = [canvas_user(1, ALICE), canvas_user(2, BOB)]  # Carol and Dave dropped
    groups = [ns(name="hteam 1", get_memberships=lambda: [ns(user_id=1)])]  # Bob left the team
    course = ns(get_users=lambda enrollment_type: users, get_groups=lambda: groups)
    monkeypatch.setattr(canvas_client.CanvasClient, "__init__", lambda self, secrets: None)
    monkeypatch.setattr(canvas_client.CanvasClient, "course", course, raising=False)
    before = (workspace.root / "hteams.csv").read_text(encoding="utf-8")
    out = CliRunner().invoke(cli.app, ["roster", "sync"], input="n\n").output
    assert "3 left the course" in out and "1 no longer in a team" in out and "Li Si" in out
    assert "not saved" in out
    assert (workspace.root / "hteams.csv").read_text(encoding="utf-8") == before
    CliRunner().invoke(cli.app, ["roster", "sync", "--yes"])
    assert "520000000003" not in (workspace.root / "hteams.csv").read_text(encoding="utf-8")
