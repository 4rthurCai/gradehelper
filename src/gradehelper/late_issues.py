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
    assignees: tuple[str, ...] = ()  # the repo's collaborators; empty when the issue exists


def late_teams(roster: Roster, result: StageResult) -> list[Team]:
    """Teams whose members were marked late (no release by the group deadline)."""
    return [t for t in roster.teams if any(result.meta.get(s.id) and result.meta[s.id].late for s in t.members)]


def plan_late_issues(
    reader: GiteaClient, teams: list[Team], hw: str, settings: LateIssueSettings
) -> list[PlannedIssue]:
    """All reads happen here (existing issues, collaborators), so the client that opens
    the issues only needs the write:issue scope."""
    title = settings.title.format(hw=hw)
    plan = []
    for t in teams:
        exists = title in reader.issue_titles(t.repo)
        assignees = () if exists else tuple(reader.collaborators(t.repo))
        plan.append(PlannedIssue(t, title, settings.body, exists, assignees))
    return plan


def open_late_issues(writer: GiteaClient, plan: list[PlannedIssue]) -> tuple[list[str], list[str]]:
    """Returns (opened, failures). Existing issues are skipped."""
    opened, failures = [], []
    for item in (p for p in plan if not p.exists):
        try:
            number = writer.create_issue(item.team.repo, item.title, item.body, list(item.assignees))
            opened.append(f"{item.team.repo}#{number}")
            log.info("opened %s#%s: %s", item.team.repo, number, item.title)
        except Exception as e:
            log.error("cannot open issue in %s: %s", item.team.repo, e)
            failures.append(f"{item.team.repo}: {e}")
    return opened, failures
