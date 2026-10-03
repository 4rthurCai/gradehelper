from __future__ import annotations

import re
from functools import cached_property

from canvasapi import Canvas

from ..config import ConfigError, Secrets


class CanvasClient:
    def __init__(self, secrets: Secrets):
        missing = secrets.missing("canvas_access_token", "canvas_course_id")
        if missing:
            raise ConfigError(f"missing in .env: {', '.join(missing)}")
        self._secrets = secrets

    @cached_property
    def course(self):
        canvas = Canvas(self._secrets.canvas_url, self._secrets.canvas_access_token)
        return canvas.get_course(self._secrets.canvas_course_id)

    def find_assignment(self, hw: int, assignment_id: int | None = None):
        if assignment_id:
            return self.course.get_assignment(assignment_id)
        pattern = re.compile(rf"^h{hw}(?!\d)", re.IGNORECASE)
        matches = [a for a in self.course.get_assignments() if pattern.match(a.name.strip())]
        if not matches:
            raise LookupError(f"no Canvas assignment named h{hw}*")
        if len(matches) > 1:
            names = ", ".join(f"{a.name} (id {a.id})" for a in matches)
            raise LookupError(
                f"several assignments match h{hw}: {names}; set canvas_assignment_id in h{hw}.toml"
            )
        return matches[0]

    def students(self) -> list:
        return list(self.course.get_users(enrollment_type=["student"]))
