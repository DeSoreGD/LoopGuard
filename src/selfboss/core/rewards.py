"""Pure reward ledger logic."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from selfboss.core.models import (
    RewardEventType,
    RewardLedgerEvent,
    RewardPolicy,
    Task,
    TaskKind,
)


class RewardService:
    """Calculate and create reward ledger events without persistence."""

    def __init__(self, policy: RewardPolicy | None = None) -> None:
        self.policy = policy or RewardPolicy()

    def reward_minutes_for_kind(self, kind: TaskKind) -> int:
        """Return default reward minutes for a task kind."""
        if kind is TaskKind.TINY:
            return self.policy.tiny_minutes
        if kind is TaskKind.NORMAL:
            return self.policy.normal_minutes
        if kind is TaskKind.IMPORTANT:
            return self.policy.important_minutes
        if kind is TaskKind.MAIN:
            return self.policy.main_minutes

        raise ValueError(f"Unsupported task kind: {kind}")

    def reward_minutes_for_task(self, task: Task) -> int:
        """Return explicit task reward minutes or the default for its kind."""
        if task.reward_minutes > 0:
            return task.reward_minutes
        return self.reward_minutes_for_kind(task.kind)

    def reward_seconds_for_task(self, task: Task) -> int:
        """Return task reward seconds from user-facing reward minutes."""
        return self.reward_minutes_for_task(task) * 60

    def complete_task(self, task: Task, *, occurred_at: datetime) -> RewardLedgerEvent:
        """Create the ledger event for completing a task."""
        minutes = self.reward_minutes_for_task(task)
        seconds = minutes * 60
        return RewardLedgerEvent(
            event_type=RewardEventType.TASK_COMPLETED,
            minutes_delta=minutes,
            occurred_at=occurred_at,
            seconds_delta=seconds,
            task_id=task.id,
            reason=f"completed:{task.kind.value}",
        )

    def spend_high_seconds(
        self,
        ledger: Iterable[RewardLedgerEvent],
        *,
        seconds: int,
        occurred_at: datetime,
    ) -> RewardLedgerEvent:
        """Create a negative ledger event for HIGH-mode seconds."""
        if seconds <= 0:
            raise ValueError("seconds must be greater than zero")

        balance = self.balance_seconds(ledger)
        if seconds > balance:
            raise ValueError("cannot spend more reward seconds than the balance")

        return RewardLedgerEvent(
            event_type=RewardEventType.HIGH_TIME_SPENT,
            minutes_delta=-(seconds // 60),
            seconds_delta=-seconds,
            occurred_at=occurred_at,
            reason="high_mode",
        )

    def spend_high_minutes(
        self,
        ledger: Iterable[RewardLedgerEvent],
        *,
        minutes: int,
        occurred_at: datetime,
    ) -> RewardLedgerEvent:
        """Create a negative ledger event for HIGH-mode time."""
        if minutes <= 0:
            raise ValueError("minutes must be greater than zero")

        balance = self.balance(ledger)
        if minutes > balance:
            raise ValueError("cannot spend more reward minutes than the balance")

        return RewardLedgerEvent(
            event_type=RewardEventType.HIGH_TIME_SPENT,
            minutes_delta=-minutes,
            seconds_delta=-(minutes * 60),
            occurred_at=occurred_at,
            reason="high_mode",
        )

    def balance(self, ledger: Iterable[RewardLedgerEvent]) -> int:
        """Calculate reward balance from ledger events."""
        return sum(event.minutes_delta for event in ledger)

    def balance_seconds(self, ledger: Iterable[RewardLedgerEvent]) -> int:
        """Calculate reward seconds from ledger events."""
        return sum(
            event.seconds_delta
            if event.seconds_delta != 0
            else event.minutes_delta * 60
            for event in ledger
        )
