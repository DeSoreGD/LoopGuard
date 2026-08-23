from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from selfboss.config import (
    ENV_APP_HOME,
    ENV_APP_MODE,
    ENV_DB_PATH,
    ENV_LOOPGUARD_APP_HOME,
    ENV_LOOPGUARD_APP_MODE,
    ENV_LOOPGUARD_DB_PATH,
    ENV_LOOPGUARD_RECOVERY_MODE,
    ENV_LOOPGUARD_SAFE_MODE,
    ENV_LOOPGUARD_TEST_MODE,
    ENV_RECOVERY_MODE,
    ENV_SAFE_MODE,
    ENV_TEST_MODE,
    default_app_home,
    load_settings,
    resolve_app_mode,
)
from selfboss.app import acquire_single_instance_server, single_instance_server_name


def test_load_settings_uses_temp_env_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_home = tmp_path / "app-home"
    db_path = tmp_path / "custom" / "selfboss-test.db"

    monkeypatch.setenv(ENV_LOOPGUARD_APP_HOME, str(app_home))
    monkeypatch.setenv(ENV_LOOPGUARD_DB_PATH, str(db_path))
    monkeypatch.setenv(ENV_LOOPGUARD_TEST_MODE, "true")
    monkeypatch.setenv(ENV_LOOPGUARD_RECOVERY_MODE, "1")
    monkeypatch.setenv(ENV_LOOPGUARD_SAFE_MODE, "yes")

    settings = load_settings()

    assert settings.app_home == app_home
    assert settings.data_dir == app_home / "data"
    assert settings.log_dir == app_home / "logs"
    assert settings.db_path == db_path
    assert settings.test_mode is True
    assert settings.recovery_mode is True
    assert settings.safe_mode is True
    assert not app_home.exists()
    assert not db_path.parent.exists()


def test_app_mode_paths_overrides_and_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(ENV_APP_HOME, raising=False)
    monkeypatch.delenv(ENV_LOOPGUARD_APP_HOME, raising=False)
    monkeypatch.delenv(ENV_APP_MODE, raising=False)
    monkeypatch.delenv(ENV_LOOPGUARD_APP_MODE, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert resolve_app_mode() == "dev"
    assert default_app_home() == tmp_path / "LoopGuard-dev"

    monkeypatch.setenv(ENV_LOOPGUARD_APP_MODE, "production")
    assert resolve_app_mode() == "production"
    assert default_app_home() == tmp_path / "LoopGuard"

    app_home = tmp_path / "explicit"
    monkeypatch.setenv(ENV_LOOPGUARD_APP_HOME, str(app_home))

    assert load_settings().app_home == app_home
    monkeypatch.setenv(ENV_LOOPGUARD_APP_MODE, "installer")

    with pytest.raises(ValueError, match=ENV_LOOPGUARD_APP_MODE):
        load_settings()


def test_single_instance_server_name_is_profile_scoped(tmp_path: Path) -> None:
    dev_home = tmp_path / "LoopGuard-dev"
    prod_home = tmp_path / "LoopGuard"

    assert single_instance_server_name(dev_home) == single_instance_server_name(
        dev_home,
    )
    assert single_instance_server_name(dev_home) != single_instance_server_name(
        prod_home,
    )
    assert single_instance_server_name(dev_home).startswith("loopguard-")


def test_single_instance_profile_lock_blocks_second_instance(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    app_home = tmp_path / "LoopGuard"
    server = acquire_single_instance_server(app_home)

    assert app is not None
    assert server is not None
    try:
        assert acquire_single_instance_server(app_home) is None
    finally:
        server.close()
        server._loopguard_profile_lock.close()


def test_legacy_self_boss_env_overrides_remain_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_home = tmp_path / "legacy-home"
    db_path = tmp_path / "legacy-db" / "selfboss.db"

    monkeypatch.setenv(ENV_APP_HOME, str(app_home))
    monkeypatch.setenv(ENV_DB_PATH, str(db_path))
    monkeypatch.setenv(ENV_APP_MODE, "production")
    monkeypatch.setenv(ENV_RECOVERY_MODE, "true")
    monkeypatch.setenv(ENV_SAFE_MODE, "true")

    assert resolve_app_mode() == "production"
    settings = load_settings()

    assert settings.app_home == app_home
    assert settings.db_path == db_path
    assert settings.recovery_mode is True
    assert settings.safe_mode is True


def test_loopguard_env_aliases_take_precedence_over_legacy_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy_home = tmp_path / "legacy-home"
    loopguard_home = tmp_path / "loopguard-home"
    legacy_db = tmp_path / "legacy-db" / "selfboss.db"
    loopguard_db = tmp_path / "loopguard-db" / "selfboss.db"

    monkeypatch.setenv(ENV_APP_HOME, str(legacy_home))
    monkeypatch.setenv(ENV_LOOPGUARD_APP_HOME, str(loopguard_home))
    monkeypatch.setenv(ENV_DB_PATH, str(legacy_db))
    monkeypatch.setenv(ENV_LOOPGUARD_DB_PATH, str(loopguard_db))
    monkeypatch.setenv(ENV_APP_MODE, "production")
    monkeypatch.setenv(ENV_LOOPGUARD_APP_MODE, "dev")

    assert resolve_app_mode() == "dev"
    settings = load_settings()

    assert settings.app_home == loopguard_home
    assert settings.db_path == loopguard_db


def test_test_mode_is_locked_on_even_if_env_requests_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_APP_HOME, str(tmp_path / "selfboss"))
    monkeypatch.setenv(ENV_TEST_MODE, "false")

    settings = load_settings()

    assert settings.test_mode is True


def test_load_settings_can_create_only_configured_temp_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_home = tmp_path / "selfboss"
    db_path = tmp_path / "db" / "selfboss.db"

    monkeypatch.setenv(ENV_APP_HOME, str(app_home))
    monkeypatch.setenv(ENV_DB_PATH, str(db_path))

    settings = load_settings(create_dirs=True)

    assert settings.data_dir.is_dir()
    assert settings.log_dir.is_dir()
    assert settings.db_path.parent.is_dir()
    assert settings.app_home.is_relative_to(tmp_path)
    assert settings.db_path.is_relative_to(tmp_path)


def test_invalid_boolean_env_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_APP_HOME, str(tmp_path / "selfboss"))
    monkeypatch.setenv(ENV_TEST_MODE, "maybe")

    with pytest.raises(ValueError, match=ENV_TEST_MODE):
        load_settings()
