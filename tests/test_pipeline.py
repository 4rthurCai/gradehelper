"""End-to-end: both stages against local git remotes and a fake Gitea."""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from git import Actor, Repo

from gradehelper.config import STAGE_FINAL, STAGE_GROUP, STAGE_INDIVIDUAL, Deadlines, HomeworkConfig
from gradehelper.models import Finding, StageResult
from gradehelper.overrides import Override, overrides_path, save_overrides
from gradehelper.pipeline import Grader, merge_results, suggest_stage

from .conftest import ALICE, BOB, CAROL, DAVE, ns, user

TZ = timezone(timedelta(hours=8))
IND_DEADLINE = datetime(2026, 10, 1, 23, 59, 59, tzinfo=TZ)
GROUP_DEADLINE = datetime(2026, 10, 3, 23, 59, 59, tzinfo=TZ)
ACTOR = Actor("bot", "bot@example.com")
GOOD_REVIEW = "Your loop skips the last element when n is odd; consider using <= in the bound."


def commit(repo: Repo, files: dict[str, str], message: str, when: datetime | None = None) -> None:
    root = Path(repo.working_dir)
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    repo.git.add("-A")
    stamp = f"{int((when or IND_DEADLINE - timedelta(days=1)).timestamp())} +0800"
    repo.index.commit(message, author=ACTOR, committer=ACTOR, author_date=stamp, commit_date=stamp)


def make_team_remote(remotes: Path, name: str, branches: dict[str, dict[str, str]], tag_files: dict | None) -> None:
    repo = Repo.init(remotes / "org" / f"{name}.git", initial_branch="master")
    commit(repo, {"README.md": "team repo\n", ".gitignore": "*.o\n"}, "init")
    for branch, files in branches.items():
        repo.git.checkout("-b", branch, "master")
        commit(repo, files, f"work {branch}")
    repo.git.checkout("master")
    if tag_files is not None:
        commit(repo, tag_files, "release")
        repo.create_tag("h1")


def joj_conf(*stages: tuple[str, list[float]]) -> str:
    """A compiled JOJ3 conf.json: stage name -> per-case diff scores."""
    return json.dumps({"stages": [
        {"name": name, "parsers": [{"name": "diff", "with": {"cases": [{"outputs": [{"score": s}]} for s in scores]}}]}
        for name, scores in stages
    ]})


def make_joj_remote(remotes: Path) -> None:
    repo = Repo.init(remotes / "org" / "engr151-joj.git", initial_branch="master")
    base = "home/tt/.config/joj/homework/h1"
    commit(repo, {
        f"{base}/conf-release.json": joj_conf(("[oj] ex2", [45]), ("[oj] ex5", [30]), ("[oj] ex6", [5] * 5)),
        f"{base}/ex2/conf.json": joj_conf(("[oj] h1/ex2", [10])),
        f"{base}/ex5/conf.json": joj_conf(("[oj] h1/ex5", [10])),
        f"{base}/ex6/conf.json": joj_conf(("[oj] h1/ex6", [10] * 5)),
    }, "joj configs")
    repo.git.checkout("--orphan", "grading")
    repo.git.rm("-rf", "--cached", ".")
    for leftover in Path(repo.working_dir).iterdir():
        if leftover.name != ".git":
            shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
    early = (
        ",h1,h1/ex2,h1/ex3,h1/ex5,h1/ex6\n"
        "zhangsan@sjtu.edu.cn,60,10,0,10,50\n"
        "john.doe,20,10,0,0,50\n"
        "wangwu,60,10,0,10,50\n"
    )
    late = early + "lisi,60,10,0,10,50\n"  # Li Si only submitted after the deadline
    commit(repo, {"homework/h1.csv": early}, "board 1", IND_DEADLINE - timedelta(hours=1))
    commit(repo, {"homework/h1.csv": late}, "board 2", IND_DEADLINE + timedelta(hours=1))


