"""Per-exercise maxima from the JOJ3 configs in the engr151-joj repo.

home/tt/.config/joj/homework/hN/conf-release.json   the release run (group JOJ)
home/tt/.config/joj/homework/hN/exK/conf.json       one exercise (individual JOJ)

The .json files are what JOJ3 runs, with every case score spelled out. A stage's maximum
is the sum of its diff case scores plus any positive result-status score. Stages of the
same exercise ([oj] h4/ex1, [oj] h4/ex1-asan, [run] h7/ex3-valgrind, ...) add up.
The exercises in the release config are the graded (mandatory) ones.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from git import Repo
from git.exc import GitCommandError

from ..config import HomeworkConfig, JojSettings

log = logging.getLogger(__name__)
_EXERCISE = re.compile(r"^\[(?:oj|run)\] (?:h\d+/)?(ex\d+)\b")


def exercise_of(stage_name: str) -> str | None:
    """'[oj] h4/ex1-asan' -> 'ex1'; '[run] h7/ex3-valgrind' -> 'ex3'; '[cq] ...' -> None."""
    match = _EXERCISE.match(stage_name.strip())
    return match.group(1) if match else None


def stage_max(stage: dict) -> float:
    total = 0.0
    for parser in stage.get("parsers", []):
        options = parser.get("with", {})
        if parser.get("name") == "diff":
            total += sum(o.get("score", 0) for case in options.get("cases", []) for o in case.get("outputs", []))
        elif parser.get("name") == "result-status" and options.get("score", 0) > 0:
            total += options["score"]
    return total


def exercise_maxima(conf: dict) -> dict[str, float]:
    maxima: dict[str, float] = {}
    for stage in conf.get("stages", []):
        ex = exercise_of(stage.get("name", ""))
        if ex:
            maxima[ex] = maxima.get(ex, 0.0) + stage_max(stage)
    return maxima


@dataclass(frozen=True)
class JojMaxima:
    individual: dict[int, float]  # graded exercise number -> max on the scoreboard
    release: dict[str, float]  # "ex2" -> max in the release run
    source: str  # where the numbers came from, for the run log


def _read_json(repo: Repo, ref: str, path: str) -> dict | None:
    try:
        return json.loads(repo.git.show(f"{ref}:{path}"))
    except GitCommandError:
        return None


def load_maxima(repo: Repo, joj: JojSettings, hw: HomeworkConfig) -> JojMaxima:
    """hN.toml values ([joj.exercises], [joj.release]) override the JOJ3 configs."""
    ref = f"origin/{joj.config_branch}"
    base = f"{joj.config_dir}/{hw.name}"
    release_conf = _read_json(repo, ref, f"{base}/conf-release.json")
    release = dict(hw.joj_release_exercises) or (exercise_maxima(release_conf) if release_conf else {})

    if hw.joj_exercises:
        individual = {ex: top for ex, top in hw.joj_exercises.items() if top > 0}
    else:
        individual = {}
        for ex in release:
            conf = _read_json(repo, ref, f"{base}/{ex}/conf.json")
            top = sum(exercise_maxima(conf).values()) if conf else 0
            if top > 0:
                individual[int(ex[2:])] = top
            else:
                log.warning("%s/%s: no individual JOJ config with a score; exercise not graded", hw.name, ex)

    source = "hN.toml" if hw.joj_exercises or hw.joj_release_exercises else f"{joj.repo}:{ref}:{base}"
    if not release_conf and not hw.joj_release_exercises:
        log.warning("%s: no %s/conf-release.json on %s", hw.name, base, ref)
    return JojMaxima(individual, release, source)
