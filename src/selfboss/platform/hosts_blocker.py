"""Marker-based Windows hosts blocker adapter."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from selfboss.core.models import EnforcementReadinessCheck
from selfboss.platform.test_mode import BlockerActionResult, BlockerPlan


BEGIN_MARKER = "# SELF-BOSS BEGIN"
END_MARKER = "# SELF-BOSS END"
REDIRECT_IP = "127.0.0.1"
DEFAULT_HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")


@dataclass(frozen=True)
class HostsActionResult:
    """Structured result for real hosts-file operations."""

    action: str
    status: str
    success: bool
    target: str
    domain_count: int
    message: str
    rollback_status: str = ""


class HostsBlocker:
    """Apply and remove only LoopGuard-managed hosts entries."""

    def __init__(
        self,
        *,
        hosts_path: Path | str = DEFAULT_HOSTS_PATH,
        backup_path: Path | str | None = None,
    ) -> None:
        self.hosts_path = Path(hosts_path)
        self.backup_path = Path(backup_path) if backup_path else self.hosts_path.with_suffix(
            self.hosts_path.suffix + ".selfboss.bak"
        )

    def apply(
        self, domains: Iterable[str], plan: BlockerPlan
    ) -> list[BlockerActionResult]:
        """Replace the managed hosts block with entries for domains."""
        normalized = _normalize_domains(domains)
        original = self._read_hosts()
        updated = self._replace_managed_block(original, normalized)

        if plan.dry_run:
            return [
                BlockerActionResult(
                    action="write_hosts",
                    target=str(self.hosts_path),
                    dry_run=True,
                    success=True,
                    message=f"would write managed hosts block for {len(normalized)} domains",
                )
            ]

        plan.require_real_system_action_allowed()
        self._backup_hosts()
        self._write_hosts(updated)
        return [
            BlockerActionResult(
                action="write_hosts",
                target=str(self.hosts_path),
                dry_run=False,
                success=True,
                message=f"wrote managed hosts block for {len(normalized)} domains",
            )
        ]

    def clear(self, plan: BlockerPlan) -> list[BlockerActionResult]:
        """Remove only the LoopGuard-managed hosts block."""
        original = self._read_hosts()
        updated = remove_managed_block(original)

        if plan.dry_run:
            return [
                BlockerActionResult(
                    action="clear_hosts",
                    target=str(self.hosts_path),
                    dry_run=True,
                    success=True,
                    message="would clear managed hosts block",
                )
            ]

        plan.require_real_system_action_allowed()
        self._backup_hosts()
        self._write_hosts(updated)
        return [
            BlockerActionResult(
                action="clear_hosts",
                target=str(self.hosts_path),
                dry_run=False,
                success=True,
                message="cleared managed hosts block",
            )
        ]

    def apply_real(self, domains: Iterable[str]) -> HostsActionResult:
        """Write the managed hosts block for exact domains."""
        normalized = _normalize_domains(domains)
        if not normalized:
            return self.clear_real()

        try:
            original = self._read_hosts()
        except PermissionError:
            return self._hosts_result(
                action="write_hosts",
                status="permission_denied",
                success=False,
                domain_count=len(normalized),
                message=(
                    "Real Hosts Blocking could not read hosts file. "
                    "Run LoopGuard as administrator to enable website blocking."
                ),
            )
        except OSError as error:
            return self._hosts_result(
                action="write_hosts",
                status="failed",
                success=False,
                domain_count=len(normalized),
                message=f"Could not read hosts file: {error}",
            )

        updated = add_or_replace_managed_block(original, normalized)
        if updated == original:
            return self._hosts_result(
                action="write_hosts",
                status="success",
                success=True,
                domain_count=len(normalized),
                message=f"managed hosts block already current for {len(normalized)} domains",
            )

        backup_result = self._backup_for_real_operation()
        if backup_result is not None:
            return backup_result

        try:
            self._write_hosts(updated)
        except PermissionError:
            return self._hosts_result(
                action="write_hosts",
                status="permission_denied",
                success=False,
                domain_count=len(normalized),
                message=(
                    "Real Hosts Blocking could not write hosts file. "
                    "Run LoopGuard as administrator to enable website blocking."
                ),
            )
        except OSError as error:
            rollback_status = self._rollback_to_content(original)
            return self._hosts_result(
                action="write_hosts",
                status=rollback_status,
                success=False,
                domain_count=len(normalized),
                message=f"Hosts write failed and rollback was attempted: {error}",
                rollback_status=rollback_status,
            )

        return self._hosts_result(
            action="write_hosts",
            status="success",
            success=True,
            domain_count=len(normalized),
            message=f"wrote managed hosts block for {len(normalized)} domains",
        )

    def clear_real(self) -> HostsActionResult:
        """Remove only the LoopGuard-managed hosts block."""
        try:
            original = self._read_hosts()
        except PermissionError:
            return self._hosts_result(
                action="clear_hosts",
                status="permission_denied",
                success=False,
                domain_count=0,
                message=(
                    "Real Hosts Blocking could not read hosts file. "
                    "Run LoopGuard as administrator to enable website blocking."
                ),
            )
        except OSError as error:
            return self._hosts_result(
                action="clear_hosts",
                status="failed",
                success=False,
                domain_count=0,
                message=f"Could not read hosts file: {error}",
            )

        updated = remove_managed_block(original)
        if updated == original:
            return self._hosts_result(
                action="clear_hosts",
                status="no_entries_to_write",
                success=True,
                domain_count=0,
                message="no LoopGuard hosts entries to remove",
            )

        backup_result = self._backup_for_real_operation(action="clear_hosts")
        if backup_result is not None:
            return backup_result

        try:
            self._write_hosts(updated)
        except PermissionError:
            return self._hosts_result(
                action="clear_hosts",
                status="permission_denied",
                success=False,
                domain_count=0,
                message=(
                    "Real Hosts Blocking could not write hosts file. "
                    "Run LoopGuard as administrator to enable website blocking."
                ),
            )
        except OSError as error:
            rollback_status = self._rollback_to_content(original)
            return self._hosts_result(
                action="clear_hosts",
                status=rollback_status,
                success=False,
                domain_count=0,
                message=f"Hosts clear failed and rollback was attempted: {error}",
                rollback_status=rollback_status,
            )

        return self._hosts_result(
            action="clear_hosts",
            status="success",
            success=True,
            domain_count=0,
            message="removed LoopGuard managed hosts block",
        )

    def restore_backup(self) -> BlockerActionResult:
        """Restore the hosts file from the LoopGuard backup."""
        if not self.backup_path.exists():
            return BlockerActionResult(
                action="restore_hosts_backup",
                target=str(self.backup_path),
                dry_run=False,
                success=False,
                message="backup file does not exist",
            )

        self.hosts_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.backup_path, self.hosts_path)
        return BlockerActionResult(
            action="restore_hosts_backup",
            target=str(self.backup_path),
            dry_run=False,
            success=True,
            message="restored hosts backup",
        )

    def managed_block(self, domains: Iterable[str]) -> str:
        """Return a marker-wrapped managed hosts block."""
        return build_managed_block(domains)

    def _read_hosts(self) -> str:
        if not self.hosts_path.exists():
            return ""
        return self.hosts_path.read_text(encoding="utf-8")

    def _write_hosts(self, content: str) -> None:
        self.hosts_path.parent.mkdir(parents=True, exist_ok=True)
        self.hosts_path.write_text(content, encoding="utf-8")

    def _backup_hosts(self) -> None:
        self.hosts_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        if self.hosts_path.exists():
            shutil.copyfile(self.hosts_path, self.backup_path)
        else:
            self.backup_path.write_text("", encoding="utf-8")

    def _replace_managed_block(self, content: str, domains: list[str]) -> str:
        return add_or_replace_managed_block(content, domains)

    def _backup_for_real_operation(
        self,
        *,
        action: str = "write_hosts",
    ) -> HostsActionResult | None:
        try:
            self._backup_hosts()
        except PermissionError:
            return self._hosts_result(
                action=action,
                status="permission_denied",
                success=False,
                domain_count=0,
                message=(
                    "Real Hosts Blocking could not create a hosts backup. "
                    "Run LoopGuard as administrator to enable website blocking."
                ),
            )
        except OSError as error:
            return self._hosts_result(
                action=action,
                status="backup_failed",
                success=False,
                domain_count=0,
                message=f"Could not create hosts backup: {error}",
            )
        return None

    def _rollback_to_content(self, content: str) -> str:
        try:
            self._write_hosts(content)
        except OSError:
            return "rollback_failed"
        return "rollback_succeeded"

    def _hosts_result(
        self,
        *,
        action: str,
        status: str,
        success: bool,
        domain_count: int,
        message: str,
        rollback_status: str = "",
    ) -> HostsActionResult:
        return HostsActionResult(
            action=action,
            status=status,
            success=success,
            target=str(self.hosts_path),
            domain_count=domain_count,
            message=message,
            rollback_status=rollback_status,
        )


def hosts_blocking_readiness_checks() -> tuple[EnforcementReadinessCheck, ...]:
    """Return read-only readiness facts for future hosts blocking."""
    transform_ready = _managed_section_transform_ready()
    return (
        EnforcementReadinessCheck(
            key="hosts_adapter_exists",
            label="Hosts blocker adapter",
            ready=True,
            detail="Hosts blocker adapter is available.",
        ),
        EnforcementReadinessCheck(
            key="managed_section_transform",
            label="Managed marker section",
            ready=transform_ready,
            detail=(
                "Managed section add/remove preserves unrelated hosts entries."
                if transform_ready
                else "Managed section add/remove readiness check failed."
            ),
        ),
        EnforcementReadinessCheck(
            key="backup_supported",
            label="Hosts backup",
            ready=True,
            detail="Hosts backup support is available.",
        ),
        EnforcementReadinessCheck(
            key="rollback_supported",
            label="Hosts rollback",
            ready=True,
            detail="Managed hosts rollback support is available.",
        ),
        EnforcementReadinessCheck(
            key="temp_file_test_support",
            label="Temp-file tests",
            ready=True,
            detail="Temp-file hosts tests are available.",
        ),
        EnforcementReadinessCheck(
            key="admin_requirement_known",
            label="Admin requirement",
            ready=True,
            detail="Windows hosts writes require admin rights.",
        ),
        EnforcementReadinessCheck(
            key="recovery_removal_supported",
            label="Recovery removal",
            ready=True,
            detail="Recovery can remove LoopGuard-managed hosts entries.",
        ),
        EnforcementReadinessCheck(
            key="real_hosts_blocking_implementation",
            label="Real Hosts Blocking implementation",
            ready=True,
            detail="Real Hosts Blocking can update the managed hosts section.",
        ),
    )


def build_managed_block(domains: Iterable[str]) -> str:
    """Return a marker-wrapped managed hosts block for exact domains."""
    entries = generate_hosts_entries(domains)
    if not entries:
        return ""
    lines = [BEGIN_MARKER]
    lines.extend(entries)
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def add_or_replace_managed_block(content: str, domains: Iterable[str]) -> str:
    """Add or replace the LoopGuard-managed hosts section in text content."""
    block = build_managed_block(domains)
    lines = content.splitlines(keepends=True)
    begin_index, end_index = _managed_block_bounds(lines)
    if begin_index is not None:
        replacement_lines = block.splitlines(keepends=True)
        stop_index = len(lines) if end_index is None else end_index + 1
        return "".join(lines[:begin_index] + replacement_lines + lines[stop_index:])

    if not block:
        return content
    if not content:
        return block
    separator = "\n" if content.endswith("\n") else "\n\n"
    if content.endswith("\n\n"):
        separator = ""
    return f"{content}{separator}{block}"


def remove_managed_block(content: str) -> str:
    """Remove only lines between LoopGuard markers."""
    output: list[str] = []
    in_managed_block = False

    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            in_managed_block = True
            continue
        if stripped == END_MARKER:
            in_managed_block = False
            continue
        if not in_managed_block:
            output.append(line)

    return "".join(output)


def generate_hosts_entries(domains: Iterable[str]) -> list[str]:
    """Return hosts entries for exact domains, expanding bare domains to www."""
    return [f"{REDIRECT_IP} {domain}" for domain in _expand_bare_domains(domains)]


def _expand_bare_domains(domains: Iterable[str]) -> list[str]:
    """Normalize domains and add www for bare two-label domains."""
    seen: set[str] = set()
    expanded: list[str] = []
    for domain in _normalize_domains(domains):
        for candidate in _domain_with_www_variant(domain):
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded.append(candidate)
    return expanded


def _domain_with_www_variant(domain: str) -> tuple[str, ...]:
    labels = domain.split(".")
    if len(labels) == 2 and labels[0] != "www":
        return domain, f"www.{domain}"
    return (domain,)


def _normalize_domains(domains: Iterable[str]) -> list[str]:
    """Normalize domains while preserving first-seen order."""
    seen: set[str] = set()
    normalized: list[str] = []
    for domain in domains:
        clean = domain.strip().lower()
        if not _is_exact_domain(clean) or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _managed_block_bounds(lines: list[str]) -> tuple[int | None, int | None]:
    begin_index: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == BEGIN_MARKER and begin_index is None:
            begin_index = index
            continue
        if stripped == END_MARKER and begin_index is not None:
            return begin_index, index
    return begin_index, None


def _managed_section_transform_ready() -> bool:
    sample = "127.0.0.1 localhost\n\n# local comment\n10.0.0.5 intranet.local\n"
    added = add_or_replace_managed_block(sample, ["YouTube.com"])
    removed = remove_managed_block(added)
    replaced = add_or_replace_managed_block(added, ["example.com"])
    return (
        "127.0.0.1 localhost\n\n# local comment\n10.0.0.5 intranet.local"
        in added
        and "127.0.0.1 youtube.com" in added
        and removed.rstrip("\n") == sample.rstrip("\n")
        and "youtube.com" not in replaced
        and "127.0.0.1 example.com" in replaced
    )


def _is_exact_domain(value: str) -> bool:
    if (
        not value
        or value.endswith(".exe")
        or value.endswith(".")
        or "://" in value
        or "/" in value
        or "\\" in value
        or "?" in value
        or "#" in value
        or any(character.isspace() for character in value)
    ):
        return False
    labels = value.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    for label in labels:
        if not _valid_domain_label(label):
            return False
    return len(labels[-1]) >= 2 and labels[-1].isalpha()


def _valid_domain_label(label: str) -> bool:
    if label.startswith("-") or label.endswith("-"):
        return False
    return all(character.isalnum() or character == "-" for character in label)
