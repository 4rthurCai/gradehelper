"""Homework deadlines, normally taken from the Canvas assignment.

group      = Canvas due date ("due_at")
final      = Canvas "available until" ("lock_at"), else group + final_grace_hours
individual = group - individual_days_before_group (course.toml)

Values written in hN.toml [deadlines] win. If hN.toml sets the group deadline, Canvas is
not contacted at all (offline use, tests).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .config import ConfigError, CourseConfig, Deadlines, HomeworkConfig, Secrets
from .models import as_datetime

log = logging.getLogger(__name__)
_CACHE_SECONDS = 600
_cache: dict[tuple[str, int], tuple[float, "CanvasDates"]] = {}
_HW_NAME = re.compile(r"^h(\d+)(?!\d)", re.IGNORECASE)


@dataclass(frozen=True)
class CanvasAssignment:
    id: int
    name: str
    due: datetime | None
    lock: datetime | None


@dataclass(frozen=True)
class CanvasDates:
    by_number: dict[int, CanvasAssignment]
    by_id: dict[int, CanvasAssignment]


def read_canvas_dates(assignments) -> CanvasDates:
    by_number: dict[int, list[CanvasAssignment]] = {}
    by_id = {}
    for a in assignments:
        item = CanvasAssignment(a.id, a.name, as_datetime(a.due_at), as_datetime(getattr(a, "lock_at", None)))
        by_id[a.id] = item
        match = _HW_NAME.match((a.name or "").strip())
        if match:
            by_number.setdefault(int(match.group(1)), []).append(item)
    unique = {}
    for number, items in by_number.items():
        if len(items) == 1:
            unique[number] = items[0]
        else:
            log.warning("several Canvas assignments look like h%s: %s; set canvas_assignment_id",
                        number, ", ".join(f"{i.name} ({i.id})" for i in items))
    return CanvasDates(unique, by_id)


def fetch_canvas_dates(secrets: Secrets) -> CanvasDates:
    """All assignments in one request, cached for a few minutes (the web UI asks often)."""
    from .clients.canvas import CanvasClient

    key = (secrets.canvas_url, secrets.canvas_course_id)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    dates = read_canvas_dates(CanvasClient(secrets).course.get_assignments())
    _cache[key] = (time.monotonic(), dates)
    return dates


def resolve_deadlines(
    hw: HomeworkConfig, course: CourseConfig, canvas: Callable[[], CanvasDates]
) -> tuple[HomeworkConfig, str]:
    """Returns the homework with all three deadlines filled in, and where they came from."""
    own = hw.deadlines
    if own.group is not None:
        group, lock, source = own.group, None, f"{hw.name}.toml"
    else:
        try:
            dates = canvas()
        except Exception as e:
            raise ConfigError(
                f"{hw.name}: no group deadline in {hw.name}.toml and Canvas is unavailable ({e})"
            ) from e
        assignment = dates.by_id.get(hw.canvas_assignment_id) if hw.canvas_assignment_id else None
        assignment = assignment or dates.by_number.get(hw.number)
        if assignment is None or assignment.due is None:
            raise ConfigError(
                f"{hw.name}: no Canvas assignment with a due date; set [deadlines] group in {hw.name}.toml"
            )
        group, lock, source = assignment.due, assignment.lock, f"Canvas assignment {assignment.name} ({assignment.id})"

    tz = ZoneInfo(course.timezone)
    individual = own.individual or group - timedelta(days=course.individual_days_before_group)
    final = own.final or lock or group + timedelta(hours=course.final_grace_hours)
    resolved = Deadlines(
        individual=individual.astimezone(tz), group=group.astimezone(tz), final=final.astimezone(tz)
    )
    return hw.model_copy(update={"deadlines": resolved}), source
