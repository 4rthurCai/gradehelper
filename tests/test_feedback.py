"""Feedback issues: one per team after the group run, answered in the same issue after the final run."""

from __future__ import annotations

from gradehelper.config import STAGE_FINAL, STAGE_GROUP, STAGE_INDIVIDUAL_FINAL, FeedbackSettings
from gradehelper.feedback import plan_feedback, post_feedback, render_message
from gradehelper.models import Finding, StageResult, StudentMeta
from gradehelper.overrides import Override, overrides_path, save_overrides
from gradehelper.pipeline import Grader
from gradehelper.report import result_path, save_result

from .conftest import ALICE, BOB, CAROL, DAVE, FakeIssues, ns

SETTINGS = FeedbackSettings()
STUDENTS = (ALICE.id, BOB.id, CAROL.id, DAVE.id)


def save(ws, stage: str, findings=(), late=()) -> None:
    meta = {s: StudentMeta(late=s in late) for s in STUDENTS}
    save_result(result_path(ws.output_dir, 1, stage), StageResult(
        1, stage, "2026-10-04T00:00:00+00:00", students=STUDENTS, findings=tuple(findings), meta=meta,
    ))


def team_rows(ws, stage: str, team: str):
    return [r for r in Grader(ws, 1).feedback_rows(stage) if r.student.team == team]


# ---------- messages ----------

def test_group_issue_without_problems(workspace):
    save(workspace, STAGE_GROUP)
    body = render_message(STAGE_GROUP, team_rows(workspace, STAGE_GROUP, "hteam-01"), "h1", SETTINGS)
    assert body.startswith("## h1: auto-grader result at the group deadline\n")
    assert SETTINGS.group_no_problem in body
    assert "|" not in body


def test_group_issue_lists_problems_with_who_points_and_details(workspace):
    untidy = ("tags/h1 redundant files: a.out",)
    joj = ("JOJ h1-release #3/ex2 0% (< 25%)", "JOJ h1-release #3/ex5 0% (< 25%)")
    save(workspace, STAGE_GROUP, [
        Finding(ALICE.id, "groupUntidy", details=untidy),
        Finding(BOB.id, "groupUntidy", details=untidy),
        Finding(BOB.id, "noReview"),
        Finding(ALICE.id, "jojGroupFailExercise", 2, joj),
        Finding(BOB.id, "jojGroupFailExercise", 2, joj),
    ])
    body = render_message(STAGE_GROUP, team_rows(workspace, STAGE_GROUP, "hteam-01"), "h1", SETTINGS)
    assert "| group submission untidy | whole team | -0.25 |" in body
    assert "| missing others' code review | John Doe | -1 |" in body
    assert "| JOJ group exercise not passed (x2) | whole team | -0.5 |" in body
    assert body.count("- tags/h1 redundant files: a.out\n") == 1  # whole team: no names
    assert "- JOJ h1-release #3/ex5 0% (< 25%)" in body
    assert body.rstrip().endswith(SETTINGS.group_problems)
    assert SETTINGS.group_no_problem not in body


def test_override_notes_name_the_student(workspace):
    save(workspace, STAGE_GROUP, [Finding(ALICE.id, "noReview"), Finding(BOB.id, "noReview")])
    save_overrides(overrides_path(workspace.output_dir, 1),
                   [Override(student=BOB.id, remove=("noReview",), note="review left on Mattermost")])
    body = render_message(STAGE_GROUP, team_rows(workspace, STAGE_GROUP, "hteam-01"), "h1", SETTINGS)
    assert "| missing others' code review | Zhang San | -1 |" in body
    assert "- Note (John Doe): review left on Mattermost" in body


def test_final_reply_has_individual_problems_and_every_students_score(workspace):
    save(workspace, STAGE_INDIVIDUAL_FINAL, [Finding(BOB.id, "noIndividualPR"),
                                             Finding(BOB.id, "indvFailSubmit", details=("individual branch missing",))])
    save(workspace, STAGE_FINAL, [Finding(ALICE.id, "noReview"), Finding(CAROL.id, "groupFailSubmit"),
                                  Finding(DAVE.id, "groupFailSubmit")], late=(CAROL.id, DAVE.id))
    on_time = render_message(STAGE_FINAL, team_rows(workspace, STAGE_FINAL, "hteam-01"), "h1", SETTINGS)
    assert on_time.startswith("## h1: auto-grader result 24 hours after the group deadline\n")
    assert "| missing others' code review | Zhang San | -1 |" in on_time
    assert "| no individual PR found | John Doe | -0.5 |" in on_time  # appeals cover the individual part
    assert "- individual branch missing (John Doe)\n" in on_time  # not the whole team: say whose
    assert SETTINGS.final_scores in on_time
    assert "\n| Zhang San | -1 |\n" in on_time and "\n| John Doe | -1.5 |\n" in on_time
    assert on_time.rstrip().endswith(SETTINGS.final_problems)
    assert SETTINGS.late not in on_time


