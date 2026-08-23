from __future__ import annotations

from pathlib import Path

from selfboss.config import (
    ENV_APP_HOME,
    ENV_SAFE_MODE,
    ENV_TEST_MODE,
    load_settings,
)
from selfboss.platform.hosts_blocker import BEGIN_MARKER, END_MARKER
from selfboss.platform.recovery import RecoveryManager, main


def make_manager(tmp_path: Path) -> RecoveryManager:
    return RecoveryManager(
        app_home=tmp_path / "app-home",
        hosts_path=tmp_path / "hosts",
        backup_path=tmp_path / "hosts.selfboss.bak",
    )


def test_status_reports_markers_backup_and_flags(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.hosts_blocker.hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            f"{BEGIN_MARKER}\n"
            "127.0.0.1 youtube.com\n"
            f"{END_MARKER}\n"
        ),
        encoding="utf-8",
    )
    manager.hosts_blocker.backup_path.write_text("backup\n", encoding="utf-8")
    manager.force_safe_mode()
    manager.reset_test_mode()

    status = manager.status()

    assert status.hosts_markers_present is True
    assert status.backup_present is True
    assert status.safe_mode_forced is True
    assert status.test_mode_forced is True


def test_unlock_restores_backup_when_present(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.hosts_blocker.hosts_path.write_text(
        f"original\n{BEGIN_MARKER}\n127.0.0.1 youtube.com\n{END_MARKER}\n",
        encoding="utf-8",
    )
    manager.hosts_blocker.backup_path.write_text("restored\n", encoding="utf-8")

    result = manager.unlock(force_safe_mode=True)

    assert result.success is True
    assert "restored" in result.message
    assert manager.hosts_blocker.hosts_path.read_text(encoding="utf-8") == "restored\n"
    assert manager.safe_mode_flag.exists()


def test_unlock_without_backup_removes_only_managed_hosts_block(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.hosts_blocker.hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            f"{BEGIN_MARKER}\n"
            "127.0.0.1 youtube.com\n"
            f"{END_MARKER}\n"
            "10.0.0.5 intranet.local\n"
        ),
        encoding="utf-8",
    )

    result = manager.unlock()
    content = manager.hosts_blocker.hosts_path.read_text(encoding="utf-8")

    assert result.success is True
    assert "127.0.0.1 localhost" in content
    assert "10.0.0.5 intranet.local" in content
    assert "youtube.com" not in content
    assert BEGIN_MARKER not in content
    assert END_MARKER not in content


def test_reset_test_mode_writes_safe_and_test_flags(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    result = manager.reset_test_mode()

    assert result.success is True
    assert manager.safe_mode_flag.exists()
    assert manager.test_mode_flag.exists()


def test_config_reads_safe_mode_flag_even_when_env_false(
    monkeypatch, tmp_path: Path
) -> None:
    app_home = tmp_path / "app-home"
    manager = RecoveryManager(app_home=app_home, hosts_path=tmp_path / "hosts")
    manager.force_safe_mode()

    monkeypatch.setenv(ENV_APP_HOME, str(app_home))
    monkeypatch.setenv(ENV_SAFE_MODE, "false")

    settings = load_settings()

    assert settings.safe_mode is True


def test_config_reads_test_mode_flag_when_env_false(monkeypatch, tmp_path: Path) -> None:
    app_home = tmp_path / "app-home"
    manager = RecoveryManager(app_home=app_home, hosts_path=tmp_path / "hosts")
    manager.reset_test_mode()

    monkeypatch.setenv(ENV_APP_HOME, str(app_home))
    monkeypatch.setenv(ENV_TEST_MODE, "false")

    settings = load_settings()

    assert settings.test_mode is True
    assert settings.safe_mode is True


def test_cli_status_uses_injected_temp_paths(tmp_path: Path, capsys) -> None:
    app_home = tmp_path / "app-home"
    hosts_path = tmp_path / "hosts"
    backup_path = tmp_path / "hosts.selfboss.bak"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    exit_code = main(
        [
            "--app-home",
            str(app_home),
            "--hosts-path",
            str(hosts_path),
            "--backup-path",
            str(backup_path),
            "status",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "LoopGuard recovery status" in captured.out