class FakeGitea:
    def __init__(self):
        self._pulls = {
            "hteam01": [ns(number=1, title=f"h1 {ALICE.id}", body="I wrote the parser and tested edge cases " * 3),
                        ns(number=2, title=f"h1 {BOB.id}", body="")],
            "hteam02": [ns(number=1, title=f"h1 {CAROL.id}", body="Implemented ex2 and ex5 with input checks " * 3)],
        }
        self._comments = {
            ("hteam01", 1): [ns(user=user(BOB.id), body=GOOD_REVIEW, created_at=GROUP_DEADLINE - timedelta(hours=5))],
            ("hteam01", 2): [ns(user=user(ALICE.id), body=GOOD_REVIEW, created_at=GROUP_DEADLINE + timedelta(hours=5))],
            ("hteam02", 1): [ns(user=user(DAVE.id), body="lgtm", created_at=GROUP_DEADLINE - timedelta(hours=5))],
        }
        self._issues = {
            "hteam01": [release_issue(1, 100, "a" * 40, {"ex2": 45, "ex5": 30, "ex6": 25})],
        }
        self._tags = {"hteam01": "a" * 40, "hteam02": "b" * 40}
        self._tagged = {"hteam01"}
        self._status = {}
        self._releases = {
            "hteam01": [ns(name="h1", tag_name="h1", created_at=GROUP_DEADLINE - timedelta(hours=2))],
            "hteam02": [],
        }

    def pulls(self, repo):
        return self._pulls.get(repo, [])

    def pull_comments(self, repo, number):
        return self._comments.get((repo, number), [])

    def pull_reviews(self, repo, number):
        return []

    def review_comments(self, repo, number, review_id):
        return []

    def releases(self, repo):
        return self._releases.get(repo, [])

    def tag_commit(self, repo, tag):
        return self._tags[repo]

    def repo_issues(self, repo):
        return self._issues.get(repo, [])

    def latest_status(self, repo, sha, context_prefix):
        assert context_prefix == "Run JOJ3 on Release"
        return self._status.get(repo, "success")

    def has_tag(self, repo, tag):
        return repo in self._tagged


def release_issue(number, score, sha, exercises, total=100, created=None):
    body = f"Generated from run, commit {sha}, triggered by @bot.\n## Health Check - Score: 0\n" + "".join(
        f"## [oj] {ex} - Score: {pts}\n<details>case</details>\n" for ex, pts in exercises.items()
    )
    return ns(number=number, title=f"JOJ3 Result for h1-release by @bot - Score: {score} / {total}", body=body,
              created_at=created or GROUP_DEADLINE - timedelta(hours=3))


GOOD_FILES = {"h1/ex2.m": "x = 1;\n", "h1/ex5.m": "y = 2;\n", "h1/ex6.m": "z = 3;\n", "h1/README.md": "notes\n"}


@pytest.fixture
def grader(workspace, tmp_path):
    remotes = tmp_path / "remotes"
    make_team_remote(
        remotes, "hteam01",
        {ALICE.id: GOOD_FILES, BOB.id: {"h1/ex2.m": "x = 1;\n", "h1/a.out": "bin"}},
        tag_files=GOOD_FILES,
    )
    make_team_remote(
        remotes, "hteam02",
        {CAROL.id: GOOD_FILES, DAVE.id: GOOD_FILES},
        tag_files=None,
    )
    make_joj_remote(remotes)
    with (workspace.root / ".env").open("a") as f:
        f.write(f"GIT_HOST=file://{remotes}\n")
    h1 = workspace.root / "config" / "homeworks" / "h1.toml"
    head, _, rest = h1.read_text().partition("[deadlines]")
    rest = "\n".join(line for line in rest.splitlines() if not re.match(r"\s*(individual|group|final)\s*=", line))
    deadlines = f'individual = "{IND_DEADLINE.isoformat()}"\ngroup = "{GROUP_DEADLINE.isoformat()}"\n'
    h1.write_text(f"{head}[deadlines]\n{deadlines}{rest}\n")
    g = Grader(workspace, 1)
    g.gitea = FakeGitea()
    return g


def read_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return {row["ID"]: row for row in csv.DictReader(f)}


def deduction_keys(grader: Grader, stage: str) -> dict[str, set[str]]:
    return {r.student.id: r.grade.keys for r in grader.rows(stage)}


def test_individual_stage(grader: Grader):
    grader.run_individual()
    keys = deduction_keys(grader, STAGE_INDIVIDUAL)
    assert keys[ALICE.id] == set()
    assert keys[BOB.id] == {"indvFailSubmit", "indvUntidy", "notWritingPR", "jojFailExercise"}
    assert keys[CAROL.id] == {"indvFailSubmit"}  # not on the scoreboard at the deadline
    assert keys[DAVE.id] == {"noIndividualPR"}

    rows = read_csv(grader.ws.output_dir / "h1indv.csv")
    assert rows[ALICE.id]["Individual_Score"] == "0"
    assert rows[BOB.id]["Individual_Score"] == "-1.75"
    assert "h1/ex5.m file missing" in rows[BOB.id]["Individual_Comments"]
    assert "redundant files: a.out" in rows[BOB.id]["Individual_Comments"]
    assert rows[CAROL.id]["Name"] == "Li Si"


