"""Core data types. Everything is immutable and keyed by student ID."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_CJK = re.compile("[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]+")
_STUDENT_ID = re.compile(r"(?<!\d)(\d{12})(?!\d)")


def english_name(raw: str) -> str:
    """'Zhang San 张三' -> 'Zhang San'; 'JOHN DOE' -> 'John Doe'."""
    return " ".join(_CJK.sub(" ", raw).split()).title()


def extract_student_id(text: str | None) -> str | None:
    """Find a 12-digit student ID in free text (PR title, Gitea full name)."""
    if not text:
        return None
    match = _STUDENT_ID.search(text)
    if match:
        return match.group(1)
    digits = "".join(c for c in text if c.isdigit())
    return digits[:12] if len(digits) >= 12 else None


def as_datetime(value: Any) -> datetime | None:
    """Gitea returns datetimes or ISO strings depending on the field."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def team_number(team_key: str) -> int | None:
    digits = "".join(c for c in team_key if c.isdigit())
    return int(digits) if digits else None


@dataclass(frozen=True)
class Student:
    id: str
    name: str
    jaccount: str
    team: str

    @property
    def display_name(self) -> str:
        return english_name(self.name)


@dataclass(frozen=True)
class Team:
    key: str  # e.g. "hteam-01"
    members: tuple[Student, ...]

    @property
    def repo(self) -> str:
        """Gitea repo name: 'hteam-01' -> 'hteam01'."""
        return self.key.replace("-", "")

    @property
    def number(self) -> int:
        return team_number(self.key) or 0


@dataclass(frozen=True)
class Roster:
    students: tuple[Student, ...]

    @property
    def teams(self) -> tuple[Team, ...]:
        keys = sorted({s.team for s in self.students if s.team}, key=lambda k: (team_number(k) or 0, k))
        return tuple(
            Team(key, tuple(s for s in self.students if s.team == key)) for key in keys
        )

    @property
    def graded(self) -> tuple[Student, ...]:
        """Students that belong to a team, in team order."""
        return tuple(s for t in self.teams for s in t.members)

    def by_id(self) -> dict[str, Student]:
        return {s.id: s for s in self.students}

    def by_jaccount(self) -> dict[str, Student]:
        return {s.jaccount: s for s in self.students if s.jaccount}

    def select_teams(self, spec: str) -> Roster:
        """Keep only teams named in spec ("1,5" or "hteam-01,hteam-05")."""
        if not spec.strip():
            return self
        wanted = set()
        for part in (p.strip() for p in spec.split(",") if p.strip()):
            wanted.add(f"hteam-{int(part):02d}" if part.isdigit() else part)
        unknown = wanted - {t.key for t in self.teams}
        if unknown:
            raise ValueError(f"unknown team(s): {', '.join(sorted(unknown))}")
        return Roster(tuple(s for s in self.students if s.team in wanted))


@dataclass(frozen=True)
class Finding:
    """One rubric hit for one student. count multiplies the rubric points."""

    student_id: str
    key: str
    count: int = 1
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class StudentMeta:
    late: bool = False
    release_time: str = ""
    notes: tuple[str, ...] = ()  # for TAs only; never shown to students


@dataclass(frozen=True)
class StageResult:
    hw: int
    stage: str
    generated_at: str
    params: dict = field(default_factory=dict)
    students: tuple[str, ...] = ()  # IDs that were checked
    findings: tuple[Finding, ...] = ()
    meta: dict[str, StudentMeta] = field(default_factory=dict)

    def findings_for(self, student_id: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.student_id == student_id)
