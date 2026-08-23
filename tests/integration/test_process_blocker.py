from __future__ import annotations

import pytest

from selfboss.platform.process_blocker import ProcessBlocker
from selfboss.platform.test_mode import BlockerPlan


def fake_processes() -> list[str]:
    return ["Steam.exe", "notepad.exe"]


def test_scan_matches_process_names_case_insensitively() -> None:
    blocker = ProcessBlocker(process_provider=fake_processes)

    results = blocker.scan(["steam.exe", "missing.exe"])

    assert results[0].success is True
    assert results[0].target == "steam.exe"
    assert results[1].success is False
    assert "not running" in results[1].message


def test_test_mode_forces_dry_run_diagnostics() -> None:
    blocker = ProcessBlocker(process_provider=fake_processes)
    plan = BlockerPlan(test_mode=True, dry_run=False)

    results = blocker.block(["steam.exe"], plan)

    assert plan.dry_run is True
    assert len(results) == 1
    assert results[0].dry_run is True
    assert results[0].success is True
    assert "would terminate process: steam.exe" == results[0].message


def test_dry_run_reports_missing_process_without_real_termination() -> None:
    blocker = ProcessBlocker(process_provider=fake_processes)

    results = blocker.block(["missing.exe"], BlockerPlan())

    assert results[0].action == "terminate_process"
    assert results[0].dry_run is True
    assert results[0].success is False
    assert "process not running: missing.exe" == results[0].message


def test_blocker_uses_injected_process_provider_only() -> None:
    calls = []

    def provider() -> list[str]:
        calls.append("called")
        return ["steam.exe"]

    blocker = ProcessBlocker(process_provider=provider)

    blocker.block(["steam.exe"], BlockerPlan())

    assert calls == ["called"]


def test_real_action_guard_rejects_test_mode() -> None:
    plan = BlockerPlan(test_mode=True, dry_run=False)

    with pytest.raises(RuntimeError, match="real system action blocked"):
        plan.require_real_system_action_allowed()
