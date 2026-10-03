"""Issues for teams without an on-time release ("Late submission, no feedback provided.")."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .clients.gitea import GiteaClient
from .config import LateIssueSettings
from .models import Roster, StageResult, Team

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlannedIssue:
    team: Team
    title: str
    body: str
    exists: bool  # an issue with this title is already there


def late_teams(roster: Roster, result: StageResult) -> list[Team]:
    """Teams whose members were marked late (no release by the group deadline)."""
    return [t for t in roster.teams if any(result.meta.get(s.id) and result.meta[s.id].late for s in t.members)]


def plan_late_issues(gitea: GiteaClient, teams: list[Team], hw: str, settings: LateIssueSettings) -> list[PlannedIssue]:
    title = settings.title.format(hw=hw)
    return [PlannedIssue(t, title, settings.body, title in gitea.issue_titles(t.repo)) for t in teams]


def open_late_issues(gitea: GiteaClient, plan: list[PlannedIssue]) -> tuple[list[str], list[str]]:
    """Returns (opened, failures). Existing issues are skipped."""
    opened, failures = [], []
    for item in (p for p in plan if not p.exists):
        try:
            number = gitea.create_issue(item.team.repo, item.title, item.body, gitea.collaborators(item.team.repo))
            opened.append(f"{item.team.repo}#{number}")
            log.info("opened %s#%s: %s", item.team.repo, number, item.title)
        except Exception as e:
            log.error("cannot open issue in %s: %s", item.team.repo, e)
            failures.append(f"{item.team.repo}: {e}")
    return opened, failures
