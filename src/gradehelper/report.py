"""Stage results (JSON, structured) and CSV reports (legacy-compatible columns)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import STAGE_FINAL, STAGE_GROUP, STAGE_INDIVIDUAL, CourseConfig
from .grading import Grade, compute_grade, fmt_points
from .models import Finding, Roster, StageResult, Student, StudentMeta
from .overrides import Override, apply_overrides

log = logging.getLogger(__name__)
WRITTEN_STATE = ".gradehelper-written.json"  # not a CSV, so the hw-scoreboard repo ignores it

INDIVIDUAL_HEADER = ["Name", "ID", "Jaccount", "Team", "Individual_Score", "Individual_Comments"]
FINAL_HEADER = ["Name", "ID", "Jaccount", "Team", "Score", "Late", "Release Time", "Comments", "TA Notes"]


# ---------- stage results ----------

def result_path(output_dir: Path, hw: int, stage: str) -> Path:
    suffix = {STAGE_INDIVIDUAL: "indv", STAGE_GROUP: "group", STAGE_FINAL: "final"}[stage]
    return output_dir / f"h{hw}.{suffix}.json"


def save_result(path: Path, result: StageResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(result)
    data["meta"] = {k: asdict(v) for k, v in result.meta.items()}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_result(path: Path) -> StageResult | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return StageResult(
        hw=data["hw"],
        stage=data["stage"],
        generated_at=data["generated_at"],
        params=data.get("params", {}),
        students=tuple(data.get("students", [])),
        findings=tuple(
            Finding(f["student_id"], f["key"], f.get("count", 1), tuple(f.get("details", [])))
            for f in data.get("findings", [])
        ),
        meta={k: StudentMeta(**{**v, "notes": tuple(v.get("notes", ()))}) for k, v in data.get("meta", {}).items()},
    )


# ---------- grading rows ----------

@dataclass(frozen=True)
class Row:
    student: Student
    grade: Grade
    meta: StudentMeta

    def as_dict(self) -> dict:
        return {
            "id": self.student.id,
            "name": self.student.display_name,
            "jaccount": self.student.jaccount,
            "team": self.student.team,
            "score": self.grade.score,
            "deductions": [asdict(d) for d in self.grade.deductions],
            "details": list(self.grade.details),
            "notes": list(self.grade.notes),
            "comment": self.grade.comment,
            "late": self.meta.late,
            "release_time": self.meta.release_time,
            "ta_notes": list(self.meta.notes),
        }


def build_rows(
    roster: Roster,
    course: CourseConfig,
    results: list[StageResult],
    overrides: tuple[Override, ...] = (),
    stage_keys: set[str] | None = None,
) -> list[Row]:
    """One graded row per student checked in the last (most recent) stage result."""
    checked = set(results[-1].students) if results else set()
    rubric = {k: v for k, v in course.rubric.items() if stage_keys is None or k in stage_keys}
    rows = []
    for student in roster.graded:
        if student.id not in checked:
            continue
        findings = [f for r in results for f in r.findings_for(student.id)]
        adjusted = apply_overrides(student, findings, overrides)
        kept = [f for f in adjusted.findings if f.key in rubric]
        grade = compute_grade(kept, rubric, course.score_floor, adjusted.notes, adjusted.fixed_score)
        meta = next((r.meta[student.id] for r in results if student.id in r.meta), StudentMeta())
        rows.append(Row(student, grade, meta))
    return rows


class HandEditedError(Exception):
    """The CSV changed since gradehelper last wrote it (e.g. TA adjustments)."""


def write_individual_csv(path: Path, rows: list[Row], overwrite: bool = False) -> None:
    _write_guarded(path, INDIVIDUAL_HEADER, [
        [r.student.display_name, r.student.id, r.student.jaccount, r.student.team,
         fmt_points(r.grade.score), r.grade.comment]
        for r in rows
    ], overwrite)


def write_final_csv(path: Path, rows: list[Row], overwrite: bool = False) -> None:
    _write_guarded(path, FINAL_HEADER, [
        [r.student.display_name, r.student.id, r.student.jaccount, r.student.team,
         fmt_points(r.grade.score), "Yes" if r.meta.late else "No", r.meta.release_time, r.grade.comment,
         "; ".join(r.meta.notes)]
        for r in rows
    ], overwrite)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_guarded(path: Path, header: list[str], rows: list[list[str]], overwrite: bool) -> None:
    """Never silently replace a CSV someone edited by hand (or that another TA pushed)."""
    state_path = path.parent / WRITTEN_STATE
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if path.exists() and not overwrite and state.get(path.name) != _sha(path.read_bytes()):
        raise HandEditedError(
            f"{path} was edited or not written by gradehelper; keeping it. "
            "Re-run with --overwrite to regenerate it (hand edits are lost)."
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    state_path.write_text(json.dumps({**state, path.name: _sha(data)}, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class UploadRow:
    name: str
    id: str
    jaccount: str
    score: float
    comment: str


def read_final_csv(path: Path) -> list[UploadRow]:
    """The final CSV is the upload source of truth, so hand edits are respected."""
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            score = (row.get("Score") or "").strip()
            if not score:
                continue
            try:
                value = float(score)
            except ValueError as e:
                raise ValueError(f"{path}:{i}: score {score!r} is not a number") from e
            rows.append(UploadRow(
                name=(row.get("Name") or "").strip(),
                id=(row.get("ID") or "").strip(),
                jaccount=(row.get("Jaccount") or "").strip(),
                score=value,
                comment=(row.get("Comments") or "").strip(),
            ))
    return rows
