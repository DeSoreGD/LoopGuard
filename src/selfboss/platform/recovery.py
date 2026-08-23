"""GUI-independent recovery manager and command-line entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from selfboss.config import (
    default_app_home,
    safe_mode_flag_path,
    test_mode_flag_path,
)
from selfboss.core.models import EnforcementReadinessCheck
from selfboss.platform.hosts_blocker import (
    BEGIN_MARKER,
    END_MARKER,
    DEFAULT_HOSTS_PATH,
    HostsBlocker,
    remove_managed_block,
)
from selfboss.platform.test_mode import BlockerActionResult


@dataclass(frozen=True)
class RecoveryStatus:
    """Current recovery-relevant local state."""

    app_home: Path
    hosts_path: Path
    backup_path: Path
    hosts_markers_present: bool
    backup_present: bool
    safe_mode_forced: bool
    test_mode_forced: bool


@dataclass(frozen=True)
class RecoveryResult:
    """Result of a recovery operation."""

    action: str
    success: bool
    message: str
    details: tuple[BlockerActionResult, ...] = ()


class RecoveryManager:
    """Recover LoopGuard-managed system changes without starting the GUI."""

    def __init__(
        self,
        *,
        app_home: Path | str | None = None,
        hosts_path: Path | str = DEFAULT_HOSTS_PATH,
        backup_path: Path | str | None = None,
    ) -> None:
        resolved_app_home = Path(app_home).expanduser() if app_home else default_app_home()
        self.app_home = resolved_app_home
        self.hosts_blocker = HostsBlocker(
            hosts_path=hosts_path,
            backup_path=backup_path,
        )
        self.safe_mode_flag = safe_mode_flag_path(self.app_home)
        self.test_mode_flag = test_mode_flag_path(self.app_home)

    def status(self) -> RecoveryStatus:
        """Inspect recovery state without changing files."""
        hosts_content = self._read_hosts()
        return RecoveryStatus(
            app_home=self.app_home,
            hosts_path=self.hosts_blocker.hosts_path,
            backup_path=self.hosts_blocker.backup_path,
            hosts_markers_present=_has_managed_markers(hosts_content),
            backup_present=self.hosts_blocker.backup_path.exists(),
            safe_mode_forced=self.safe_mode_flag.exists(),
            test_mode_forced=self.test_mode_flag.exists(),
        )

    def unlock(self, *, force_safe_mode: bool = False) -> RecoveryResult:
        """Undo only LoopGuard-managed hosts changes."""
        details: list[BlockerActionResult] = []

        if self.hosts_blocker.backup_path.exists():
            details.append(self.hosts_blocker.restore_backup())
            message = "restored LoopGuard hosts backup"
        else:
            original = self._read_hosts()
            updated = remove_managed_block(original)
            self._write_hosts(updated)
            details.append(
                BlockerActionResult(
                    action="remove_managed_hosts_block",
                    target=str(self.hosts_blocker.hosts_path),
                    dry_run=False,
                    success=True,
                    message="removed only LoopGuard-managed hosts block",
                )
            )
            message = "removed LoopGuard-managed hosts block"

        if force_safe_mode:
            details.append(self.force_safe_mode())

        success = all(detail.success for detail in details)
        return RecoveryResult(
            action="unlock",
            success=success,
            message=message,
            details=tuple(details),
        )

    def force_safe_mode(self) -> BlockerActionResult:
        """Persist safe mode for the next app launch."""
        self.safe_mode_flag.parent.mkdir(parents=True, exist_ok=True)
        self.safe_mode_flag.write_text("safe mode forced by recovery\n", encoding="utf-8")
        return BlockerActionResult(
            action="force_safe_mode",
            target=str(self.safe_mode_flag),
            dry_run=False,
            success=True,
            message="safe mode will be active on next launch",
        )

    def reset_test_mode(self) -> RecoveryResult:
        """Persist safe mode and test mode for the next app launch."""
        self.test_mode_flag.parent.mkdir(parents=True, exist_ok=True)
        self.test_mode_flag.write_text("test mode forced by recovery\n", encoding="utf-8")
        safe_mode_result = self.force_safe_mode()
        test_mode_result = BlockerActionResult(
            action="force_test_mode",
            target=str(self.test_mode_flag),
            dry_run=False,
            success=True,
            message="test mode will be active on next launch",
        )
        return RecoveryResult(
            action="reset_test_mode",
            success=True,
            message="safe mode and test mode will be active on next launch",
            details=(test_mode_result, safe_mode_result),
        )

    def _read_hosts(self) -> str:
        if not self.hosts_blocker.hosts_path.exists():
            return ""
        return self.hosts_blocker.hosts_path.read_text(encoding="utf-8")

    def _write_hosts(self, content: str) -> None:
        self.hosts_blocker.hosts_path.parent.mkdir(parents=True, exist_ok=True)
        self.hosts_blocker.hosts_path.write_text(content, encoding="utf-8")


def recovery_readiness_checks() -> tuple[EnforcementReadinessCheck, ...]:
    """Return read-only readiness facts for recovery-safe enforcement."""
    return (
        EnforcementReadinessCheck(
            key="recovery_module_exists",
            label="Recovery module",
            ready=True,
            detail="Recovery module is available.",
        ),
        EnforcementReadinessCheck(
            key="safe_mode_exists",
            label="Safe Mode",
            ready=True,
            detail="Safe Mode flag support is available.",
        ),
        EnforcementReadinessCheck(
            key="test_mode_recovery_exists",
            label="Test Mode recovery",
            ready=True,
            detail="Recovery can force Test Mode on next launch.",
        ),
        EnforcementReadinessCheck(
            key="disable_real_enforcement_modes",
            label="Disable real enforcement modes",
            ready=False,
            detail="Missing recovery path to disable persisted real enforcement modes.",
        ),
        EnforcementReadinessCheck(
            key="crash_recovery_instructions",
            label="Crash recovery instructions",
            ready=False,
            detail="Missing user-facing crash recovery instructions for real enforcement.",
        ),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the recovery command-line interface."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    manager = RecoveryManager(
        app_home=args.app_home,
        hosts_path=args.hosts_path,
        backup_path=args.backup_path,
    )

    if args.command == "status":
        print(_format_status(manager.status()))
        return 0
    if args.command == "unlock":
        result = manager.unlock(force_safe_mode=args.force_safe_mode)
        print(_format_result(result))
        return 0 if result.success else 1
    if args.command == "reset-test-mode":
        result = manager.reset_test_mode()
        print(_format_result(result))
        return 0 if result.success else 1

    parser.error("unknown command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoopGuard recovery tools")
    parser.add_argument("--app-home", type=Path, default=None)
    parser.add_argument("--hosts-path", type=Path, default=DEFAULT_HOSTS_PATH)
    parser.add_argument("--backup-path", type=Path, default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")

    unlock = subparsers.add_parser("unlock")
    unlock.add_argument("--force-safe-mode", action="store_true")

    subparsers.add_parser("reset-test-mode")
    return parser


def _format_status(status: RecoveryStatus) -> str:
    return "\n".join(
        [
            "LoopGuard recovery status",
            f"app_home: {status.app_home}",
            f"hosts_path: {status.hosts_path}",
            f"backup_path: {status.backup_path}",
            f"hosts_markers_present: {status.hosts_markers_present}",
            f"backup_present: {status.backup_present}",
            f"safe_mode_forced: {status.safe_mode_forced}",
            f"test_mode_forced: {status.test_mode_forced}",
        ]
    )


def _format_result(result: RecoveryResult) -> str:
    lines = [
        f"{result.action}: {'success' if result.success else 'failed'}",
        result.message,
    ]
    lines.extend(f"- {detail.action}: {detail.message}" for detail in result.details)
    return "\n".join(lines)


def _has_managed_markers(content: str) -> bool:
    return BEGIN_MARKER in content and END_MARKER in content


if __name__ == "__main__":
    raise SystemExit(main())
