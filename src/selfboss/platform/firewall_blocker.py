"""Firewall blocker stub.

Firewall enforcement is intentionally deferred. This module exists to document
the future adapter boundary without adding fake production logic or changing
Windows firewall rules.
"""

from __future__ import annotations

from selfboss.platform.test_mode import BlockerActionResult, BlockerPlan


class FirewallBlocker:
    """Documented stub for future firewall enforcement."""

    def block(self, target: str, plan: BlockerPlan) -> BlockerActionResult:
        """Return a TODO diagnostic without applying firewall changes."""
        return BlockerActionResult(
            action="firewall_block",
            target=target,
            dry_run=True,
            success=False,
            message=(
                "TODO: firewall enforcement is deferred; no firewall rules were changed"
            ),
        )
