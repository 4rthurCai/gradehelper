"""Grading stages. Reads remote systems; never writes to them (warnings/upload live elsewhere).

individual stage (at the individual deadline): individual branches, individual PRs,
    PR descriptions, JOJ scoreboard pinned at the deadline. Saved to hws/hN.indv.json.
group stage (at the group deadline): hN tag contents, peer reviews, release status and
    lateness. Pre-grading that is posted on Canvas. The individual part is reused as frozen
    at the individual deadline and is NOT re-checked; if it does not exist yet it is
    generated from PRs + JOJ only.
final stage (group deadline + 1 day): the same group checks with the later cutoff, so late
    releases are graded too; "late" still means no release by the group deadline. Not posted:
    TAs review and adjust hN.csv in the hw-scoreboard repo, then `upload` posts that file.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import cached_property
from typing import Callable

from .checks import joj, joj_config, joj_release, pull_requests, release
from .checks.repo import RepoContext, check_group_tag, check_individual_branches
from .clients.canvas import CanvasClient
from .clients.git import GitRepos
from .clients.gitea import GiteaClient
from .clients.mattermost import MattermostClient
from .config import RUNS, STAGE_FINAL, STAGE_GROUP, STAGE_INDIVIDUAL, HomeworkConfig, Workspace
from .models import Finding, Roster, StageResult, StudentMeta, Team, as_datetime
from .overrides import load_overrides, overrides_path
from .report import (
    HandEditedError,
    Row,
    build_rows,
    load_result,
    result_path,
    save_result,
    write_final_csv,
    write_individual_csv,
)
from .roster import load_roster

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merge_results(previous: StageResult | None, new: StageResult) -> StageResult:
    """Re-running a subset of teams replaces only those students' findings."""
    if previous is None:
        return new
    redone = set(new.students)
    return replace(
        new,
        students=tuple(dict.fromkeys([*previous.students, *new.students])),
        findings=tuple(f for f in previous.findings if f.student_id not in redone) + new.findings,
        meta={**{k: v for k, v in previous.meta.items() if k not in redone}, **new.meta},
    )