def test_late_teams_get_their_scores_then_the_late_note(workspace):
    save(workspace, STAGE_FINAL, [Finding(CAROL.id, "groupFailSubmit"), Finding(DAVE.id, "groupFailSubmit")],
         late=(CAROL.id, DAVE.id))
    late = render_message(STAGE_FINAL, team_rows(workspace, STAGE_FINAL, "hteam-02"), "h1", SETTINGS)
    assert late.startswith("## h1: auto-grader result 24 hours after the group deadline\n")
    assert "| group submission missing | whole team | -2.5 |" in late
    assert "\n| Li Si | -2.5 |\n" in late and "\n| Wang Wu | -2.5 |\n" in late
    assert late.rstrip().endswith(SETTINGS.late.rstrip())


def test_final_reply_without_problems_still_gives_the_scores(workspace):
    save(workspace, STAGE_FINAL)
    clean = render_message(STAGE_FINAL, team_rows(workspace, STAGE_FINAL, "hteam-01"), "h1", SETTINGS)
    assert SETTINGS.final_no_problem in clean and SETTINGS.final_problems not in clean
    assert "\n| Zhang San | 0 |\n" in clean and "|---" not in clean.split(SETTINGS.final_no_problem)[0]


def test_group_issue_has_no_scores(workspace):
    save(workspace, STAGE_GROUP, [Finding(ALICE.id, "noReview")])
    body = render_message(STAGE_GROUP, team_rows(workspace, STAGE_GROUP, "hteam-01"), "h1", SETTINGS)
    assert SETTINGS.final_scores not in body and "| Student | Score |" not in body


# ---------- plan and post ----------

def test_group_stage_opens_one_issue_per_team_and_skips_existing(workspace):
    save(workspace, STAGE_GROUP)
    g = Grader(workspace, 1)
    reader = FakeIssues({"hteam02": [ns(number=4, title="h1 feedback")]})
    plan = plan_feedback(reader, STAGE_GROUP, g.feedback_rows(STAGE_GROUP), g.roster.teams, "h1", SETTINGS)
    assert [(p.team.repo, p.action) for p in plan] == [("hteam01", "open"), ("hteam02", "exists")]
    assert plan[0].assignees == ("bot", "member")

    writer = FakeIssues()
    assert post_feedback(writer, plan) == (["hteam01#7"], [])
    assert writer.opened == [("hteam01", "h1 feedback", plan[0].body, ("bot", "member"))]
    assert writer.replied == []


def test_final_stage_replies_once_in_the_existing_issue(workspace):
    save(workspace, STAGE_FINAL, late=(CAROL.id, DAVE.id))
    g = Grader(workspace, 1)
    rows = g.feedback_rows(STAGE_FINAL)
    issues = {"hteam01": [ns(number=3, title="h1 feedback")], "hteam02": [ns(number=4, title="h1 feedback")]}
    reader = FakeIssues(issues, {
        ("hteam01", 3): [ns(body="We fixed the README, thanks!")],  # a student reply is not ours
        ("hteam02", 4): [ns(body=SETTINGS.late)],  # already answered
    })
    plan = plan_feedback(reader, STAGE_FINAL, rows, g.roster.teams, "h1", SETTINGS)
    assert [(p.team.repo, p.action, p.issue) for p in plan] == [("hteam01", "reply", 3), ("hteam02", "replied", 4)]

    writer = FakeIssues()
    assert post_feedback(writer, plan) == (["hteam01#3"], [])
    assert writer.replied == [("hteam01", 3, plan[0].body)] and writer.opened == []

    answered = FakeIssues(issues, {("hteam01", 3): [ns(body=plan[0].body)], ("hteam02", 4): [ns(body=SETTINGS.late)]})
    assert {p.action for p in plan_feedback(answered, STAGE_FINAL, rows, g.roster.teams, "h1", SETTINGS)} == {
        "replied"
    }
    old_late_issue = FakeIssues({"hteam02": [ns(number=4, title="h1 feedback", body=SETTINGS.late)]})
    assert [p.action for p in plan_feedback(old_late_issue, STAGE_FINAL, rows, g.roster.teams, "h1", SETTINGS)
            if p.team.repo == "hteam02"] == ["replied"]
    missing = plan_feedback(FakeIssues(), STAGE_FINAL, rows, g.roster.teams, "h1", SETTINGS)
    assert {p.action for p in missing} == {"no issue"}


def test_post_failures_are_reported_and_do_not_stop_other_teams(workspace):
    save(workspace, STAGE_GROUP)
    g = Grader(workspace, 1)
    plan = plan_feedback(FakeIssues(), STAGE_GROUP, g.feedback_rows(STAGE_GROUP), g.roster.teams, "h1",
                         SETTINGS)
    writer = FakeIssues(fail_for={"hteam01"})
    opened, failures = post_feedback(writer, plan)
    assert opened == ["hteam02#7"]
    assert len(failures) == 1 and failures[0].startswith("hteam01: ")
