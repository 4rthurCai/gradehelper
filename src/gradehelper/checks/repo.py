"""Checks on team repositories: individual branches and the hN release tag."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from git import Repo

from ..clients.git import GitRepos, force_checkout, remote_branches, tag_names
from ..config import CourseConfig, HomeworkConfig
from ..models import Finding, Team
from . import code_quality
from .layout import LayoutReport, inspect_layout, list_dir, root_allowlist

log = logging.getLogger(__name__)
_GITEA_DIR = ".gitea"


@dataclass(frozen=True)
class RepoContext:
    course: CourseConfig
    hw: HomeworkConfig
    repos: GitRepos

    @property
    def allowed_root(self) -> set[str]:
        layout = self.course.layout
        return root_allowlist(layout.root_allowlist, layout.max_homework_dirs)

    @property
    def allowed_gitea(self) -> set[str]:
        return self.allowed_root | set(self.course.layout.gitea_dir_allowlist)


def _layout_at(ctx: RepoContext, workdir: Path, include_gitea: bool) -> LayoutReport:
    root = list_dir(workdir) or []
    root = [e for e in root if e != ".git"]
    if include_gitea and (workdir / _GITEA_DIR).is_dir():
        root += [e for e in list_dir(workdir / _GITEA_DIR) or [] if e not in ctx.allowed_gitea]
    return inspect_layout(
        root,
        list_dir(workdir / ctx.hw.name),
        ctx.hw.mandatory,
        ctx.hw.optional,
        ctx.allowed_root,
    )


# ---------- individual branches ----------

def _individual_details(hw: str, report: LayoutReport) -> tuple[list[str], list[str]]:
    incomplete, untidy = [], []
    if report.hw_dir_missing:
        incomplete.append(f"individual branch {hw} dir missing")
    incomplete += [f"individual branch {hw}/{f} file missing" for f in report.missing_files]
    if report.readme_missing:
        incomplete.append(f"individual branch {hw}/README file missing")
    if report.extra_root:
        untidy.append(f"individual branch redundant files: {', '.join(report.extra_root)}")
    if report.extra_hw:
        untidy.append(f"individual branch redundant files: {', '.join(report.extra_hw)}")
    return incomplete, untidy


def check_individual_branches(ctx: RepoContext, team: Team) -> list[Finding]:
    """Each member must have branch <student id> with a complete, tidy hN/."""
    repo = ctx.repos.sync(team.repo, team.key)
    branches = remote_branches(repo)
    findings: list[Finding] = []
    for student in team.members:
        if student.id not in branches:
            log.warning("%s %s %s: individual branch missing", team.repo, student.id, student.display_name)
            findings.append(Finding(student.id, "indvFailSubmit", details=("individual branch missing",)))
            continue
        try:
            force_checkout(repo, f"origin/{student.id}")
            report = _layout_at(ctx, Path(repo.working_dir), include_gitea=False)
        except Exception:
            log.exception("%s %s: cannot inspect individual branch", team.repo, student.id)
            continue
        incomplete, untidy = _individual_details(ctx.hw.name, report)
        if incomplete:
            findings.append(Finding(student.id, "indvFailSubmit", details=tuple(incomplete)))
        if untidy:
            findings.append(Finding(student.id, "indvUntidy", details=tuple(untidy)))
        for detail in incomplete + untidy:
            log.warning("%s %s %s: %s", team.repo, student.id, student.display_name, detail)
    return findings


# ---------- group release tag ----------

def _quality_issues(ctx: RepoContext, hw_dir: Path) -> tuple[set[str], list[str]]:
    kinds: set[str] = set()
    details: list[str] = []
    for name in [*ctx.hw.mandatory, *ctx.hw.optional]:
        path = hw_dir / name
        if not path.is_file() or not code_quality.applies_to(name, ctx.hw.language):
            continue
        issues = code_quality.check_file(path, ctx.hw.language)
        kinds.update(issues)
        details += [f"group {name}: {issue}" for issue in issues]
    return kinds, details


def _team_findings(team: Team, key: str, details: list[str], count: int = 1) -> list[Finding]:
    return [Finding(s.id, key, count, tuple(details)) for s in team.members]


def check_group_tag(ctx: RepoContext, team: Team) -> list[Finding]:
    """The hN tag must contain a complete, tidy, clean hN/ directory."""
    hw = ctx.hw.name
    repo: Repo = ctx.repos.sync(team.repo, team.key)
    if hw not in tag_names(repo):
        log.warning("%s: tags/%s missing", team.repo, hw)
        return _team_findings(team, "groupFailSubmit", [f"tags/{hw} missing"])

    force_checkout(repo, f"tags/{hw}")
    workdir = Path(repo.working_dir)
    report = _layout_at(ctx, workdir, include_gitea=True)

    incomplete = []
    if report.hw_dir_missing:
        incomplete.append(f"tags/{hw} {hw} dir missing")
    incomplete += [f"tags/{hw} {hw}/{f} missing" for f in report.missing_files]
    if report.readme_missing:
        incomplete.append(f"tags/{hw} {hw}/README file missing")
    untidy = [
        f"tags/{hw} redundant files: {', '.join(extra)}"
        for extra in (report.extra_root, report.extra_hw)
        if extra
    ]

    findings = []
    if incomplete:
        findings += _team_findings(team, "groupFailSubmit", incomplete)
    if untidy:
        findings += _team_findings(team, "groupUntidy", untidy)
    if not report.hw_dir_missing:
        kinds, details = _quality_issues(ctx, workdir / hw)
        if kinds:
            findings += _team_findings(team, "groupLowCodeQuality", details, count=len(kinds))
    for f in findings[:: max(len(team.members), 1)]:
        log.warning("%s: %s %s", team.repo, f.key, "; ".join(f.details))
    return findings
