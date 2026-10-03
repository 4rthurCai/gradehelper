"""Group JOJ: the JOJ3 run of the hN release, read from the result issue in the team repo.

Title: "JOJ3 Result for h1-release by @user - Score: 100 / 100"
Body:  "... commit <sha> ..." and one "## [oj] ex2 - Score: 45" section per exercise.
The body has scores only; per-exercise maxima come from checks/joj_config.py.
Sections of one exercise ([oj] h4/ex1, [oj] h4/ex1-asan, ...) add up, as in the config.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from ..config import HomeworkConfig, JojSettings
from ..models import Finding, Team, as_datetime
from .joj import evaluate_percents
from .joj_config import exercise_of

log = logging.getLogger(__name__)
_SECTION = re.compile(r"^## (.+?) - Score: (-?\d+(?:\.\d+)?)\s*$", re.MULTILINE)
_COMMIT = re.compile(r"\bcommit ([0-9a-f]{7,40})\b")


@dataclass(frozen=True)
class ReleaseRun:
    issue: int
    score: float
    max_score: float
    commit: str
    created: datetime | None
    exercises: dict[str, float] = field(default_factory=dict)


def parse_release_issues(issues: Iterable[Any], hw: str, title_pattern: str) -> list[ReleaseRun]:
    title = re.compile(title_pattern.format(hw=re.escape(hw)))
    runs = []
    for issue in issues:
        match = title.match(issue.title or "")
        if not match:
            continue
        body = issue.body or ""
        commit = _COMMIT.search(body)
        runs.append(ReleaseRun(
            issue=issue.number,
            score=float(match.group(1)),
            max_score=float(match.group(2)),
            commit=commit.group(1) if commit else "",
            created=as_datetime(issue.created_at),
            exercises=_exercise_scores(body),
        ))
    return runs


def _exercise_scores(body: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for section, score in _SECTION.findall(body):
        ex = exercise_of(section)
        if ex:
            scores[ex] = scores.get(ex, 0.0) + float(score)
    return scores


def pick_run(runs: list[ReleaseRun], tag_sha: str | None, cutoff: datetime | None) -> ReleaseRun | None:
    """The run of the commit the hN tag points to; else the latest run before the cutoff."""
    def latest(candidates: list[ReleaseRun]) -> ReleaseRun | None:
        return max(candidates, key=lambda r: (r.created is not None, r.created or datetime.min), default=None)

    if tag_sha:
        matching = [r for r in runs if r.commit and tag_sha.startswith(r.commit)]
        if matching:
            return latest(matching)
    return latest([r for r in runs if cutoff is None or (r.created is not None and r.created <= cutoff)])


def group_joj_findings(
    team: Team, run: ReleaseRun, maxima: dict[str, float], hw: HomeworkConfig, joj: JojSettings
) -> list[Finding]:
    percents = {ex: run.exercises.get(ex, 0) / top * 100 for ex, top in maxima.items() if top > 0}
    findings = evaluate_percents(
        percents,
        joj.group,
        [s.id for s in team.members],
        ("jojGroupFailHomework", "jojGroupFailExercise"),
        f"JOJ {hw.name}-release #{run.issue}",
    )
    if findings:
        log.warning("%s: group JOJ %s/%s below the line", team.repo, run.score, run.max_score)
    return findings
