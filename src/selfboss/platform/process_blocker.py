"""Windows process blocker adapter with strict dry-run support."""

from __future__ import annotations

import csv
import subprocess
from collections.abc import Callable, Iterable

from selfboss.core.models import EnforcementReadinessCheck
from selfboss.platform.test_mode import BlockerActionResult, BlockerPlan


ProcessProvider = Callable[[], Iterable[str]]
TerminationRunner = Callable[[str], subprocess.CompletedProcess[str]]
TASKKILL_TIMEOUT_SECONDS = 2.0

PROTECTED_PROCESS_NAMES: tuple[str, ...] = (
    "system",
    "system idle process",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "fontdrvhost.exe",
    "dwm.exe",
    "explorer.exe",
    "conhost.exe",
    "taskhostw.exe",
    "selfboss.exe",
    "python.exe",
    "pythonw.exe",
)


class ProcessBlocker:
    """Scan and optionally terminate target processes by executable name."""

    def __init__(
        self,
        process_provider: ProcessProvider | None = None,
        termination_runner: TerminationRunner | None = None,
    ) -> None:
        self._process_provider = process_provider or _tasklist_process_names
        self._termination_runner = termination_runner or _taskkill_soft

    def scan(self, target_names: Iterable[str]) -> list[BlockerActionResult]:
        """Return diagnostics for matching target process names."""
        active_names = self.running_process_names()
        results: list[BlockerActionResult] = []

        for target in _normalize_names(target_names):
            if target in active_names:
                results.append(
                    BlockerActionResult(
                        action="scan_process",
                        target=target,
                        dry_run=True,
                        success=True,
                        message=f"matched active process: {target}",
                    )
                )
            else:
                results.append(
                    BlockerActionResult(
                        action="scan_process",
                        target=target,
                        dry_run=True,
                        success=False,
                        message=f"process not running: {target}",
                    )
                )

        return results

    def block(
        self, target_names: Iterable[str], plan: BlockerPlan
    ) -> list[BlockerActionResult]:
        """Block target process names according to the provided plan."""
        active_names = self.running_process_names()
        results: list[BlockerActionResult] = []

        for target in _normalize_names(target_names):
            if is_protected_process_name(target):
                results.append(
                    BlockerActionResult(
                        action="terminate_process",
                        target=target,
                        dry_run=True,
                        success=False,
                        message=f"protected process skipped: {target}",
                    )
                )
                continue
            if target not in active_names:
                results.append(
                    BlockerActionResult(
                        action="terminate_process",
                        target=target,
                        dry_run=plan.dry_run,
                        success=False,
                        message=f"process not running: {target}",
                    )
                )
                continue

            if plan.dry_run:
                results.append(
                    BlockerActionResult(
                        action="terminate_process",
                        target=target,
                        dry_run=True,
                        success=True,
                        message=f"would terminate process: {target}",
                    )
                )
                continue

            plan.require_real_system_action_allowed()
            results.append(self._terminate_one(target))

        return results

    def running_process_names(self) -> list[str]:
        """Return normalized process names from this blocker's provider."""
        return list_running_process_names(self._process_provider)

    def terminate(
        self,
        target_names: Iterable[str],
        *,
        active_process_names: Iterable[str] | None = None,
    ) -> list[BlockerActionResult]:
        """Request soft termination for target process names."""
        active_names = (
            _normalize_names(active_process_names)
            if active_process_names is not None
            else list_running_process_names(self._process_provider)
        )
        active_set = set(active_names)
        results: list[BlockerActionResult] = []

        for target in _normalize_names(target_names):
            if is_protected_process_name(target):
                results.append(
                    BlockerActionResult(
                        action="skipped_protected",
                        target=target,
                        dry_run=False,
                        success=False,
                        message=f"protected process skipped: {target}",
                    )
                )
                continue
            if target not in active_set:
                results.append(
                    BlockerActionResult(
                        action="not_found",
                        target=target,
                        dry_run=False,
                        success=False,
                        message=f"process not running: {target}",
                    )
                )
                continue
            results.append(self._terminate_one(target))

        return results

    def _terminate_one(self, target: str) -> BlockerActionResult:
        try:
            completed = self._termination_runner(target)
        except Exception as error:
            return BlockerActionResult(
                action="failed",
                target=target,
                dry_run=False,
                success=False,
                message=f"failed to request termination for {target}: {error}",
            )
        action, success, message = _classify_taskkill_result(completed, target)
        return BlockerActionResult(
            action=action,
            target=target,
            dry_run=False,
            success=success,
            message=message,
        )


