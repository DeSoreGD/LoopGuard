"""Shared test-mode primitives for blocker adapters."""

from __future__ import annotations

from dataclasses import dataclass

from selfboss.core.models import EnforcementReadinessCheck


@dataclass(frozen=True)
class BlockerActionResult:
    """Diagnostic result from a blocker adapter action."""

    action: str
    target: str
    dry_run: bool
    success: bool
    message: str


@dataclass(frozen=True)
class BlockerPlan:
    """Execution plan for blocker adapters.

    Test mode always forces dry-run behavior. Real system actions require both
    test_mode=False and dry_run=False.
    """

    test_mode: bool = True
    dry_run: bool = True

    def __post_init__(self) -> None:
        if self.test_mode and not self.dry_run:
            object.__setattr__(self, "dry_run", True)

    def require_real_system_action_allowed(self) -> None:
        """Reject real system actions unless the plan explicitly allows them."""
        if self.test_mode or self.dry_run:
            raise RuntimeError("real system action blocked by test/dry-run mode")


def test_mode_readiness_checks() -> tuple[EnforcementReadinessCheck, ...]:
    """Return read-only readiness facts for safe test/dry-run behavior."""
    return (
        EnforcementReadinessCheck(
            key="test_mode_forces_dry_run",
            label="Test Mode dry-run guard",
            ready=True,
            detail="Test Mode forces blocker plans into dry-run behavior.",
        ),
        EnforcementReadinessCheck(
            key="real_action_guard",
            label="Real action guard",
            ready=True,
            detail="Blocker plans reject real actions while test/dry-run is active.",
        ),
    )
