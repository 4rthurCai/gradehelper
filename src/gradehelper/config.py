"""Configuration: secrets from .env, course and homework rules from config/*.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

STAGE_INDIVIDUAL = "individual"
STAGE_GROUP = "group"
STAGE_FINAL = "final"
STAGES = (STAGE_INDIVIDUAL, STAGE_GROUP)  # rubric stages
RUNS = (STAGE_INDIVIDUAL, STAGE_GROUP, STAGE_FINAL)  # the three runs per homework
LANGUAGES = ("matlab", "c", "cc")


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


class Secrets(BaseSettings):
    """Credentials and endpoints. Variable names match Joint-Teapot's .env."""

    canvas_domain_name: str = "oc.sjtu.edu.cn"
    canvas_suffix: str = "/"
    canvas_access_token: str = ""
    canvas_course_id: int = 0

    gitea_domain_name: str = "focs.gc.sjtu.edu.cn"
    gitea_suffix: str = "/git"
    gitea_access_token: str = ""
    gitea_org_name: str = ""

    git_host: str = "ssh://git@focs.gc.sjtu.edu.cn:2222"
    repos_dir: str = "./repos"

    mattermost_domain_name: str = "focs.gc.sjtu.edu.cn"
    mattermost_suffix: str = "/mm"
    mattermost_access_token: str = ""
    mattermost_team: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def canvas_url(self) -> str:
        suffix = self.canvas_suffix.rstrip("/")
        return f"https://{self.canvas_domain_name}{suffix}"

    @property
    def gitea_api_url(self) -> str:
        return f"https://{self.gitea_domain_name}{self.gitea_suffix}/api/v1"

    def missing(self, *fields: str) -> list[str]:
        return [f.upper() for f in fields if not getattr(self, f)]


class RubricItem(BaseModel):
    points: float
    description: str
    stage: str

    @field_validator("stage")
    @classmethod
    def _known_stage(cls, value: str) -> str:
        if value not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}, got {value!r}")
        return value


class JojThresholds(BaseModel):
    exercise_fail_percent: float  # an exercise below this fails
    homework_fail_percent: float  # the average over graded exercises below this fails
    max_exercise_failures: int = 2


class JojSettings(BaseModel):
    repo: str = "engr151-joj"
    branch: str = "grading"
    scoreboard_path: str = "homework/h{hw}.csv"
    # JOJ3 opens "JOJ3 Result for h1-release by @user - Score: 100 / 100" in the team repo.
    release_issue_title: str = r"JOJ3 Result for {hw}-release by .* - Score: (\d+) / (\d+)"
    # JOJ3 configs, for per-exercise maxima (see checks/joj_config.py).
    config_branch: str = "master"
    config_dir: str = "home/tt/.config/joj/homework"
    # Commit status of the release workflow; failure/error (red cross) = jojFailCompile.
    release_status_context: str = "Run JOJ3 on Release"
    individual: JojThresholds = JojThresholds(exercise_fail_percent=10, homework_fail_percent=25)
    group: JojThresholds = JojThresholds(exercise_fail_percent=25, homework_fail_percent=50)


class LayoutSettings(BaseModel):
    root_allowlist: list[str] = Field(default_factory=list)
    gitea_dir_allowlist: list[str] = Field(default_factory=list)
    max_homework_dirs: int = 20


class ReviewSettings(BaseModel):
    min_length: int = 15
    min_substantive_ratio: float = 0.5
    perfunctory_phrases: list[str] = Field(default_factory=list)


class PrDescriptionSettings(BaseModel):
    templates: list[str] = Field(default_factory=list)
    unfilled_threshold: float = 0.6


class LateIssueSettings(BaseModel):
    title: str = "{hw} feedback"
    body: str = "Late submission, no feedback provided."


class CourseConfig(BaseModel):
    score_floor: float = -2.5
    final_grace_hours: float = 24
    default_language: str = "matlab"
    default_pass_threshold: float = 50
    workers: int = 8
    roster_file: str = "hteams.csv"
    output_dir: str = "hws"
    rubric: dict[str, RubricItem]
    joj: JojSettings = JojSettings()
    layout: LayoutSettings = LayoutSettings()
    review: ReviewSettings = ReviewSettings()
    pr_description: PrDescriptionSettings = PrDescriptionSettings()
    late_issue: LateIssueSettings = LateIssueSettings()

    def rubric_keys(self, stage: str) -> list[str]:
        return [key for key, item in self.rubric.items() if item.stage == stage]


class Deadlines(BaseModel):
    individual: datetime | None = None
    group: datetime | None = None
    final: datetime | None = None  # defaults to group + course final_grace_hours

    model_config = ConfigDict(extra="forbid")  # catch typos and removed keys

    @field_validator("individual", "group", "final")
    @classmethod
    def _timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("deadlines need a UTC offset, e.g. 2026-10-02T23:59:59+08:00")
        return value

    def final_cutoff(self, grace_hours: float) -> datetime | None:
        if self.final is not None:
            return self.final
        return self.group + timedelta(hours=grace_hours) if self.group else None


class HomeworkConfig(BaseModel):
    number: int
    language: str
    pass_threshold: float
    mandatory: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    # Optional overrides; by default both come from the JOJ3 configs in the joj repo.
    joj_exercises: dict[int, float] = Field(default_factory=dict)  # individual max; 0 = not graded
    joj_release_exercises: dict[str, float] = Field(default_factory=dict)  # release max, {"ex2": 45}
    deadlines: Deadlines = Deadlines()
    canvas_assignment_id: int | None = None

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"language must be one of {LANGUAGES}, got {value!r}")
        return value

    @property
    def name(self) -> str:
        return f"h{self.number}"



def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e


@dataclass(frozen=True)
class Workspace:
    """Everything resolved relative to one working directory."""

    root: Path

    @cached_property
    def secrets(self) -> Secrets:
        return Secrets(_env_file=self.root / ".env")

    @cached_property
    def course(self) -> CourseConfig:
        return CourseConfig.model_validate(_read_toml(self.root / "config" / "course.toml"))

    def homework(self, number: int) -> HomeworkConfig:
        path = self.root / "config" / "homeworks" / f"h{number}.toml"
        raw = _read_toml(path)
        joj = raw.pop("joj", {})
        data = {
            "number": number,
            "language": raw.pop("language", self.course.default_language),
            "pass_threshold": raw.pop("pass_threshold", self.course.default_pass_threshold),
            "joj_exercises": {int(k): v for k, v in joj.get("exercises", {}).items()},
            "joj_release_exercises": dict(joj.get("release", {})),
            **raw,
        }
        try:
            return HomeworkConfig.model_validate(data)
        except ValueError as e:
            raise ConfigError(f"invalid homework config {path}: {e}") from e

    def homework_numbers(self) -> list[int]:
        files = (self.root / "config" / "homeworks").glob("h*.toml")
        return sorted(int(p.stem[1:]) for p in files if p.stem[1:].isdigit())

    @property
    def roster_path(self) -> Path:
        return self.root / self.course.roster_file

    @property
    def output_dir(self) -> Path:
        return self.root / self.course.output_dir

    @property
    def repos_dir(self) -> Path:
        return (self.root / self.secrets.repos_dir).resolve()

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def log_path(self) -> Path:
        return self.root / "gradehelper.log"
