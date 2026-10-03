from datetime import datetime, timedelta, timezone

import pytest

from gradehelper.config import ConfigError, CourseConfig, Deadlines, HomeworkConfig, RubricItem
from gradehelper.deadlines import read_canvas_dates, resolve_deadlines

from .conftest import ns

COURSE = CourseConfig(rubric={"x": RubricItem(points=-1, description="x", stage="group")})
CST = timezone(timedelta(hours=8))


def assignment(id_, name, due="2026-10-02T15:59:00Z", lock="2026-10-03T15:59:00Z"):
    return ns(id=id_, name=name, due_at=due, lock_at=lock)


def hw(number=1, **deadlines):
    return HomeworkConfig(number=number, language="matlab", pass_threshold=50, deadlines=Deadlines(**deadlines))


def test_names_match_exact_homework_numbers():
    dates = read_canvas_dates([assignment(1, "h1"), assignment(10, "h10 Homework 10"), assignment(5, "p1 project")])
    assert set(dates.by_number) == {1, 10}
    assert dates.by_number[1].id == 1


def test_duplicate_names_are_not_guessed():
    dates = read_canvas_dates([assignment(1, "h2"), assignment(2, "h2 (late)")])
    assert 2 not in dates.by_number and set(dates.by_id) == {1, 2}


def test_from_canvas_due_lock_and_derived_individual():
    canvas = lambda: read_canvas_dates([assignment(427173, "h1")])  # noqa: E731
    resolved, source = resolve_deadlines(hw(), COURSE, canvas)
    d = resolved.deadlines
    assert d.group == datetime(2026, 10, 2, 23, 59, tzinfo=CST)
    assert d.final == datetime(2026, 10, 3, 23, 59, tzinfo=CST)
    assert d.individual == datetime(2026, 9, 30, 23, 59, tzinfo=CST)
    assert d.group.utcoffset() == timedelta(hours=8)  # shown in Shanghai time
    assert "427173" in source


def test_missing_lock_falls_back_to_grace():
    canvas = lambda: read_canvas_dates([assignment(1, "h1", lock=None)])  # noqa: E731
    d = resolve_deadlines(hw(), COURSE, canvas)[0].deadlines
    assert d.final - d.group == timedelta(hours=24)


def test_canvas_assignment_id_wins_over_name():
    canvas = lambda: read_canvas_dates([assignment(1, "h1"), assignment(2, "Homework one", due="2026-10-05T15:59:00Z")])  # noqa: E731
    special = HomeworkConfig(number=1, language="matlab", pass_threshold=50, canvas_assignment_id=2)
    assert resolve_deadlines(special, COURSE, canvas)[0].deadlines.group.day == 5


def test_toml_group_deadline_means_no_canvas_call():
    def canvas():
        raise AssertionError("Canvas must not be contacted")

    group = datetime(2026, 10, 2, 23, 59, tzinfo=CST)
    resolved, source = resolve_deadlines(hw(group=group), COURSE, canvas)
    assert resolved.deadlines.individual == group - timedelta(days=2)
    assert resolved.deadlines.final == group + timedelta(hours=24)
    assert source == "h1.toml"


def test_toml_values_override_single_fields():
    canvas = lambda: read_canvas_dates([assignment(1, "h1")])  # noqa: E731
    own_final = datetime(2026, 10, 5, 12, 0, tzinfo=CST)
    d = resolve_deadlines(hw(final=own_final), COURSE, canvas)[0].deadlines
    assert d.final == own_final and d.group.day == 2


def test_canvas_errors_are_reported_clearly():
    def broken():
        raise RuntimeError("connection refused")

    with pytest.raises(ConfigError, match="Canvas is unavailable.*connection refused"):
        resolve_deadlines(hw(), COURSE, broken)
    with pytest.raises(ConfigError, match="no Canvas assignment"):
        resolve_deadlines(hw(number=9), COURSE, lambda: read_canvas_dates([assignment(1, "h1")]))
