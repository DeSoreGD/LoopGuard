from __future__ import annotations

from pathlib import Path

from selfboss.platform.hosts_blocker import (
    BEGIN_MARKER,
    END_MARKER,
    HostsBlocker,
    add_or_replace_managed_block,
    generate_hosts_entries,
    remove_managed_block,
)
from selfboss.platform.test_mode import BlockerPlan


class PermissionDeniedHostsBlocker(HostsBlocker):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.write_attempts = 0

    def _write_hosts(self, content: str) -> None:
        self.write_attempts += 1
        raise PermissionError("denied")


class FailingWriteHostsBlocker(HostsBlocker):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.write_attempts = 0

    def _write_hosts(self, content: str) -> None:
        self.write_attempts += 1
        if self.write_attempts == 1:
            self.hosts_path.write_text("partial write\n", encoding="utf-8")
            raise OSError("write failed")
        super()._write_hosts(content)


def make_blocker(fake_hosts_path: Path, fake_hosts_backup_path: Path) -> HostsBlocker:
    return HostsBlocker(
        hosts_path=fake_hosts_path,
        backup_path=fake_hosts_backup_path,
    )


def test_test_mode_does_not_write_hosts_file(
    fake_hosts_path: Path, fake_hosts_backup_path: Path
) -> None:
    blocker = make_blocker(fake_hosts_path, fake_hosts_backup_path)
    blocker.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")

    results = blocker.apply(["youtube.com"], BlockerPlan(test_mode=True, dry_run=False))

    assert results[0].dry_run is True
    assert "would write managed hosts block" in results[0].message
    assert blocker.hosts_path.read_text(encoding="utf-8") == "127.0.0.1 localhost\n"
    assert not blocker.backup_path.exists()


def test_apply_adds_marker_block_and_backup_to_temp_hosts(
    fake_hosts_path: Path, fake_hosts_backup_path: Path
) -> None:
    blocker = make_blocker(fake_hosts_path, fake_hosts_backup_path)
    blocker.hosts_path.write_text(
        "127.0.0.1 localhost\n10.0.0.5 intranet.local\n",
        encoding="utf-8",
    )

    results = blocker.apply(
        ["YouTube.com", "instagram.com", "youtube.com"],
        BlockerPlan(test_mode=False, dry_run=False),
    )
    content = blocker.hosts_path.read_text(encoding="utf-8")

    assert results[0].dry_run is False
    assert blocker.backup_path.read_text(encoding="utf-8") == (
        "127.0.0.1 localhost\n10.0.0.5 intranet.local\n"
    )
    assert "10.0.0.5 intranet.local" in content
    assert BEGIN_MARKER in content
    assert "127.0.0.1 youtube.com" in content
    assert "127.0.0.1 instagram.com" in content
    assert content.count("127.0.0.1 youtube.com") == 1
    assert END_MARKER in content


def test_apply_replaces_only_existing_managed_block(
    fake_hosts_path: Path, fake_hosts_backup_path: Path
) -> None:
    blocker = make_blocker(fake_hosts_path, fake_hosts_backup_path)
    blocker.hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            "# SELF-BOSS BEGIN\n"
            "127.0.0.1 old.example\n"
            "# SELF-BOSS END\n"
            "10.0.0.5 intranet.local\n"
        ),
        encoding="utf-8",
    )

    blocker.apply(["new.example"], BlockerPlan(test_mode=False, dry_run=False))
    content = blocker.hosts_path.read_text(encoding="utf-8")

    assert "127.0.0.1 localhost" in content
    assert "10.0.0.5 intranet.local" in content
    assert "old.example" not in content
    assert "127.0.0.1 new.example" in content
    assert content.count(BEGIN_MARKER) == 1
    assert content.count(END_MARKER) == 1


def test_clear_removes_only_managed_entries(
    fake_hosts_path: Path, fake_hosts_backup_path: Path
) -> None:
    blocker = make_blocker(fake_hosts_path, fake_hosts_backup_path)
    blocker.hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            "# SELF-BOSS BEGIN\n"
            "127.0.0.1 youtube.com\n"
            "# SELF-BOSS END\n"
            "10.0.0.5 intranet.local\n"
        ),
        encoding="utf-8",
    )

    blocker.clear(BlockerPlan(test_mode=False, dry_run=False))
    content = blocker.hosts_path.read_text(encoding="utf-8")

    assert "127.0.0.1 localhost" in content
    assert "10.0.0.5 intranet.local" in content
    assert "youtube.com" not in content
    assert BEGIN_MARKER not in content
    assert END_MARKER not in content


def test_restore_backup_restores_temp_hosts_file(
    fake_hosts_path: Path, fake_hosts_backup_path: Path
) -> None:
    blocker = make_blocker(fake_hosts_path, fake_hosts_backup_path)
    blocker.hosts_path.write_text("original\n", encoding="utf-8")
    blocker.apply(["youtube.com"], BlockerPlan(test_mode=False, dry_run=False))

    result = blocker.restore_backup()

    assert result.success is True
    assert blocker.hosts_path.read_text(encoding="utf-8") == "original\n"