@dataclass
class Grader:
    ws: Workspace
    hw_number: int
    teams: str = ""
    progress: Progress = field(default=lambda msg: log.info(msg))
    overwrite: bool = False  # replace hand-edited CSVs

    # ---------- wiring ----------

    @cached_property
    def hw(self) -> HomeworkConfig:
        return self.ws.homework(self.hw_number)

    @cached_property
    def full_roster(self) -> Roster:
        return load_roster(self.ws.roster_path)

    @cached_property
    def roster(self) -> Roster:
        return self.full_roster.select_teams(self.teams)

    @cached_property
    def gitea(self) -> GiteaClient:
        return GiteaClient(self.ws.secrets)

    @cached_property
    def git(self) -> GitRepos:
        s = self.ws.secrets
        return GitRepos(s.git_host, s.gitea_org_name, self.ws.repos_dir)

    def canvas(self) -> CanvasClient:
        return CanvasClient(self.ws.secrets)

    def mattermost(self) -> MattermostClient:
        return MattermostClient(self.ws.secrets)

    @property
    def known_ids(self) -> set[str]:
        return {s.id for s in self.full_roster.students}

    def _per_team(self, label: str, fn: Callable[[Team], list[Finding]]) -> list[Finding]:
        teams = self.roster.teams
        self.progress(f"{label}: {len(teams)} teams")
        with ThreadPoolExecutor(max_workers=self.ws.course.workers) as pool:
            batches = list(pool.map(fn, teams))
        return [f for batch in batches for f in batch]

    def _individual_pulls(self, team: Team) -> list[pull_requests.IndividualPull]:
        return pull_requests.individual_pulls(team.repo, self.gitea.pulls(team.repo), self.hw.name, self.known_ids)

    @cached_property
    def _templates(self) -> list[str]:
        paths = [self.ws.templates_dir / name for name in self.ws.course.pr_description.templates]
        return [p.read_text(encoding="utf-8") for p in paths if p.exists()]

    # ---------- individual stage ----------

    def _pr_findings(self, team: Team) -> list[Finding]:
        pulls = self._individual_pulls(team)
        return [
            *pull_requests.missing_pr_findings(team.members, pulls),
            *pull_requests.description_findings(pulls, self._templates, self.ws.course.pr_description),
        ]

    @cached_property
    def joj_maxima(self) -> joj_config.JojMaxima:
        maxima = joj_config.load_maxima(self.git.sync(self.ws.course.joj.repo), self.ws.course.joj, self.hw)
        self.progress(
            f"JOJ maxima from {maxima.source}: individual {maxima.individual}, release {maxima.release}"
        )
        return maxima

    def _joj_findings(self, deadline: datetime | None, flag_missing: bool) -> list[Finding]:
        self.progress(f"JOJ scoreboard at {deadline or 'latest'}")
        board = joj.load_scoreboard(self.git, self.ws.course.joj, self.hw_number, deadline)
        exercises = self.joj_maxima.individual
        if not exercises:
            self.progress("individual JOJ thresholds skipped: no graded exercises found")
        return joj.check_joj(board, self.roster.graded, self.hw, exercises, self.ws.course.joj, flag_missing)

    def run_individual(self, deadline: datetime | None = None, check_branches: bool = True) -> StageResult:
        deadline = deadline or self.hw.deadlines.individual
        findings: list[Finding] = []
        if check_branches:
            ctx = RepoContext(self.ws.course, self.hw, self.git)
            findings += self._per_team("individual branches", lambda t: check_individual_branches(ctx, t))
        findings += self._per_team("individual PRs", self._pr_findings)
        findings += self._joj_findings(deadline, flag_missing=check_branches)
        result = StageResult(
            hw=self.hw_number,
            stage=STAGE_INDIVIDUAL,
            generated_at=_now(),
            params={"joj_deadline": deadline.isoformat() if deadline else None,
                    "teams": self.teams, "branches_checked": check_branches},
            students=tuple(s.id for s in self.roster.graded),
            findings=tuple(findings),
        )
        return self._save(result)

    # ---------- group stage ----------

    def _group_team(
        self, team: Team, ctx: RepoContext, cutoff: datetime | None, deadline: datetime | None
    ) -> tuple[list[Finding], dict[str, StudentMeta], list[joj_release.ReleaseRun], joj_release.ReleaseRun | None]:
        status = release.release_status(self.gitea, team, self.hw.name, self.ws.course.joj.release_status_context)
        findings, accepted = release.submission_findings(team, self.hw.name, status, cutoff)
        runs: list[joj_release.ReleaseRun] = []
        picked = None
        notes: tuple[str, ...] = ()
        if accepted:
            findings += check_group_tag(ctx, team)
            runs = joj_release.parse_release_issues(
                self.gitea.repo_issues(team.repo), self.hw.name, self.ws.course.joj.release_issue_title
            )
            picked = joj_release.pick_run(runs, status.sha, cutoff)
            if runs and not any(status.sha.startswith(r.commit) for r in runs if r.commit):
                notes = (f"{self.hw.name} tag points to {status.sha[:8]}, which has no JOJ release run "
                         "(tag moved after the release?)",)
        reviewers: set[str] = set()
        for pull in self._individual_pulls(team):
            reviewers |= pull_requests.reviewers_of(
                self.gitea, pull, self.known_ids, self.ws.course.review, cutoff
            )
        findings += pull_requests.review_findings(team.members, reviewers)
        return findings, release.lateness(team, status, deadline, notes), runs, picked

    def _group_joj(self, per_team: list[tuple[Team, list, joj_release.ReleaseRun | None]]) -> list[Finding]:
        maxima = self.joj_maxima.release
        if not maxima:
            self.progress(f"group JOJ skipped: no release exercise maxima for {self.hw.name}")
            return []
        return [
            f
            for team, _, picked in per_team
            if picked is not None
            for f in joj_release.group_joj_findings(team, picked, maxima, self.hw, self.ws.course.joj)
        ]

    def run_group(self, deadline: datetime | None = None) -> StageResult:
        """At the group deadline: reviews and release runs count up to that time."""
        return self._run_group_checks(STAGE_GROUP, deadline or self.hw.deadlines.group)

    def run_final(self, deadline: datetime | None = None) -> StageResult:
        """At group deadline + grace: grade late releases too, but keep 'late' as it was."""
        cutoff = deadline or self.hw.deadlines.final_cutoff(self.ws.course.final_grace_hours)
        return self._run_group_checks(STAGE_FINAL, cutoff)

    def _run_group_checks(self, stage: str, cutoff: datetime | None) -> StageResult:
        if load_result(result_path(self.ws.output_dir, self.hw_number, STAGE_INDIVIDUAL)) is None:
            self.progress("no individual result yet: generating it from PRs + JOJ only (branches not checked)")
            self.run_individual(check_branches=False)

        late_after = self.hw.deadlines.group or cutoff
        ctx = RepoContext(self.ws.course, self.hw, self.git)
        teams = self.roster.teams
        self.progress(f"{stage}: tag, reviews, release for {len(teams)} teams (cutoff {cutoff})")
        with ThreadPoolExecutor(max_workers=self.ws.course.workers) as pool:
            per_team = list(pool.map(lambda t: self._group_team(t, ctx, cutoff, late_after), teams))
        findings = [f for team_findings, *_ in per_team for f in team_findings]
        meta = {k: v for _, team_meta, *_ in per_team for k, v in team_meta.items()}
        releases = [(t, runs, picked) for t, (_, _, runs, picked) in zip(teams, per_team, strict=True)]
        findings += self._group_joj(releases)

        result = StageResult(
            hw=self.hw_number,
            stage=stage,
            generated_at=_now(),
            params={"cutoff": cutoff.isoformat() if cutoff else None,
                    "late_after": late_after.isoformat() if late_after else None, "teams": self.teams},
            students=tuple(s.id for s in self.roster.graded),
            findings=tuple(findings),
            meta=meta,
        )
        return self._save(result)

    # ---------- results and reports ----------

    def _save(self, result: StageResult) -> StageResult:
        path = result_path(self.ws.output_dir, self.hw_number, result.stage)
        merged = merge_results(load_result(path), result) if self.teams else result
        save_result(path, merged)
        self.progress(f"saved {path.name}")
        for problem in self.write_reports():
            self.progress(problem)
        return merged

    def results(self) -> dict[str, StageResult]:
        found = {}
        for stage in RUNS:
            r = load_result(result_path(self.ws.output_dir, self.hw_number, stage))
            if r is not None:
                found[stage] = r
        return found

    def run_times(self) -> dict[str, datetime]:
        """When each run last happened (for `run` to tell previews from real runs)."""
        return {stage: as_datetime(r.generated_at) for stage, r in self.results().items()}

    def latest_group_stage(self) -> str | None:
        results = self.results()
        return next((s for s in (STAGE_FINAL, STAGE_GROUP) if s in results), None)

    def rows(self, stage: str, stage_only: bool = False) -> list[Row]:
        """Rows for one run. group/final include the frozen individual part unless stage_only
        (used for warnings: only that run's own deductions)."""
        results = self.results()
        if stage not in results:
            return []
        course = self.ws.course
        overrides = load_overrides(overrides_path(self.ws.output_dir, self.hw_number), set(course.rubric))
        rubric_stage = STAGE_INDIVIDUAL if stage == STAGE_INDIVIDUAL else STAGE_GROUP
        if stage == STAGE_INDIVIDUAL or stage_only:
            keys = set(course.rubric_keys(rubric_stage))
            return build_rows(self.full_roster, course, [results[stage]], overrides, keys)
        chain = [results[s] for s in (STAGE_INDIVIDUAL, stage) if s in results]
        return build_rows(self.full_roster, course, chain, overrides)

    def write_reports(self) -> list[str]:
        """Write hNindv.csv and hN.csv (from the final run if present, else group).

        Returns problems instead of raising, e.g. a CSV kept because TAs edited it."""
        out = self.ws.output_dir
        jobs = []
        if STAGE_INDIVIDUAL in self.results():
            jobs.append((write_individual_csv, out / f"h{self.hw_number}indv.csv", STAGE_INDIVIDUAL))
        group_stage = self.latest_group_stage()
        if group_stage:
            jobs.append((write_final_csv, out / f"h{self.hw_number}.csv", group_stage))
        problems = []
        for writer, path, stage in jobs:
            try:
                writer(path, self.rows(stage), self.overwrite)
            except HandEditedError as e:
                problems.append(str(e))
        return problems


