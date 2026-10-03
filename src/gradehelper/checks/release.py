"""hN release: was it submitted in time for this run, did its JOJ workflow fail, was it late.

A submission is a published (non-draft) Gitea release whose tag is exactly hN, created no
later than the run's cutoff (group deadline for the group run, +24h for the final run).
Anything else counts as not submitted: no tag, tag without release, draft only, or a
release created after the cutoff (more than 24h late in the final run = rejected).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..clients.gitea import GiteaClient
from ..models import Finding, StudentMeta, Team, as_datetime

log = logging.getLogger(__name__)
FAILED_STATES = ("failure", "error")


@dataclass(frozen=True)
class ReleaseStatus:
    tag_exists: bool
    exists: bool  # a published (non-draft) Gitea release with tag hN
    draft_only: bool = False
    status: str | None = None  # newest status of the release workflow on the tagged commit
    created: datetime | None = None
    sha: str = ""  # commit the hN tag points to

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATES

    def after(self, cutoff: datetime | None) -> bool:
        return cutoff is not None and self.created is not None and self.created > cutoff


def find_release(releases: list[Any], hw: str) -> Any | None:
    """Course rule: the release counts only if its tag is exactly hN. Drafts do not count."""
    release = next((r for r in releases if r.tag_name == hw and not getattr(r, "draft", False)), None)
    if release is not None and release.name != hw:
        log.info("release for tag %s is named %r", hw, release.name)
    return release


def release_status(gitea: GiteaClient, team: Team, hw: str, status_context: str) -> ReleaseStatus:
    """API errors propagate: a flaky request must not turn into a deduction."""
    releases = gitea.releases(team.repo)
    release = find_release(releases, hw)
    if release is None:
        draft_only = any(r.tag_name == hw for r in releases)
        return ReleaseStatus(tag_exists=gitea.has_tag(team.repo, hw), exists=False, draft_only=draft_only)
    # target_commitish is a branch name, which the status API does not resolve,
    # so look up the commit the release tag points to.
    sha = gitea.tag_commit(team.repo, release.tag_name)
    return ReleaseStatus(
        tag_exists=True,
        exists=True,
        status=gitea.latest_status(team.repo, sha, status_context),
        created=as_datetime(release.created_at),
        sha=sha,
    )


def submission_findings(
    team: Team, hw: str, status: ReleaseStatus, cutoff: datetime | None
) -> tuple[list[Finding], bool]:
    """(findings, accepted). When not accepted, the tag contents, compile status and group
    JOJ are not graded: the team gets one groupFailSubmit with the reason."""
    if not status.tag_exists:
        reason = f"tags/{hw} missing"
    elif status.draft_only:
        reason = f"only a draft {hw} release, never published"
    elif not status.exists:
        reason = f"tags/{hw} exists but no {hw} release was created"
    elif status.after(cutoff):
        reason = f"{hw} release created {status.created:%Y-%m-%d %H:%M}, after the cutoff {cutoff:%Y-%m-%d %H:%M}"
    else:
        return compile_findings(team, hw, status), True
    log.warning("%s: %s", team.repo, reason)
    return [Finding(s.id, "groupFailSubmit", details=(reason,)) for s in team.members], False


def compile_findings(team: Team, hw: str, status: ReleaseStatus) -> list[Finding]:
    """Red cross on the release workflow -> jojFailCompile; anything but success is logged."""
    if status.failed:
        detail = f"{hw} release JOJ workflow {status.status}"
        log.warning("%s: %s", team.repo, detail)
        return [Finding(s.id, "jojFailCompile", details=(detail,)) for s in team.members]
    if status.status != "success":
        log.warning(
            "%s: %s release workflow status is %s; not graded as a compile failure",
            team.repo, hw, status.status or "missing",
        )
    return []


def lateness(
    team: Team, status: ReleaseStatus, deadline: datetime | None, notes: tuple[str, ...] = ()
) -> dict[str, StudentMeta]:
    """Late = no published release by the group deadline. A flag, not a deduction."""
    shown = status.created.isoformat() if status.exists and status.created else ""
    if deadline is None:
        late = False
    else:
        late = not status.exists or status.created is None or status.created > deadline
    meta = StudentMeta(late=late, release_time=shown, notes=notes)
    return {s.id: meta for s in team.members}
