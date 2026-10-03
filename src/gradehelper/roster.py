"""Roster file (hteams.csv: Name,ID,Email,Team) and Canvas sync."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from .models import Roster, Student, team_number

log = logging.getLogger(__name__)
HEADER = ["Name", "ID", "Email", "Team"]


def jaccount_of(email: str) -> str:
    return email.split("@", 1)[0].strip() if email else ""


def load_roster(path: Path) -> Roster:
    if not path.exists():
        raise FileNotFoundError(
            f"roster {path} not found; run `gradehelper roster sync` first"
        )
    students = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sid = (row.get("ID") or "").strip()
            if not sid:
                log.warning("roster row without ID skipped: %s", row)
                continue
            email = (row.get("Email") or "").strip()
            students.append(
                Student(
                    id=sid,
                    name=(row.get("Name") or "").strip(),
                    jaccount=jaccount_of(email),
                    team=(row.get("Team") or "").strip(),
                )
            )
    return Roster(tuple(students))


def save_roster(path: Path, roster: Roster, emails: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for s in roster.students:
            writer.writerow([s.name, s.id, emails.get(s.id, f"{s.jaccount}@sjtu.edu.cn"), s.team])


def roster_from_canvas(course) -> tuple[Roster, dict[str, str]]:
    """Build a roster from Canvas students and their hteam groups."""
    users = list(course.get_users(enrollment_type=["student"]))
    team_of_user: dict[int, str] = {}
    for group in course.get_groups():
        if not group.name.lower().startswith("hteam"):
            continue
        num = team_number(group.name)
        if num is None:
            log.warning("skipping group %r: no team number", group.name)
            continue
        for membership in group.get_memberships():
            team_of_user[membership.user_id] = f"hteam-{num:02d}"

    students, emails = [], {}
    for user in users:
        sid = str(getattr(user, "sis_user_id", None) or getattr(user, "login_id", None) or user.id)
        email = getattr(user, "email", "") or ""
        emails[sid] = email
        students.append(
            Student(
                id=sid,
                name=user.name.strip(),
                jaccount=jaccount_of(email),
                team=team_of_user.get(user.id, ""),
            )
        )
    return Roster(tuple(students)), emails


LEFT_COURSE = "left the course"
JOINED_COURSE = "joined the course"
LEFT_TEAM = "no longer in a team"
JOINED_TEAM = "added to a team"
MOVED = "moved to another team"
CHANGE_ORDER = (LEFT_COURSE, LEFT_TEAM, MOVED, JOINED_TEAM, JOINED_COURSE)


@dataclass(frozen=True)
class RosterChange:
    kind: str
    student: Student
    before: str  # team before ("" = none)
    after: str


def diff_rosters(old: Roster, new: Roster) -> list[RosterChange]:
    """What a sync would change, grouped by kind (order of CHANGE_ORDER), then by team."""
    before, after = old.by_id(), new.by_id()
    changes = []
    for sid in before.keys() | after.keys():
        b, a = before.get(sid), after.get(sid)
        if b is None:
            changes.append(RosterChange(JOINED_COURSE, a, "", a.team))
        elif a is None:
            changes.append(RosterChange(LEFT_COURSE, b, b.team, ""))
        elif b.team != a.team:
            kind = LEFT_TEAM if not a.team else JOINED_TEAM if not b.team else MOVED
            changes.append(RosterChange(kind, a, b.team, a.team))
    return sorted(changes, key=lambda c: (CHANGE_ORDER.index(c.kind), c.before or c.after, c.student.id))
