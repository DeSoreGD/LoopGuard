from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from selfboss.core.models import AppSettings, Task, TaskKind, TaskStatus  # noqa: E402


@pytest.fixture
def fixed_now() -> datetime:
    """Return a deterministic timestamp for domain tests."""
    return datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def temp_app_root(tmp_path: Path) -> Path:
    """Return a temp app root that cannot touch real user state."""
    return tmp_path / "selfboss-app"


@pytest.fixture
def temp_db_path(temp_app_root: Path) -> Path:
    """Return a temp SQLite database path."""
    return temp_app_root / "data" / "selfboss.db"


@pytest.fixture
def fake_hosts_path(tmp_path: Path) -> Path:
    """Return a temp hosts file path."""
    return tmp_path / "hosts"


@pytest.fixture
def fake_hosts_backup_path(tmp_path: Path) -> Path:
    """Return a temp hosts backup path."""
    return tmp_path / "hosts.selfboss.bak"


@pytest.fixture
def test_settings(temp_app_root: Path, temp_db_path: Path) -> AppSettings:
    """Return settings rooted under temp paths with test mode enabled."""
    return AppSettings(
        app_home=temp_app_root,
        data_dir=temp_app_root / "data",
        db_path=temp_db_path,
        log_dir=temp_app_root / "logs",
        test_mode=True,
        recovery_mode=False,
        safe_mode=False,
    )


@pytest.fixture
def make_task(fixed_now: datetime) -> Callable[..., Task]:
    """Return a small task factory for pure domain tests."""

    def factory(
        *,
        task_id: int = 1,
        kind: TaskKind = TaskKind.NORMAL,
        reward_minutes: int = 0,
    ) -> Task:
        return Task(
            id=task_id,
            title="Task",
            description="",
            status=TaskStatus.PENDING,
            reward_minutes=reward_minutes,
            allowed_url=None,
            created_at=fixed_now.isoformat(),
            updated_at=fixed_now.isoformat(),
            completed_at=None,
            kind=kind,
        )

    return factory
