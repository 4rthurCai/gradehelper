from datetime import datetime, timedelta, timezone
from pathlib import Path

from git import Actor, Repo

from gradehelper.checks.joj import check_joj, evaluate_row, parse_scoreboard
from gradehelper.clients.git import file_at
from gradehelper.config import HomeworkConfig, JojSettings

from .conftest import ALICE, BOB, CAROL

HW = HomeworkConfig(number=1, language="matlab", pass_threshold=50)
EXERCISES = {2: 10, 3: 0, 5: 10, 6: 50}
JOJ = JojSettings()
BOARD = """,h1,h1/ex2,h1/ex3,h1/ex5,h1/ex6
zhangsan@sjtu.edu.cn,60,0,0,0,0
john.doe,20,10,0,0,50
lisi,,0,10,2,5
"""


def keys(findings):
    return {(f.key, f.count) for f in findings}


def test_total_at_threshold_passes_everything():
    assert evaluate_row({"h1": "50", "h1/ex2": "0"}, HW, EXERCISES, JOJ, "x") == []


def test_one_low_exercise():
    row = {"h1": "20", "h1/ex2": "10", "h1/ex5": "0", "h1/ex6": "50"}
    assert keys(evaluate_row(row, HW, EXERCISES, JOJ, "x")) == {("jojFailExercise", 1)}


def test_exactly_at_the_individual_threshold_passes():
    row = {"h1": "20", "h1/ex2": "1", "h1/ex5": "1", "h1/ex6": "25"}  # 10%, 10%, 50%: average 23%
    assert keys(evaluate_row(row, HW, EXERCISES, JOJ, "x")) == {("jojFailHomework", 1)}


def test_low_average_and_exercise_failures_capped_at_two():
    row = {"h1": "", "h1/ex2": "0", "h1/ex5": "0", "h1/ex6": "4"}
    assert keys(evaluate_row(row, HW, EXERCISES, JOJ, "x")) == {("jojFailHomework", 1), ("jojFailExercise", 2)}


def test_ungraded_exercises_are_ignored():
    row = {"h1": "0", "h1/ex2": "10", "h1/ex3": "0", "h1/ex5": "10", "h1/ex6": "50"}
    assert evaluate_row(row, HW, EXERCISES, JOJ, "x") == []


def test_scoreboard_matches_by_jaccount_and_flags_missing_students():
    board = parse_scoreboard(BOARD)
    missing = CAROL.__class__("520000000099", "Nobody", "nobody", "hteam-02")
    findings = check_joj(board, (ALICE, BOB, CAROL, missing), HW, EXERCISES, JOJ)
    by_student = {}
    for f in findings:
        by_student.setdefault(f.student_id, set()).add(f.key)
    assert ALICE.id not in by_student
    assert by_student[BOB.id] == {"jojFailExercise"}
    assert by_student[CAROL.id] == {"jojFailHomework", "jojFailExercise"}
    assert by_student[missing.id] == {"indvFailSubmit"}


def test_missing_students_not_flagged_when_disabled():
    board = parse_scoreboard(BOARD)
    ghost = CAROL.__class__("520000000099", "Nobody", "nobody", "hteam-02")
    assert check_joj(board, (ghost,), HW, EXERCISES, JOJ, flag_missing=False) == []


def test_file_at_picks_newest_commit_before_deadline(tmp_path):
    repo = Repo.init(tmp_path)
    actor = Actor("bot", "bot@example.com")
    base = datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=8)))
    target = tmp_path / "h1.csv"
    for i, text in enumerate(["v1", "v2", "v3"]):
        target.write_text(text)
        repo.index.add(["h1.csv"])
        when = f"{int((base + timedelta(hours=i)).timestamp())} +0800"
        repo.index.commit(text, author=actor, committer=actor, author_date=when, commit_date=when)
    ref = repo.active_branch.name
    assert file_at(repo, ref, "h1.csv")[0] == "v3"
    assert file_at(repo, ref, "h1.csv", before=base + timedelta(minutes=90))[0] == "v2"
    assert file_at(repo, ref, "h1.csv", before=base - timedelta(days=1))[0] == "v3"  # nothing earlier: latest


def test_sync_points_cached_clone_at_configured_host(tmp_path):
    from gradehelper.clients.git import GitRepos

    remote = Repo.init(tmp_path / "new-host" / "org" / "r.git", initial_branch="master")
    actor = Actor("bot", "bot@example.com")
    (Path(remote.working_dir) / "a").write_text("x")
    remote.index.add(["a"])
    remote.index.commit("c", author=actor, committer=actor)
    cached = Repo.clone_from(remote.working_dir, tmp_path / "repos" / "r")
    cached.remote().set_url("ssh://git@old-host.example:2222/org/r")

    repo = GitRepos(f"file://{tmp_path / 'new-host'}", "org", tmp_path / "repos").sync("r")
    assert repo.remote().url == f"file://{tmp_path / 'new-host'}/org/r.git"
