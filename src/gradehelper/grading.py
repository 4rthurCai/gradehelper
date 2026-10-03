"""Turn findings into a score and a student-facing comment. The only place rubric math happens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import RubricItem
from .models import Finding

NO_PROBLEM = "no problem detected by the auto-grader"


@dataclass(frozen=True)
class Deduction:
    key: str
    count: int
    points: float
    description: str


@dataclass(frozen=True)
class Grade:
    score: float
    deductions: tuple[Deduction, ...]
    details: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def keys(self) -> set[str]:
        return {d.key for d in self.deductions}

    @property
    def comment(self) -> str:
        return render_comment(self)


def fmt_points(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def aggregate(findings: Iterable[Finding]) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Per rubric key: the largest count seen and all details, deduplicated, in order."""
    counts: dict[str, int] = {}
    details: dict[str, dict[str, None]] = {}
    for f in findings:
        counts[f.key] = max(counts.get(f.key, 0), f.count)
        details.setdefault(f.key, {}).update(dict.fromkeys(f.details))
    return {k: (counts[k], tuple(details[k])) for k in counts}


def compute_grade(
    findings: Iterable[Finding],
    rubric: dict[str, RubricItem],
    floor: float,
    notes: tuple[str, ...] = (),
    fixed_score: float | None = None,
) -> Grade:
    hits = aggregate(findings)
    deductions, details = [], []
    for key, item in rubric.items():  # rubric order decides comment order
        count, key_details = hits.get(key, (0, ()))
        if count <= 0:
            continue
        deductions.append(Deduction(key, count, item.points * count, item.description))
        details.extend(d for d in key_details if d not in details)
    unknown = set(hits) - set(rubric)
    if unknown:
        raise KeyError(f"findings use keys missing from the rubric: {sorted(unknown)}")
    score = max(sum(d.points for d in deductions), floor)
    if fixed_score is not None:
        score = fixed_score
    return Grade(score, tuple(deductions), tuple(details), notes)


def render_comment(grade: Grade) -> str:
    parts = []
    if grade.deductions:
        items = "; ".join(
            f"{d.description}{f' (x{d.count})' if d.count > 1 else ''}, {fmt_points(d.points)}"
            for d in grade.deductions
        )
        parts.append(f"General Info: {items}.")
        if grade.details:
            parts.append(f"Detail: {', '.join(grade.details)}.")
    else:
        parts.append(NO_PROBLEM)
    if grade.notes:
        parts.append(f"Note: {' '.join(grade.notes)}")
    return " ".join(parts)
