"""Application configuration and local path resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from selfboss.core.models import AppSettings


APP_MODE_DEV = "dev"
APP_MODE_PRODUCTION = "production"
ENV_APP_HOME = "SELF_BOSS_HOME"
ENV_APP_MODE = "SELF_BOSS_APP_MODE"
ENV_DB_PATH = "SELF_BOSS_DB_PATH"
ENV_TEST_MODE = "SELF_BOSS_TEST_MODE"
ENV_RECOVERY_MODE = "SELF_BOSS_RECOVERY_MODE"
ENV_SAFE_MODE = "SELF_BOSS_SAFE_MODE"
ENV_LOOPGUARD_APP_HOME = "LOOPGUARD_HOME"
ENV_LOOPGUARD_APP_MODE = "LOOPGUARD_APP_MODE"
ENV_LOOPGUARD_DB_PATH = "LOOPGUARD_DB_PATH"
ENV_LOOPGUARD_TEST_MODE = "LOOPGUARD_TEST_MODE"
ENV_LOOPGUARD_RECOVERY_MODE = "LOOPGUARD_RECOVERY_MODE"
ENV_LOOPGUARD_SAFE_MODE = "LOOPGUARD_SAFE_MODE"
SAFE_MODE_FLAG_NAME = "safe_mode.flag"
TEST_MODE_FLAG_NAME = "test_mode.flag"
DEV_APP_HOME_FOLDER = "LoopGuard-dev"
PRODUCTION_APP_HOME_FOLDER = "LoopGuard"


def _env_override(
    primary_name: str,
    fallback_name: str | None = None,
) -> tuple[str, str] | None:
    """Return the first configured env override, preferring LoopGuard aliases."""
    value = os.environ.get(primary_name)
    if value is not None:
        return primary_name, value
    if fallback_name is not None:
        value = os.environ.get(fallback_name)
        if value is not None:
            return fallback_name, value
    return None


def _read_bool_env(
    primary_name: str,
    fallback_name: str | None = None,
    *,
    default: bool,
) -> bool:
    """Read a boolean environment variable using strict, readable values."""
    override = _env_override(primary_name, fallback_name)
    if override is None:
        return default
    name, value = override

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{name} must be one of: 1, 0, true, false, yes, no, on, off"
    )


def resolve_app_mode() -> str:
    """Return the runtime app mode: source/dev or installed/production."""
    override = _env_override(ENV_LOOPGUARD_APP_MODE, ENV_APP_MODE)
    if override is not None:
        name, value = override
        normalized = value.strip().lower()
        if normalized in {APP_MODE_DEV, APP_MODE_PRODUCTION}:
            return normalized
        raise ValueError(
            f"{name} must be one of: {APP_MODE_DEV}, {APP_MODE_PRODUCTION}"
        )

    return APP_MODE_PRODUCTION if getattr(sys, "frozen", False) else APP_MODE_DEV


def is_production_app_mode() -> bool:
    """Return whether the app is running as the installed/user build."""
    return resolve_app_mode() == APP_MODE_PRODUCTION


def default_app_home() -> Path:
    """Return the default local-only application home for Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    folder_name = (
        PRODUCTION_APP_HOME_FOLDER
        if is_production_app_mode()
        else DEV_APP_HOME_FOLDER
    )
    if local_app_data:
        return Path(local_app_data) / folder_name

    return Path.home() / "AppData" / "Local" / folder_name


def safe_mode_flag_path(app_home: Path) -> Path:
    """Return the local flag path that forces safe mode on next launch."""
    return app_home / SAFE_MODE_FLAG_NAME


def test_mode_flag_path(app_home: Path) -> Path:
    """Return the local flag path that forces test mode on next launch."""
    return app_home / TEST_MODE_FLAG_NAME


def load_settings(*, create_dirs: bool = False) -> AppSettings:
    """Load application settings from environment overrides and defaults.

    Directory creation is opt-in so tests and dry configuration reads never
    touch real user state by accident.
    """
    # Always validate app mode, even when an explicit home override is present.
    resolve_app_mode()

    app_home_override = _env_override(ENV_LOOPGUARD_APP_HOME, ENV_APP_HOME)
    app_home = Path(
        app_home_override[1] if app_home_override else default_app_home()
    ).expanduser()
    data_dir = app_home / "data"
    log_dir = app_home / "logs"

    db_override = _env_override(ENV_LOOPGUARD_DB_PATH, ENV_DB_PATH)
    db_path = Path(db_override[1]).expanduser() if db_override else data_dir / "selfboss.db"
    safe_mode_flag = safe_mode_flag_path(app_home)

    # Validate the env value, but keep Test Mode locked on for this MVP.
    _read_bool_env(ENV_LOOPGUARD_TEST_MODE, ENV_TEST_MODE, default=True)

    settings = AppSettings(
        app_home=app_home,
        data_dir=data_dir,
        db_path=db_path,
        log_dir=log_dir,
        test_mode=True,
        recovery_mode=_read_bool_env(
            ENV_LOOPGUARD_RECOVERY_MODE,
            ENV_RECOVERY_MODE,
            default=False,
        ),
        safe_mode=_read_bool_env(
            ENV_LOOPGUARD_SAFE_MODE,
            ENV_SAFE_MODE,
            default=False,
        )
        or safe_mode_flag.exists(),
    )

    if create_dirs:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    return settings