def suggest_stage(
    hw: HomeworkConfig, ran: dict[str, datetime], grace_hours: float, now: datetime | None = None
) -> tuple[str, str]:
    """Pick the run `gradehelper run` should execute, with a reason.

    ran: when each run last happened. A run only counts as done if it happened after its
    deadline; an earlier run was a preview and the real one is still due.
    """
    now = now or datetime.now(timezone.utc)
    d = hw.deadlines
    final_at = d.final_cutoff(grace_hours)

    def done(stage: str, deadline: datetime | None) -> bool:
        at = ran.get(stage)
        return at is not None and (deadline is None or at >= deadline)

    if d.individual is None or d.group is None or final_at is None:
        nxt = next((s for s in RUNS if s not in ran), STAGE_FINAL)
        return nxt, "deadlines not configured: first run not done yet"
    if now < d.individual:
        return STAGE_INDIVIDUAL, f"before the individual deadline ({d.individual}): preview"
    if not done(STAGE_INDIVIDUAL, d.individual):
        return STAGE_INDIVIDUAL, f"individual deadline ({d.individual}) passed, not graded since"
    if now < d.group:
        return STAGE_GROUP, f"before the group deadline ({d.group}): preview"
    if now < final_at or not done(STAGE_GROUP, d.group):
        return STAGE_GROUP, f"group deadline ({d.group}) passed: pre-grading for Canvas"
    return STAGE_FINAL, f"group deadline + grace ({final_at}) passed: full grading for TA review"