def test_group_stage_reuses_frozen_individual_result(grader: Grader):
    grader.run_individual()
    # Bob fixes his branch after the individual deadline: must not change his individual part.
    grader.git.sync("hteam01", "hteam-01")
    grader.run_group()
    keys = deduction_keys(grader, STAGE_GROUP)
    assert keys[ALICE.id] == {"noReview"}  # her review came after the cutoff
    assert keys[BOB.id] >= {"indvFailSubmit", "indvUntidy"} and "noReview" not in keys[BOB.id]
    assert keys[CAROL.id] >= {"groupFailSubmit", "noReview"}
    assert "jojFailCompile" not in keys[CAROL.id]  # no tag is no submission, not a compile failure
    assert keys[DAVE.id] >= {"groupFailSubmit", "noReview"}  # "lgtm" does not count

    rows = read_csv(grader.ws.output_dir / "h1.csv")
    assert rows[ALICE.id]["Score"] == "-1" and rows[ALICE.id]["Late"] == "No"
    assert rows[CAROL.id]["Score"] == "-2.5" and rows[CAROL.id]["Late"] == "Yes"


def test_group_stage_generates_missing_individual_part_without_branch_checks(grader: Grader):
    grader.run_group()
    individual = grader.results()[STAGE_INDIVIDUAL]
    assert individual.params["branches_checked"] is False
    keys = deduction_keys(grader, STAGE_INDIVIDUAL)
    assert "indvUntidy" not in keys[BOB.id]
    assert "indvFailSubmit" not in keys[CAROL.id]  # missing from scoreboard is not flagged here


def test_overrides_apply_to_reports(grader: Grader):
    grader.run_individual()
    save_overrides(
        overrides_path(grader.ws.output_dir, 1),
        [Override(student=DAVE.id, remove=("noIndividualPR",), note="PR title had a typo, checked by TA")],
    )
    grader.write_reports()
    row = read_csv(grader.ws.output_dir / "h1indv.csv")[DAVE.id]
    assert row["Individual_Score"] == "0"
    assert row["Individual_Comments"].endswith("Note: PR title had a typo, checked by TA")


def test_rerunning_one_team_keeps_the_others(grader: Grader):
    grader.run_individual()
    before = deduction_keys(grader, STAGE_INDIVIDUAL)
    partial = Grader(grader.ws, 1, teams="1")
    partial.gitea = grader.gitea
    partial.run_individual()
    assert deduction_keys(partial, STAGE_INDIVIDUAL) == before


def test_merge_results_replaces_only_rerun_students():
    old = StageResult(1, STAGE_INDIVIDUAL, "t0", students=("a", "b"),
                      findings=(Finding("a", "noReview"), Finding("b", "noReview")))
    new = StageResult(1, STAGE_INDIVIDUAL, "t1", students=("a",), findings=())
    merged = merge_results(old, new)
    assert merged.students == ("a", "b")
    assert merged.findings == (Finding("b", "noReview"),)


def test_suggest_stage_follows_the_three_runs():
    hw = HomeworkConfig(number=1, language="matlab", pass_threshold=50,
                        deadlines=Deadlines(individual=IND_DEADLINE, group=GROUP_DEADLINE))
    hour = timedelta(hours=1)
    ind = {STAGE_INDIVIDUAL: IND_DEADLINE + hour}
    grp = {**ind, STAGE_GROUP: GROUP_DEADLINE + hour}
    assert suggest_stage(hw, {}, 24, IND_DEADLINE - hour)[0] == STAGE_INDIVIDUAL
    assert suggest_stage(hw, {}, 24, IND_DEADLINE + hour)[0] == STAGE_INDIVIDUAL
    assert suggest_stage(hw, ind, 24, IND_DEADLINE + 2 * hour)[0] == STAGE_GROUP  # preview
    assert suggest_stage(hw, ind, 24, GROUP_DEADLINE + hour)[0] == STAGE_GROUP
    assert suggest_stage(hw, grp, 24, GROUP_DEADLINE + 23 * hour)[0] == STAGE_GROUP
    assert suggest_stage(hw, grp, 24, GROUP_DEADLINE + 25 * hour)[0] == STAGE_FINAL
    assert suggest_stage(hw, ind, 24, GROUP_DEADLINE + 25 * hour)[0] == STAGE_GROUP  # group run missing


