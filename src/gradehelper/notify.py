"""Mattermost warnings: build a preview first, send only after confirmation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .clients.mattermost import MattermostClient
from .report import Row

log = logging.getLogger(__name__)

# What to tell students per rubric key. Keys not listed here produce no warning.
ADVICE = {
    "indvFailSubmit": (
        "your individual submission is missing or incomplete; commit and push it to your individual branch"
    ),
    "indvUntidy": "your individual branch contains extra files; keep only the required ones",
    "noIndividualPR": "no individual PR found; open one following the expected title format",
    "notWritingPR": (
        "your PR description looks like an unfilled template; "
        "describe what you implemented, bugs met and a self-evaluation"
    ),
    "jojFailHomework": "your JOJ homework score is below the passing line",
    "jojFailExercise": "some JOJ exercises score below 25%",
    "groupFailSubmit": "your team's release (tag) is missing or incomplete; create it as soon as possible",
    "groupUntidy": "your team's release contains extra files",
    "groupLowCodeQuality": "your team's code has quality issues",
    "noReview": "you have not left a substantive review on your teammates' PRs",
    "jojFailCompile": "your team's release did not pass JOJ (compile/status check)",
}


@dataclass(frozen=True)
class StudentWarning:
    student_id: str
    name: str
    jaccount: str
    text: str


def build_warnings(rows: list[Row], hw: str) -> list[StudentWarning]:
    warnings = []
    for row in rows:
        lines = [f"- {ADVICE[d.key]}" for d in row.grade.deductions if d.key in ADVICE]
        if not lines:
            continue
        details = f"\nDetails: {', '.join(row.grade.details)}" if row.grade.details else ""
        text = f"[{hw}] Auto-grader warning:\n" + "\n".join(lines) + details + f"\n@{row.student.jaccount}"
        warnings.append(StudentWarning(row.student.id, row.student.display_name, row.student.jaccount, text))
    return warnings


def send_warnings(client: MattermostClient, warnings: list[StudentWarning]) -> tuple[int, list[str]]:
    """Returns (sent count, failures)."""
    sent, failures = 0, []
    for w in warnings:
        try:
            client.post_to_student_channel(w.student_id, w.text)
            sent += 1
            log.info("warned %s %s", w.student_id, w.name)
        except Exception as e:
            log.error("cannot warn %s %s: %s", w.student_id, w.name, e)
            failures.append(f"{w.student_id} {w.name}: {e}")
    return sent, failures
