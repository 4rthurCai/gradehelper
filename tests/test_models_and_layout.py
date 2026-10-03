import pytest

from gradehelper.checks.layout import inspect_layout, is_readme, root_allowlist
from gradehelper.models import Roster, english_name, extract_student_id

from .conftest import ALICE, CAROL


def test_english_name_strips_cjk_and_title_cases():
    assert english_name("Zhang San 张三") == "Zhang San"
    assert english_name("JOHN DOE") == "John Doe"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("h3 520000000001 Zhang San", "520000000001"),
        ("Zhang San(520000000001)", "520000000001"),
        ("no id here", None),
        ("5200 0000 0001", "520000000001"),  # digits spread out: fallback
        (None, None),
    ],
)
def test_extract_student_id(text, expected):
    assert extract_student_id(text) == expected


def test_roster_teams_and_graded_skip_students_without_team(roster: Roster):
    assert [t.key for t in roster.teams] == ["hteam-01", "hteam-02"]
    assert roster.teams[0].repo == "hteam01"
    assert len(roster.graded) == 4


def test_select_teams_by_number_and_name(roster: Roster):
    assert {s.id for s in roster.select_teams("2").graded} == {CAROL.id, "520000000004"}
    assert ALICE in roster.select_teams("hteam-01, 2").graded
    assert roster.select_teams("").graded == roster.graded


def test_select_unknown_team_is_an_error(roster: Roster):
    with pytest.raises(ValueError, match="hteam-09"):
        roster.select_teams("9")


def test_is_readme():
    assert is_readme("README.md") and is_readme("readme") and is_readme("Readme.txt")
    assert not is_readme("read.md") and not is_readme("READMEX")


ALLOWED = root_allowlist([".gitignore", ".gitea"], 20)


def test_layout_complete_and_tidy():
    report = inspect_layout(
        [".gitignore", "h1", "h2", "README.md"], ["ex1.m", "ex2.m", "README.md"], ["ex1.m"], ["ex2.m"], ALLOWED
    )
    assert not report.incomplete and not report.untidy


def test_layout_missing_dir():
    report = inspect_layout(["h2"], None, ["ex1.m"], [], ALLOWED)
    assert report.hw_dir_missing and report.incomplete


def test_layout_missing_files_readme_and_extras():
    report = inspect_layout(
        ["h1", "notes.txt", ".DS_Store"], ["ex1.m", "a.out"], ["ex1.m", "ex2.m"], [], ALLOWED
    )
    assert report.missing_files == ("ex2.m",)
    assert report.readme_missing
    assert report.extra_root == ("notes.txt", ".DS_Store")
    assert report.extra_hw == ("a.out",)


def test_unknown_deadline_keys_are_rejected(workspace):
    from gradehelper.config import ConfigError

    h1 = workspace.root / "config" / "homeworks" / "h1.toml"
    h1.write_text(h1.read_text().replace("[deadlines]\n", '[deadlines]\nreview = "2026-10-03T23:59:59+08:00"\n'))
    with pytest.raises(ConfigError, match="review"):
        workspace.homework(1)
