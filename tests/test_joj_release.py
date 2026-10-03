from datetime import datetime, timedelta, timezone

from gradehelper.checks.joj_release import group_joj_findings, parse_release_issues, pick_run
from gradehelper.config import HomeworkConfig, JojSettings

from .conftest import ns

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 3, 20, tzinfo=TZ)
TITLE = JojSettings().release_issue_title
HW = HomeworkConfig(number=1, language="matlab", pass_threshold=50)
BODY = """Generated from [Gitea Actions #134](https://example/runs/134), commit {sha}, triggered by @bot.
## Health Check - Score: 0
<details><summary>Case 0 - Score: 0</summary></details>
## [oj] ex2 - Score: {a}
<details><summary>Case 0 - Score: {a}</summary></details>
## [oj] ex5 - Score: {b}
## [oj] ex6 - Score: {c}
"""


def issue(number, score, sha, a, b, c, created, hw="h1", total=100):
    return ns(number=number, title=f"JOJ3 Result for {hw}-release by @bot - Score: {score} / {total}",
              body=BODY.format(sha=sha, a=a, b=b, c=c), created_at=created)


def test_parse_ignores_other_issues_and_reads_exercises():
    issues = [
        issue(1, 100, "a" * 40, 45, 30, 25, T0),
        issue(2, 100, "b" * 40, 45, 30, 25, T0, hw="h10"),
        ns(number=3, title="JOJ3 Result for h1/ex2 by @bot - Score: 10 / 10", body="", created_at=T0),
        ns(number=4, title="Late submission", body=None, created_at=T0),
    ]
    runs = parse_release_issues(issues, "h1", TITLE)
    assert [(r.issue, r.score, r.commit) for r in runs] == [(1, 100.0, "a" * 40)]
    assert runs[0].exercises == {"ex2": 45.0, "ex5": 30.0, "ex6": 25.0}


def test_pick_run_prefers_the_tagged_commit_then_latest_before_cutoff():
    runs = parse_release_issues([
        issue(1, 40, "a" * 40, 0, 30, 10, T0),
        issue(2, 100, "b" * 40, 45, 30, 25, T0 + timedelta(hours=30)),
        issue(3, 70, "c" * 40, 45, 0, 25, T0 + timedelta(hours=2)),
    ], "h1", TITLE)
    assert pick_run(runs, "a" * 40, None).issue == 1
    assert pick_run(runs, "f" * 40, T0 + timedelta(hours=24)).issue == 3
    assert pick_run(runs, None, T0 - timedelta(hours=1)) is None


def test_group_thresholds_25_and_50(roster):
    team = roster.teams[0]
    maxima = {"ex2": 45.0, "ex5": 30.0, "ex6": 25.0}
    run = parse_release_issues([issue(1, 70, "a" * 40, 45, 0, 25, T0)], "h1", TITLE)[0]
    found = {(f.student_id, f.key, f.count) for f in group_joj_findings(team, run, maxima, HW, JojSettings())}
    assert found == {(s.id, "jojGroupFailExercise", 1) for s in team.members}  # ex5 0%, average 67%
    zero = parse_release_issues([issue(2, 0, "a" * 40, 0, 0, 0, T0)], "h1", TITLE)[0]
    keys = {(f.key, f.count) for f in group_joj_findings(team, zero, maxima, HW, JojSettings())}
    assert keys == {("jojGroupFailHomework", 1), ("jojGroupFailExercise", 2)}


def test_sections_of_one_exercise_add_up():
    body = ("commit " + "a" * 40 + "\n## [build] h4_compile - Score: 0\n## [cq] h4_cpplint - Score: -5\n"
            "## [oj] h4/ex1-asan - Score: 40\n## [oj] h4/ex1 - Score: 150\n## [run] h4/ex1-valgrind - Score: 10\n"
            "## [oj] h4/ex2 - Score: 0\n")
    run = parse_release_issues(
        [ns(number=9, title="JOJ3 Result for h4-release by @bot - Score: 200 / 600", body=body, created_at=T0)],
        "h4", TITLE,
    )[0]
    assert run.exercises == {"ex1": 200.0, "ex2": 0.0}
