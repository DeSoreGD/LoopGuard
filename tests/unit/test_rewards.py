from __future__ import annotations

import pytest

from selfboss.core.models import (
    RewardEventType,
    RewardLedgerEvent,
    TaskKind,
)
from selfboss.core.rewards import RewardService


def test_default_rewards_by_task_kind() -> None:
    rewards = RewardService()

    assert rewards.reward_minutes_for_kind(TaskKind.TINY) == 5
    assert rewards.reward_minutes_for_kind(TaskKind.NORMAL) == 15
    assert rewards.reward_minutes_for_kind(TaskKind.IMPORTANT) == 30
    assert rewards.reward_minutes_for_kind(TaskKind.MAIN) == 30


def test_explicit_task_reward_overrides_kind_default(make_task, fixed_now) -> None:
    rewards = RewardService()
    task = make_task(kind=TaskKind.TINY, reward_minutes=12)

    event = rewards.complete_task(task, occurred_at=fixed_now)

    assert event.event_type is RewardEventType.TASK_COMPLETED
    assert event.minutes_delta == 12
    assert event.seconds_delta == 12 * 60
    assert event.task_id == task.id


def test_balance_is_derived_from_ledger_events(make_task, fixed_now) -> None:
    rewards = RewardService()
    ledger = [
        rewards.complete_task(
            make_task(task_id=1, kind=TaskKind.NORMAL),
            occurred_at=fixed_now,
        ),
        rewards.complete_task(
            make_task(task_id=2, kind=TaskKind.TINY),
            occurred_at=fixed_now,
        ),
        RewardLedgerEvent(
            event_type=RewardEventType.HIGH_TIME_SPENT,
            minutes_delta=-10,
            seconds_delta=-600,
            occurred_at=fixed_now,
            reason="high_mode",
        ),
    ]

    assert rewards.balance(ledger) == 10
    assert rewards.balance_seconds(ledger) == 600


def test_spending_more_than_balance_is_rejected(make_task, fixed_now) -> None:
    rewards = RewardService()
    ledger = [
        rewards.complete_task(make_task(kind=TaskKind.TINY), occurred_at=fixed_now)
    ]

    with pytest.raises(ValueError, match="cannot spend more"):
        rewards.spend_high_minutes(ledger, minutes=10, occurred_at=fixed_now)


def test_spending_high_minutes_creates_negative_ledger_event(make_task, fixed_now) -> None:
    rewards = RewardService()
    ledger = [
        rewards.complete_task(make_task(kind=TaskKind.NORMAL), occurred_at=fixed_now)
    ]

    event = rewards.spend_high_minutes(ledger, minutes=10, occurred_at=fixed_now)

    assert event.event_type is RewardEventType.HIGH_TIME_SPENT
    assert event.minutes_delta == -10
    assert event.seconds_delta == -600
    assert rewards.balance([*ledger, event]) == 5
    assert rewards.balance_seconds([*ledger, event]) == 300


def test_spending_high_seconds_creates_precise_negative_event(
    make_task,
    fixed_now,
) -> None:
    rewards = RewardService()
    ledger = [
        rewards.complete_task(make_task(kind=TaskKind.NORMAL), occurred_at=fixed_now)
    ]

    event = rewards.spend_high_seconds(ledger, seconds=650, occurred_at=fixed_now)

    assert event.event_type is RewardEventType.HIGH_TIME_SPENT
    assert event.minutes_delta == -10
    assert event.seconds_delta == -650
    assert rewards.balance_seconds([*ledger, event]) == 250