def list_running_process_names(
    process_provider: ProcessProvider | None = None,
) -> list[str]:
    """Return normalized running process names without mutating processes."""
    provider = process_provider or _tasklist_process_names
    try:
        names = provider()
    except Exception:
        return []
    return _normalize_names(names)


def is_protected_process_name(process_name: str) -> bool:
    """Return whether a process name must never be a real-blocking candidate."""
    normalized = _normalize_names([process_name])
    return bool(normalized and normalized[0] in PROTECTED_PROCESS_NAMES)


def process_blocking_readiness_checks() -> tuple[EnforcementReadinessCheck, ...]:
    """Return read-only readiness facts for future process blocking."""
    return (
        EnforcementReadinessCheck(
            key="process_adapter_exists",
            label="Process blocker adapter",
            ready=True,
            detail="Process blocker adapter is available.",
        ),
        EnforcementReadinessCheck(
            key="dry_run_scan_available",
            label="Dry-run process matching",
            ready=True,
            detail="Dry-run process scan is available.",
        ),
        EnforcementReadinessCheck(
            key="system_process_allowlist",
            label="System/self process allowlist",
            ready=True,
            detail="System/self process allowlist is available.",
        ),
        EnforcementReadinessCheck(
            key="process_action_logging",
            label="Process action logging",
            ready=True,
            detail="Dry-run process action logging is available.",
        ),
        EnforcementReadinessCheck(
            key="process_recovery_interaction",
            label="Process recovery interaction",
            ready=True,
            detail="Safe Mode and Recovery Mode disable process monitoring.",
        ),
        EnforcementReadinessCheck(
            key="real_process_blocking_implementation",
            label="Real process blocking implementation",
            ready=True,
            detail=(
                "Real process blocking implementation is available for explicit "
                "app rules."
            ),
        ),
    )


def _normalize_names(names: Iterable[str]) -> list[str]:
    """Normalize process names for case-insensitive matching in input order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for name in names:
        clean = name.strip().lower() if name else ""
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _tasklist_process_names() -> list[str]:
    """Return process names from the Windows tasklist command."""
    completed = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True,
        check=False,
        text=True,
        **_hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        return []

    rows = csv.reader(completed.stdout.splitlines())
    return [row[0] for row in rows if row]


def _taskkill_soft(process_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["taskkill", "/IM", process_name],
        capture_output=True,
        check=False,
        text=True,
        timeout=TASKKILL_TIMEOUT_SECONDS,
        **_hidden_subprocess_kwargs(),
    )


def _hidden_subprocess_kwargs() -> dict[str, object]:
    """Return Windows subprocess flags that prevent console windows."""
    kwargs: dict[str, object] = {}
    creation_no_window = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if creation_no_window is not None:
        kwargs["creationflags"] = creation_no_window

    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    startf_use_show_window = getattr(subprocess, "STARTF_USESHOWWINDOW", None)
    sw_hide = getattr(subprocess, "SW_HIDE", None)
    if (
        startupinfo_factory is not None
        and startf_use_show_window is not None
        and sw_hide is not None
    ):
        startupinfo = startupinfo_factory()
        startupinfo.dwFlags |= startf_use_show_window
        startupinfo.wShowWindow = sw_hide
        kwargs["startupinfo"] = startupinfo

    return kwargs


def _classify_taskkill_result(
    completed: subprocess.CompletedProcess[str],
    target: str,
) -> tuple[str, bool, str]:
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    if completed.returncode == 0:
        return (
            "terminate_requested",
            True,
            f"termination requested for process: {target}",
        )
    if "not found" in output or "not running" in output:
        return "not_found", False, f"process not found: {target}"
    if "access is denied" in output or "access denied" in output:
        return "access_denied", False, f"access denied while terminating: {target}"
    return "failed", False, f"failed to request termination for process: {target}"
