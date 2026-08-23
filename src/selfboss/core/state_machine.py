"""Pure access state machine for LoopGuard."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Iterable

from selfboss.core.models import (
    AccessLevel,
    AccessRuntimeState,
    RewardLedgerEvent,
    StateTransition,
    SurrenderState,
    TaskKind,
)
from selfboss.core.rewards import RewardService


class AccessStateMachine:
    """Make deterministic LOW, MEDIUM, HIGH, bad-day, and surrender decisions."""

    def __init__(
        self,
        *,
        surrender_delay: timedelta = timedelta(hours=12),
        reward_service: RewardService | None = None,
    ) -> None:
        self.surrender_delay = surrender_delay
        self.reward_service = reward_service or RewardService()

    def initial_state(self) -> AccessRuntimeState:
        """Return the default access state for a new day."""
        return AccessRuntimeState()

    def complete_task(
        self, state: AccessRuntimeState, *, task_kind: TaskKind
    ) -> StateTransition:
        """Apply access effects of task completion."""
        if task_kind is not TaskKind.MAIN:
            return StateTransition(state=state)

        next_level = state.access_level
        if state.access_level is AccessLevel.LOW:
            next_level = AccessLevel.MEDIUM

        return StateTransition(
            state=replace(
                state,
                access_level=next_level,
                medium_unlocked=True,
            )
        )

    def enable_bad_day_mode(self, state: AccessRuntimeState) -> StateTransition:
        """Enable bad-day mode without granting reward access."""
        if state.bad_day_mode:
            return StateTransition(state=state)
        return StateTransition(state=replace(state, bad_day_mode=True))

    def start_high(
        self,
        state: AccessRuntimeState,
        ledger: Iterable[RewardLedgerEvent],
        *,
        minutes: int,
        occurred_at: datetime,
    ) -> StateTransition:
        """Enter HIGH mode by spending earned reward minutes."""
        if state.access_level is AccessLevel.HIGH:
            raise ValueError("HIGH mode is already active")

        spend_event = self.reward_service.spend_high_minutes(
            ledger,
            minutes=minutes,
            occurred_at=occurred_at,
        )
        next_state = replace(
            state,
            access_level=AccessLevel.HIGH,
            high_started_at=occurred_at,
            high_minutes_total=minutes,
        )
        return StateTransition(state=next_state, reward_events=(spend_event,))

    def expire_high_if_needed(
        self, state: AccessRuntimeState, *, occurred_at: datetime
    ) -> StateTransition:
        """Return from HIGH after the configured HIGH minutes have elapsed."""
        if state.access_level is not AccessLevel.HIGH:
            return StateTransition(state=state)
        if state.high_started_at is None or state.high_minutes_total <= 0:
            return StateTransition(state=self._fallback_from_high(state))

        elapsed = occurred_at - state.high_started_at
        if elapsed < timedelta(minutes=state.high_minutes_total):
            return StateTransition(state=state)

        return StateTransition(state=self._fallback_from_high(state))

    def request_surrender(
        self, surrender_state: SurrenderState, *, occurred_at: datetime
    ) -> SurrenderState:
        """Record a surrender request without unlocking immediately."""
        if surrender_state.requested_at is not None:
            return surrender_state
        return SurrenderState(requested_at=occurred_at)

    def can_surrender(
        self, surrender_state: SurrenderState, *, occurred_at: datetime
    ) -> bool:
        """Return whether delayed surrender is eligible."""
        if surrender_state.requested_at is None:
            return False
        if surrender_state.surrendered_at is not None:
            return True
        return occurred_at >= surrender_state.requested_at + self.surrender_delay

    def surrender(
        self, surrender_state: SurrenderState, *, occurred_at: datetime
    ) -> SurrenderState:
        """Complete delayed surrender after the configured delay."""
        if not self.can_surrender(surrender_state, occurred_at=occurred_at):
            raise ValueError("surrender delay has not elapsed")
        return replace(surrender_state, surrendered_at=occurred_at)

    def _fallback_from_high(self, state: AccessRuntimeState) -> AccessRuntimeState:
        fallback_level = AccessLevel.MEDIUM if state.medium_unlocked else AccessLevel.LOW
        return replace(
            state,
            access_level=fallback_level,
            high_started_at=None,
            high_minutes_total=0,
        )