def test_suggest_stage_does_not_count_previews_as_done():
    hw = HomeworkConfig(number=1, language="matlab", pass_threshold=50,
                        deadlines=Deadlines(individual=IND_DEADLINE, group=GROUP_DEADLINE))
    hour = timedelta(hours=1)
    preview_ind = {STAGE_INDIVIDUAL: IND_DEADLINE - 5 * hour}
    stage, reason = suggest_stage(hw, preview_ind, 24, IND_DEADLINE + hour)
    assert stage == STAGE_INDIVIDUAL and "not graded since" in reason
    preview_grp = {STAGE_INDIVIDUAL: IND_DEADLINE + hour, STAGE_GROUP: GROUP_DEADLINE - 5 * hour}
    assert suggest_stage(hw, preview_grp, 24, GROUP_DEADLINE + 30 * hour)[0] == STAGE_GROUP


def test_suggest_stage_without_deadlines_picks_first_missing_run():
    hw = HomeworkConfig(number=1, language="matlab", pass_threshold=50)
    assert suggest_stage(hw, {}, 24)[0] == STAGE_INDIVIDUAL
    assert suggest_stage(hw, {STAGE_INDIVIDUAL: IND_DEADLINE}, 24)[0] == STAGE_GROUP


def test_final_run_grades_late_release_but_keeps_it_late(grader: Grader, tmp_path):
    grader.run_individual()
    grader.run_group()
    assert "groupFailSubmit" in deduction_keys(grader, STAGE_GROUP)[CAROL.id]

    # Team 2 releases 5 hours after the group deadline.
    late_at = GROUP_DEADLINE + timedelta(hours=5)
    remote = Repo(tmp_path / "remotes" / "org" / "hteam02.git")
    commit(remote, GOOD_FILES, "late release", late_at)
    remote.create_tag("h1")
    grader.gitea._releases["hteam02"] = [ns(name="h1", tag_name="h1", created_at=late_at)]

    grader.run_final()
    final_keys = deduction_keys(grader, STAGE_FINAL)
    assert not final_keys[CAROL.id] & {"groupFailSubmit", "jojFailCompile"}
    rows = read_csv(grader.ws.output_dir / "h1.csv")  # now written from the final run
    assert rows[CAROL.id]["Late"] == "Yes" and rows[ALICE.id]["Late"] == "No"
    assert grader.results()[STAGE_FINAL].params["late_after"] == GROUP_DEADLINE.isoformat()


def test_hand_edited_csv_is_kept_unless_overwrite(grader: Grader):
    grader.run_individual()
    grader.run_group()
    sheet = grader.ws.output_dir / "h1.csv"
    edited = sheet.read_text(encoding="utf-8").replace("missing others' code review", "TA: review accepted")
    sheet.write_text(edited, encoding="utf-8")

    problems = grader.write_reports()
    assert len(problems) == 1 and "h1.csv" in problems[0]
    assert sheet.read_text(encoding="utf-8") == edited

    grader.overwrite = True
    assert grader.write_reports() == []
    assert "TA: review accepted" not in sheet.read_text(encoding="utf-8")


def test_untracked_existing_csv_is_kept(grader: Grader):
    sheet = grader.ws.output_dir / "h1indv.csv"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_text("pulled from hw-scoreboard\n", encoding="utf-8")
    grader.run_individual()  # results saved, CSV left alone
    assert sheet.read_text(encoding="utf-8") == "pulled from hw-scoreboard\n"
    assert STAGE_INDIVIDUAL in grader.results()


def _team2_releases_with_two_runs(grader: Grader, tmp_path) -> None:
    at = GROUP_DEADLINE - timedelta(hours=1)
    remote = Repo(tmp_path / "remotes" / "org" / "hteam02.git")
    commit(remote, GOOD_FILES, "release", at)
    remote.create_tag("h1")
    grader.gitea._releases["hteam02"] = [ns(name="h1", tag_name="h1", created_at=at)]
    grader.gitea._issues["hteam02"] = [
        release_issue(5, 10, "b" * 40, {"ex2": 0, "ex5": 10, "ex6": 0}, created=at),
        release_issue(6, 100, "c" * 40, {"ex2": 45, "ex5": 30, "ex6": 25}, created=at),  # not the tag
    ]


