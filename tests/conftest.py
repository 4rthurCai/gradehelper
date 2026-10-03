"""Shared fixtures. All people in tests are fictional."""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from gradehelper.config import Workspace
from gradehelper.models import Roster, Student

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ALICE = Student("520000000001", "Zhang San 张三", "zhangsan", "hteam-01")
BOB = Student("520000000002", "JOHN DOE", "john.doe", "hteam-01")
CAROL = Student("520000000003", "Li Si 李四", "lisi", "hteam-02")
DAVE = Student("520000000004", "Wang Wu", "wangwu", "hteam-02")
LONER = Student("520000000005", "Zhao Liu", "zhaoliu", "")


@pytest.fixture
def roster() -> Roster:
    return Roster((ALICE, BOB, CAROL, DAVE, LONER))


@pytest.fixture
def workspace(tmp_path: Path, roster: Roster) -> Workspace:
    """A workspace with the real course config and a fictional roster."""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    shutil.copytree(PROJECT_ROOT / "templates", tmp_path / "templates")
    lines = ["Name,ID,Email,Team"] + [
        f"{s.name},{s.id},{s.jaccount}@sjtu.edu.cn,{s.team}" for s in roster.students
    ]
    (tmp_path / "hteams.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GITEA_ORG_NAME=org\nGITEA_ACCESS_TOKEN=x\n", encoding="utf-8")
    return Workspace(tmp_path)


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def user(student_id: str, login: str = "") -> SimpleNamespace:
    return ns(full_name=f"{login or 'someone'} {student_id}", login=login or student_id)
