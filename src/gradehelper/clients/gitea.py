from __future__ import annotations

import logging
from typing import Any, Callable

import focs_gitea
from focs_gitea.rest import ApiException

from ..config import ConfigError, Secrets

log = logging.getLogger(__name__)


def list_all(method: Callable[..., list], *args: Any, **kwargs: Any) -> list:
    """Follow Gitea pagination until an empty page."""
    items, page = [], 1
    while True:
        batch = method(*args, **kwargs, page=page)
        if not batch:
            return items
        items.extend(batch)
        page += 1


class GiteaClient:
    def __init__(self, secrets: Secrets):
        missing = secrets.missing("gitea_access_token", "gitea_org_name")
        if missing:
            raise ConfigError(f"missing in .env: {', '.join(missing)}")
        configuration = focs_gitea.Configuration()
        configuration.api_key["access_token"] = secrets.gitea_access_token
        configuration.host = secrets.gitea_api_url
        for logger in configuration.logger.values():
            logger.handlers = []
        api_client = focs_gitea.ApiClient(configuration)
        self.org = secrets.gitea_org_name
        self.repos = focs_gitea.RepositoryApi(api_client)
        self.issues = focs_gitea.IssueApi(api_client)

    def pulls(self, repo: str) -> list:
        return list_all(self.repos.repo_list_pull_requests, self.org, repo, state="all")

    def pull_comments(self, repo: str, number: int) -> list:
        return self.issues.issue_get_comments(self.org, repo, number)

    def pull_reviews(self, repo: str, number: int) -> list:
        return list_all(self.repos.repo_list_pull_reviews, self.org, repo, number)

    def review_comments(self, repo: str, number: int, review_id: int) -> list:
        return self.repos.repo_get_pull_review_comments(self.org, repo, number, review_id)

    def releases(self, repo: str) -> list:
        try:
            return list_all(self.repos.repo_list_releases, self.org, repo)
        except ApiException as e:
            if e.status == 404:
                return []
            raise

    def tag_commit(self, repo: str, tag: str) -> str:
        return self.repos.repo_get_tag(self.org, repo, tag).commit.sha

    def has_tag(self, repo: str, tag: str) -> bool:
        try:
            self.repos.repo_get_tag(self.org, repo, tag)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def latest_status(self, repo: str, sha: str, context_prefix: str) -> str | None:
        """Newest commit status whose context starts with context_prefix (e.g. the release workflow)."""
        statuses = [
            s for s in list_all(self.repos.repo_list_statuses, self.org, repo, sha)
            if (s.context or "").startswith(context_prefix)
        ]
        newest = max(statuses, key=lambda s: s.created_at, default=None)
        return newest.status if newest else None

    def repo_issues(self, repo: str) -> list:
        return list_all(self.issues.issue_list_issues, self.org, repo, state="all", type="issues")

    def issue_titles(self, repo: str) -> set[str]:
        return {i.title for i in self.repo_issues(repo)}

    def collaborators(self, repo: str) -> list[str]:
        return [u.login for u in list_all(self.repos.repo_list_collaborators, self.org, repo)]

    def create_issue(self, repo: str, title: str, body: str, assignees: list[str]) -> int:
        option = focs_gitea.CreateIssueOption(title=title, body=body, assignees=assignees)
        return self.issues.issue_create_issue(self.org, repo, body=option).number
