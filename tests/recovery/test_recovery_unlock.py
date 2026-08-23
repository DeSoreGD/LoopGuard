from __future__ import annotations

from pathlib import Path

from selfboss.platform.hosts_blocker import BEGIN_MARKER, END_MARKER
from selfboss.platform.recovery import RecoveryManager, main


def make_manager(
    temp_app_root: Path,
    fake_hosts_path: Path,
    fake_hosts_backup_path: Path,
) -> RecoveryManager:
    return RecoveryManager(
        app_home=temp_app_root,
        hosts_path=fake_hosts_path,
        backup_path=fake_hosts_backup_path,
    )


def test_unlock_restores_backup_when_present(
    temp_app_root: Path,
    fake_hosts_path: Path,
    fake_hosts_backup_path: Path,
) -> None:
    manager = make_manager(temp_app_root, fake_hosts_path, fake_hosts_backup_path)
    fake_hosts_path.write_text(
        f"current\n{BEGIN_MARKER}\n127.0.0.1 youtube.com\n{END_MARKER}\n",
        encoding="utf-8",
    )
    fake_hosts_backup_path.write_text("backup\n", encoding="utf-8")

    result = manager.unlock(force_safe_mode=True)

    assert result.success is True
    assert fake_hosts_path.read_text(encoding="utf-8") == "backup\n"
    assert manager.safe_mode_flag.exists()


def test_unlock_without_backup_removes_only_selfboss_markers(
    temp_app_root: Path,
    fake_hosts_path: Path,
    fake_hosts_backup_path: Path,
) -> None:
    manager = make_manager(temp_app_root, fake_hosts_path, fake_hosts_backup_path)
    fake_hosts_path.write_text(
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
    content = fake_hosts_path.read_text(encoding="utf-8")

    assert result.success is True
    assert "127.0.0.1 localhost" in content
    assert "10.0.0.5 intranet.local" in content
    assert "youtube.com" not in content
    assert BEGIN_MARKER not in content
    assert END_MARKER not in content


def test_reset_test_mode_writes_flags_under_temp_app_root(
    temp_app_root: Path,
    fake_hosts_path: Path,
    fake_hosts_backup_path: Path,
) -> None:
    manager = make_manager(temp_app_root, fake_hosts_path, fake_hosts_backup_path)

    result = manager.reset_test_mode()

    assert result.success is True
    assert manager.safe_mode_flag.is_relative_to(temp_app_root)
    assert manager.test_mode_flag.is_relative_to(temp_app_root)
    assert manager.safe_mode_flag.exists()
    assert manager.test_mode_flag.exists()


def test_recovery_cli_runs_against_injected_temp_paths(
    temp_app_root: Path,
    fake_hosts_path: Path,
    fake_hosts_backup_path: Path,
    capsys,
) -> None:
    fake_hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    exit_code = main(
        [
            "--app-home",
            str(temp_app_root),
            "--hosts-path",
            str(fake_hosts_path),
            "--backup-path",
            str(fake_hosts_backup_path),
            "status",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "LoopGuard recovery status" in captured.out