def test_add_or_replace_managed_block_preserves_unrelated_hosts_text() -> None:
    content = (
        "127.0.0.1 localhost\n"
        "\n"
        "# office override\n"
        "10.0.0.5 intranet.local"
    )

    updated = add_or_replace_managed_block(
        content,
        ["YouTube.com", "www.youtube.com"],
    )

    assert content in updated
    assert BEGIN_MARKER in updated
    assert "127.0.0.1 youtube.com" in updated
    assert "127.0.0.1 www.youtube.com" in updated
    assert END_MARKER in updated


def test_add_or_replace_managed_block_replaces_existing_section() -> None:
    content = (
        "127.0.0.1 localhost\n"
        f"{BEGIN_MARKER}\n"
        "127.0.0.1 old.example\n"
        f"{END_MARKER}\n"
        "# keep me\n"
    )

    updated = add_or_replace_managed_block(content, ["new.example"])

    assert "127.0.0.1 localhost\n" in updated
    assert "# keep me\n" in updated
    assert "old.example" not in updated
    assert "127.0.0.1 new.example" in updated
    assert updated.count(BEGIN_MARKER) == 1
    assert updated.count(END_MARKER) == 1


def test_remove_managed_block_preserves_comments_blanks_and_no_trailing_newline() -> None:
    content = (
        "# start\n"
        "\n"
        f"{BEGIN_MARKER}\n"
        "127.0.0.1 youtube.com\n"
        f"{END_MARKER}\n"
        "10.0.0.5 intranet.local"
    )

    assert remove_managed_block(content) == "# start\n\n10.0.0.5 intranet.local"


def test_generate_hosts_entries_uses_exact_domains_only() -> None:
    entries = generate_hosts_entries(
        [
            "YouTube.com",
            "youtube.com",
            "www.youtube.com",
            "https://youtube.com",
            "youtube.com/watch",
            "steam.exe",
            "not a domain",
            "localhost",
        ]
    )

    assert entries == [
        "127.0.0.1 youtube.com",
        "127.0.0.1 www.youtube.com",
    ]


def test_generate_hosts_entries_expands_only_bare_domains_to_www() -> None:
    entries = generate_hosts_entries(
        [
            "reddit.com",
            "www.reddit.com",
            "old.reddit.com",
            "docs.python.org",
        ]
    )

    assert entries == [
        "127.0.0.1 reddit.com",
        "127.0.0.1 www.reddit.com",
        "127.0.0.1 old.reddit.com",
        "127.0.0.1 docs.python.org",
    ]
    assert "127.0.0.1 www.www.reddit.com" not in entries


def test_apply_real_writes_managed_section_and_creates_backup(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    backup_path = tmp_path / "hosts.selfboss.bak"
    hosts_path.write_text(
        "127.0.0.1 localhost\n10.0.0.5 intranet.local",
        encoding="utf-8",
    )
    blocker = HostsBlocker(hosts_path=hosts_path, backup_path=backup_path)

    result = blocker.apply_real(["YouTube.com", "www.youtube.com"])

    content = hosts_path.read_text(encoding="utf-8")
    assert result.success is True
    assert result.status == "success"
    assert backup_path.read_text(encoding="utf-8") == (
        "127.0.0.1 localhost\n10.0.0.5 intranet.local"
    )
    assert "10.0.0.5 intranet.local" in content
    assert f"{BEGIN_MARKER}\n" in content
    assert "127.0.0.1 youtube.com" in content
    assert "127.0.0.1 www.youtube.com" in content
    assert f"{END_MARKER}\n" in content


def test_clear_real_removes_only_managed_section(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            f"{BEGIN_MARKER}\n"
            "127.0.0.1 youtube.com\n"
            f"{END_MARKER}\n"
            "10.0.0.5 intranet.local"
        ),
        encoding="utf-8",
    )
    blocker = HostsBlocker(hosts_path=hosts_path)

    result = blocker.clear_real()

    content = hosts_path.read_text(encoding="utf-8")
    assert result.success is True
    assert result.status == "success"
    assert "127.0.0.1 localhost" in content
    assert "10.0.0.5 intranet.local" in content
    assert "youtube.com" not in content
    assert BEGIN_MARKER not in content
    assert END_MARKER not in content


def test_apply_real_permission_denied_returns_structured_result(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    blocker = PermissionDeniedHostsBlocker(hosts_path=hosts_path)

    result = blocker.apply_real(["youtube.com"])

    assert result.success is False
    assert result.status == "permission_denied"
    assert "administrator" in result.message
    assert blocker.write_attempts == 1


def test_apply_real_write_failure_rolls_back_previous_content(tmp_path: Path) -> None:
    hosts_path = tmp_path / "hosts"
    original = "127.0.0.1 localhost\n"
    hosts_path.write_text(original, encoding="utf-8")
    blocker = FailingWriteHostsBlocker(hosts_path=hosts_path)

    result = blocker.apply_real(["youtube.com"])

    assert result.success is False
    assert result.status == "rollback_succeeded"
    assert result.rollback_status == "rollback_succeeded"
    assert hosts_path.read_text(encoding="utf-8") == original
