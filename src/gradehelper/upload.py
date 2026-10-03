"""Canvas upload: plan (match CSV rows to submissions, diff) then apply."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .report import UploadRow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannedGrade:
    row: UploadRow
    submission: Any
    current_score: float | None
    unchanged: bool  # same score and comment as our last upload


@dataclass(frozen=True)
class UploadPlan:
    grades: tuple[PlannedGrade, ...]
    unmatched_rows: tuple[UploadRow, ...]
    unmatched_users: tuple[str, ...]

    @property
    def to_send(self) -> tuple[PlannedGrade, ...]:
        return tuple(g for g in self.grades if not g.unchanged)


def ledger_path(output_dir: Path, hw: int) -> Path:
    return output_dir / f"h{hw}.uploaded.json"


def _load_ledger(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _match(user: Any, by_id: dict[str, UploadRow], by_jaccount: dict[str, UploadRow]) -> UploadRow | None:
    for key in (getattr(user, "login_id", None), getattr(user, "sis_user_id", None)):
        if key and str(key) in by_id:
            return by_id[str(key)]
    email = getattr(user, "email", "") or ""
    for key in (email.split("@", 1)[0], getattr(user, "login_id", None)):
        if key and key in by_jaccount:
            return by_jaccount[key]
    return None


def plan_upload(rows: list[UploadRow], assignment: Any, students: list[Any], ledger: Path, force: bool) -> UploadPlan:
    by_id = {r.id: r for r in rows if r.id}
    by_jaccount = {r.jaccount: r for r in rows if r.jaccount}
    users = {u.id: u for u in students}
    sent_before = {} if force else _load_ledger(ledger)

    grades, matched, unmatched_users = [], set(), []
    for submission in assignment.get_submissions():
        user = users.get(submission.user_id)
        row = _match(user, by_id, by_jaccount) if user else None
        if row is None:
            if user is not None:
                unmatched_users.append(f"{user.name} ({getattr(user, 'login_id', '?')})")
            continue
        matched.add(row.id)
        last = sent_before.get(row.id)
        unchanged = last == {"score": row.score, "comment": row.comment}
        grades.append(PlannedGrade(row, submission, getattr(submission, "score", None), unchanged))
    unmatched_rows = tuple(r for r in rows if r.id not in matched)
    return UploadPlan(tuple(grades), unmatched_rows, tuple(unmatched_users))


def apply_upload(plan: UploadPlan, ledger: Path) -> tuple[int, list[str]]:
    record = _load_ledger(ledger)
    sent, failures = 0, []
    for g in plan.to_send:
        try:
            g.submission.edit(
                submission={"posted_grade": g.row.score},
                comment={"text_comment": g.row.comment},
            )
        except Exception as e:
            log.error("upload failed for %s %s: %s", g.row.id, g.row.name, e)
            failures.append(f"{g.row.id} {g.row.name}: {e}")
            continue
        record[g.row.id] = {"score": g.row.score, "comment": g.row.comment}
        sent += 1
        # Persist after every success so an interrupted upload never double-posts comments.
        ledger.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("uploaded %d grades, %d failures", sent, len(failures))
    return sent, failures


def plan_summary(plan: UploadPlan) -> dict:
    return {
        "to_send": [asdict(g.row) | {"current_score": g.current_score} for g in plan.to_send],
        "unchanged": len(plan.grades) - len(plan.to_send),
        "unmatched_rows": [asdict(r) for r in plan.unmatched_rows],
        "unmatched_users": list(plan.unmatched_users),
    }
