"""Manual grade adjustments that survive re-runs (hws/hN.overrides.toml).

    [[override]]
    student = "520000000001"      # student ID or jaccount; or use team = "hteam-03"
    remove = ["noReview"]         # drop these deductions
    add = { indvUntidy = 1 }      # add deductions (key = count)
    score = -0.5                  # optional: force the final score
    note = "review left on Mattermost, checked by TA"
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .models import Finding, Student


@dataclass(frozen=True)
class Override:
    student: str = ""
    team: str = ""
    remove: tuple[str, ...] = ()
    add: dict[str, int] = field(default_factory=dict)
    score: float | None = None
    note: str = ""

    def matches(self, s: Student) -> bool:
        if self.team:
            return s.team == self.team
        return self.student in (s.id, s.jaccount)


@dataclass(frozen=True)
class Adjusted:
    findings: tuple[Finding, ...]
    notes: tuple[str, ...]
    fixed_score: float | None


def overrides_path(output_dir: Path, hw: int) -> Path:
    return output_dir / f"h{hw}.overrides.toml"


def load_overrides(path: Path, rubric_keys: set[str]) -> tuple[Override, ...]:
    if not path.exists():
        return ()
    with path.open("rb") as f:
        raw = tomllib.load(f).get("override", [])
    result = []
    for i, entry in enumerate(raw, 1):
        item = Override(
            student=str(entry.get("student", "")),
            team=str(entry.get("team", "")),
            remove=tuple(entry.get("remove", [])),
            add={k: int(v) for k, v in entry.get("add", {}).items()},
            score=float(entry["score"]) if "score" in entry else None,
            note=str(entry.get("note", "")),
        )
        if bool(item.student) == bool(item.team):
            raise ValueError(f"{path} override #{i}: set exactly one of student / team")
        bad = (set(item.remove) | set(item.add)) - rubric_keys
        if bad:
            raise ValueError(f"{path} override #{i}: unknown rubric keys {sorted(bad)}")
        result.append(item)
    return tuple(result)


def save_overrides(path: Path, overrides: Iterable[Override]) -> None:
    def q(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)  # JSON strings are valid TOML strings

    blocks = []
    for o in overrides:
        lines = ["[[override]]", f"team = {q(o.team)}" if o.team else f"student = {q(o.student)}"]
        if o.remove:
            lines.append(f"remove = [{', '.join(q(k) for k in o.remove)}]")
        if o.add:
            lines.append("add = { " + ", ".join(f"{k} = {v}" for k, v in o.add.items()) + " }")
        if o.score is not None:
            lines.append(f"score = {o.score:g}")
        if o.note:
            lines.append(f"note = {q(o.note)}")
        blocks.append("\n".join(lines))
    path.write_text("\n\n".join(blocks) + "\n" if blocks else "", encoding="utf-8")


def apply_overrides(student: Student, findings: Iterable[Finding], overrides: Iterable[Override]) -> Adjusted:
    current = tuple(findings)
    notes: list[str] = []
    fixed = None
    for o in (o for o in overrides if o.matches(student)):
        current = tuple(f for f in current if f.key not in o.remove)
        current += tuple(Finding(student.id, key, count, ("manual adjustment",)) for key, count in o.add.items())
        if o.score is not None:
            fixed = o.score
        if o.note:
            notes.append(o.note)
    return Adjusted(current, tuple(notes), fixed)
