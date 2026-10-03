import json

import pytest
from git import Actor, Repo

from gradehelper.checks.joj_config import exercise_maxima, exercise_of, load_maxima, stage_max
from gradehelper.config import HomeworkConfig, JojSettings


@pytest.mark.parametrize(
    "name, expected",
    [("[oj] ex2", "ex2"), ("[oj] h1/ex6", "ex6"), ("[oj] h4/ex1-asan", "ex1"), ("[run] h7/ex3-valgrind", "ex3"),
     ("[cq] h4_clang-tidy", None), ("[build] h4_compile", None), ("Health Check", None), ("[oj] ex12", "ex12")],
)
def test_exercise_of(name, expected):
    assert exercise_of(name) == expected


def diff(*scores):
    return {"name": "diff", "with": {"cases": [{"outputs": [{"score": s}]} for s in scores]}}


def test_stage_max_sums_diff_cases_and_positive_status():
    status = {"name": "result-status", "with": {"score": 10}}
    detail = {"name": "result-detail", "with": {"score": 0}}
    stage = {"parsers": [status, detail, diff(5, 5, 5)]}
    assert stage_max(stage) == 25
    assert stage_max({"parsers": [{"name": "keyword", "with": {"score": 3}}]}) == 0


def test_exercise_maxima_merges_sanitizer_and_valgrind_stages():
    conf = {"stages": [
        {"name": "[build] h7_compile", "parsers": [diff(99)]},
        {"name": "[oj] h7/ex3-asan", "parsers": [diff(50, 50)]},
        {"name": "[oj] h7/ex3", "parsers": [diff(150, 150)]},
        {"name": "[run] h7/ex3-valgrind", "parsers": [diff(100)]},
        {"name": "[oj] h7/ex4", "parsers": [diff(400)]},
    ]}
    assert exercise_maxima(conf) == {"ex3": 500, "ex4": 400}


@pytest.fixture
def joj_repo(tmp_path):
    remote = Repo.init(tmp_path / "remote", initial_branch="master")
    actor = Actor("bot", "bot@example.com")
    base = tmp_path / "remote" / "home/tt/.config/joj/homework/h3"
    files = {
        "conf-release.json": {"stages": [{"name": "[oj] ex1", "parsers": [diff(20)]},
                                         {"name": "[oj] ex5", "parsers": [diff(*[5] * 10)]}]},
        "ex1/conf.json": {"stages": [{"name": "[oj] h3/ex1", "parsers": [diff(10)]}]},
        "ex5/conf.json": {"stages": [{"name": "[oj] h3/ex5", "parsers": [diff(*[10] * 10)]}]},
        "ex2/conf.json": {"stages": [{"name": "[oj] h3/ex2", "parsers": [diff(10)]}]},  # optional exercise
    }
    for rel, content in files.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(json.dumps(content))
    remote.git.add("-A")
    remote.index.commit("configs", author=actor, committer=actor)
    return Repo.clone_from(remote.working_dir, tmp_path / "clone")


def test_load_maxima_from_joj_configs(joj_repo):
    maxima = load_maxima(joj_repo, JojSettings(), HomeworkConfig(number=3, language="matlab", pass_threshold=50))
    assert maxima.release == {"ex1": 20, "ex5": 50}
    assert maxima.individual == {1: 10, 5: 100}  # only exercises in the release are graded
    assert "engr151-joj" in maxima.source


def test_toml_overrides_win(joj_repo):
    hw = HomeworkConfig(number=3, language="matlab", pass_threshold=50,
                        joj_exercises={1: 0, 2: 10}, joj_release_exercises={"ex2": 60})
    maxima = load_maxima(joj_repo, JojSettings(), hw)
    assert maxima.individual == {2: 10} and maxima.release == {"ex2": 60}


def test_missing_homework_config_gives_empty_maxima(joj_repo):
    maxima = load_maxima(joj_repo, JojSettings(), HomeworkConfig(number=9, language="matlab", pass_threshold=50))
    assert maxima.individual == {} and maxima.release == {}
