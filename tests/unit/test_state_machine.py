from __future__ import annotations

from datetime import timedelta

import pytest

from selfboss.core.models import (
    AccessLevel,
    AccessRuntimeState,
    SurrenderState,
    TaskKind,
)
from selfboss.core.rewards import RewardService
from selfboss.core.state_machine import AccessStateMachine


def test_initial_state_starts_in_low() -> None:
    machine = AccessStateMachine()

    state = machine.initial_state()

    assert state.access_level is AccessLevel.LOW
    assert state.medium_unlocked is False


def test_main_task_completion_unlocks_medium() -> None:
    machine = AccessStateMachine()

    transition = machine.complete_task(
        machine.initial_state(),
        task_kind=TaskKind.MAIN,
    )

    assert transition.state.access_level is AccessLevel.MEDIUM
    assert transition.state.medium_unlocked is True


def test_non_main_task_completion_does_not_unlock_medium() -> None:
    machine = AccessStateMachine()

    transition = machine.complete_task(
        machine.initial_state(),
        task_kind=TaskKind.NORMAL,
    )

    assert transition.state.access_level is AccessLevel.LOW
    assert transition.state.medium_unlocked is False


def test_high_cannot_start_without_reward_balance(fixed_now) -> None:
    machine = AccessStateMachine()

    with pytest.raises(ValueError, match="cannot spend more"):
        machine.start_high(
            machine.initial_state(),
            [],
            minutes=1,
            occurred_at=fixed_now,
        )


def test_high_starts_by_spending_reward_minutes_from_ledger(
    make_task, fixed_now
) -> None:
    rewards = RewardService()
    machine = AccessStateMachine(reward_service=rewards)
    ledger = [
        rewards.complete_task(make_task(kind=TaskKind.NORMAL), occurred_at=fixed_now)
    ]

    transition = machine.start_high(
        machine.initial_state(),
        ledger,
        minutes=10,
        occurred_at=fixed_now,
    )

    assert transition.state.access_level is AccessLevel.HIGH
    assert transition.state.high_started_at == fixed_now
    assert transition.state.high_minutes_total == 10
    assert transition.reward_events[0].minutes_delta == -10
    assert rewards.balance([*ledger, *transition.reward_events]) == 5


def test_high_expiry_falls_back_to_medium_when_unlocked(fixed_now) -> None:
    machine = AccessStateMachine()
    state = AccessRuntimeState(
        access_level=AccessLevel.HIGH,
        medium_unlocked=True,
        high_started_at=fixed_now,
        high_minutes_total=10,
    )

    transition = machine.expire_high_if_needed(
        state,
        occurred_at=fixed_now + timedelta(minutes=10),
    )

    assert transition.state.access_level is AccessLevel.MEDIUM
    assert transition.state.high_started_at is None
    assert transition.state.high_minutes_total == 0


def test_high_expiry_falls_back_to_low_when_medium_is_locked(fixed_now) -> None:
    machine = AccessStateMachine()
    state = AccessRuntimeState(
        access_level=AccessLevel.HIGH,
        medium_unlocked=False,
        high_started_at=fixed_now,
        high_minutes_total=10,
    )

    transition = machine.expire_high_if_needed(
        state,
        occurred_at=fixed_now + timedelta(minutes=11),
    )

    assert transition.state.access_level is AccessLevel.LOW


def test_high_does_not_expire_before_elapsed_minutes(fixed_now) -> None:
    machine = AccessStateMachine()
    state = AccessRuntimeState(
        access_level=AccessLevel.HIGH,
        medium_unlocked=True,
        high_started_at=fixed_now,
        high_minutes_total=10,
    )

    transition = machine.expire_high_if_needed(
        state,
        occurred_at=fixed_now + timedelta(minutes=9),
    )

    assert transition.state == state


def test_bad_day_mode_does_not_grant_high_access() -> None:
    machine = AccessStateMachine()

    transition = machine.enable_bad_day_mode(machine.initial_state())

    assert transition.state.bad_day_mode is True
    assert transition.state.access_level is AccessLevel.LOW
    assert transition.state.medium_unlocked is False


def test_surrender_is_delayed_until_configured_duration(fixed_now) -> None:
    machine = AccessStateMachine(surrender_delay=timedelta(hours=12))
    requested = machine.request_surrender(SurrenderState(), occurred_at=fixed_now)

    assert requested.requested_at == fixed_now
    assert machine.can_surrender(
        requested,
        occurred_at=fixed_now + timedelta(hours=11, minutes=59),
    ) is False
    assert machine.can_surrender(
        requested,
        occurred_at=fixed_now + timedelta(hours=12),
    ) is True


def test_surrender_before_delay_is_rejected(fixed_now) -> None:
    machine = AccessStateMachine()
    requested = machine.request_surrender(SurrenderState(), occurred_at=fixed_now)

    with pytest.raises(ValueError, match="delay has not elapsed"):
        machine.surrender(requested, occurred_at=fixed_now + timedelta(hours=1))


def test_surrender_after_delay_records_completion_time(fixed_now) -> None:
    machine = AccessStateMachine()
    requested = machine.request_surrender(SurrenderState(), occurred_at=fixed_now)
    surrender_time = fixed_now + timedelta(hours=12)

    completed = machine.surrender(requested, occurred_at=surrender_time)

    assert completed.requested_at == fixed_now
    assert completed.surrendered_at == surrender_time
