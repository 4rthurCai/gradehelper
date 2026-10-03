from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from git import Repo
from git.exc import GitCommandError

log = logging.getLogger(__name__)
_TRANSIENT = ("Connection refused", "Connection reset", "Connection closed")
_MAX_BACKOFF = 64


def _with_retry(action, what: str):
    """Retry on connection drops (the JI firewall resets SSH occasionally)."""
    delay = 2
    while True:
        try:
            return action()
        except GitCommandError as e:
            if not any(t in (e.stderr or "") for t in _TRANSIENT) or delay > _MAX_BACKOFF:
                raise
            log.warning("%s: connection dropped, retrying in %ss", what, delay)
            time.sleep(delay)
            delay *= 2


class GitRepos:
    def __init__(self, git_host: str, org: str, repos_dir: Path):
        self.git_host = git_host.rstrip("/")
        self.org = org
        self.repos_dir = repos_dir

    def sync(self, remote_name: str, local_name: str | None = None) -> Repo:
        """Clone if needed, then fetch all branches and tags."""
        path = self.repos_dir / (local_name or remote_name)
        url = f"{self.git_host}/{self.org}/{remote_name}.git"
        if not path.exists():
            self.repos_dir.mkdir(parents=True, exist_ok=True)
            log.info("cloning %s", url)
            return _with_retry(lambda: Repo.clone_from(url, path), remote_name)
        repo = Repo(path)
        if repo.remote().url != url:
            # Cached clones may point at an old host; follow the configured one.
            log.info("%s: origin %s -> %s", remote_name, repo.remote().url, url)
            repo.remote().set_url(url)
        _with_retry(lambda: repo.git.fetch("--all", "--tags", "--force", "--prune"), remote_name)
        return repo


def force_checkout(repo: Repo, ref: str) -> None:
    repo.git.reset("--hard")
    repo.git.clean("-d", "-f", "-x")
    repo.git.checkout(ref, "-f")


def remote_branches(repo: Repo) -> set[str]:
    return {ref.remote_head for ref in repo.remote().refs}


def tag_names(repo: Repo) -> set[str]:
    return {t.name for t in repo.tags}


def file_at(repo: Repo, ref: str, path: str, before: datetime | None = None) -> tuple[str, str]:
    """Content of path at ref, or at the newest commit touching it before a deadline.

    Returns (content, commit sha). Reads straight from git; the work tree is untouched.
    """
    sha = repo.commit(ref).hexsha
    if before is not None:
        for commit in repo.iter_commits(ref, paths=path):
            if commit.committed_datetime <= before:
                sha = commit.hexsha
                break
        else:
            log.warning("no commit of %s before %s; using %s", path, before, ref)
    return repo.git.show(f"{sha}:{path}"), sha