def test_group_joj_uses_the_tagged_release_run(grader: Grader, tmp_path):
    _team2_releases_with_two_runs(grader, tmp_path)  # h1.toml ships ex2/ex5/ex6 = 45/30/25
    grader.run_individual()
    grader.run_group()
    keys = deduction_keys(grader, STAGE_GROUP)
    assert {"jojGroupFailHomework", "jojGroupFailExercise"} <= keys[CAROL.id]
    assert not keys[ALICE.id] & {"jojGroupFailHomework", "jojGroupFailExercise"}
    row = {r.student.id: r for r in grader.rows(STAGE_GROUP)}[DAVE.id]
    assert any("h1-release #5" in d for d in row.grade.details)


def test_group_joj_skipped_without_joj_configs(grader: Grader, tmp_path):
    Repo(tmp_path / "remotes" / "org" / "engr151-joj.git").delete_head("master", force=True)
    _team2_releases_with_two_runs(grader, tmp_path)
    messages = []
    grader.progress = messages.append
    grader.run_individual()
    grader.run_group()
    assert not any(k.startswith("jojGroup") for keys in deduction_keys(grader, STAGE_GROUP).values() for k in keys)
    assert any("group JOJ skipped" in m for m in messages)


def _release_team2_at(grader: Grader, tmp_path, when: datetime) -> None:
    remote = Repo(tmp_path / "remotes" / "org" / "hteam02.git")
    commit(remote, GOOD_FILES, "release", when)
    remote.create_tag("h1")
    grader.gitea._tagged.add("hteam02")
    grader.gitea._releases["hteam02"] = [ns(name="h1", tag_name="h1", created_at=when)]
    grader.gitea._issues["hteam02"] = [release_issue(9, 100, "b" * 40, {"ex2": 45, "ex5": 30, "ex6": 25}, created=when)]


def test_late_within_24h_counts_as_missing_in_group_run_even_if_run_later(grader: Grader, tmp_path):
    _release_team2_at(grader, tmp_path, GROUP_DEADLINE + timedelta(hours=5))  # case B, created before the run
    grader.run_individual()
    grader.run_group()
    keys = deduction_keys(grader, STAGE_GROUP)
    assert "groupFailSubmit" in keys[CAROL.id]
    detail = {r.student.id: r for r in grader.rows(STAGE_GROUP)}[CAROL.id].grade.details
    assert any("after the cutoff" in d for d in detail)

    grader.run_final()
    final = deduction_keys(grader, STAGE_FINAL)
    assert "groupFailSubmit" not in final[CAROL.id]
    assert read_csv(grader.ws.output_dir / "h1.csv")[CAROL.id]["Late"] == "Yes"


def test_more_than_24h_late_is_rejected_in_final_run(grader: Grader, tmp_path):
    _release_team2_at(grader, tmp_path, GROUP_DEADLINE + timedelta(hours=30))  # case C
    grader.run_individual()
    grader.run_final()
    row = {r.student.id: r for r in grader.rows(STAGE_FINAL)}[CAROL.id]
    assert row.grade.score == -2.5
    assert {d.key for d in row.grade.deductions} >= {"groupFailSubmit"}
    assert not {d.key for d in row.grade.deductions} & {"jojGroupFailHomework", "jojGroupFailExercise", "groupUntidy"}
    sheet = read_csv(grader.ws.output_dir / "h1.csv")[CAROL.id]
    assert sheet["Late"] == "Yes" and sheet["Release Time"].startswith("2026-10-05")


def test_moved_tag_gets_a_ta_note_but_no_deduction(grader: Grader):
    grader.gitea._tags["hteam01"] = "d" * 40  # the only JOJ release run is for "a" * 40
    grader.run_individual()
    grader.run_group()
    sheet = read_csv(grader.ws.output_dir / "h1.csv")
    assert "tag moved after the release?" in sheet[ALICE.id]["TA Notes"]
    assert "tag moved" not in sheet[ALICE.id]["Comments"]
    assert sheet[CAROL.id]["TA Notes"] == ""
