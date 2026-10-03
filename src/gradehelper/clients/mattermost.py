from __future__ import annotations

from functools import cached_property

from mattermostdriver import Driver

from ..config import ConfigError, Secrets


class MattermostClient:
    def __init__(self, secrets: Secrets):
        missing = secrets.missing("mattermost_access_token", "mattermost_team")
        if missing:
            raise ConfigError(f"missing in .env: {', '.join(missing)}")
        self._secrets = secrets

    @cached_property
    def driver(self) -> Driver:
        driver = Driver(
            {
                "url": self._secrets.mattermost_domain_name,
                "port": 443,
                "basepath": self._secrets.mattermost_suffix + "/api/v4",
                "token": self._secrets.mattermost_access_token,
            }
        )
        driver.login()
        return driver

    def post_to_student_channel(self, student_id: str, message: str) -> None:
        """Each student has a private channel named by their student ID."""
        channel = self.driver.channels.get_channel_by_name_and_team_name(
            self._secrets.mattermost_team, student_id
        )
        self.driver.posts.create_post({"channel_id": channel["id"], "message": message})
