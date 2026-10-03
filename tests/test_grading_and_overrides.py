import pytest

from gradehelper.config import RubricItem
from gradehelper.grading import NO_PROBLEM, aggregate, compute_grade, fmt_points
from gradehelper.models import Finding
from gradehelper.overrides import Override, apply_overrides, load_overrides, save_overrides

from .conftest import ALICE, BOB, CAROL

RUBRIC = {
    "indvFailSubmit": RubricItem(points=-1, description="individual submission missing", stage="individual"),
    "jojFailExercise": RubricItem(points=-0.25, description="JOJ exercise not passed", stage="individual"),
    "groupFailSubmit": RubricItem(points=-2.5, description="group submission missing", stage="group"),
    "noReview": RubricItem(points=-1, description="missing others' code review", stage="group"),
}


def test_fmt_points():
    assert fmt_points(-1.0) == "-1" and fmt_points(-0.25) == "-0.25" and fmt_points(0) == "0"


def test_aggregate_takes_max_count_and_dedups_details():
    findings = [
        Finding("1", "indvFailSubmit", 1, ("a",)),
        Finding("1", "indvFailSubmit", 1, ("b", "a")),
        Finding("1", "jojFailExercise", 2, ()),
    ]
    assert aggregate(findings) == {"indvFailSubmit": (1, ("a", "b")), "jojFailExercise": (2, ())}


def test_clean_student():
    grade = compute_grade([], RUBRIC, -2.5)
    assert grade.score == 0 and grade.comment == NO_PROBLEM


def test_comment_lists_items_in_rubric_order_with_counts_and_details():
    findings = [
        Finding("1", "jojFailExercise", 2, ("ex2 0%",)),
        Finding("1", "indvFailSubmit", 1, ("h1/ex1.m file missing",)),
    ]
    grade = compute_grade(findings, RUBRIC, -2.5)
    assert grade.score == -1.5
    assert grade.comment == (
        "General Info: individual submission missing, -1; JOJ exercise not passed (x2), -0.5. "
        "Detail: h1/ex1.m file missing, ex2 0%."
    )


def test_score_floor():
    findings = [Finding("1", "groupFailSubmit"), Finding("1", "noReview")]
    assert compute_grade(findings, RUBRIC, -2.5).score == -2.5


def test_unknown_rubric_key_is_an_error():
    with pytest.raises(KeyError):
        compute_grade([Finding("1", "typo")], RUBRIC, -2.5)


def test_override_remove_add_score_note():
    findings = [Finding(ALICE.id, "noReview"), Finding(ALICE.id, "indvFailSubmit")]
    overrides = [
        Override(student=ALICE.jaccount, remove=("noReview",), note="reviewed offline"),
        Override(team="hteam-01", add={"jojFailExercise": 1}),
        Override(student=CAROL.id, score=0),
    ]
    adjusted = apply_overrides(ALICE, findings, overrides)
    assert {f.key for f in adjusted.findings} == {"indvFailSubmit", "jojFailExercise"}
    assert adjusted.notes == ("reviewed offline",)
    assert adjusted.fixed_score is None
    assert apply_overrides(CAROL, [], overrides).fixed_score == 0
    assert {f.key for f in apply_overrides(BOB, [], overrides).findings} == {"jojFailExercise"}


def test_overrides_roundtrip(tmp_path):
    path = tmp_path / "h1.overrides.toml"
    items = (
        Override(student=ALICE.id, remove=("noReview",), note='said "hi" — ok'),
        Override(team="hteam-02", add={"noReview": 1}, score=-0.5),
    )
    save_overrides(path, items)
    assert load_overrides(path, set(RUBRIC)) == items


def test_overrides_validation(tmp_path):
    path = tmp_path / "o.toml"
    path.write_text('[[override]]\nstudent = "1"\nremove = ["nope"]\n')
    with pytest.raises(ValueError, match="unknown rubric keys"):
        load_overrides(path, set(RUBRIC))
    path.write_text('[[override]]\nremove = ["noReview"]\n')
    with pytest.raises(ValueError, match="exactly one"):
        load_overrides(path, set(RUBRIC))
