"""JOJ scoreboard: per-student homework and exercise pass checks."""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from ..clients.git import GitRepos, file_at
from ..config import HomeworkConfig, JojSettings, JojThresholds
from ..models import Finding, Student
from ..roster import jaccount_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scoreboard:
    rows: dict[str, dict[str, str]]  # jaccount -> row
    commit: str


def _number(value: str | None) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_scoreboard(text: str, commit: str = "") -> Scoreboard:
    rows = {}
    for row in csv.DictReader(io.StringIO(text)):
        key = jaccount_of(row.get("", ""))  # first, unnamed column holds the jaccount/email
        if key:
            rows[key] = row
    return Scoreboard(rows, commit)


def load_scoreboard(repos: GitRepos, joj: JojSettings, hw: int, deadline: datetime | None) -> Scoreboard:
    repo = repos.sync(joj.repo)
    path = joj.scoreboard_path.format(hw=hw)
    text, sha = file_at(repo, f"origin/{joj.branch}", path, before=deadline)
    log.info("JOJ scoreboard %s at %s (deadline %s)", path, sha[:8], deadline)
    return parse_scoreboard(text, sha)


def evaluate_percents(
    percents: dict[str, float],
    thresholds: JojThresholds,
    student_ids: Iterable[str],
    keys: tuple[str, str],
    label: str,
) -> list[Finding]:
    """Shared rule: average over graded exercises, and each exercise, against thresholds.

    keys = (homework key, exercise key); label prefixes details, e.g. "JOJ h1".
    """
    if not percents:
        return []
    homework_key, exercise_key = keys
    hits: list[tuple[str, int, tuple[str, ...]]] = []
    overall = sum(percents.values()) / len(percents)
    if overall < thresholds.homework_fail_percent:
        hits.append((homework_key, 1, (f"{label} average {overall:.0f}% (< {thresholds.homework_fail_percent:g}%)",)))
    low = [ex for ex, pct in percents.items() if pct < thresholds.exercise_fail_percent]
    if low:
        details = tuple(f"{label}/{ex} {percents[ex]:.0f}% (< {thresholds.exercise_fail_percent:g}%)" for ex in low)
        hits.append((exercise_key, min(len(low), thresholds.max_exercise_failures), details))
    return [Finding(sid, key, count, details) for sid in student_ids for key, count, details in hits]


def evaluate_row(
    row: dict[str, str], hw: HomeworkConfig, exercises: dict[int, float], joj: JojSettings, student_id: str
) -> list[Finding]:
    """Individual JOJ over the graded exercises (number -> max score).

    The hN total at/above pass_threshold passes everything."""
    total = _number(row.get(hw.name))
    if total is not None and total >= hw.pass_threshold:
        return []
    percents = {
        f"ex{ex}": (_number(row.get(f"{hw.name}/ex{ex}")) or 0) / top * 100
        for ex, top in exercises.items()
        if top > 0
    }
    return evaluate_percents(
        percents, joj.individual, [student_id], ("jojFailHomework", "jojFailExercise"), f"JOJ {hw.name}"
    )


def check_joj(
    board: Scoreboard,
    students: tuple[Student, ...],
    hw: HomeworkConfig,
    exercises: dict[int, float],
    joj: JojSettings,
    flag_missing: bool = True,
) -> list[Finding]:
    """flag_missing: students absent from the scoreboard fail individual submission."""
    findings = []
    for student in students:
        row = board.rows.get(student.jaccount)
        if row is None:
            if flag_missing:
                log.warning("%s %s: not in JOJ scoreboard", student.id, student.display_name)
                findings.append(
                    Finding(student.id, "indvFailSubmit", details=("JOJ submission not found in scoreboard",))
                )
            continue
        findings += evaluate_row(row, hw, exercises, joj, student.id)
    return findings
