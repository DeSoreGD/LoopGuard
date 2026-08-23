from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from selfboss.core.models import (
    AccessLevel,
    EnforcementMode,
    SurrenderState,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
import selfboss.core.use_cases as use_cases_module
from selfboss.core.rewards import RewardService
from selfboss.core.state_machine import AccessStateMachine
from selfboss.core.use_cases import (
    BROWSER_HEARTBEAT_FILE_NAME,
    DAILY_RECREATION_CAP_DEFAULT_MINUTES,
    DAILY_RECREATION_CAP_MAX_MINUTES,
    DAILY_RECREATION_CAP_MIN_MINUTES,
    END_DAY_CONFIRM_DELAY_SECONDS,
    DAY_CLOSE_REVIEW_NORMAL,
    DAY_CLOSE_REVIEW_RECOVERY,
    HIGH_BROWSER_BLOCKING_NOT_READY,
    HIGH_COOLDOWN_SECONDS,
    HIGH_DAILY_MAX_MINUTES,
    HIGH_SESSION_MAX_MINUTES,
    PERSONAL_TRIAL_QA_STEP_DEFINITIONS,
    SURRENDER_DELAY_SECONDS,
    SURRENDER_STRICTNESS_DELAYS,
    SelfBossAppService,
    START_DAY_BROWSER_REQUIRED,
    STARTER_RULE_PRESETS,
    REST_TOKEN_PRE_START_ONLY,
    TASK_COMPLETION_CLAIM_DELAY_SECONDS,
    TASK_COMPLETION_CLAIM_ALREADY_PENDING,
    attempt_local_day,
    canonical_rule_target_for_display,
    canonical_task_allowed_url,
    format_attempt_local_time,
    high_warning_threshold_seconds,
    rule_duplicate_equivalence_key,
    suggest_escape_family_for_rule,
    utility_leakage_warning_for_rule,
)
from selfboss.data.db import initialize_database
from selfboss.data.repositories import (
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.platform import process_blocker as process_blocker_module
from selfboss.platform.hosts_blocker import HostsBlocker
from selfboss.platform.process_blocker import ProcessBlocker, is_protected_process_name


class CountingHostsBlocker(HostsBlocker):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.write_attempts = 0

    def _write_hosts(self, content: str) -> None:
        self.write_attempts += 1
        super()._write_hosts(content)


class PermissionDeniedCountingHostsBlocker(HostsBlocker):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.write_attempts = 0

    def _write_hosts(self, content: str) -> None:
        self.write_attempts += 1
        raise PermissionError("denied")


def _complete_task_after_claim_delay(service: SelfBossAppService, task_id: int):
    task = service.tasks.get(task_id)
    if (
        task is not None
        and task.planning_status is TaskPlanningStatus.PLANNED
        and task.status is TaskStatus.PENDING
    ):
        claimed = service.claim_task_done(task_id).task
        service.tasks.claim_completion(
            task_id,
            claimed_at=claimed.completion_claimed_at or service._now().isoformat(),
            available_at=service._now().isoformat(),
        )
    return service.confirm_task_done(task_id)


def test_main_task_to_medium_then_high_reward_flow(make_task, fixed_now) -> None:
    rewards = RewardService()
    machine = AccessStateMachine(reward_service=rewards)
    task = make_task(kind=TaskKind.MAIN)

    medium_transition = machine.complete_task(
        machine.initial_state(),
        task_kind=task.kind,
    )
    earned = rewards.complete_task(task, occurred_at=fixed_now)
    high_transition = machine.start_high(
        medium_transition.state,
        [earned],
        minutes=15,
        occurred_at=fixed_now,
    )

    assert medium_transition.state.access_level is AccessLevel.MEDIUM
    assert rewards.balance([earned]) == 30
    assert high_transition.state.access_level is AccessLevel.HIGH
    assert rewards.balance([earned, *high_transition.reward_events]) == 15


def test_bad_day_mode_does_not_unlock_reward_access() -> None:
    machine = AccessStateMachine()

    transition = machine.enable_bad_day_mode(machine.initial_state())

    assert transition.state.bad_day_mode is True
    assert transition.state.access_level is AccessLevel.LOW
    assert transition.state.medium_unlocked is False


def test_surrender_flow_requires_delay(fixed_now) -> None:
    machine = AccessStateMachine(surrender_delay=timedelta(hours=12))
    requested = machine.request_surrender(SurrenderState(), occurred_at=fixed_now)

    before_delay = machine.can_surrender(
        requested,
        occurred_at=fixed_now + timedelta(hours=6),
    )
    after_delay = machine.surrender(
        requested,
        occurred_at=fixed_now + timedelta(hours=12),
    )

    assert before_delay is False
    assert after_delay.surrendered_at == fixed_now + timedelta(hours=12)


def test_app_service_creates_task_with_ui_fields(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        task = service.create_task(
            title="  Read PySide docs  ",
            kind=TaskKind.IMPORTANT,
            reward_minutes_override=20,
            allowed_url="  https://doc.qt.io/  ",
        )

        assert task.title == "Read PySide docs"
        assert task.kind is TaskKind.IMPORTANT
        assert task.reward_minutes == 20
        assert task.allowed_url == "https://doc.qt.io/"
        assert task.planning_status is TaskPlanningStatus.PLANNED
        assert service.list_tasks() == [task]


def test_allowed_url_is_canonicalized_and_validated_before_day_starts(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        task = service.create_task(
            title="Watch one tutorial",
            kind=TaskKind.NORMAL,
            allowed_url=" HTTPS://WWW.YOUTUBE.COM:443/watch?v=abc123#section ",
        )

        assert task.allowed_url == "https://www.youtube.com/watch?v=abc123"
        assert (
            canonical_task_allowed_url("http://Example.test:80/path?q=1#frag")
            == "http://example.test/path?q=1"
        )

        try:
            service.create_task(
                title="Invalid task URL",
                kind=TaskKind.NORMAL,
                allowed_url="chrome://extensions",
            )
        except ValueError as error:
            assert "Allowed URL must start with http:// or https://" in str(error)
        else:
            raise AssertionError("invalid allowed URL should be rejected")


def test_start_day_marks_day_started_and_is_idempotent(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    later = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)

        before = service.dashboard_snapshot()
        service.start_day()
        now = later
        after_second_click = service.start_day()
        day = service.day_state.get()

        assert before.day_started is False
        assert before.day_status_label == "Planning"
        assert before.can_start_day is True
        assert after_second_click.day_started is True
        assert after_second_click.day_status_label == "Day started"
        assert after_second_click.can_start_day is False
        assert day.day_started_at == "2026-05-08T09:00:00+00:00"


def test_production_start_day_requires_browser_setup(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("LOOPGUARD_APP_MODE", "production")
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        _create_required_main(service)

        try:
            service.start_day()
        except ValueError as error:
            assert str(error) == START_DAY_BROWSER_REQUIRED
        else:
            raise AssertionError("production Start Day should require Chrome setup")

        _write_browser_heartbeat(
            test_settings,
            now,
            incognito_allowed=True,
            browser_blocking_available=True,
        )
        assert service.start_day().day_started is True


def test_soft_start_defaults_and_can_be_disabled(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, soft_start_enabled=None)
        _create_required_main(service)

        default_snapshot = service.dashboard_snapshot()
        service.set_soft_start_enabled(False)
        disabled = service.dashboard_snapshot()
        service.set_soft_start_enabled(True)
        service.set_soft_start_duration_minutes(0)
        zero_minutes = service.start_day()

        assert default_snapshot.soft_start_enabled is True
        assert default_snapshot.soft_start_duration_minutes == 15
        assert default_snapshot.soft_start_active is False
        assert disabled.soft_start_enabled is False
        assert zero_minutes.soft_start_enabled is True
        assert zero_minutes.soft_start_duration_minutes == 0
        assert zero_minutes.soft_start_active is False


def test_daily_recreation_cap_defaults_and_persists(
    tmp_path,
    test_settings,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings)

        assert (
            service.get_daily_recreation_cap_minutes()
            == DAILY_RECREATION_CAP_DEFAULT_MINUTES
        )
        assert service.dashboard_snapshot().high_daily_cap_minutes == (
            DAILY_RECREATION_CAP_DEFAULT_MINUTES
        )

        updated = service.set_daily_recreation_cap_minutes(
            DAILY_RECREATION_CAP_MAX_MINUTES
        )

        assert updated == DAILY_RECREATION_CAP_MAX_MINUTES

    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings)

        assert service.get_daily_recreation_cap_minutes() == (
            DAILY_RECREATION_CAP_MAX_MINUTES
        )
        assert service.get_high_access_options().daily_cap_minutes == (
            DAILY_RECREATION_CAP_MAX_MINUTES
        )


def test_daily_recreation_cap_rejects_out_of_range_values(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        for minutes in (
            DAILY_RECREATION_CAP_MIN_MINUTES - 1,
            DAILY_RECREATION_CAP_MAX_MINUTES + 1,
        ):
            try:
                service.set_daily_recreation_cap_minutes(minutes)
            except ValueError as error:
                assert "between 15 and 300" in str(error)
            else:
                raise AssertionError("daily Recreation cap should reject range")

        assert (
            service.get_daily_recreation_cap_minutes()
            == DAILY_RECREATION_CAP_DEFAULT_MINUTES
        )


def test_start_day_rejects_without_pending_planned_main(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=HostsBlocker(hosts_path=tmp_path / "hosts"),
        )
        normal = service.create_task(title="Normal task", kind=TaskKind.NORMAL)

        try:
            service.start_day()
        except ValueError as error:
            assert "Add a planned MAIN task" in str(error)
        else:
            raise AssertionError("Start Day should require a pending planned MAIN")

        snapshot = service.dashboard_snapshot()
        assert normal.planning_status is TaskPlanningStatus.PLANNED
        assert snapshot.day_started is False
        assert snapshot.can_start_day is False
        assert snapshot.start_day_unavailable_reason == (
            "Add a planned MAIN task before starting the day."
        )


def test_start_day_rejects_completed_main_and_unplanned_main(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        completed_main = service.create_task(title="Done main", kind=TaskKind.MAIN)
        service.tasks.update_status(completed_main.id, TaskStatus.DONE)

        try:
            service.start_day()
        except ValueError as error:
            assert "Add a planned MAIN task" in str(error)
        else:
            raise AssertionError("completed MAIN should not satisfy Start Day")

        service.day_state.start_day("2026-05-08T09:00:00+00:00")
        try:
            service.create_task(title="Late main", kind=TaskKind.MAIN)
        except ValueError as error:
            assert "MAIN must be planned before Start Day" in str(error)
        else:
            raise AssertionError("MAIN after Start Day should be rejected")


def test_end_day_requires_started_day(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.create_task(title="Main task", kind=TaskKind.MAIN)

        try:
            service.end_day()
        except ValueError as error:
            assert "Start Day before ending the day" in str(error)
        else:
            raise AssertionError("End Day should reject before Start Day")


def test_end_day_requires_completed_planned_main(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        snapshot = service.dashboard_snapshot()
        try:
            service.end_day()
        except ValueError as error:
            assert "End Day is available after completing today's MAIN task" in str(
                error
            )
        else:
            raise AssertionError("End Day should reject before MAIN completion")

    assert snapshot.can_end_day is False
    assert snapshot.end_day_unavailable_reason == (
        "End Day is available after completing today's MAIN task."
    )


def test_end_day_rejects_active_day_without_planned_main(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.tasks.delete(main.id)

        snapshot = service.dashboard_snapshot()
        try:
            service.end_day()
        except ValueError as error:
            assert "End Day is available after completing today's MAIN task" in str(
                error
            )
        else:
            raise AssertionError("End Day should reject without a planned MAIN")

    assert snapshot.main_task is None
    assert snapshot.can_end_day is False


def test_recovery_close_today_closes_without_completed_main(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: now)
        service.set_surrender_strictness("high")
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        rewards_before = service.rewards.list()

        try:
            service.end_day()
        except ValueError as error:
            assert "End Day is available after completing today's MAIN task" in str(
                error
            )
        else:
            raise AssertionError("End Day should reject before MAIN completion")

        snapshot = service.recovery_close_today()
        updated = service.set_surrender_strictness("low")
        main_after = service.tasks.get(main.id)

        assert snapshot.day_closed is True
        assert snapshot.day_status_label == "Day ended"
        assert snapshot.day_ended_at == now.isoformat()
        assert snapshot.reward_balance_seconds == 0
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.can_end_day is False
        assert snapshot.can_recovery_close_today is False
        assert main_after.status is TaskStatus.PENDING
        assert main_after.planning_status is TaskPlanningStatus.PLANNED
        assert service.rewards.list() == rewards_before
        assert updated == "low"


def test_end_day_closes_started_day_and_preserves_state(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        tiny = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        rewards_before = service.rewards.list()

        snapshot = service.end_day()

        assert snapshot.day_closed is True
        assert snapshot.day_status_label == "Day ended"
        assert snapshot.day_ended_at == now.isoformat()
        assert snapshot.can_end_day is False
        assert snapshot.reward_balance_seconds == 30 * 60
        assert "Planned: 1 / 2 done." in snapshot.day_summary_label
        assert service.tasks.get(tiny.id) is not None
        assert service.rewards.list() == rewards_before

        try:
            service.end_day()
        except ValueError as error:
            assert "already ended" in str(error)
        else:
            raise AssertionError("End Day should reject an already closed day")


def test_closed_day_blocks_completion_high_and_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        tiny = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.end_day()
        rewards_before = service.rewards.list()

        try:
            _complete_task_after_claim_delay(service, tiny.id)
        except ValueError as error:
            assert "End Day" in str(error)
        else:
            raise AssertionError("closed day should reject task completion")

        try:
            service.start_high_access(5, "planned recreation")
        except ValueError as error:
            assert "End Day" in str(error)
        else:
            raise AssertionError("closed day should reject HIGH access")

        try:
            service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        except ValueError as error:
            assert "End Day" in str(error)
        else:
            raise AssertionError("closed day should reject planned-use pass")

        options = service.get_high_access_options()
        snapshot = service.dashboard_snapshot()

        assert service.tasks.get(main.id) is not None
        assert service.tasks.get(tiny.id).status is TaskStatus.PENDING
        assert service.rewards.list() == rewards_before
        assert options.can_start_high is False
        assert "End Day" in options.unavailable_reason
        assert snapshot.high_active is False


def test_end_day_closes_active_high_and_active_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_high_access(5, "planned recreation")

        high_snapshot = service.end_day()

        assert high_snapshot.day_closed is True
        assert high_snapshot.high_active is False
        assert high_snapshot.reward_balance_seconds == 30 * 60
        assert service.high_sessions.active_for_day("2026-05-08") is None

    with initialize_database(tmp_path / "selfboss-pass.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)

        pass_snapshot = service.end_day()

        assert pass_snapshot.day_closed is True
        assert service.get_active_planned_use_pass() is None
        assert service.list_recent_planned_use_passes(limit=1)[0].status == "ended"
        assert service.preview_blocking().active_planned_use_pass is None


def test_recovery_close_today_reuses_high_and_pass_cleanup(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss-high.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.day_state.add_reward_seconds(5 * 60)
        service.start_high_access(5, "planned recovery close test")

        high_snapshot = service.recovery_close_today()

        assert high_snapshot.day_closed is True
        assert high_snapshot.high_active is False
        assert high_snapshot.access_level is AccessLevel.LOW
        assert high_snapshot.reward_balance_seconds == 5 * 60
        assert service.high_sessions.active_for_day("2026-05-08") is None

    with initialize_database(tmp_path / "selfboss-pass.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)

        pass_snapshot = service.recovery_close_today()

        assert pass_snapshot.day_closed is True
        assert service.get_active_planned_use_pass() is None
        assert service.list_recent_planned_use_passes(limit=1)[0].status == "ended"
        assert service.preview_blocking().active_planned_use_pass is None


def test_day_close_review_summarizes_normal_end_day_inputs(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.create_task(title="Tiny task", kind=TaskKind.TINY)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.create_task(title="Unplanned task", kind=TaskKind.TINY)
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_high_access(10, "planned recreation")
        service.log_manual_rule_attempt(rule.id)

        review = service.get_day_close_review(DAY_CLOSE_REVIEW_NORMAL)

        assert review.close_type == DAY_CLOSE_REVIEW_NORMAL
        assert review.title == "Day closed"
        assert review.main_completed is True
        assert review.planned_done_count == 1
        assert review.planned_task_count == 2
        assert review.unplanned_done_count == 0
        assert review.unplanned_task_count == 1
        assert review.recreation_used_seconds == 10 * 60
        assert review.recent_attempt_count == 1
        assert review.recent_family_path == "video"
        assert review.next_action == "Plan tomorrow when ready."


def test_day_close_review_summarizes_recovery_close_without_shame_copy(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        tiny = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        _complete_task_after_claim_delay(service, tiny.id)
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)

        review = service.get_day_close_review(DAY_CLOSE_REVIEW_RECOVERY)
        review_text = " ".join(
            str(value)
            for value in (
                review.title,
                review.next_action,
                review.recent_family_path,
                review.active_planned_use_pass_target or "",
            )
        ).lower()

        assert review.close_type == DAY_CLOSE_REVIEW_RECOVERY
        assert review.title == "Today closed in Recovery"
        assert review.main_completed is False
        assert review.planned_done_count == 1
        assert review.planned_task_count == 2
        assert review.active_planned_use_pass_target == "youtube.com"
        assert review.active_planned_use_pass_type == "site"
        assert review.next_action == "Plan a smaller anchor task next time."
        for shame_term in ("failure", "relapse", "weak", "addicted", "punish"):
            assert shame_term not in review_text


def test_day_close_review_rejects_unknown_close_type(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        try:
            service.get_day_close_review("unknown")
        except ValueError as error:
            assert "Unsupported day close review type" in str(error)
        else:
            raise AssertionError("unknown day close review type should be rejected")


def test_closed_day_resets_on_next_local_day(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 17, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.end_day()

        now = now + timedelta(days=1)
        snapshot = service.dashboard_snapshot()

        assert snapshot.day_closed is False
        assert snapshot.day_ended_at is None
        assert snapshot.day_status_label == "Planning"


def test_start_day_activates_soft_start_and_blocks_completion(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            soft_start_enabled=True,
        )
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)

        started = service.start_day()
        try:
            _complete_task_after_claim_delay(service, task.id)
        except ValueError as error:
            assert "Soft Start active" in str(error)
        else:
            raise AssertionError("task completion should reject during Soft Start")

        snapshot = service.dashboard_snapshot()

        assert started.day_started is True
        assert started.day_started_at == "2026-05-08T09:00:00+00:00"
        assert started.soft_start_active is True
        assert started.soft_start_remaining_seconds == 15 * 60
        assert service.tasks.get(task.id).status is TaskStatus.PENDING
        assert service.rewards.list() == []
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.effective_restriction_state == "low"


def test_soft_start_expires_to_normal_completion_and_medium_unlock(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            soft_start_enabled=True,
        )
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        now = start + timedelta(minutes=15)
        result = _complete_task_after_claim_delay(service, task.id)
        snapshot = service.dashboard_snapshot()

        assert result.reward_entry is not None
        assert result.reward_entry.seconds_delta == 30 * 60
        assert snapshot.soft_start_active is False
        assert snapshot.reward_balance_seconds == 30 * 60
        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.effective_restriction_state == "medium"


def test_soft_start_settings_are_locked_after_start_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            soft_start_enabled=True,
        )
        service.set_soft_start_duration_minutes(5)
        _create_required_main(service)
        service.start_day()

        for action in (
            lambda: service.set_soft_start_enabled(False),
            lambda: service.set_soft_start_duration_minutes(15),
        ):
            try:
                action()
            except ValueError as error:
                assert "Soft Start can only be changed before Start Day" in str(error)
            else:
                raise AssertionError("Soft Start settings should lock after Start Day")

        assert service.get_soft_start_enabled() is True
        assert service.get_soft_start_duration_minutes() == 5


def test_soft_start_duration_is_captured_for_active_day(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            soft_start_enabled=True,
        )
        service.set_soft_start_duration_minutes(5)
        _create_required_main(service)
        service.start_day()

        now = start + timedelta(minutes=5)
        expired = service.dashboard_snapshot()
        service.app_settings.set_soft_start_duration_minutes(15)
        still_expired = service.dashboard_snapshot()

        assert expired.soft_start_active is False
        assert expired.soft_start_duration_minutes == 5
        assert still_expired.soft_start_active is False
        assert still_expired.soft_start_duration_minutes == 5
        assert service.get_soft_start_duration_minutes() == 15

        now = start + timedelta(days=1)
        next_planning = service.dashboard_snapshot()
        assert next_planning.day_started is False

        service.set_soft_start_duration_minutes(15)
        _create_required_main(service)
        next_started = service.start_day()

        assert next_started.soft_start_active is True
        assert next_started.soft_start_duration_minutes == 15
        assert next_started.soft_start_remaining_seconds == 15 * 60


def test_bad_day_and_surrender_are_unavailable_during_soft_start(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            soft_start_enabled=True,
        )
        service.set_surrender_strictness("low")
        _create_required_main(service)
        service.start_day()

        for action, expected in (
            (service.activate_bad_day_mode, "Bad Day Mode is unavailable"),
            (service.activate_surrender, "Surrender is unavailable"),
        ):
            try:
                action()
            except ValueError as error:
                assert expected in str(error)
            else:
                raise AssertionError("Soft Start should block safe-mode activation")

        snapshot = service.dashboard_snapshot()
        assert snapshot.bad_day_active_today is False
        assert snapshot.surrender_active_today is False
        assert snapshot.surrender_available is False


def test_surrender_delay_starts_after_soft_start_end(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            soft_start_enabled=True,
        )
        service.set_surrender_strictness("medium")
        _create_required_main(service)
        service.start_day()

        now = start + timedelta(hours=6)
        before = service.dashboard_snapshot()
        now = start + timedelta(hours=6, minutes=15)
        after = service.dashboard_snapshot()

        assert before.soft_start_active is False
        assert before.surrender_available is False
        assert before.surrender_remaining_seconds == 15 * 60
        assert after.surrender_available is True
        assert after.surrender_remaining_seconds == 0


def test_rules_preview_remains_low_during_soft_start_unless_high_active(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: start,
            soft_start_enabled=True,
        )
        service.add_rule("site", "medium.example", allow_from_level="medium")
        service.add_rule("site", "high.example", allow_from_level="high")
        _create_required_main(service)
        service.start_day()

        low_preview = service.preview_blocking()
        service.day_state.add_reward_seconds(5 * 60)
        _write_browser_heartbeat(test_settings, start, incognito_allowed=True)
        high = service.start_high_access(5, "planned recreation")
        high_preview = service.preview_blocking()

        assert low_preview.access_level is AccessLevel.LOW
        assert low_preview.restriction_state == "low"
        assert low_preview.blocked_sites == ["medium.example", "high.example"]
        assert high.soft_start_active is True
        assert high.access_level is AccessLevel.HIGH
        assert high_preview.access_level is AccessLevel.HIGH
        assert high_preview.allowed_sites == ["medium.example", "high.example"]


def test_new_calendar_day_returns_to_planning_state(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    next_day = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()

        now = next_day
        snapshot = service.dashboard_snapshot()

        assert snapshot.day_started is False
        assert snapshot.day_status_label == "Planning"
        assert snapshot.can_start_day is False
        assert snapshot.start_day_unavailable_reason == (
            "Add a planned MAIN task before starting the day."
        )
        assert service.day_state.get().day_started_at is None


def test_surrender_is_unavailable_before_start_day(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        snapshot = service.dashboard_snapshot()

        try:
            service.activate_surrender()
        except ValueError as error:
            assert "Start Day before activating surrender" in str(error)
        else:
            raise AssertionError("surrender activation before Start Day should fail")

        assert snapshot.surrender_active is False
        assert snapshot.surrender_active_today is False
        assert snapshot.effective_restriction_state == "low"
        assert snapshot.surrender_available is False
        assert snapshot.surrender_remaining_seconds == SURRENDER_DELAY_SECONDS
        assert snapshot.surrender_strictness == "medium"
        assert snapshot.surrender_delay_seconds == SURRENDER_DELAY_SECONDS


def test_surrender_requires_six_hours_after_start_day(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()
        now = start + timedelta(hours=5, minutes=59)
        snapshot = service.dashboard_snapshot()

        try:
            service.activate_surrender()
        except ValueError as error:
            assert "Surrender is not available yet" in str(error)
        else:
            raise AssertionError("surrender activation before delay should fail")

        assert snapshot.surrender_active is False
        assert snapshot.surrender_active_today is False
        assert snapshot.surrender_available is False
        assert snapshot.surrender_remaining_seconds == 60
        assert service.day_state.get().surrender_requested_at is None


def test_surrender_activates_after_six_hours_and_resets_next_day(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)

        available = service.dashboard_snapshot()
        activated = service.activate_surrender()

        assert available.surrender_available is True
        assert available.surrender_remaining_seconds == 0
        assert activated.surrender_active is True
        assert activated.surrender_active_today is True
        assert activated.effective_restriction_state == "surrender"
        assert activated.surrender_available is False
        assert service.day_state.get().surrender_requested_at == (
            "2026-05-08T15:00:00+00:00"
        )

        now = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)
        next_day = service.dashboard_snapshot()

        assert next_day.day_started is False
        assert next_day.surrender_active is False
        assert next_day.surrender_active_today is False
        assert next_day.effective_restriction_state == "low"
        assert next_day.surrender_available is False
        assert next_day.surrender_remaining_seconds == SURRENDER_DELAY_SECONDS
        assert service.day_state.get().surrender_requested_at is None


def test_surrender_strictness_controls_delay(tmp_path, test_settings) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)

        assert service.get_surrender_strictness() == "medium"

        for strictness, delay_seconds in SURRENDER_STRICTNESS_DELAYS.items():
            service.day_state.reset_for_day("2026-05-08")
            service.set_surrender_strictness(strictness)
            _create_required_main(service)
            now = start
            service.start_day()
            now = start + timedelta(seconds=delay_seconds - 60)
            before = service.dashboard_snapshot()
            now = start + timedelta(seconds=delay_seconds)
            after = service.dashboard_snapshot()

            assert before.surrender_strictness == strictness
            assert before.surrender_delay_seconds == delay_seconds
            assert before.surrender_available is False
            assert before.surrender_remaining_seconds == 60
            assert after.surrender_available is True
            assert after.surrender_remaining_seconds == 0


def test_invalid_surrender_strictness_falls_back_to_medium(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.app_settings.set_value("surrender_strictness", "later")

        snapshot = service.dashboard_snapshot()

        assert service.get_surrender_strictness() == "medium"
        assert snapshot.surrender_strictness == "medium"
        assert snapshot.surrender_delay_seconds == SURRENDER_DELAY_SECONDS


def test_surrender_strictness_locks_during_active_day(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        service.set_surrender_strictness("high")
        _create_required_main(service)
        service.start_day()

        try:
            service.set_surrender_strictness("low")
        except ValueError as error:
            assert "Locked after Start Day" in str(error)
        else:
            raise AssertionError("Surrender strictness should lock after Start Day")

        snapshot = service.dashboard_snapshot()

        assert service.get_surrender_strictness() == "high"
        assert snapshot.surrender_strictness == "high"
        assert snapshot.surrender_available is False


def test_surrender_strictness_can_change_after_end_day(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        main = service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.end_day()

        updated = service.set_surrender_strictness("low")

        assert updated == "low"
        assert service.get_surrender_strictness() == "low"


def test_daily_recreation_cap_locks_during_active_day_and_reopens_after_close(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        service.set_daily_recreation_cap_minutes(120)
        main = service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()

        try:
            service.set_daily_recreation_cap_minutes(150)
        except ValueError as error:
            assert "Locked after Start Day" in str(error)
        else:
            raise AssertionError("daily Recreation cap should lock after Start Day")

        assert service.get_daily_recreation_cap_minutes() == 120

        _complete_task_after_claim_delay(service, main.id)
        service.end_day()

        updated = service.set_daily_recreation_cap_minutes(150)

        assert updated == 150
        assert service.get_daily_recreation_cap_minutes() == 150


def test_surrender_snapshot_uses_override_state_over_underlying_medium(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)

        snapshot = service.activate_surrender()

        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.surrender_active_today is True
        assert snapshot.surrender_active is True
        assert snapshot.effective_restriction_state == "surrender"


def test_complete_task_rejects_after_surrender_without_reward_or_medium_unlock(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.set_surrender_strictness("low")
        main = service.create_task(title="Main after surrender", kind=TaskKind.MAIN)
        service.start_day()

        now = start + timedelta(hours=3)
        service.activate_surrender()

        try:
            _complete_task_after_claim_delay(service, main.id)
        except ValueError as error:
            assert "Task completion is unavailable after Surrender." in str(error)
        else:
            raise AssertionError("task completion should reject after Surrender")

        unchanged = service.tasks.get(main.id)
        snapshot = service.dashboard_snapshot()

        assert unchanged is not None
        assert unchanged.status is TaskStatus.PENDING
        assert service.rewards.list() == []
        assert snapshot.reward_balance_seconds == 0
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.effective_restriction_state == "surrender"


def test_start_high_access_rejects_surrender_without_spending_reward(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        service.activate_surrender()
        ledger_before = service.rewards.list()
        balance_before = service.dashboard_snapshot().reward_balance_seconds
        options = service.get_high_access_options()

        assert options.can_start_high is False
        assert options.unavailable_reason == (
            "HIGH access is not needed while Surrender is active"
        )
        assert [option.enabled for option in options.options] == [False, False, False]

        try:
            service.start_high_access(15, "planned recreation")
        except ValueError as error:
            assert "HIGH access is not needed while Surrender is active" in str(error)
        else:
            raise AssertionError("HIGH access should be rejected during surrender")

        snapshot = service.dashboard_snapshot()
        assert snapshot.reward_balance_seconds == balance_before
        assert service.rewards.list() == ledger_before
        assert service.high_sessions.active_for_day("2026-05-08") is None


def test_activate_surrender_during_high_refunds_remaining_seconds(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        now = start + timedelta(hours=5, minutes=55)
        service.start_high_access(15, "planned recreation")

        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        snapshot = service.activate_surrender()
        ledger = service.rewards.list()
        session = service.high_sessions.list()[0]

        assert snapshot.surrender_active_today is True
        assert snapshot.high_active is False
        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.reward_balance_seconds == 1500
        assert ledger[-1].reason == "high_mode_refund"
        assert ledger[-1].seconds_delta == 600
        assert session.end_reason == "ended_early"


def test_surrender_reconciles_legacy_active_high_after_restart(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(15, "planned recreation")
        service.day_state.activate_surrender(start.isoformat())

        now = start + timedelta(minutes=1)
        reloaded = _make_service(connection, test_settings, now=current_now)
        snapshot = reloaded.dashboard_snapshot()
        ledger = reloaded.rewards.list()
        session = reloaded.high_sessions.list()[0]

        assert snapshot.surrender_active_today is True
        assert snapshot.effective_restriction_state == "surrender"
        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.high_active is False
        assert snapshot.high_remaining_seconds == 0
        assert snapshot.reward_balance_seconds == 1740
        assert reloaded.high_sessions.active_for_day("2026-05-08") is None
        assert ledger[-1].reason == "high_mode_refund"
        assert ledger[-1].seconds_delta == 840
        assert session.end_reason == "ended_early"


def test_bad_day_rejects_before_start_day(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        try:
            service.activate_bad_day_mode()
        except ValueError as error:
            assert "Start Day before activating Bad Day Mode" in str(error)
        else:
            raise AssertionError("Bad Day Mode should reject before Start Day")

        snapshot = service.dashboard_snapshot()
        assert snapshot.bad_day_active_today is False
        assert snapshot.effective_restriction_state == "low"


def test_bad_day_activates_medium_baseline_without_mutating_work(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Planned task", kind=TaskKind.NORMAL)
        service.add_rule("site", "example.com", allow_from_level="high")
        _create_required_main(service)
        service.start_day()

        first = service.activate_bad_day_mode()
        second = service.activate_bad_day_mode()

        stored_task = service.tasks.get(task.id)
        assert first.bad_day_active_today is True
        assert first.access_level is AccessLevel.MEDIUM
        assert first.effective_restriction_state == "bad_day"
        assert second.bad_day_active_today is True
        assert first.reward_balance_seconds == 0
        assert service.rewards.list() == []
        assert stored_task is not None
        assert stored_task.status is TaskStatus.PENDING
        assert [rule.target for rule in service.get_rules("site")] == ["example.com"]


def test_high_access_during_bad_day_returns_to_medium_after_end(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        bad_day = service.activate_bad_day_mode()
        options = service.get_high_access_options()

        high = service.start_high_access(5, "planned recreation")
        now = start + timedelta(minutes=1)
        ended = service.end_high_access()

        assert bad_day.access_level is AccessLevel.MEDIUM
        assert bad_day.effective_restriction_state == "bad_day"
        assert options.can_start_high is True
        assert options.options[0].enabled is True
        assert high.access_level is AccessLevel.HIGH
        assert high.effective_restriction_state == "high"
        assert ended.access_level is AccessLevel.MEDIUM
        assert ended.bad_day_active_today is True
        assert ended.effective_restriction_state == "bad_day"
        assert ended.reward_balance_seconds == 240


def test_surrender_overrides_bad_day_and_blocks_late_bad_day_activation(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()
        service.activate_bad_day_mode()
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)

        surrendered = service.activate_surrender()
        try:
            service.activate_bad_day_mode()
        except ValueError as error:
            assert "Surrender is active" in str(error)
        else:
            raise AssertionError("Bad Day Mode should reject during Surrender")

        assert surrendered.bad_day_active_today is True
        assert surrendered.surrender_active_today is True
        assert surrendered.effective_restriction_state == "surrender"
        assert surrendered.access_level is AccessLevel.MEDIUM


def test_bad_day_resets_on_new_day(tmp_path, test_settings) -> None:
    day_one = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)
    now = day_one

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()
        service.activate_bad_day_mode()

        now = day_two
        snapshot = service.dashboard_snapshot()

        assert snapshot.day_started is False
        assert snapshot.bad_day_active_today is False
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.effective_restriction_state == "low"
        assert service.day_state.get().bad_day_mode is False


def test_daily_rollover_clears_bad_day_and_surrender_together(
    tmp_path,
    test_settings,
) -> None:
    day_one = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 5, 9, 9, 0, tzinfo=timezone.utc)
    now = day_one

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        _create_required_main(service)
        service.start_day()
        service.activate_bad_day_mode()
        now = day_one + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        surrendered = service.activate_surrender()

        now = day_two
        next_day = service.dashboard_snapshot()
        stored = service.day_state.get()

        assert surrendered.bad_day_active_today is True
        assert surrendered.surrender_active_today is True
        assert surrendered.effective_restriction_state == "surrender"
        assert next_day.day_started is False
        assert next_day.bad_day_active_today is False
        assert next_day.surrender_active_today is False
        assert next_day.surrender_active is False
        assert next_day.access_level is AccessLevel.LOW
        assert next_day.effective_restriction_state == "low"
        assert stored.bad_day_mode is False
        assert stored.surrender_requested_at is None


def test_effective_mode_priority_surrender_high_bad_day_normal(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)

        normal = service.dashboard_snapshot()
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        bad_day = service.activate_bad_day_mode()
        high = service.start_high_access(5, "planned recreation")
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        surrender = service.activate_surrender()

        assert normal.access_level is AccessLevel.LOW
        assert normal.effective_restriction_state == "low"
        assert bad_day.access_level is AccessLevel.MEDIUM
        assert bad_day.effective_restriction_state == "bad_day"
        assert high.access_level is AccessLevel.HIGH
        assert high.effective_restriction_state == "high"
        assert surrender.access_level is AccessLevel.MEDIUM
        assert surrender.high_active is False
        assert surrender.effective_restriction_state == "surrender"


def test_app_service_completes_normal_task_once(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Small useful thing", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()

        first = _complete_task_after_claim_delay(service, task.id)
        second = _complete_task_after_claim_delay(service, task.id)
        ledger = service.rewards.list()
        snapshot = service.dashboard_snapshot()

        assert first.task.status is TaskStatus.DONE
        assert first.reward_entry is not None
        assert first.reward_entry.minutes_delta == 5
        assert first.reward_entry.seconds_delta == 300
        assert second.reward_entry is None
        assert len(ledger) == 1
        assert snapshot.reward_balance_minutes == 5
        assert snapshot.reward_balance_seconds == 300
        assert snapshot.access_level is AccessLevel.LOW


def test_complete_task_before_start_day_is_rejected(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Planning-only task", kind=TaskKind.MAIN)

        try:
            _complete_task_after_claim_delay(service, task.id)
        except ValueError as error:
            assert "Start Day before completing tasks" in str(error)
        else:
            raise AssertionError("completion before Start Day should be rejected")

        unchanged = service.tasks.get(task.id)
        snapshot = service.dashboard_snapshot()

        assert unchanged is not None
        assert unchanged.status is TaskStatus.PENDING
        assert service.rewards.list() == []
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.reward_balance_seconds == 0


def test_planned_main_claim_waits_before_reward_and_medium_unlock(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        claim = service.claim_task_done(main.id)
        pending = service.tasks.get(main.id)
        before_confirm = service.dashboard_snapshot()

        assert claim.remaining_seconds == TASK_COMPLETION_CLAIM_DELAY_SECONDS
        assert pending is not None
        assert pending.status is TaskStatus.PENDING
        assert pending.completion_claimed_at is not None
        assert pending.completion_available_at is not None
        assert service.rewards.list() == []
        assert before_confirm.access_level is AccessLevel.LOW
        assert before_confirm.can_end_day is False

        try:
            service.confirm_task_done(main.id)
        except ValueError as error:
            assert "Confirm Done is available in" in str(error)
        else:
            raise AssertionError("confirming before claim delay should fail")

        now = start + timedelta(seconds=TASK_COMPLETION_CLAIM_DELAY_SECONDS)
        result = service.confirm_task_done(main.id)
        after_confirm = service.dashboard_snapshot()

        assert result.task.status is TaskStatus.DONE
        assert result.task.completion_claimed_at is None
        assert result.task.completion_available_at is None
        assert result.reward_entry is not None
        assert after_confirm.access_level is AccessLevel.MEDIUM
        assert after_confirm.can_end_day is True


def test_complete_task_wrapper_cannot_bypass_planned_claim_delay(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        try:
            service.complete_task(task.id)
        except ValueError as error:
            assert "Claim Done before confirming completion" in str(error)
        else:
            raise AssertionError("complete_task should not bypass claim delay")

        unchanged = service.tasks.get(task.id)
        assert unchanged is not None
        assert unchanged.status is TaskStatus.PENDING
        assert service.rewards.list() == []


def test_completion_claim_persists_across_service_reload(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    db_path = tmp_path / "selfboss.db"
    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.claim_task_done(task.id)

    now = start + timedelta(seconds=TASK_COMPLETION_CLAIM_DELAY_SECONDS)
    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings, now=current_now)
        reloaded = service.tasks.get(task.id)
        assert reloaded is not None
        assert reloaded.completion_claimed_at is not None
        assert reloaded.completion_available_at is not None

        result = service.confirm_task_done(task.id)
        assert result.task.status is TaskStatus.DONE
        assert result.reward_entry is not None


def test_cancel_completion_claim_clears_pending_state(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        service.claim_task_done(task.id)
        canceled = service.cancel_task_completion_claim(task.id)

        assert canceled.status is TaskStatus.PENDING
        assert canceled.completion_claimed_at is None
        assert canceled.completion_available_at is None
        assert service.rewards.list() == []


def test_second_completion_claim_waits_for_current_claim(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        first = service.create_task(title="First tiny", kind=TaskKind.TINY)
        second = service.create_task(title="Second tiny", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        service.claim_task_done(first.id)
        try:
            service.claim_task_done(second.id)
        except ValueError as error:
            assert str(error) == TASK_COMPLETION_CLAIM_ALREADY_PENDING
        else:
            raise AssertionError("second claim should wait for the current claim")

        service.cancel_task_completion_claim(first.id)
        service.claim_task_done(second.id)
        now = start + timedelta(seconds=TASK_COMPLETION_CLAIM_DELAY_SECONDS)
        result = service.confirm_task_done(second.id)

        assert result.task.status is TaskStatus.DONE
        assert result.reward_entry is not None


def test_end_day_rejects_main_with_pending_completion_claim(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        service.claim_task_done(main.id)

        try:
            service.end_day()
        except ValueError as error:
            assert "after completing today's MAIN task" in str(error)
        else:
            raise AssertionError("End Day should require confirmed MAIN completion")


def test_request_and_confirm_end_day_use_review_delay(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)

        requested = service.request_end_day()

        assert requested.day_closed is False
        assert requested.end_day_pending is True
        assert requested.end_day_remaining_seconds == END_DAY_CONFIRM_DELAY_SECONDS
        try:
            service.confirm_end_day()
        except ValueError as error:
            assert "End Day confirmation is available" in str(error)
        else:
            raise AssertionError("confirm_end_day should enforce the delay")

        now = start + timedelta(seconds=END_DAY_CONFIRM_DELAY_SECONDS)
        closed = service.confirm_end_day()

        assert closed.day_closed is True
        assert closed.end_day_pending is False
        assert closed.day_status_label == "Day ended"


def test_rest_token_earns_caps_and_uses_only_before_start_day(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)

        def close_normal_day() -> None:
            nonlocal now
            main = service.create_task(title="Main task", kind=TaskKind.MAIN)
            service.start_day(); _complete_task_after_claim_delay(service, main.id)
            service.request_end_day()
            now += timedelta(seconds=END_DAY_CONFIRM_DELAY_SECONDS)
            service.confirm_end_day()

        close_normal_day(); now += timedelta(days=1)
        service.create_task(title="Recovery main", kind=TaskKind.MAIN)
        service.start_day(); service.recovery_close_today()
        assert service.get_rest_token_count() == 0
        now += timedelta(days=1)
        for _ in range(3):
            close_normal_day(); now += timedelta(days=1)
        assert service.get_rest_token_count() == 1
        close_normal_day()
        assert service.get_rest_token_count() == 1
        now += timedelta(days=1)
        service.create_task(title="Active main", kind=TaskKind.MAIN)
        service.start_day()
        try:
            service.use_rest_token()
        except ValueError as error:
            assert str(error) == REST_TOKEN_PRE_START_ONLY
        else:
            raise AssertionError("Rest Token should not be usable after Start Day")
        service.recovery_close_today(); now += timedelta(days=1)
        rest_day = service.use_rest_token()
        assert (
            rest_day.day_closed,
            rest_day.day_started,
            rest_day.rest_token_count,
            rest_day.reward_balance_seconds,
            rest_day.high_active,
        ) == (True, False, 0, 0, False)


def test_completed_main_in_planning_mode_does_not_drive_access(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Planned main", kind=TaskKind.MAIN)
        service.tasks.update_status(task.id, TaskStatus.DONE)

        snapshot = service.dashboard_snapshot()

        assert snapshot.day_started is False
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.main_task is not None
        assert snapshot.main_task.status is TaskStatus.DONE


def test_tasks_created_after_start_day_are_unplanned_and_no_reward(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        planned = service.create_task(title="Planned normal", kind=TaskKind.NORMAL)
        _create_required_main(service)
        service.start_day()
        try:
            service.create_task(
                title="Unexpected URL task",
                kind=TaskKind.NORMAL,
                allowed_url="https://example.test",
            )
        except ValueError as error:
            assert "URL exceptions are locked after Start Day" in str(error)
        else:
            raise AssertionError("active-day allowed_url should be rejected")
        unplanned = service.create_task(
            title="Unexpected task",
            kind=TaskKind.NORMAL,
            reward_minutes_override=45,
        )

        planned_result = _complete_task_after_claim_delay(service, planned.id)
        unplanned_result = _complete_task_after_claim_delay(service, unplanned.id)
        snapshot = service.dashboard_snapshot()

        assert planned.planning_status is TaskPlanningStatus.PLANNED
        assert unplanned.planning_status is TaskPlanningStatus.UNPLANNED
        assert unplanned.kind is TaskKind.NORMAL
        assert unplanned.reward_minutes == 0
        assert unplanned.allowed_url is None
        assert planned_result.reward_entry is not None
        assert unplanned_result.reward_entry is None
        assert snapshot.reward_balance_seconds == 15 * 60
        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.main_task is not None
        assert snapshot.main_task.title == "Required main"
        assert snapshot.planned_task_count == 2
        assert snapshot.planned_pending_count == 1
        assert snapshot.planned_done_count == 1
        assert snapshot.unplanned_task_count == 1
        assert snapshot.unplanned_pending_count == 0
        assert snapshot.unplanned_done_count == 1


def test_pending_tasks_can_be_deleted_only_when_policy_allows(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        planned = service.create_task(title="Before start", kind=TaskKind.NORMAL)

        service.delete_task(planned.id)

        assert service.list_tasks() == []


def test_delete_task_rejects_pending_planned_task_after_start_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        planned = service.create_task(title="Locked planned", kind=TaskKind.NORMAL)
        _create_required_main(service)
        service.start_day()

        try:
            service.delete_task(planned.id)
        except ValueError as error:
            assert "Tasks cannot be deleted after Start Day" in str(error)
        else:
            raise AssertionError("planned task deletion after Start Day should fail")

        assert service.tasks.get(planned.id) is not None


def test_delete_task_rejects_pending_unplanned_task_after_start_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        _create_required_main(service)
        service.start_day()
        unplanned = service.create_task(title="After start", kind=TaskKind.NORMAL)

        try:
            service.delete_task(unplanned.id)
        except ValueError as error:
            assert "Tasks cannot be deleted after Start Day" in str(error)
        else:
            raise AssertionError("unplanned task deletion after Start Day should fail")

        assert service.tasks.get(unplanned.id) is not None


def test_completed_tasks_cannot_be_deleted(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Done task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        try:
            service.delete_task(task.id)
        except ValueError as error:
            assert "Completed tasks cannot be deleted" in str(error)
        else:
            raise AssertionError("completed task deletion should be rejected")

        assert service.tasks.get(task.id) is not None
        assert len(service.rewards.list()) == 1


def test_planned_main_unlocks_medium_after_start_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Planned main", kind=TaskKind.MAIN)
        service.start_day()

        _complete_task_after_claim_delay(service, task.id)
        snapshot = service.dashboard_snapshot()

        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.main_task is not None
        assert snapshot.main_task.id == task.id
        assert snapshot.reward_balance_seconds == 30 * 60


def test_app_service_completing_main_task_unlocks_medium(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()

        result = _complete_task_after_claim_delay(service, task.id)
        duplicate = _complete_task_after_claim_delay(service, task.id)
        snapshot = service.dashboard_snapshot()

        assert result.task.status is TaskStatus.DONE
        assert duplicate.reward_entry is None
        assert len(service.rewards.list()) == 1
        assert snapshot.reward_balance_minutes == 30
        assert snapshot.reward_balance_seconds == 1800
        assert snapshot.access_level is AccessLevel.MEDIUM


def test_app_service_spend_requires_available_reward_minutes(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        try:
            service.start_high_access(5, "planned recreation")
        except ValueError as error:
            assert "No reward time available" in str(error)
        else:
            raise AssertionError("start_high_access should reject zero balance")


def test_start_high_access_requires_declared_intent(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.day_state.add_reward_seconds(10 * 60)

        for intent in ("", "   ", "abcd"):
            try:
                service.start_high_access(5, intent)
            except ValueError as error:
                assert "HIGH intent" in str(error)
            else:
                raise AssertionError("HIGH start should require an intent")

        assert service.dashboard_snapshot().high_active is False


def test_start_high_access_rejects_duration_above_session_cap(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.day_state.add_reward_seconds((HIGH_SESSION_MAX_MINUTES + 15) * 60)

        try:
            service.start_high_access(
                HIGH_SESSION_MAX_MINUTES + 1,
                "watch one tutorial",
            )
        except ValueError as error:
            assert f"{HIGH_SESSION_MAX_MINUTES} minutes" in str(error)
        else:
            raise AssertionError("HIGH start should reject durations above cap")

        assert service.dashboard_snapshot().high_active is False


def test_start_high_access_allows_session_cap_when_daily_cap_allows(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.day_state.add_reward_seconds(HIGH_SESSION_MAX_MINUTES * 60)

        snapshot = service.start_high_access(
            HIGH_SESSION_MAX_MINUTES,
            "planned recreation",
        )

        assert snapshot.high_active is True
        assert snapshot.high_minutes_total == HIGH_SESSION_MAX_MINUTES
        assert snapshot.high_daily_used_seconds == HIGH_SESSION_MAX_MINUTES * 60
        assert snapshot.high_daily_remaining_seconds == (
            (HIGH_DAILY_MAX_MINUTES - HIGH_SESSION_MAX_MINUTES) * 60
        )


def test_high_access_options_expose_reward_wallet(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        options = service.get_high_access_options()

        assert options.available_minutes == 5
        assert options.available_seconds == 300
        assert options.max_session_minutes == HIGH_SESSION_MAX_MINUTES
        assert options.daily_cap_minutes == HIGH_DAILY_MAX_MINUTES
        assert options.daily_used_seconds == 0
        assert options.daily_remaining_seconds == HIGH_DAILY_MAX_MINUTES * 60
        assert options.daily_cap_reached is False
        assert options.high_active is False
        assert options.can_start_high is True
        assert options.unavailable_reason == ""
        assert [option.minutes for option in options.options] == [5, 15, 30]
        assert [option.enabled for option in options.options] == [True, False, False]


def test_high_access_requires_ready_browser_when_active_site_rules_exist(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)

        options = service.get_high_access_options()
        try:
            service.start_high_access(5, "watch one tutorial")
        except ValueError as error:
            assert str(error) == HIGH_BROWSER_BLOCKING_NOT_READY
        else:
            raise AssertionError("website HIGH should require ready browser control")

        assert options.can_start_high is False
        assert options.unavailable_reason == HIGH_BROWSER_BLOCKING_NOT_READY
        assert service.high_sessions.active_for_day("2026-05-08") is None
        assert service.dashboard_snapshot().reward_balance_seconds == 30 * 60


def test_high_access_browser_gate_ignores_app_only_rules(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)

        snapshot = service.start_high_access(5, "play one match")

        assert snapshot.high_active is True
        assert snapshot.reward_balance_seconds == 25 * 60


def test_spend_reward_minutes_starts_high_session(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        snapshot = service.start_high_access(15, "planned recreation")
        ledger = service.rewards.list()

        assert snapshot.access_level is AccessLevel.HIGH
        assert snapshot.reward_balance_minutes == 15
        assert snapshot.reward_balance_seconds == 900
        assert snapshot.high_active is True
        assert snapshot.high_minutes_total == 15
        assert snapshot.high_remaining_seconds == 15 * 60
        assert snapshot.high_intent == "planned recreation"
        assert ledger[-1].minutes_delta == -15
        assert ledger[-1].seconds_delta == -900
        assert ledger[-1].reason == "high_mode"
        active_session = service.high_sessions.active_for_day("2026-05-07")
        assert active_session is not None
        assert active_session.allocated_minutes == 15
        assert active_session.allocated_seconds == 900
        assert active_session.intent == "planned recreation"
        assert active_session.ended_at is None


def test_start_high_access_rejects_when_daily_cap_is_reached(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.day_state.add_reward_seconds((HIGH_DAILY_MAX_MINUTES + 5) * 60)
        service.high_sessions.start(
            day_date="2026-05-08",
            started_at=(now - timedelta(hours=2)).isoformat(),
            ends_at=(now - timedelta(minutes=30)).isoformat(),
            allocated_minutes=HIGH_DAILY_MAX_MINUTES,
            allocated_seconds=HIGH_DAILY_MAX_MINUTES * 60,
            intent="earlier recreation",
        )

        options = service.get_high_access_options()
        try:
            service.start_high_access(5, "planned recreation")
        except ValueError as error:
            assert "Recreation cap reached" in str(error)
        else:
            raise AssertionError("HIGH start should reject daily cap overflow")

        assert options.can_start_high is False
        assert options.daily_cap_reached is True
        assert options.unavailable_reason == "Recreation cap reached for today."
        assert service.dashboard_snapshot().high_active is False


def test_start_high_access_uses_configured_daily_recreation_cap(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.set_daily_recreation_cap_minutes(120)
        service.day_state.add_reward_seconds(40 * 60)
        service.high_sessions.start(
            day_date="2026-05-08",
            started_at=(now - timedelta(hours=2)).isoformat(),
            ends_at=(now - timedelta(minutes=30)).isoformat(),
            allocated_minutes=90,
            allocated_seconds=90 * 60,
            intent="earlier recreation",
        )

        options = service.get_high_access_options()
        snapshot = service.start_high_access(30, "planned recreation")

        assert options.daily_cap_minutes == 120
        assert options.daily_used_seconds == 90 * 60
        assert options.daily_remaining_seconds == 30 * 60
        assert snapshot.high_active is True
        assert snapshot.high_daily_used_seconds == 120 * 60
        assert snapshot.high_daily_cap_reached is True


def test_start_high_access_rejects_configured_daily_cap_overflow(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.set_daily_recreation_cap_minutes(60)
        service.day_state.add_reward_seconds(20 * 60)
        service.high_sessions.start(
            day_date="2026-05-08",
            started_at=(now - timedelta(hours=2)).isoformat(),
            ends_at=(now - timedelta(minutes=30)).isoformat(),
            allocated_minutes=50,
            allocated_seconds=50 * 60,
            intent="earlier recreation",
        )

        try:
            service.start_high_access(15, "planned recreation")
        except ValueError as error:
            assert "60-minute Recreation cap" in str(error)
        else:
            raise AssertionError("configured cap should reject overflow")

        snapshot = service.dashboard_snapshot()
        assert snapshot.high_daily_cap_minutes == 60
        assert snapshot.high_daily_remaining_seconds == 10 * 60
        assert snapshot.high_active is False


def test_high_daily_cap_counts_today_only(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.day_state.add_reward_seconds(HIGH_DAILY_MAX_MINUTES * 60)
        service.high_sessions.start(
            day_date="2026-05-07",
            started_at=(now - timedelta(days=1, hours=2)).isoformat(),
            ends_at=(now - timedelta(days=1, minutes=30)).isoformat(),
            allocated_minutes=HIGH_DAILY_MAX_MINUTES,
            allocated_seconds=HIGH_DAILY_MAX_MINUTES * 60,
            intent="yesterday recreation",
        )

        options = service.get_high_access_options()
        snapshot = service.start_high_access(5, "planned recreation")

        assert options.daily_used_seconds == 0
        assert options.can_start_high is True
        assert snapshot.high_active is True
        assert snapshot.high_daily_used_seconds == 5 * 60


def test_high_daily_cap_tracks_allocated_time_not_refunded_time(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds((HIGH_DAILY_MAX_MINUTES + 10) * 60)
        service.start_high_access(HIGH_SESSION_MAX_MINUTES, "planned recreation")
        now = now + timedelta(minutes=1)
        service.end_high_access()
        now = now + timedelta(seconds=HIGH_COOLDOWN_SECONDS)
        service.start_high_access(HIGH_SESSION_MAX_MINUTES, "second recreation")
        now = now + timedelta(minutes=1)
        service.end_high_access()

        capped = service.dashboard_snapshot()
        try:
            service.start_high_access(5, "third recreation")
        except ValueError as error:
            assert "Recreation cap reached" in str(error)
        else:
            raise AssertionError("refunded HIGH time should not restore daily cap")

        assert capped.high_daily_used_seconds == HIGH_DAILY_MAX_MINUTES * 60
        assert capped.high_daily_cap_reached is True
        assert capped.reward_balance_seconds > 0


def test_active_high_session_survives_service_reconstruction(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    later = start + timedelta(minutes=2)
    db_path = tmp_path / "selfboss.db"
    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings, now=lambda: start)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(15, "planned recreation")

    with initialize_database(db_path) as connection:
        service = _make_service(connection, test_settings, now=lambda: later)
        snapshot = service.dashboard_snapshot()

        assert snapshot.access_level is AccessLevel.HIGH
        assert snapshot.high_active is True
        assert snapshot.high_remaining_seconds == 13 * 60
        assert snapshot.high_intent == "planned recreation"
        assert snapshot.reward_balance_minutes == 15
        assert snapshot.reward_balance_seconds == 900


def test_ending_high_early_refunds_unused_seconds(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(15, "planned recreation")

        now = now + timedelta(minutes=4, seconds=10)
        snapshot = service.end_high_access()
        ledger = service.rewards.list()
        session = service.high_sessions.list()[0]

        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.high_active is False
        assert snapshot.high_intent is None
        assert snapshot.reward_balance_minutes == 25
        assert snapshot.reward_balance_seconds == 1550
        assert ledger[-1].minutes_delta == 10
        assert ledger[-1].seconds_delta == 650
        assert ledger[-1].reason == "high_mode_refund"
        assert session.end_reason == "ended_early"


def test_high_cooldown_blocks_immediate_restart_after_early_end(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds(20 * 60)
        service.start_high_access(5, "planned recreation")
        now = now + timedelta(minutes=1)
        ended = service.end_high_access()
        balance_after_refund = ended.reward_balance_seconds
        ledger_count = len(service.rewards.list())

        try:
            service.start_high_access(5, "second recreation")
        except ValueError as error:
            assert "Recreation cooldown: 5m remaining." in str(error)
        else:
            raise AssertionError("HIGH start should reject during cooldown")

        snapshot = service.dashboard_snapshot()
        assert snapshot.high_cooldown_active is True
        assert snapshot.high_cooldown_remaining_seconds == HIGH_COOLDOWN_SECONDS
        assert snapshot.reward_balance_seconds == balance_after_refund
        assert len(service.rewards.list()) == ledger_count


def test_high_cooldown_allows_restart_after_wait(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds(20 * 60)
        service.start_high_access(5, "planned recreation")
        now = now + timedelta(minutes=1)
        service.end_high_access()
        now = now + timedelta(seconds=HIGH_COOLDOWN_SECONDS)

        snapshot = service.start_high_access(5, "second recreation")

        assert snapshot.high_active is True
        assert snapshot.high_cooldown_active is False
        assert snapshot.high_daily_used_seconds == 10 * 60


def test_ending_high_early_refunds_less_than_one_minute(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        now = now + timedelta(minutes=4, seconds=1)
        snapshot = service.end_high_access()
        ledger = service.rewards.list()

        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.reward_balance_seconds == 59
        assert snapshot.reward_balance_minutes == 0
        assert ledger[-1].seconds_delta == 59


def test_ending_high_early_falls_back_to_low_without_today_main(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        now = now + timedelta(minutes=1)
        snapshot = service.end_high_access()

        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.reward_balance_minutes == 4
        assert snapshot.reward_balance_seconds == 240


def test_start_high_access_rejects_overspend_and_active_high(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        try:
            service.start_high_access(45, "planned recreation")
        except ValueError as error:
            assert "cannot spend more reward time" in str(error)
        else:
            raise AssertionError("overspend should fail")

        service.start_high_access(5, "planned recreation")
        try:
            service.start_high_access(5, "planned recreation")
        except ValueError as error:
            assert "HIGH mode is already active" in str(error)
        else:
            raise AssertionError("active HIGH should reject another start")


def test_high_session_expires_back_to_previous_level(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    start = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    expired = start + timedelta(minutes=5, seconds=1)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: start)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        monkeypatch.setattr(service, "_now", lambda: expired)
        snapshot = service.dashboard_snapshot()

        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.high_active is False
        assert snapshot.high_remaining_seconds == 0
        assert snapshot.high_intent is None


def test_high_session_expires_back_to_low_without_today_main(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    start = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    expired = start + timedelta(minutes=5, seconds=1)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: start)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        monkeypatch.setattr(service, "_now", lambda: expired)
        snapshot = service.dashboard_snapshot()

        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.high_active is False


def test_expired_high_session_does_not_refund_time(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    expired = start + timedelta(minutes=5, seconds=1)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: start)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        _create_required_main(service)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        monkeypatch.setattr(service, "_now", lambda: expired)
        snapshot = service.dashboard_snapshot()
        ledger = service.rewards.list()
        session = service.high_sessions.list()[0]

        assert snapshot.access_level is AccessLevel.LOW
        assert snapshot.reward_balance_minutes == 0
        assert snapshot.reward_balance_seconds == 0
        assert snapshot.high_intent is None
        assert ledger[-1].minutes_delta == -5
        assert ledger[-1].seconds_delta == -300
        assert session.end_reason == "expired"


def test_expired_high_session_starts_cooldown_from_session_end(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds(15 * 60)
        service.start_high_access(5, "planned recreation")
        now = start + timedelta(minutes=5, seconds=1)
        expired = service.dashboard_snapshot()

        try:
            service.start_high_access(5, "second recreation")
        except ValueError as error:
            assert "Recreation cooldown: 5m remaining." in str(error)
        else:
            raise AssertionError("expired HIGH should create cooldown")

        assert expired.high_active is False
        assert expired.high_cooldown_active is True
        assert expired.high_cooldown_remaining_seconds == HIGH_COOLDOWN_SECONDS - 1
        assert service.high_sessions.list()[0].end_reason == "expired"


def test_expired_high_cooldown_allows_restart_after_window(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds(15 * 60)
        service.start_high_access(5, "planned recreation")
        now = start + timedelta(minutes=10, seconds=1)

        snapshot = service.start_high_access(5, "second recreation")

        assert snapshot.high_active is True
        assert snapshot.high_cooldown_active is False
        assert snapshot.reward_balance_seconds == 5 * 60


def test_high_warning_threshold_helper() -> None:
    assert high_warning_threshold_seconds(15 * 60) == 5 * 60
    assert high_warning_threshold_seconds(14 * 60) == 60
    assert high_warning_threshold_seconds(5 * 60) == 60
    assert high_warning_threshold_seconds(4 * 60 + 59) is None


def test_high_notification_events_are_once_only(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.day_state.add_reward_seconds(15 * 60)
        service.start_high_access(15, "watch one tutorial")

        assert service.collect_high_notification_events() == ()

        now = start + timedelta(minutes=10)
        warning = service.collect_high_notification_events()
        repeated_warning = service.collect_high_notification_events()

        assert [event.event_type for event in warning] == ["warning"]
        assert warning[0].title == "HIGH ending soon"
        assert "5m" in warning[0].message
        assert repeated_warning == ()

        now = start + timedelta(minutes=15, seconds=1)
        ended = service.collect_high_notification_events()
        repeated_ended = service.collect_high_notification_events()

        assert [event.event_type for event in ended] == ["ended"]
        assert ended[0].title == "HIGH ended"
        assert repeated_ended == ()


def test_end_high_access_returns_to_today_fallback(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(5, "planned recreation")

        snapshot = service.end_high_access()

        assert snapshot.access_level is AccessLevel.MEDIUM
        assert snapshot.high_active is False


def test_dashboard_snapshot_includes_main_task_and_high_session(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: now)
        first = service.create_task(title="First main", kind=TaskKind.MAIN)
        second = service.create_task(title="Second main", kind=TaskKind.MAIN)

        assert service.dashboard_snapshot().main_task == first

        service.start_day()
        _complete_task_after_claim_delay(service, first.id)
        assert service.dashboard_snapshot().main_task == second

        _complete_task_after_claim_delay(service, second.id)
        snapshot = service.dashboard_snapshot()

        assert snapshot.main_task is not None
        assert snapshot.main_task.id == second.id
        assert snapshot.main_task.status is TaskStatus.DONE

        high_snapshot = service.start_high_access(5, "planned recreation")
        assert high_snapshot.access_level is AccessLevel.HIGH
        assert high_snapshot.high_active is True
        assert high_snapshot.high_remaining_seconds == 5 * 60
        assert high_snapshot.main_task is not None
        assert high_snapshot.main_task.id == second.id


def test_app_service_adds_removes_and_lists_rules(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        site = service.add_rule("site", "example.com")
        app = service.add_rule("app", "game.exe")

        assert service.get_rules("site") == [site]
        assert service.get_rules("app") == [app]

        service.remove_rule("site", "example.com")
        assert service.get_rules("site") == []
        assert service.get_rules("app") == [app]


def test_add_website_rule_persists_allow_from_level(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        rule = service.add_rule("site", "  EXAMPLE.com. ", allow_from_level="high")

        assert rule.allow_from_level == "high"
        assert rule.target == "example.com"
        assert service.get_rules("site")[0].allow_from_level == "high"


def test_rule_target_display_canonicalizes_browser_path_variants() -> None:
    assert (
        canonical_rule_target_for_display("site", "youtube.com/shorts")
        == "youtube.com/shorts/*"
    )
    assert (
        canonical_rule_target_for_display("site", "youtube.com/shorts/")
        == "youtube.com/shorts/*"
    )
    assert (
        canonical_rule_target_for_display("site", "youtube.com/shorts/*")
        == "youtube.com/shorts/*"
    )
    assert canonical_rule_target_for_display("site", "bad target") == "bad target"


def test_obvious_rules_get_default_escape_family(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        shorts = service.add_rule("site", "youtube.com/shorts")
        reddit = service.add_rule("site", "reddit.com")
        steam = service.add_rule("app", "steam.exe")
        custom = service.add_rule("site", "focus.example")
        explicit = service.add_rule(
            "site",
            "www.youtube.com/shorts",
            escape_family="fake_productivity",
        )

        assert shorts.target == "youtube.com/shorts/*"
        assert shorts.escape_family == "video"
        assert reddit.escape_family == "random_browsing"
        assert steam.escape_family == "launcher"
        assert custom.escape_family == "none"
        assert explicit.escape_family == "fake_productivity"
        assert (
            suggest_escape_family_for_rule("site", "mangalib.me")
            == "reading_binge"
        )


def test_utility_leakage_warning_for_obvious_escape_targets() -> None:
    warning = (
        "Utility mode warning: this looks like an escape target. HIGH is "
        "recommended."
    )

    assert (
        utility_leakage_warning_for_rule(
            "site",
            "youtube.com/shorts",
            "medium",
            "none",
        )
        == warning
    )
    assert (
        utility_leakage_warning_for_rule(
            "site",
            "reddit.com",
            "low",
            "none",
        )
        == warning
    )
    assert (
        utility_leakage_warning_for_rule(
            "app",
            "steam.exe",
            "medium",
            "none",
        )
        == warning
    )
    assert (
        utility_leakage_warning_for_rule(
            "site",
            "docs.example.com",
            "medium",
            "none",
        )
        == ""
    )
    assert (
        utility_leakage_warning_for_rule(
            "site",
            "youtube.com",
            "high",
            "video",
        )
        == ""
    )
    assert (
        utility_leakage_warning_for_rule(
            "app",
            "internal-tool.exe",
            "medium",
            "games",
        )
        == warning
    )


def test_rule_duplicate_equivalence_key_uses_canonical_target_and_metadata(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rule_a = RuleRepository(connection).add(
            rule_type="site",
            target="youtube.com/shorts",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        rule_b = RuleRepository(connection).add(
            rule_type="site",
            target="youtube.com/shorts/",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        rule_c = RuleRepository(connection).add(
            rule_type="site",
            target="youtube.com/shorts/*",
            allow_from_level="medium",
            purpose="compulsive_stimulation",
            escape_family="video",
        )

        assert rule_duplicate_equivalence_key(rule_a) == rule_duplicate_equivalence_key(
            rule_b
        )
        assert rule_duplicate_equivalence_key(rule_a) != rule_duplicate_equivalence_key(
            rule_c
        )


def test_add_app_rule_persists_allow_from_level(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        rule = service.add_rule("app", "  DISCORD.exe  ", allow_from_level="medium")

        assert rule.allow_from_level == "medium"
        assert rule.target == "discord.exe"
        assert service.get_rules("app")[0].allow_from_level == "medium"


def test_rule_purpose_suggests_default_allow_from_level(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        high_risk = service.add_rule(
            "site",
            "youtube.com",
            purpose="high_risk_escape",
            escape_family="video",
        )
        gateway = service.add_rule(
            "app",
            "steam.exe",
            purpose="gateway_app",
            escape_family="launcher",
        )
        work_tool = service.add_rule(
            "site",
            "docs.python.org",
            purpose="work_tool",
        )

        assert high_risk.allow_from_level == "high"
        assert high_risk.purpose == "high_risk_escape"
        assert high_risk.escape_family == "video"
        assert gateway.allow_from_level == "high"
        assert gateway.purpose == "gateway_app"
        assert gateway.escape_family == "launcher"
        assert work_tool.allow_from_level == "low"
        assert work_tool.purpose == "work_tool"
        assert work_tool.escape_family == "none"


def test_rule_allow_from_manual_override_wins_over_purpose_default(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        rule = service.add_rule(
            "site",
            "work.example",
            allow_from_level="high",
            purpose="work_tool",
            escape_family="none",
        )

        assert rule.allow_from_level == "high"
        assert rule.purpose == "work_tool"
        assert rule.escape_family == "none"


def test_update_rule_allow_from_level_can_update_metadata(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("app", "steam.exe")

        updated = service.update_rule_allow_from_level(
            "app",
            "steam.exe",
            "medium",
            purpose="controlled_recreation",
            escape_family="games",
        )

        assert updated.allow_from_level == "medium"
        assert updated.purpose == "controlled_recreation"
        assert updated.escape_family == "games"
        assert service.get_rules("app") == [updated]


def test_active_day_rejects_rule_removal_and_allow_level_updates(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="medium")
        service.start_day()

        for action in (
            lambda: service.remove_rule("site", "example.com"),
            lambda: service.update_rule_allow_from_level("site", "example.com", "low"),
            lambda: service.update_rule_allow_from_level("app", "steam.exe", "low"),
            lambda: service.update_rule_allow_from_level("app", "steam.exe", "high"),
        ):
            try:
                action()
            except ValueError as error:
                assert "Rules are locked during an active day" in str(error)
            else:
                raise AssertionError("active-day rule update should be rejected")

        added = service.add_rule("app", "notepad.exe", allow_from_level="high")

        assert service.get_rules("site")[0].allow_from_level == "high"
        assert service.get_rules("app")[0].allow_from_level == "medium"
        assert added.target == "notepad.exe"


def test_rule_removal_and_allow_level_updates_work_outside_active_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="medium")

        weakened = service.update_rule_allow_from_level("app", "steam.exe", "low")
        service.remove_rule("site", "example.com")

        assert weakened.allow_from_level == "low"
        assert service.get_rules("site") == []
        assert [rule.target for rule in service.get_rules("app")] == ["steam.exe"]

        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.end_day()

        updated_after_end = service.update_rule_allow_from_level(
            "app",
            "steam.exe",
            "high",
        )

        assert updated_after_end.allow_from_level == "high"


def test_starter_rule_presets_create_expected_rules(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        result = service.add_starter_rule_presets()

        assert result.created_count == len(STARTER_RULE_PRESETS)
        assert result.skipped_existing_count == 0
        assert result.failed_presets == ()

        site_rules = {rule.target: rule for rule in service.get_rules("site")}
        app_rules = {rule.target: rule for rule in service.get_rules("app")}

        assert set(site_rules) == {
            "youtube.com",
            "www.youtube.com",
            "youtube.com/shorts/*",
            "www.youtube.com/shorts/*",
            "m.youtube.com/shorts/*",
            "discord.com",
            "reddit.com",
            "mangadex.org",
            "mangalib.me",
        }
        assert set(app_rules) == {
            "steam.exe",
            "steamwebhelper.exe",
            "discord.exe",
            "epicgameslauncher.exe",
            "riotclientservices.exe",
            "battlenet.exe",
        }
        assert site_rules["youtube.com"].allow_from_level == "high"
        assert site_rules["youtube.com"].purpose == "compulsive_stimulation"
        assert site_rules["youtube.com"].escape_family == "video"
        assert site_rules["www.youtube.com"].escape_family == "video"
        assert site_rules["youtube.com/shorts/*"].allow_from_level == "high"
        assert site_rules["youtube.com/shorts/*"].purpose == "compulsive_stimulation"
        assert site_rules["youtube.com/shorts/*"].escape_family == "video"
        assert site_rules["www.youtube.com/shorts/*"].allow_from_level == "high"
        assert site_rules["www.youtube.com/shorts/*"].escape_family == "video"
        assert site_rules["m.youtube.com/shorts/*"].allow_from_level == "high"
        assert site_rules["m.youtube.com/shorts/*"].escape_family == "video"
        assert site_rules["discord.com"].allow_from_level == "high"
        assert site_rules["discord.com"].escape_family == "chat"
        assert site_rules["reddit.com"].allow_from_level == "high"
        assert site_rules["reddit.com"].purpose == "high_risk_escape"
        assert site_rules["reddit.com"].escape_family == "random_browsing"
        assert site_rules["mangadex.org"].allow_from_level == "high"
        assert site_rules["mangadex.org"].escape_family == "reading_binge"
        assert site_rules["mangalib.me"].allow_from_level == "high"
        assert site_rules["mangalib.me"].escape_family == "reading_binge"
        assert app_rules["steam.exe"].allow_from_level == "high"
        assert app_rules["steam.exe"].purpose == "gateway_app"
        assert app_rules["steam.exe"].escape_family == "launcher"
        assert app_rules["steamwebhelper.exe"].allow_from_level == "high"
        assert app_rules["steamwebhelper.exe"].escape_family == "launcher"
        assert app_rules["epicgameslauncher.exe"].allow_from_level == "high"
        assert app_rules["epicgameslauncher.exe"].escape_family == "launcher"
        assert app_rules["riotclientservices.exe"].allow_from_level == "high"
        assert app_rules["riotclientservices.exe"].escape_family == "launcher"
        assert app_rules["battlenet.exe"].allow_from_level == "high"
        assert app_rules["battlenet.exe"].escape_family == "launcher"
        assert app_rules["discord.exe"].allow_from_level == "high"
        assert app_rules["discord.exe"].purpose == "high_risk_escape"
        assert app_rules["discord.exe"].escape_family == "chat"


def test_starter_rule_presets_are_idempotent_and_preserve_existing_rules(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        custom_site = RuleRepository(connection).add(
            rule_type="site",
            target="youtube.com",
            allow_from_level="medium",
            purpose="work_tool",
            escape_family="none",
        )
        custom_app = RuleRepository(connection).add(
            rule_type="app",
            target="steam.exe",
            allow_from_level="medium",
            purpose="controlled_recreation",
            escape_family="games",
        )
        connection.execute(
            "UPDATE rules SET enabled = 0 WHERE id = ?",
            (custom_app.id,),
        )

        result = service.add_starter_rule_presets()

        assert result.created_count == len(STARTER_RULE_PRESETS) - 2
        assert result.skipped_existing_count == 2
        assert result.failed_presets == ()

        all_rules = RuleRepository(connection).list(enabled_only=False)
        by_key = {(rule.rule_type, rule.target): rule for rule in all_rules}
        youtube = by_key[("site", "youtube.com")]
        steam = by_key[("app", "steam.exe")]

        assert youtube.id == custom_site.id
        assert youtube.allow_from_level == "medium"
        assert youtube.purpose == "work_tool"
        assert youtube.escape_family == "none"
        assert steam.id == custom_app.id
        assert steam.enabled is False
        assert steam.allow_from_level == "medium"
        assert steam.purpose == "controlled_recreation"
        assert steam.escape_family == "games"
        assert len(all_rules) == len(STARTER_RULE_PRESETS)

        second = service.add_starter_rule_presets()

        assert second.created_count == 0
        assert second.skipped_existing_count == len(STARTER_RULE_PRESETS)
        assert second.failed_presets == ()
        assert len(RuleRepository(connection).list(enabled_only=False)) == (
            len(STARTER_RULE_PRESETS)
        )


def test_enforcement_mode_defaults_to_preview_and_armed_dry_run_persists(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        default_status = service.get_enforcement_status()

        assert service.get_enforcement_mode() is EnforcementMode.PREVIEW_ONLY
        assert default_status.selected_mode is EnforcementMode.PREVIEW_ONLY
        assert default_status.effective_mode is EnforcementMode.PREVIEW_ONLY
        assert default_status.real_blocking_active is False
        assert default_status.next_available_mode is EnforcementMode.ARMED_DRY_RUN

        armed = service.set_enforcement_mode("armed_dry_run")

        assert armed.selected_mode is EnforcementMode.ARMED_DRY_RUN
        assert armed.effective_mode is EnforcementMode.ARMED_DRY_RUN
        assert service.get_enforcement_mode() is EnforcementMode.ARMED_DRY_RUN

        reloaded = _make_service(connection, test_settings)

        assert reloaded.get_enforcement_mode() is EnforcementMode.ARMED_DRY_RUN

        preview = reloaded.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)

        assert preview.selected_mode is EnforcementMode.PREVIEW_ONLY
        assert reloaded.get_enforcement_mode() is EnforcementMode.PREVIEW_ONLY


def test_real_process_mode_is_available_and_hosts_modes_stay_locked(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=HostsBlocker(hosts_path=tmp_path / "hosts"),
        )
        status = service.get_enforcement_status()

        assert status.process_readiness.ready is True
        assert status.hosts_readiness.ready is True
        assert status.recovery_readiness.ready is False
        assert status.process_readiness.missing_items == ()
        assert {
            check.key: check.ready for check in status.process_readiness.checks
        } == {
            "process_adapter_exists": True,
            "dry_run_scan_available": True,
            "system_process_allowlist": True,
            "process_action_logging": True,
            "process_recovery_interaction": True,
            "real_process_blocking_implementation": True,
        }
        assert status.hosts_readiness.missing_items == ()
        assert {
            check.key: check.ready for check in status.hosts_readiness.checks
        } == {
            "hosts_adapter_exists": True,
            "managed_section_transform": True,
            "backup_supported": True,
            "rollback_supported": True,
            "temp_file_test_support": True,
            "admin_requirement_known": True,
            "recovery_removal_supported": True,
            "real_hosts_blocking_implementation": True,
        }
        assert "Missing recovery path to disable persisted real enforcement modes." in (
            status.recovery_readiness.missing_items
        )
        assert status.full_readiness.ready is True
        assert status.full_readiness.missing_items == ()
        assert {
            check.key: check.ready for check in status.full_readiness.checks
        } == {
            "process_ready": True,
            "hosts_ready": True,
        }

        real_process = service.set_enforcement_mode(
            EnforcementMode.REAL_PROCESS_BLOCKING
        )
        assert real_process.effective_mode is EnforcementMode.REAL_PROCESS_BLOCKING

        service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)
        real_hosts = service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        assert real_hosts.effective_mode is EnforcementMode.REAL_HOSTS_BLOCKING

        service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)
        full = service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        assert full.effective_mode is EnforcementMode.FULL_ENFORCEMENT

        assert service.get_enforcement_mode() is EnforcementMode.FULL_ENFORCEMENT


def test_safe_and_recovery_modes_force_preview_and_block_real_modes(
    tmp_path,
    test_settings,
) -> None:
    safe_settings = replace(test_settings, safe_mode=True)
    recovery_settings = replace(test_settings, recovery_mode=True)

    with initialize_database(tmp_path / "safe.db") as connection:
        service = _make_service(connection, safe_settings)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        status = service.get_enforcement_status()

        assert status.selected_mode is EnforcementMode.ARMED_DRY_RUN
        assert status.effective_mode is EnforcementMode.PREVIEW_ONLY
        assert status.real_blocking_active is False
        try:
            service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)
        except ValueError as error:
            assert "Safe Mode" in str(error)
        else:
            raise AssertionError("Safe Mode should block real enforcement")
        try:
            service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        except ValueError as error:
            assert "Safe Mode" in str(error)
        else:
            raise AssertionError("Safe Mode should block full enforcement")

    with initialize_database(tmp_path / "recovery.db") as connection:
        service = _make_service(connection, recovery_settings)

        status = service.get_enforcement_status()

        assert status.effective_mode is EnforcementMode.PREVIEW_ONLY
        try:
            service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        except ValueError as error:
            assert "Recovery Mode" in str(error)
        else:
            raise AssertionError("Recovery Mode should block real enforcement")
        try:
            service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        except ValueError as error:
            assert "Recovery Mode" in str(error)
        else:
            raise AssertionError("Recovery Mode should block full enforcement")


def test_armed_dry_run_process_scan_logs_matching_app_rule_decisions(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        service.add_rule("site", "steam.example", allow_from_level="high")
        disabled = service.add_rule("app", "discord.exe", allow_from_level="high")
        connection.execute(
            "UPDATE rules SET enabled = 0 WHERE id = ?",
            (disabled.id,),
        )

        assert service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        ) == []

        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        logged = service.run_armed_dry_run_process_scan_cycle(
            process_names=["Steam.EXE", "discord.exe", "unknown.exe"]
        )

        assert len(logged) == 1
        assert logged[0].target == "steam.exe"
        assert logged[0].target_type == "app"
        assert logged[0].decision == "would_block"
        assert logged[0].access_level_at_attempt == "low"
        assert logged[0].allow_from_level == "high"
        assert logged[0].source == "armed_dry_run_process"
        assert logged[0].enforcement_mode == "armed_dry_run"
        assert logged[0].action_taken == "none"
        assert service.list_recent_dry_run_process_attempts() == logged


def test_armed_dry_run_process_scan_uses_access_level_thresholds(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("app", "chat.exe", allow_from_level="medium")
        main = _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        low_attempt = service.run_armed_dry_run_process_scan_cycle(
            process_names=["chat.exe"]
        )[0]

        assert low_attempt.decision == "would_block"
        assert low_attempt.access_level_at_attempt == "low"

        now = now + timedelta(seconds=61)
        _complete_task_after_claim_delay(service, main.id)

        medium_attempt = service.run_armed_dry_run_process_scan_cycle(
            process_names=["chat.exe"]
        )[0]

        assert medium_attempt.decision == "would_allow"
        assert medium_attempt.access_level_at_attempt == "medium"

        now = now + timedelta(seconds=61)
        service.add_rule("app", "game.exe", allow_from_level="high")
        service.start_high_access(5, "play one match")

        high_attempt = service.run_armed_dry_run_process_scan_cycle(
            process_names=["game.exe"]
        )[0]

        assert high_attempt.decision == "would_allow"
        assert high_attempt.access_level_at_attempt == "high"


def test_armed_dry_run_process_scan_suppresses_duplicate_logs(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        first = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        )
        second = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        )
        now = now + timedelta(seconds=60)
        third = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        )

        assert len(first) == 1
        assert second == []
        assert len(third) == 1
        assert len(service.list_recent_dry_run_process_attempts(limit=10)) == 2


def test_recent_dry_run_process_attempts_can_be_filtered(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("app", "chat.exe", allow_from_level="medium")
        main = _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        first = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe", "chat.exe"]
        )
        assert [attempt.decision for attempt in first] == [
            "would_block",
            "would_block",
        ]

        now = now + timedelta(seconds=61)
        _complete_task_after_claim_delay(service, main.id)
        second = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe", "chat.exe"]
        )

        assert [attempt.target for attempt in second] == ["steam.exe", "chat.exe"]
        assert second[1].decision == "would_allow"

        assert [
            attempt.target
            for attempt in service.list_recent_dry_run_process_attempts(
                limit=10,
                decision="would_allow",
            )
        ] == ["chat.exe"]
        assert [
            attempt.target
            for attempt in service.list_recent_dry_run_process_attempts(
                limit=10,
                process_query="STEAM",
            )
        ] == ["steam.exe", "steam.exe"]
        assert [
            attempt.target
            for attempt in service.list_recent_dry_run_process_attempts(
                limit=10,
                access_level="medium",
            )
        ] == ["chat.exe", "steam.exe"]


def test_dry_run_process_summary_counts_today_would_block_attempts(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("app", "chat.exe", allow_from_level="medium")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe", "chat.exe"]
        )
        summary = service.get_dry_run_process_attempt_summary(limit=10)

        assert summary.total_recent_attempts == 2
        assert summary.today_would_block_count == 2
        assert summary.last_would_block_target == "chat.exe"
        assert (
            summary.real_blocking_note
            == "Armed Dry Run logs matching app rules without blocking."
        )
        assert all(attempt.action_taken == "none" for attempt in summary.latest_attempts)


def test_dry_run_process_summary_defaults_to_today_without_deleting_history(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)
        service.run_armed_dry_run_process_scan_cycle(process_names=["steam.exe"])

        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
        today_summary = service.get_dry_run_process_attempt_summary(limit=10)

        assert today_summary.total_recent_attempts == 0
        assert today_summary.today_would_block_count == 0
        assert today_summary.last_would_block_target is None
        assert service.list_recent_dry_run_process_attempts(limit=10) == []

        history_attempts = service.list_recent_dry_run_process_attempts(
            limit=10,
            today_only=False,
        )
        history_summary = service.get_dry_run_process_attempt_summary(
            limit=10,
            today_only=False,
        )

        assert [attempt.target for attempt in history_attempts] == ["steam.exe"]
        assert history_summary.total_recent_attempts == 1
        assert history_summary.last_would_block_target == "steam.exe"


def test_protected_process_names_are_not_dry_run_block_candidates(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("app", "python.exe", allow_from_level="high")
        service.add_rule("app", "selfboss.exe", allow_from_level="high")
        service.add_rule("app", "svchost.exe", allow_from_level="high")
        service.add_rule("app", "game.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        assert is_protected_process_name("PYTHON.EXE") is True
        assert is_protected_process_name("pythonw.exe") is True
        assert is_protected_process_name("selfboss.exe") is True
        assert is_protected_process_name("System Idle Process") is True
        assert is_protected_process_name("game.exe") is False

        logged = service.run_armed_dry_run_process_scan_cycle(
            process_names=[
                "python.exe",
                "selfboss.exe",
                "svchost.exe",
                "game.exe",
            ]
        )

        assert [attempt.target for attempt in logged] == ["game.exe"]
        assert logged[0].decision == "would_block"


def test_real_process_blocking_terminates_blocked_explicit_app_rules(
    tmp_path,
    test_settings,
) -> None:
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        service.add_rule("site", "steam.example", allow_from_level="high")
        disabled = service.add_rule("app", "discord.exe", allow_from_level="high")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (disabled.id,))
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["Steam.EXE", "discord.exe", "unknown.exe"]
        )

        assert terminated == ["steam.exe"]
        assert len(logged) == 1
        assert logged[0].source == "real_process_blocking_process"
        assert logged[0].enforcement_mode == "real_process_blocking"
        assert logged[0].decision == "would_block"
        assert logged[0].action_taken == "terminate_requested"


def test_process_scan_cycles_are_active_day_only(
    tmp_path,
    test_settings,
) -> None:
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        assert service.run_real_process_blocking_scan_cycle(["steam.exe"]) == []
        main = _start_active_day_low(service)
        assert service.run_real_process_blocking_scan_cycle(["steam.exe"])
        _complete_task_after_claim_delay(service, main.id)
        service.end_day()
        assert service.run_real_process_blocking_scan_cycle(["steam.exe"]) == []

    assert terminated == ["steam.exe"]


def test_real_process_blocking_respects_access_thresholds(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def current_now() -> datetime:
        return now

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule("app", "chat.exe", allow_from_level="medium")
        main = _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        low_attempt = service.run_real_process_blocking_scan_cycle(
            process_names=["chat.exe"]
        )[0]

        assert low_attempt.target == "chat.exe"
        assert low_attempt.action_taken == "terminate_requested"
        assert terminated == ["chat.exe"]

        now = now + timedelta(seconds=61)
        _complete_task_after_claim_delay(service, main.id)

        assert service.run_real_process_blocking_scan_cycle(
            process_names=["chat.exe"]
        ) == []
        assert terminated == ["chat.exe"]

        now = now + timedelta(seconds=61)
        service.add_rule("app", "game.exe", allow_from_level="high")
        service.start_high_access(5, "play one match")

        assert service.run_real_process_blocking_scan_cycle(
            process_names=["game.exe"]
        ) == []
        assert terminated == ["chat.exe"]


def test_real_process_blocking_respects_matching_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def current_now() -> datetime:
        return now

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        notepad = service.add_rule("app", "notepad.exe", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.start_planned_use_pass(
            notepad.id,
            "Use notepad for declared work",
            5 * 60,
        )
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["notepad.exe", "steam.exe"]
        )

        assert terminated == ["steam.exe"]
        assert [(attempt.target, attempt.decision, attempt.action_taken) for attempt in logged] == [
            ("notepad.exe", "allowed_by_planned_use_pass", "none"),
            ("steam.exe", "would_block", "terminate_requested"),
        ]

        now = now + timedelta(minutes=6)
        after_expiry = service.run_real_process_blocking_scan_cycle(
            process_names=["notepad.exe"]
        )

        assert [attempt.target for attempt in after_expiry] == ["notepad.exe"]
        assert after_expiry[0].action_taken == "terminate_requested"
        assert terminated == ["steam.exe", "notepad.exe"]


def test_real_process_blocking_pass_for_other_app_does_not_allow_target(
    tmp_path,
    test_settings,
) -> None:
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        notepad = service.add_rule("app", "notepad.exe", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.start_planned_use_pass(
            notepad.id,
            "Use notepad for declared work",
            5 * 60,
        )
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )

        assert terminated == ["steam.exe"]
        assert logged[0].target == "steam.exe"
        assert logged[0].decision == "would_block"
        assert logged[0].action_taken == "terminate_requested"


def test_end_day_ended_pass_no_longer_allows_real_process_blocking(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        notepad = service.add_rule("app", "notepad.exe", allow_from_level="high")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.start_planned_use_pass(
            notepad.id,
            "Use notepad for declared work",
            5 * 60,
        )
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        service.end_day()
        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["notepad.exe"]
        )

        assert service.get_active_planned_use_pass() is None
        assert terminated == []
        assert logged == []


def test_armed_dry_run_respects_matching_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        notepad = service.add_rule("app", "notepad.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.start_planned_use_pass(
            notepad.id,
            "Use notepad for declared work",
            5 * 60,
        )
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        logged = service.run_armed_dry_run_process_scan_cycle(
            process_names=["notepad.exe"]
        )

        assert logged[0].decision == "allowed_by_planned_use_pass"
        assert logged[0].action_taken == "none"


def test_real_process_blocking_classifies_taskkill_failures(
    tmp_path,
    test_settings,
) -> None:
    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        if target == "missing.exe":
            return subprocess.CompletedProcess(
                ["taskkill", "/IM", target],
                128,
                stderr='ERROR: The process "missing.exe" not found.',
            )
        if target == "denied.exe":
            return subprocess.CompletedProcess(
                ["taskkill", "/IM", target],
                5,
                stderr="ERROR: Access is denied.",
            )
        if target == "timeout.exe":
            raise subprocess.TimeoutExpired(["taskkill", "/IM", target], 2)
        return subprocess.CompletedProcess(
            ["taskkill", "/IM", target],
            1,
            stderr="unexpected taskkill failure",
        )

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        for target in ("missing.exe", "denied.exe", "timeout.exe", "failed.exe"):
            service.add_rule("app", target, allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["missing.exe", "denied.exe", "timeout.exe", "failed.exe"]
        )

        assert [attempt.action_taken for attempt in logged] == [
            "not_found",
            "access_denied",
            "failed",
            "failed",
        ]


def test_soft_taskkill_runner_does_not_use_force_flag(monkeypatch) -> None:
    captured: dict[str, object] = {}
    hidden_kwargs = {"creationflags": 123}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_blocker_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        process_blocker_module,
        "_hidden_subprocess_kwargs",
        lambda: hidden_kwargs,
    )

    process_blocker_module._taskkill_soft("notepad.exe")

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert command == ["taskkill", "/IM", "notepad.exe"]
    assert "/F" not in command
    assert kwargs["timeout"] == process_blocker_module.TASKKILL_TIMEOUT_SECONDS
    assert kwargs["creationflags"] == hidden_kwargs["creationflags"]


def test_tasklist_runner_uses_hidden_subprocess_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}
    hidden_kwargs = {"creationflags": 123}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout='"notepad.exe","1"\n')

    monkeypatch.setattr(process_blocker_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        process_blocker_module,
        "_hidden_subprocess_kwargs",
        lambda: hidden_kwargs,
    )

    names = process_blocker_module._tasklist_process_names()

    assert captured["command"] == ["tasklist", "/FO", "CSV", "/NH"]
    assert captured["kwargs"]["creationflags"] == hidden_kwargs["creationflags"]
    assert names == ["notepad.exe"]


def test_protected_processes_are_skipped_by_real_process_blocking(
    tmp_path,
    test_settings,
) -> None:
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule("app", "python.exe", allow_from_level="high")
        service.add_rule("app", "selfboss.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["PYTHON.EXE", "selfboss.exe"]
        )

        assert terminated == []
        assert [attempt.target for attempt in logged] == ["python.exe", "selfboss.exe"]
        assert {attempt.action_taken for attempt in logged} == {"skipped_protected"}


def test_real_process_blocking_suppresses_duplicate_logs(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def current_now() -> datetime:
        return now

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        first = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        second = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        now = now + timedelta(seconds=5)
        third = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        now = now + timedelta(seconds=60)
        fourth = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )

        assert len(first) == 1
        assert second == []
        assert third == []
        assert len(fourth) == 1
        assert terminated == ["steam.exe", "steam.exe", "steam.exe"]
        assert len(service.list_recent_process_enforcement_attempts(limit=10)) == 2


def test_armed_dry_run_does_not_call_real_process_terminator(
    tmp_path,
    test_settings,
) -> None:
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        logged = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        )

        assert terminated == []
        assert logged[0].action_taken == "none"


def test_safe_and_recovery_modes_prevent_armed_dry_run_process_logging(
    tmp_path,
    test_settings,
) -> None:
    safe_settings = replace(test_settings, safe_mode=True)
    recovery_settings = replace(test_settings, recovery_mode=True)

    with initialize_database(tmp_path / "safe.db") as connection:
        service = _make_service(connection, safe_settings)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        assert service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        ) == []
        assert service.list_recent_access_attempts() == []

    with initialize_database(tmp_path / "recovery.db") as connection:
        service = _make_service(connection, recovery_settings)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        assert service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        ) == []
        assert service.list_recent_access_attempts() == []


def test_website_rule_target_validation(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        for target in (
            "youtube.com",
            "docs.python.org",
            "site.com.ua",
            "youtube.com/shorts",
            "reddit.com/r/all/",
            "x.com/home",
            "*.example.com/feed/*",
        ):
            service.add_rule("site", target)
        service.add_rule("site", "youtube.com/shorts/")
        service.add_rule("site", "youtube.com/shorts/*")

        assert [rule.target for rule in service.get_rules("site")] == [
            "youtube.com",
            "docs.python.org",
            "site.com.ua",
            "youtube.com/shorts/*",
            "reddit.com/r/all/*",
            "x.com/home/*",
            "*.example.com/feed/*",
        ]

        for target in (
            "https://youtube.com",
            "http://youtube.com/shorts/*",
            "youtube.com/watch?v=123",
            "youtube.com/#shorts",
            "youtube",
            "steam.exe",
            "steam.exe/feed/*",
            ".com",
            "youtube.",
            "*",
            "*/*",
            "*.*/*",
            "/shorts/*",
            "youtube.com/",
            "youtube.com/*",
            "*.com/feed/*",
        ):
            _assert_rule_rejected(service, "site", target)


def test_app_rule_target_validation(tmp_path, test_settings) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        for target in ("steam.exe", "epicgameslauncher.exe"):
            service.add_rule("app", target)

        assert [rule.target for rule in service.get_rules("app")] == [
            "steam.exe",
            "epicgameslauncher.exe",
        ]

        for target in (
            "steam",
            "youtube.com",
            r"C:\Program Files\Steam\steam.exe",
            "app with spaces.exe",
            "steam.exe.exe",
        ):
            _assert_rule_rejected(service, "app", target)


def test_rule_preview_blocks_high_only_item_in_medium(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.add_rule("site", "youtube.com", allow_from_level="high")

        preview = service.preview_blocking()

        assert preview.access_level is AccessLevel.MEDIUM
        assert preview.blocked_sites == ["youtube.com"]
        assert preview.allowed_sites == []
        assert preview.sites == ["youtube.com"]
        assert preview.message == "Preview only — Test Mode. No system changes."


def test_rule_preview_allows_medium_item_after_unlock(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.add_rule("app", "discord.exe", allow_from_level="medium")

        preview = service.preview_blocking()

        assert preview.access_level is AccessLevel.MEDIUM
        assert preview.blocked_apps == []
        assert preview.allowed_apps == ["discord.exe"]
        assert preview.apps == []


def test_rule_preview_ignores_disabled_rules(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("site", "disabled.example", allow_from_level="high")
        connection.execute(
            "UPDATE rules SET enabled = 0 WHERE target = ?",
            ("disabled.example",),
        )

        preview = service.preview_blocking()

        assert preview.blocked_sites == []
        assert preview.allowed_sites == []


def test_hosts_blocking_dry_run_preview_uses_blocked_enabled_site_rules_only(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.add_rule("site", "youtube.com/shorts/*", allow_from_level="high")
        service.add_rule("site", "disabled.example", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="high")
        connection.execute(
            "UPDATE rules SET enabled = 0 WHERE target = ?",
            ("disabled.example",),
        )

        preview = service.preview_hosts_blocking_dry_run()

        assert preview.blocked_domains == ["youtube.com", "www.youtube.com"]
        assert preview.hosts_entries == [
            "127.0.0.1 youtube.com",
            "127.0.0.1 www.youtube.com",
        ]
        assert "127.0.0.1 youtube.com" in preview.managed_section
        assert "127.0.0.1 www.youtube.com" in preview.managed_section
        assert "shorts" not in preview.managed_section
        assert "steam.exe" not in preview.managed_section
        assert preview.message == "Dry-run only. Websites are not blocked yet."


def test_real_hosts_blocking_applies_access_level_site_rules_only(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            "# SELF-BOSS BEGIN\n"
            "127.0.0.1 reddit.com\n"
            "# SELF-BOSS END\n"
        ),
        encoding="utf-8",
    )
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="medium")
        service.add_rule("site", "docs.python.org", allow_from_level="low")
        service.add_rule("app", "steam.exe", allow_from_level="high")
        disabled = service.add_rule("site", "disabled.example", allow_from_level="high")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (disabled.id,))

        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        planning_content = hosts_path.read_text(encoding="utf-8")
        planning_status = service.get_hosts_blocking_status()

        assert "youtube.com" not in planning_content
        assert "reddit.com" not in planning_content
        assert planning_status.status == "armed_idle"
        assert planning_status.message == (
            "Websites: Blocking armed. Starts when day starts."
        )

        main = service.create_task(title="Main", kind=TaskKind.MAIN)
        service.start_day()
        service.run_real_hosts_blocking_cycle(force=True)
        low_content = hosts_path.read_text(encoding="utf-8")
        status = service.get_hosts_blocking_status()

        assert "127.0.0.1 youtube.com" in low_content
        assert "127.0.0.1 reddit.com" in low_content
        assert status.blocked_domain_examples == (
            "youtube.com",
            "www.youtube.com",
            "reddit.com",
        )
        assert "docs.python.org" not in low_content
        assert "steam.exe" not in low_content
        assert "disabled.example" not in low_content

        _complete_task_after_claim_delay(service, main.id)
        service.run_real_hosts_blocking_cycle(force=True)
        medium_content = hosts_path.read_text(encoding="utf-8")

        assert "127.0.0.1 youtube.com" in medium_content
        assert "reddit.com" not in medium_content

        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_high_access(5, "planned recreation")
        service.run_real_hosts_blocking_cycle(force=True)
        high_content = hosts_path.read_text(encoding="utf-8")

        assert "youtube.com" not in high_content
        assert "reddit.com" not in high_content

        service.end_day()
        closed_content = hosts_path.read_text(encoding="utf-8")
        closed_status = service.get_hosts_blocking_status()

        assert "youtube.com" not in closed_content
        assert "reddit.com" not in closed_content
        assert closed_status.status == "armed_idle"


def test_real_hosts_blocking_respects_site_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            hosts_blocker=hosts_blocker,
        )
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        _create_required_main(service)
        service.start_day()
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_planned_use_pass(
            rule.id,
            "watching one planned tutorial",
            10 * 60,
        )

        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)

        hosts_content = hosts_path.read_text(encoding="utf-8") if hosts_path.exists() else ""
        assert "youtube.com" not in hosts_content
        assert service.get_hosts_blocking_status().blocked_domain_count == 0


def test_real_hosts_blocking_skips_unchanged_signature_and_clears_on_safe_mode(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "youtube.com", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        first_write_count = hosts_blocker.write_attempts

        result = service.run_real_hosts_blocking_cycle()

        assert result is None
        assert hosts_blocker.write_attempts == first_write_count

    safe_settings = replace(test_settings, safe_mode=True)
    with initialize_database(tmp_path / "safe.db") as connection:
        safe_hosts = CountingHostsBlocker(hosts_path=hosts_path)
        safe_service = _make_service(
            connection,
            safe_settings,
            hosts_blocker=safe_hosts,
        )
        safe_service.app_settings.set_enforcement_mode(
            EnforcementMode.REAL_HOSTS_BLOCKING.value
        )

        clear_result = safe_service.run_real_hosts_blocking_cycle(force=True)

        assert clear_result is not None
        assert clear_result.success is True
        assert "youtube.com" not in hosts_path.read_text(encoding="utf-8")


def test_real_hosts_permission_denied_is_suppressed_for_same_signature(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    hosts_blocker = PermissionDeniedCountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "youtube.com", allow_from_level="high")

        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        repeated = service.run_real_hosts_blocking_cycle()

        assert repeated is not None
        assert repeated.status == "permission_denied"
        assert hosts_blocker.write_attempts == 1
        assert service.get_hosts_blocking_status().status == "permission_denied"

        service.add_rule("site", "reddit.com", allow_from_level="high")
        service.run_real_hosts_blocking_cycle()

        assert hosts_blocker.write_attempts == 2


def test_real_hosts_blocking_clears_managed_section_when_switching_modes(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "reddit.com", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)

        assert "127.0.0.1 reddit.com" in hosts_path.read_text(encoding="utf-8")
        assert "127.0.0.1 www.reddit.com" in hosts_path.read_text(encoding="utf-8")

        service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)

        cleared_content = hosts_path.read_text(encoding="utf-8")
        assert "127.0.0.1 localhost" in cleared_content
        assert "reddit.com" not in cleared_content
        assert "# SELF-BOSS BEGIN" not in cleared_content
        assert "# SELF-BOSS END" not in cleared_content
        assert hosts_blocker.write_attempts == 2


def test_real_hosts_blocking_clears_when_switching_to_process_mode(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("10.0.0.5 intranet.local\n", encoding="utf-8")
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "reddit.com", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)

        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        cleared_content = hosts_path.read_text(encoding="utf-8")
        assert "10.0.0.5 intranet.local" in cleared_content
        assert "reddit.com" not in cleared_content
        assert "# SELF-BOSS BEGIN" not in cleared_content
        assert "# SELF-BOSS END" not in cleared_content


def test_real_hosts_clear_permission_denied_is_reported_without_repeat_spam(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text(
        (
            "127.0.0.1 localhost\n"
            "# SELF-BOSS BEGIN\n"
            "127.0.0.1 reddit.com\n"
            "# SELF-BOSS END\n"
        ),
        encoding="utf-8",
    )
    hosts_blocker = PermissionDeniedCountingHostsBlocker(hosts_path=hosts_path)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            hosts_blocker=hosts_blocker,
        )
        service.app_settings.set_enforcement_mode(
            EnforcementMode.REAL_HOSTS_BLOCKING.value
        )

        result = service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)
        repeated = service.run_real_hosts_blocking_cycle()
        status = service.get_hosts_blocking_status()

        assert result.effective_mode is EnforcementMode.PREVIEW_ONLY
        assert repeated is None
        assert hosts_blocker.write_attempts == 1
        assert status.status == "permission_denied"
        assert "Run LoopGuard as administrator" in status.message


def test_full_enforcement_combines_process_and_hosts_paths(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="high")
        disabled = service.add_rule("site", "disabled.example", allow_from_level="high")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (disabled.id,))
        _start_active_day_low(service)

        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        hosts_content = hosts_path.read_text(encoding="utf-8")

        assert terminated == ["steam.exe"]
        assert logged[0].action_taken == "terminate_requested"
        assert "127.0.0.1 reddit.com" in hosts_content
        assert "127.0.0.1 www.reddit.com" in hosts_content
        assert "disabled.example" not in hosts_content


def test_full_enforcement_high_and_planned_use_passes_suppress_blocks(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def current_now() -> datetime:
        return now

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="high")
        main = _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        assert "127.0.0.1 reddit.com" in hosts_path.read_text(encoding="utf-8")

        _complete_task_after_claim_delay(service, main.id)
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_high_access(5, "planned recreation")

        assert service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        ) == []
        high_hosts_content = (
            hosts_path.read_text(encoding="utf-8") if hosts_path.exists() else ""
        )
        assert "reddit.com" not in high_hosts_content

        service.end_high_access()
        now = now + timedelta(seconds=5)
        logged = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        hosts_status = service.get_hosts_blocking_status()

        assert terminated == ["steam.exe"]
        assert logged[0].action_taken == "terminate_requested"
        assert "127.0.0.1 reddit.com" in hosts_path.read_text(encoding="utf-8")
        assert hosts_status.message.startswith("Websites: Active at hosts/DNS level")

    with initialize_database(tmp_path / "passes.db") as connection:
        pass_hosts_path = tmp_path / "pass-hosts"
        pass_hosts = CountingHostsBlocker(hosts_path=pass_hosts_path)
        pass_terminated: list[str] = []

        def pass_terminate(target: str) -> subprocess.CompletedProcess[str]:
            pass_terminated.append(target)
            return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

        service = _make_service(
            connection,
            test_settings,
            now=current_now,
            process_blocker=ProcessBlocker(termination_runner=pass_terminate),
            hosts_blocker=pass_hosts,
        )
        notepad = service.add_rule("app", "notepad.exe", allow_from_level="high")
        youtube = service.add_rule("site", "youtube.com", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        assert "127.0.0.1 youtube.com" in pass_hosts_path.read_text(encoding="utf-8")
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)

        service.start_planned_use_pass(
            notepad.id,
            "Use notepad for declared work",
            5 * 60,
        )

        assert service.run_real_process_blocking_scan_cycle(
            process_names=["notepad.exe"]
        )[0].decision == "allowed_by_planned_use_pass"
        assert pass_terminated == []

        service.end_active_planned_use_pass()
        service.start_planned_use_pass(
            youtube.id,
            "Watch one planned tutorial",
            5 * 60,
        )

        hosts_content = (
            pass_hosts_path.read_text(encoding="utf-8")
            if pass_hosts_path.exists()
            else ""
        )
        assert "youtube.com" not in hosts_content

        service.end_active_planned_use_pass()
        assert "127.0.0.1 youtube.com" in pass_hosts_path.read_text(encoding="utf-8")


def test_full_enforcement_mode_switching_clears_or_keeps_hosts(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("10.0.0.5 intranet.local\n", encoding="utf-8")
    hosts_blocker = CountingHostsBlocker(hosts_path=hosts_path)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="high")
        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)

        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)
        process_only_content = hosts_path.read_text(encoding="utf-8")

        assert "10.0.0.5 intranet.local" in process_only_content
        assert "reddit.com" not in process_only_content
        assert service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )

        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)

        assert "127.0.0.1 reddit.com" in hosts_path.read_text(encoding="utf-8")
        assert service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        ) == []

        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)

        preview_content = hosts_path.read_text(encoding="utf-8")
        assert "10.0.0.5 intranet.local" in preview_content
        assert "reddit.com" not in preview_content
        assert service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        ) == []

        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)

        armed_content = hosts_path.read_text(encoding="utf-8")
        assert "10.0.0.5 intranet.local" in armed_content
        assert "reddit.com" not in armed_content
        assert service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        ) == []
        dry_run_attempts = service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe"]
        )
        assert dry_run_attempts
        assert terminated == ["steam.exe"]


def test_full_enforcement_permission_denied_keeps_process_status_separate(
    tmp_path,
    test_settings,
) -> None:
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    hosts_blocker = PermissionDeniedCountingHostsBlocker(hosts_path=hosts_path)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="high")

        _start_active_day_low(service)
        service.set_enforcement_mode(EnforcementMode.FULL_ENFORCEMENT)
        process_attempt = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )[0]
        status = service.get_hosts_blocking_status()

        assert process_attempt.action_taken == "terminate_requested"
        assert terminated == ["steam.exe"]
        assert status.status == "permission_denied"
        assert status.active is False
        assert "administrator" in status.message
        assert "App/process blocking remains separate" in status.message

        hosts_path.write_text(
            (
                "127.0.0.1 localhost\n"
                "# SELF-BOSS BEGIN\n"
                "127.0.0.1 reddit.com\n"
                "# SELF-BOSS END\n"
            ),
            encoding="utf-8",
        )
        service.set_enforcement_mode(EnforcementMode.PREVIEW_ONLY)
        repeated = service.run_real_hosts_blocking_cycle()

        assert repeated is None
        assert hosts_blocker.write_attempts == 2


def test_log_manual_rule_attempt_records_would_block_decision(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )

        attempt = service.log_manual_rule_attempt(rule.id)

        assert attempt.occurred_at == "2026-05-08T09:00:00+00:00"
        assert attempt.target_type == "site"
        assert attempt.target == "youtube.com"
        assert attempt.rule_id == rule.id
        assert attempt.access_level_at_attempt == "low"
        assert attempt.decision == "would_block_in_current_mode"
        assert attempt.allow_from_level == "high"
        assert attempt.purpose == "compulsive_stimulation"
        assert attempt.escape_family == "video"
        assert attempt.source == "manual_test"
        assert service.list_recent_access_attempts() == [attempt]


def test_log_manual_rule_attempt_records_allowed_decision(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        rule = service.add_rule(
            "app",
            "discord.exe",
            allow_from_level="medium",
            purpose="essential_communication",
            escape_family="chat",
        )

        attempt = service.log_manual_rule_attempt(rule.id)

        assert attempt.target_type == "app"
        assert attempt.target == "discord.exe"
        assert attempt.access_level_at_attempt == "medium"
        assert attempt.decision == "allowed_now"
        assert attempt.allow_from_level == "medium"
        assert attempt.purpose == "essential_communication"
        assert attempt.escape_family == "chat"


def test_log_manual_rule_attempt_uses_surrender_override(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        _create_required_main(service)
        service.start_day()
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        service.activate_surrender()

        attempt = service.log_manual_rule_attempt(rule.id)

        assert service.preview_blocking().restriction_state == "surrender"
        assert attempt.target == "youtube.com"
        assert attempt.decision == "allowed_now"
        assert attempt.access_level_at_attempt == "low"


def test_log_manual_rule_attempt_rejects_disabled_rules(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        rule = service.add_rule("site", "disabled.example", allow_from_level="high")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (rule.id,))

        try:
            service.log_manual_rule_attempt(rule.id)
        except ValueError as error:
            assert "Disabled rules are ignored" in str(error)
        else:
            raise AssertionError("disabled rule attempts should be rejected")

        assert service.list_recent_access_attempts() == []


def test_start_planned_use_pass_creates_snapshot_without_access_or_reward_changes(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        before = service.dashboard_snapshot()

        planned_pass = service.start_planned_use_pass(
            rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )
        after = service.dashboard_snapshot()

        assert planned_pass.rule_id == rule.id
        assert planned_pass.target_type == "site"
        assert planned_pass.target == "youtube.com"
        assert planned_pass.purpose == "compulsive_stimulation"
        assert planned_pass.escape_family == "video"
        assert planned_pass.reason == "Watch one PySide6 tutorial"
        assert planned_pass.duration_seconds == 900
        assert planned_pass.started_at == "2026-05-08T09:00:00+00:00"
        assert planned_pass.expires_at == "2026-05-08T09:15:00+00:00"
        assert planned_pass.status == "active"
        assert service.get_active_planned_use_pass() == planned_pass
        assert after.access_level is before.access_level
        assert after.reward_balance_seconds == before.reward_balance_seconds
        assert after.high_active is before.high_active
        assert service.get_rules("site")[0].allow_from_level == "high"
        assert service.get_rules("site")[0] == rule


def test_dashboard_snapshot_surfaces_active_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")

        empty_snapshot = service.dashboard_snapshot()
        service.start_planned_use_pass(
            rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )
        active_snapshot = service.dashboard_snapshot()
        now = start + timedelta(minutes=5)
        later_snapshot = service.dashboard_snapshot()

        assert empty_snapshot.active_planned_use_pass is None
        assert empty_snapshot.active_planned_use_pass_remaining_seconds == 0
        assert active_snapshot.active_planned_use_pass is not None
        assert active_snapshot.active_planned_use_pass.target == "youtube.com"
        assert active_snapshot.active_planned_use_pass_remaining_seconds == 15 * 60
        assert later_snapshot.active_planned_use_pass is not None
        assert later_snapshot.active_planned_use_pass_remaining_seconds == 10 * 60


def test_dashboard_snapshot_hides_ended_and_expired_planned_use_pass(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")

        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        service.end_active_planned_use_pass()
        ended_snapshot = service.dashboard_snapshot()

        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        now = start + timedelta(minutes=5)
        expired_snapshot = service.dashboard_snapshot()

        assert ended_snapshot.active_planned_use_pass is None
        assert ended_snapshot.active_planned_use_pass_remaining_seconds == 0
        assert expired_snapshot.active_planned_use_pass is None
        assert expired_snapshot.active_planned_use_pass_remaining_seconds == 0
        assert service.list_recent_planned_use_passes(limit=1)[0].status == "expired"


def test_start_planned_use_pass_validates_rule_reason_duration_and_single_active_pass(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (rule.id,))

        for kwargs, expected in (
            ((999, "Watch tutorial", 600), "Rule not found"),
            ((rule.id, "Watch tutorial", 600), "Disabled rules cannot"),
            ((rule.id, "short", 600), "Disabled rules cannot"),
        ):
            try:
                service.start_planned_use_pass(*kwargs)
            except (KeyError, ValueError) as error:
                assert expected in str(error)
            else:
                raise AssertionError("planned-use pass should have been rejected")

    with initialize_database(tmp_path / "selfboss-validations.db") as connection:
        service = _make_service(connection, test_settings)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        for reason, duration, expected in (
            ("short", 600, "at least 8 characters"),
            ("Watch tutorial", 60, "at least 5 minutes"),
            ("Watch tutorial", 30 * 60, "cannot exceed 25 minutes"),
        ):
            try:
                service.start_planned_use_pass(rule.id, reason, duration)
            except ValueError as error:
                assert expected in str(error)
            else:
                raise AssertionError("planned-use pass should have been rejected")

        service.start_planned_use_pass(rule.id, "Watch tutorial", 10 * 60)
        try:
            service.start_planned_use_pass(rule.id, "Watch another tutorial", 10 * 60)
        except ValueError as error:
            assert "already active" in str(error)
        else:
            raise AssertionError("second active planned-use pass should be rejected")


def test_planned_use_pass_overrides_preview_and_manual_attempt_until_ended(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )

        service.start_planned_use_pass(
            rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )
        preview = service.preview_blocking()
        attempt = service.log_manual_rule_attempt(rule.id)

        assert preview.active_planned_use_pass is not None
        assert preview.allowed_sites == ["youtube.com"]
        assert preview.blocked_sites == []
        assert attempt.decision == "allowed_by_planned_use_pass"
        assert attempt.access_level_at_attempt == "low"

        ended = service.end_active_planned_use_pass()
        next_preview = service.preview_blocking()
        next_attempt = service.log_manual_rule_attempt(rule.id)

        assert ended is not None
        assert ended.status == "ended"
        assert next_preview.active_planned_use_pass is None
        assert next_preview.allowed_sites == []
        assert next_preview.blocked_sites == ["youtube.com"]
        assert next_attempt.decision == "would_block_in_current_mode"


def test_planned_use_pass_only_overrides_matching_rule(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        pass_rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        other_rule = service.add_rule("site", "reddit.com", allow_from_level="high")

        service.start_planned_use_pass(
            pass_rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )

        preview = service.preview_blocking()
        other_attempt = service.log_manual_rule_attempt(other_rule.id)
        pass_attempt = service.log_manual_rule_attempt(pass_rule.id)

        assert preview.allowed_sites == ["youtube.com"]
        assert preview.blocked_sites == ["reddit.com"]
        assert other_attempt.decision == "would_block_in_current_mode"
        assert pass_attempt.decision == "allowed_by_planned_use_pass"


def test_planned_use_pass_keeps_rule_snapshot_after_rule_metadata_changes(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )

        planned_pass = service.start_planned_use_pass(
            rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )
        updated_rule = service.update_rule_allow_from_level(
            "site",
            "youtube.com",
            "low",
            purpose="work_tool",
            escape_family="none",
        )
        active_pass = service.get_active_planned_use_pass()
        attempt = service.log_manual_rule_attempt(rule.id)

        assert updated_rule.allow_from_level == "low"
        assert updated_rule.purpose == "work_tool"
        assert updated_rule.escape_family == "none"
        assert active_pass == planned_pass
        assert active_pass is not None
        assert active_pass.purpose == "compulsive_stimulation"
        assert active_pass.escape_family == "video"
        assert attempt.decision == "allowed_by_planned_use_pass"
        assert service.get_rules("site")[0] == updated_rule


def test_expired_planned_use_pass_no_longer_applies(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)

        now = start + timedelta(minutes=5)
        preview = service.preview_blocking()
        recent_pass = service.list_recent_planned_use_passes(limit=1)[0]

        assert service.get_active_planned_use_pass() is None
        assert preview.active_planned_use_pass is None
        assert preview.blocked_sites == ["youtube.com"]
        assert recent_pass.status == "expired"


def test_surrender_still_overrides_planned_use_pass_decision(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        _create_required_main(service)
        service.start_day()
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 25 * 60)
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        service.activate_surrender()

        attempt = service.log_manual_rule_attempt(rule.id)
        preview = service.preview_blocking()

        assert preview.restriction_state == "surrender"
        assert preview.allowed_sites == ["youtube.com"]
        assert attempt.decision == "allowed_now"


def test_recent_attempt_summary_is_empty_without_attempts(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        summary = service.get_recent_attempt_summary()

        assert summary.total_attempts == 0
        assert summary.by_escape_family == {}
        assert summary.by_purpose == {}
        assert summary.by_decision == {}
        assert summary.recent_family_sequence == []
        assert summary.possible_switching_detected is False
        assert summary.pattern_explanation == "No Test Mode attempts logged yet."
        assert summary.suggested_next_action == (
            "Log a test attempt from a selected rule to see a pattern."
        )


def test_recent_attempt_summary_counts_and_detects_switching(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        youtube = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        steam = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="games",
        )
        discord = service.add_rule(
            "app",
            "discord.exe",
            allow_from_level="medium",
            purpose="essential_communication",
            escape_family="chat",
        )

        service.log_manual_rule_attempt(youtube.id)
        service.log_manual_rule_attempt(steam.id)
        service.log_manual_rule_attempt(discord.id)
        service.log_manual_rule_attempt(youtube.id)
        before = service.list_recent_access_attempts(limit=10)

        summary = service.get_recent_attempt_summary(limit=10)
        after = service.list_recent_access_attempts(limit=10)

        assert summary.total_attempts == 4
        assert summary.by_escape_family == {"video": 2, "chat": 1, "games": 1}
        assert summary.by_purpose == {
            "compulsive_stimulation": 2,
            "essential_communication": 1,
            "gateway_app": 1,
        }
        assert summary.by_decision == {"would_block_in_current_mode": 4}
        assert summary.recent_family_sequence == ["video", "games", "chat", "video"]
        assert summary.possible_switching_detected is True
        assert summary.pattern_explanation == (
            "Possible escape switching detected: recent attempts moved across "
            "3 families: video -> games -> chat -> video."
        )
        assert summary.suggested_next_action == (
            "Consider returning to the anchor task, using earned Recreation, or "
            "entering Recovery if the day is breaking."
        )
        assert after == before


def test_recent_attempt_summary_ignores_none_for_switching_detection(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        docs = service.add_rule(
            "site",
            "docs.python.org",
            allow_from_level="low",
            purpose="work_tool",
            escape_family="none",
        )
        youtube = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )

        service.log_manual_rule_attempt(docs.id)
        only_none = service.get_recent_attempt_summary()
        service.log_manual_rule_attempt(youtube.id)
        one_family = service.get_recent_attempt_summary()

        assert only_none.by_escape_family == {"none": 1}
        assert only_none.recent_family_sequence == []
        assert only_none.possible_switching_detected is False
        assert only_none.pattern_explanation == (
            "Attempts are being logged, but they are not grouped into escape "
            "families yet."
        )
        assert only_none.suggested_next_action == (
            "Add escape families to rules to make the summary more useful."
        )
        assert one_family.by_escape_family == {"video": 1, "none": 1}
        assert one_family.recent_family_sequence == ["video"]
        assert one_family.possible_switching_detected is False
        assert one_family.pattern_explanation == (
            "Recent attempts are concentrated in one escape family: video."
        )
        assert one_family.suggested_next_action == (
            "Check whether this family should stay HIGH during Focus/Utility."
        )


def test_recent_attempt_summary_respects_limit(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        video = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        games = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="games",
        )

        service.log_manual_rule_attempt(video.id)
        service.log_manual_rule_attempt(games.id)

        latest_only = service.get_recent_attempt_summary(limit=1)
        none = service.get_recent_attempt_summary(limit=0)

        assert latest_only.total_attempts == 1
        assert latest_only.by_escape_family == {"games": 1}
        assert latest_only.recent_family_sequence == ["games"]
        assert latest_only.possible_switching_detected is False
        assert latest_only.pattern_explanation == (
            "Recent attempts are concentrated in one escape family: games."
        )
        assert none.total_attempts == 0


def test_recent_attempt_summary_uses_readable_family_labels(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        reading = service.add_rule(
            "site",
            "manga.example",
            allow_from_level="high",
            purpose="high_risk_escape",
            escape_family="reading_binge",
        )
        random = service.add_rule(
            "site",
            "random.example",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="random_browsing",
        )

        service.log_manual_rule_attempt(reading.id)
        service.log_manual_rule_attempt(random.id)

        summary = service.get_recent_attempt_summary()

        assert summary.pattern_explanation == (
            "Possible escape switching detected: recent attempts moved across "
            "2 families: reading binge -> random browsing."
        )


def test_recent_attempt_summary_defaults_to_today_and_preserves_history(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        discord = service.add_rule(
            "app",
            "discord.exe",
            allow_from_level="high",
            purpose="high_risk_escape",
            escape_family="chat",
        )
        steam = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        epic = service.add_rule(
            "app",
            "epicgameslauncher.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        manga = service.add_rule(
            "site",
            "manga.example",
            allow_from_level="high",
            purpose="high_risk_escape",
            escape_family="reading_binge",
        )

        service.log_manual_rule_attempt(discord.id)
        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

        empty_today = service.get_recent_attempt_summary()

        assert empty_today.total_attempts == 0
        assert empty_today.recent_family_sequence == []
        assert empty_today.possible_switching_detected is False
        assert service.list_recent_access_attempts() == []
        assert service.list_recent_access_attempts(today_only=False)[0].target == (
            "discord.exe"
        )

        service.log_manual_rule_attempt(discord.id)
        service.log_manual_rule_attempt(steam.id)
        service.log_manual_rule_attempt(epic.id)
        service.log_manual_rule_attempt(manga.id)
        today_summary = service.get_recent_attempt_summary(limit=10)

        assert today_summary.total_attempts == 4
        assert today_summary.recent_family_sequence == [
            "chat",
            "launcher",
            "reading_binge",
        ]
        assert today_summary.pattern_explanation == (
            "Possible escape switching detected: recent attempts moved across "
            "3 families: chat -> launcher -> reading binge."
        )
        assert len(service.list_recent_access_attempts(today_only=False)) == 5


def test_recent_attempt_summary_text_is_neutral(
    tmp_path,
    test_settings,
) -> None:
    shame_terms = ("failure", "relapse", "bad", "weak", "addicted", "punish")
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        service.log_manual_rule_attempt(rule.id)

        summary = service.get_recent_attempt_summary()
        combined_text = (
            f"{summary.pattern_explanation} {summary.suggested_next_action}".lower()
        )

        for term in shame_terms:
            assert term not in combined_text


def test_dashboard_snapshot_includes_empty_recent_attempt_summary(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        snapshot = service.dashboard_snapshot()

        assert snapshot.recent_attempt_summary == service.get_recent_attempt_summary()
        assert snapshot.recent_attempt_summary.total_attempts == 0
        assert snapshot.recent_attempt_summary.pattern_explanation == (
            "No Test Mode attempts logged yet."
        )
        assert snapshot.recent_attempt_summary.suggested_next_action == (
            "Log a test attempt from a selected rule to see a pattern."
        )
        assert snapshot.browser_escape_summary.has_attempts is False
        assert snapshot.browser_escape_summary.total_attempts == 0
        assert snapshot.browser_escape_summary.message == (
            "No browser escapes logged today."
        )


def test_browser_escape_summary_counts_today_browser_attempts_only(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target="www.youtube.com",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="browser",
            enforcement_mode="full_enforcement",
            action_taken="browser_redirect",
            matched_scope="path",
            matched_rule_target="youtube.com/shorts/*",
            url_family="youtube",
            path_kind="youtube_shorts",
            reason_code="path_rule_blocked",
        )
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target="www.youtube.com",
            rule_id=None,
            access_level_at_attempt="high",
            decision="would_allow",
            allow_from_level="high",
            source="browser",
            enforcement_mode="full_enforcement",
            action_taken="allowed",
            matched_scope="path",
            matched_rule_target="youtube.com/shorts/*",
            url_family="youtube",
            path_kind="youtube_shorts",
            reason_code="access_level_allowed",
        )
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target="www.reddit.com",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="browser",
            enforcement_mode="full_enforcement",
            action_taken="browser_redirect",
            matched_scope="domain",
            matched_rule_target=None,
            url_family="reddit",
            path_kind="reddit",
            reason_code="domain_rule_blocked",
        )
        service.access_attempts.add(
            occurred_at=(now - timedelta(days=1)).isoformat(),
            target_type="site",
            target="old.example",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="browser",
            enforcement_mode="full_enforcement",
            action_taken="browser_redirect",
        )
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="app",
            target="steam.exe",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="real_process_blocking_process",
            enforcement_mode="real_process_blocking",
            action_taken="terminate_requested",
        )

        summary = service.get_browser_escape_summary()

        assert summary.has_attempts is True
        assert summary.total_attempts == 3
        assert summary.last_attempt is not None
        assert summary.last_attempt.target == "www.reddit.com"
        assert summary.top_targets[0].display_target == "youtube.com/shorts/*"
        assert summary.top_targets[0].count == 2
        assert summary.top_targets[1].display_target == "www.reddit.com"
        assert summary.top_targets[1].count == 1


def test_browser_escape_summary_uses_privacy_safe_targets(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target="https://www.youtube.com/shorts/abc?secret=query",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="browser",
            enforcement_mode="full_enforcement",
            action_taken="browser_redirect",
            matched_scope="path",
            matched_rule_target="youtube.com/shorts/*?secret=query",
            url_family="youtube",
            path_kind="youtube_shorts",
            reason_code="path_rule_blocked",
        )

        summary = service.get_browser_escape_summary()
        rendered = " ".join(target.display_target for target in summary.top_targets)

        assert "secret=query" not in rendered
        assert "https://" not in rendered
        assert summary.top_targets[0].display_target == "youtube.com/shorts/*"


def test_dashboard_snapshot_surfaces_recent_attempt_switching_summary(
    tmp_path,
    test_settings,
) -> None:
    shame_terms = ("failure", "relapse", "bad", "weak", "addicted", "punish")
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        video = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        games = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="games",
        )

        service.log_manual_rule_attempt(video.id)
        service.log_manual_rule_attempt(games.id)
        snapshot = service.dashboard_snapshot()
        summary = snapshot.recent_attempt_summary
        combined_text = (
            f"{summary.pattern_explanation} {summary.suggested_next_action}".lower()
        )

        assert summary == service.get_recent_attempt_summary()
        assert summary.total_attempts == 2
        assert summary.possible_switching_detected is True
        assert summary.pattern_explanation == (
            "Possible escape switching detected: recent attempts moved across "
            "2 families: video -> games."
        )
        for term in shame_terms:
            assert term not in combined_text


def test_rule_preview_allows_everything_while_surrender_active(
    tmp_path,
    test_settings,
) -> None:
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=current_now)
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.add_rule("app", "discord.exe", allow_from_level="medium")
        _create_required_main(service)
        service.start_day()
        now = start + timedelta(seconds=SURRENDER_DELAY_SECONDS)
        service.activate_surrender()

        preview = service.preview_blocking()

        assert preview.restriction_state == "surrender"
        assert preview.blocked_sites == []
        assert preview.blocked_apps == []
        assert preview.sites == []
        assert preview.apps == []
        assert preview.allowed_sites == ["youtube.com"]
        assert preview.allowed_apps == ["discord.exe"]
        assert "Surrender active" in preview.message
        assert "Test Mode" in preview.message


def test_rule_preview_uses_medium_baseline_during_bad_day(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.add_rule("site", "medium.example", allow_from_level="medium")
        service.add_rule("site", "high.example", allow_from_level="high")
        _create_required_main(service)
        service.start_day()
        service.activate_bad_day_mode()

        preview = service.preview_blocking()

        assert preview.access_level is AccessLevel.MEDIUM
        assert preview.restriction_state == "bad_day"
        assert preview.allowed_sites == ["medium.example"]
        assert preview.blocked_sites == ["high.example"]


def test_daily_rollover_resets_access_balance_and_high_runtime(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    day_one = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_now", lambda: day_one)
        first_main = service.create_task(title="Day one main", kind=TaskKind.MAIN)

        service.start_day()
        _complete_task_after_claim_delay(service, first_main.id)
        service.start_high_access(5, "planned recreation")
        day_one_snapshot = service.dashboard_snapshot()

        assert day_one_snapshot.access_level is AccessLevel.HIGH
        assert day_one_snapshot.reward_balance_minutes == 25
        assert day_one_snapshot.reward_balance_seconds == 1500
        assert day_one_snapshot.high_active is True

        monkeypatch.setattr(service, "_now", lambda: day_two)
        day_two_snapshot = service.dashboard_snapshot()

        assert day_two_snapshot.access_level is AccessLevel.LOW
        assert day_two_snapshot.reward_balance_minutes == 0
        assert day_two_snapshot.reward_balance_seconds == 0
        assert day_two_snapshot.high_active is False
        assert day_two_snapshot.high_remaining_seconds == 0
        assert day_two_snapshot.main_task is None
        assert service.list_tasks() == []
        assert service.tasks.get(first_main.id) is not None
        assert service.tasks.get(first_main.id).status is TaskStatus.DONE
        assert service.list_all_tasks()[0].id == first_main.id
        assert service.high_sessions.list()[0].end_reason == "day_rollover"

        second_main = service.create_task(title="Day two main", kind=TaskKind.MAIN)
        assert service.dashboard_snapshot().main_task == second_main
        service.start_day()
        _complete_task_after_claim_delay(service, second_main.id)
        after_second_main = service.dashboard_snapshot()

        assert after_second_main.access_level is AccessLevel.MEDIUM
        assert after_second_main.reward_balance_minutes == 30
        assert after_second_main.reward_balance_seconds == 1800


def test_attempt_time_helpers_convert_utc_to_local_time() -> None:
    kyiv = timezone(timedelta(hours=3))

    assert format_attempt_local_time(
        "2026-05-08T13:09:00+00:00",
        include_date=False,
        tzinfo=kyiv,
    ) == "16:09"
    assert format_attempt_local_time(
        "2026-05-08T13:09:00",
        tzinfo=kyiv,
    ) == "2026-05-08 16:09"
    assert attempt_local_day("2026-05-08T21:30:00+00:00", tzinfo=kyiv) == (
        "2026-05-09"
    )


def test_recent_attempts_today_filter_uses_local_attempt_day(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        monkeypatch.setattr(service, "_today", lambda: "2026-05-09")
        monkeypatch.setattr(
            use_cases_module,
            "attempt_local_day",
            lambda value: "2026-05-09"
            if value == "2026-05-08T21:30:00+00:00"
            else "2026-05-08",
        )
        local_today = service.access_attempts.add(
            occurred_at="2026-05-08T21:30:00+00:00",
            target_type="app",
            target="notepad.exe",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="real_process_blocking_process",
            enforcement_mode="real_process_blocking",
            action_taken="terminate_requested",
        )
        service.access_attempts.add(
            occurred_at="2026-05-08T09:30:00+00:00",
            target_type="app",
            target="steam.exe",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="real_process_blocking_process",
            enforcement_mode="real_process_blocking",
            action_taken="terminate_requested",
        )

        assert service.list_recent_access_attempts() == [local_today]
        assert service.get_recent_attempt_summary().total_attempts == 1


def test_browser_integration_status_missing_heartbeat_is_disconnected(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        status = service.get_browser_integration_status()

    assert status.connection_status == "disconnected"
    assert status.connected is False
    assert status.browser_high_safety == "not_ready"
    assert status.incognito_status == "unknown"
    assert status.browser_blocking == "not_implemented"
    assert status.native_messaging_status == "not_connected"
    assert status.dnr_status == "unknown"
    assert status.youtube_spa_status == "unknown"
    assert status.next_action == (
        "Register the native host or reload the browser extension."
    )


def test_browser_setup_intro_flag_is_local_and_status_gated(
    tmp_path,
    test_settings,
    fixed_now,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: fixed_now)

        assert service.has_seen_browser_setup_intro() is False
        assert service.should_show_browser_setup_intro() is True

        service.mark_browser_setup_intro_seen()

        assert service.has_seen_browser_setup_intro() is True
        assert service.should_show_browser_setup_intro() is False
        assert "browser_setup_intro_seen" not in (
            service.export_configuration()["app_settings"]
        )

    connected_settings = replace(
        test_settings,
        app_home=test_settings.app_home / "connected",
        data_dir=test_settings.app_home / "connected" / "data",
        db_path=test_settings.app_home / "connected" / "data" / "selfboss.db",
        log_dir=test_settings.app_home / "connected" / "logs",
    )
    _write_browser_heartbeat(
        connected_settings,
        fixed_now,
        incognito_allowed=True,
        browser_blocking_available=True,
    )
    with initialize_database(connected_settings.db_path) as connection:
        service = _make_service(connection, connected_settings, now=lambda: fixed_now)

        assert service.has_seen_browser_setup_intro() is False
        assert service.should_show_browser_setup_intro() is False


def test_browser_integration_status_recent_heartbeat_is_connected_partial(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    heartbeat_path = test_settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "app": "SelfBoss",
                "protocol_version": 1,
                "browser": "chrome",
                "extension_connected": True,
                "browser_blocking": "active",
                "incognito_allowed": True,
                "last_heartbeat_at": (now - timedelta(seconds=30)).isoformat(),
                "source": "native_host",
            }
        ),
        encoding="utf-8",
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)

        status = service.get_browser_integration_status(now=now)

    assert status.connection_status == "partial"
    assert status.connected is False
    assert status.extension_heartbeat_status == "seen"
    assert status.native_host_status == "connected"
    assert status.browser_blocking_ready is False
    assert status.browser == "Chrome"
    assert status.incognito_status == "allowed"
    assert status.browser_blocking == "active"
    assert status.last_heartbeat_age_seconds == 30
    assert status.browser_high_safety == "partial"
    assert status.context == "unknown"
    assert status.native_messaging_status == "connected"
    assert status.dnr_status == "unknown"
    assert status.youtube_spa_status == "unknown"
    assert status.next_action == "Open a YouTube tab once to activate the SPA detector."


def test_browser_integration_status_old_heartbeat_is_stale_not_ready(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    heartbeat_path = test_settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "app": "SelfBoss",
                "protocol_version": 1,
                "browser": "chrome",
                "extension_connected": True,
                "browser_blocking": "active",
                "incognito_allowed": False,
                "last_heartbeat_at": (now - timedelta(seconds=121)).isoformat(),
                "source": "native_host",
            }
        ),
        encoding="utf-8",
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)

        status = service.get_browser_integration_status(now=now)

    assert status.connection_status == "stale"
    assert status.connected is False
    assert status.incognito_status == "not_allowed"
    assert status.browser_blocking == "active"
    assert status.last_heartbeat_age_seconds == 121
    assert status.browser_high_safety == "not_ready"
    assert status.native_messaging_status == "not_connected"
    assert status.next_action == (
        "Reload the extension or check the native host registration."
    )


def test_browser_integration_status_reports_diagnostics(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    heartbeat_path = test_settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "app": "SelfBoss",
                "protocol_version": 1,
                "browser": "chrome",
                "context": "regular",
                "extension_connected": True,
                "browser_blocking": "active",
                "browser_blocking_available": True,
                "incognito_allowed": True,
                "dnr_supported": True,
                "dnr_session_rule_count": 4,
                "dnr_last_update_status": "active",
                "dnr_last_error": "",
                "youtube_spa_content_script_seen": True,
                "extension_version": "0.0.1",
                "last_heartbeat_at": (now - timedelta(seconds=20)).isoformat(),
                "source": "native_host",
                "url": "https://private.example/secret",
                "domains": ["private.example"],
                "rules": ["private"],
                "reward_history": ["private"],
            }
        ),
        encoding="utf-8",
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)

        status = service.get_browser_integration_status(now=now)

    assert status.connection_status == "connected"
    assert status.context == "regular"
    assert status.native_messaging_status == "connected"
    assert status.browser_blocking_available == "yes"
    assert status.dnr_status == "active"
    assert status.dnr_session_rule_count == 4
    assert status.dnr_last_update_status == "active"
    assert status.dnr_last_error == ""
    assert status.youtube_spa_status == "seen"
    assert status.extension_version == "0.0.1"
    assert status.next_action == "Browser integration looks connected."


def test_browser_integration_status_incognito_and_dnr_next_actions(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    heartbeat_path = test_settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)

    heartbeat_path.write_text(
        json.dumps(
            {
                "browser": "chrome",
                "extension_connected": True,
                "browser_blocking": "active",
                "browser_blocking_available": True,
                "incognito_allowed": False,
                "dnr_supported": True,
                "dnr_session_rule_count": 0,
                "dnr_last_update_status": "cleared",
                "youtube_spa_content_script_seen": True,
                "last_heartbeat_at": (now - timedelta(seconds=20)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        incognito_status = service.get_browser_integration_status(now=now)

    heartbeat_path.write_text(
        json.dumps(
            {
                "browser": "chrome",
                "extension_connected": True,
                "browser_blocking": "active",
                "browser_blocking_available": True,
                "incognito_allowed": True,
                "dnr_supported": False,
                "dnr_session_rule_count": 0,
                "dnr_last_update_status": "unavailable",
                "youtube_spa_content_script_seen": True,
                "last_heartbeat_at": (now - timedelta(seconds=20)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with initialize_database(tmp_path / "selfboss-2.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        dnr_status = service.get_browser_integration_status(now=now)

    assert incognito_status.next_action == (
        "Enable Allow in Incognito for the LoopGuard extension."
    )
    assert dnr_status.dnr_status == "unavailable"
    assert dnr_status.next_action == (
        "Reload the extension and verify the DNR permission is available."
    )


def test_personal_use_readiness_missing_browser_is_not_ready(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        checklist = service.get_personal_use_readiness_checklist()

    items = {item.label: item for item in checklist.items}
    assert checklist.verdict == "not_ready"
    assert checklist.manual_qa_status == "0/9 verified"
    assert "Not ready" in checklist.summary
    assert items["Process blocking"].status == "Ready"
    assert items["Hosts blocking"].status == "Ready"
    assert items["Browser extension"].status == "Disconnected"
    assert items["Manual QA status"].status == "0/9 verified"


def test_personal_use_readiness_surfaces_browser_diagnostics(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _write_browser_heartbeat(
        test_settings,
        now,
        incognito_allowed=True,
        dnr_supported=True,
        dnr_session_rule_count=2,
        dnr_last_update_status="active",
        youtube_spa_content_script_seen=True,
        browser_blocking_available=True,
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)

        checklist = service.get_personal_use_readiness_checklist(now=now)

    items = {item.label: item for item in checklist.items}
    assert items["Browser extension"].status == "Connected"
    assert items["Incognito"].status == "Allowed"
    assert items["DNR"].status == "Active 2 rules"
    assert items["YouTube SPA detector"].status == "Seen"
    assert items["Browser path rules"].status == "Supported"
    assert items["Browser attempt logging"].status == "Supported"


def test_personal_trial_qa_defaults_rejects_unknown_and_resets(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)

        checklist = service.get_personal_trial_qa_checklist()
        with_unknown = None
        try:
            service.set_personal_trial_qa_item("unknown_step", True)
        except ValueError as error:
            with_unknown = str(error)
        service.app_settings.set_value("personal_trial_qa_checklist_v1", "not-json")
        invalid_json = service.get_personal_trial_qa_checklist()
        service.set_personal_trial_qa_item("chrome_extension_loaded", True)
        after_check = service.get_personal_trial_qa_checklist()
        reset = service.reset_personal_trial_qa_checklist()

    assert checklist.status == "not_ready"
    assert checklist.completed_count == 0
    assert checklist.total_count == len(PERSONAL_TRIAL_QA_STEP_DEFINITIONS)
    assert all(not item.checked for item in checklist.items)
    assert with_unknown == "Unsupported personal trial QA step"
    assert invalid_json.completed_count == 0
    assert after_check.status == "partial"
    assert after_check.completed_count == 1
    assert reset.status == "not_ready"
    assert reset.completed_count == 0


def test_personal_trial_qa_persists_across_services(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        service.set_personal_trial_qa_item("chrome_extension_loaded", True)
        service.set_personal_trial_qa_item("incognito_allowed", True)

        reloaded = _make_service(connection, test_settings)
        checklist = reloaded.get_personal_trial_qa_checklist()

    items = {item.key: item for item in checklist.items}
    assert checklist.completed_count == 2
    assert items["chrome_extension_loaded"].checked is True
    assert items["incognito_allowed"].checked is True
    assert items["youtube_spa_detector_seen"].checked is False


def test_personal_use_readiness_is_partial_until_manual_qa_complete(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _write_browser_heartbeat(
        test_settings,
        now,
        incognito_allowed=True,
        dnr_supported=True,
        dnr_session_rule_count=0,
        dnr_last_update_status="supported_no_rules",
        youtube_spa_content_script_seen=True,
        browser_blocking_available=True,
    )
    monkeypatch.setattr(
        use_cases_module,
        "recovery_readiness_checks",
        lambda: (
            use_cases_module.EnforcementReadinessCheck(
                key="recovery_module_exists",
                label="Recovery module",
                ready=True,
                detail="Recovery module is available.",
            ),
        ),
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.set_personal_trial_qa_item("chrome_extension_loaded", True)

        checklist = service.get_personal_use_readiness_checklist(now=now)

    items = {item.label: item for item in checklist.items}
    assert checklist.verdict == "partial"
    assert "manual QA are incomplete" in checklist.summary
    assert items["Recovery/Safe Mode"].status == "Available"
    assert items["Manual QA status"].status == "1/9 verified"


def test_personal_use_readiness_ready_after_all_manual_qa(
    tmp_path,
    test_settings,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _write_browser_heartbeat(
        test_settings,
        now,
        incognito_allowed=True,
        dnr_supported=True,
        dnr_session_rule_count=0,
        dnr_last_update_status="supported_no_rules",
        youtube_spa_content_script_seen=True,
        browser_blocking_available=True,
    )
    monkeypatch.setattr(
        use_cases_module,
        "recovery_readiness_checks",
        lambda: (
            use_cases_module.EnforcementReadinessCheck(
                key="recovery_module_exists",
                label="Recovery module",
                ready=True,
                detail="Recovery module is available.",
            ),
        ),
    )
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        _complete_personal_trial_qa(service)

        checklist = service.get_personal_use_readiness_checklist(now=now)

    items = {item.label: item for item in checklist.items}
    assert checklist.verdict == "ready_for_personal_trial"
    assert "Manual QA is complete" in checklist.summary
    assert items["Manual QA status"].status == "All manual QA verified"


def test_configuration_export_includes_rules_settings_and_excludes_history(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings, now=lambda: now)
        service.add_rule(
            "site",
            "youtube.com/shorts",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        service.set_enforcement_mode(EnforcementMode.ARMED_DRY_RUN)
        service.set_daily_recreation_cap_minutes(120)
        service.set_personal_trial_qa_item("chrome_extension_loaded", True)
        service.create_task(title="Private task title", kind=TaskKind.NORMAL)
        service.rewards.add(minutes_delta=5, reason="private_reward")
        service.high_sessions.start(
            day_date=service.day_state.get().day,
            started_at=now.isoformat(),
            ends_at=(now + timedelta(minutes=5)).isoformat(),
            allocated_minutes=5,
            intent="private recreation intent",
        )
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target="private.example.com/path?token=secret",
            rule_id=None,
            access_level_at_attempt="low",
            decision="would_block",
            allow_from_level="high",
            source="browser",
        )

        payload = service.export_configuration()

    exported = json.dumps(payload, sort_keys=True)
    assert payload["export_version"] == 1
    assert payload["app"] == "SelfBoss"
    assert "rules" in payload
    assert payload["rules"] == [
        {
            "rule_type": "site",
            "target": "youtube.com/shorts/*",
            "enabled": True,
            "allow_from_level": "high",
            "purpose": "compulsive_stimulation",
            "escape_family": "video",
        }
    ]
    assert payload["app_settings"]["enforcement_mode"] == "armed_dry_run"
    assert payload["app_settings"]["daily_recreation_cap_minutes"] == "120"
    assert "personal_trial_qa_checklist_v1" in payload["app_settings"]
    assert "Private task title" not in exported
    assert "private_reward" not in exported
    assert "private recreation intent" not in exported
    assert "private.example.com" not in exported
    assert "token=secret" not in exported


def test_configuration_import_validates_and_applies_rules_and_settings(
    tmp_path,
    test_settings,
) -> None:
    source_now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "source.db") as connection:
        source = _make_service(connection, test_settings, now=lambda: source_now)
        source.add_rule("site", "reddit.com", allow_from_level="high")
        source.add_rule("app", "steam.exe", allow_from_level="high")
        connection.execute(
            "UPDATE rules SET enabled = 0 WHERE rule_type = ? AND target = ?",
            ("app", "steam.exe"),
        )
        source.set_surrender_strictness("high")
        source.set_soft_start_enabled(False)
        source.set_soft_start_duration_minutes(20)
        source.set_daily_recreation_cap_minutes(150)
        source.set_personal_trial_qa_item("incognito_allowed", True)
        raw_json = json.dumps(source.export_configuration())

    with initialize_database(tmp_path / "target.db") as connection:
        target = _make_service(connection, test_settings)
        target.add_rule("site", "old.example.com", allow_from_level="medium")

        preview = target.preview_configuration_import(raw_json)
        result = target.import_configuration(raw_json)
        rules = target.rules.list(enabled_only=False)

    assert preview.rule_count == 2
    assert preview.setting_count == 5
    assert result.message == "Imported 2 rules and 5 settings."
    assert [(rule.rule_type, rule.target, rule.enabled) for rule in rules] == [
        ("site", "reddit.com", True),
        ("app", "steam.exe", False),
    ]
    assert target.get_surrender_strictness() == "high"
    assert target.get_soft_start_enabled() is False
    assert target.get_soft_start_duration_minutes() == 20
    assert target.get_daily_recreation_cap_minutes() == 150
    assert target.get_personal_trial_qa_checklist().completed_count == 1


def test_configuration_import_rejects_invalid_json_marker_version_and_day_lock(
    tmp_path,
    test_settings,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, test_settings)
        valid_payload = service.export_configuration()

        invalid_messages: list[str] = []
        for raw_json in (
            "{not-json",
            json.dumps({**valid_payload, "app": "OtherApp"}),
            json.dumps({**valid_payload, "export_version": 99}),
        ):
            try:
                service.preview_configuration_import(raw_json)
            except ValueError as error:
                invalid_messages.append(str(error))
            else:
                raise AssertionError("invalid import should be rejected")

        service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        try:
            service.import_configuration(json.dumps(valid_payload))
        except ValueError as error:
            active_day_error = str(error)
        else:
            raise AssertionError("active-day import should be rejected")

    assert invalid_messages == [
        "Configuration import must be valid JSON.",
        "Configuration import is not a SelfBoss export.",
        "Unsupported configuration export version.",
    ]
    assert "locked after Start Day" in active_day_error


def test_configuration_import_does_not_bypass_enforcement_readiness(
    tmp_path,
    test_settings,
) -> None:
    safe_settings = replace(test_settings, safe_mode=True)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(connection, safe_settings)
        payload = service.export_configuration()
        payload["app_settings"]["enforcement_mode"] = "full_enforcement"

        service.import_configuration(json.dumps(payload))
        status = service.get_enforcement_status()

    assert service.get_enforcement_mode() is EnforcementMode.FULL_ENFORCEMENT
    assert status.effective_mode is EnforcementMode.PREVIEW_ONLY
    assert status.real_blocking_active is False
    assert status.full_readiness.ready is True


def test_trusted_browser_heartbeat_allows_website_high_hosts_release(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            hosts_blocker=HostsBlocker(hosts_path=tmp_path / "hosts"),
        )
        service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        service.day_state.add_reward_seconds(5 * 60)
        service.add_rule("site", "reddit.com", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        service.start_high_access(5, "planned website access")

        preview = service.preview_hosts_blocking_dry_run()
        release = service.get_website_high_release_status(now=now)

    assert preview.blocked_domains == []
    assert release.status == "allowed"
    assert release.trusted_browser_ready is True
    assert release.other_browsers_status == "guard_active"


def test_untrusted_browser_heartbeat_holds_website_high_hosts_closed(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    cases = (
        ("missing", None, 0),
        ("stale", False, 121),
        ("incognito_false", False, 30),
        ("incognito_unknown", "unknown", 30),
    )
    for label, incognito_allowed, age_seconds in cases:
        app_home = tmp_path / label
        settings = replace(
            test_settings,
            app_home=app_home,
            data_dir=app_home / "data",
            db_path=app_home / "data" / "selfboss.db",
            log_dir=app_home / "logs",
        )
        if incognito_allowed is not None:
            _write_browser_heartbeat(
                settings,
                now,
                incognito_allowed=incognito_allowed,
                age_seconds=age_seconds,
            )
        with initialize_database(settings.db_path) as connection:
            service = _make_service(
                connection,
                settings,
                now=lambda: now,
                hosts_blocker=HostsBlocker(hosts_path=app_home / "hosts"),
            )
            service.create_task(title="Required main", kind=TaskKind.MAIN)
            service.start_day()
            service.day_state.add_reward_seconds(5 * 60)
            service.add_rule("site", "reddit.com", allow_from_level="high")
            service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
            try:
                service.start_high_access(5, "planned website access")
            except ValueError as error:
                assert str(error) == HIGH_BROWSER_BLOCKING_NOT_READY
            else:
                raise AssertionError("untrusted browser should block website HIGH")


def test_website_gate_does_not_change_app_high_behavior(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
        )
        service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        service.day_state.add_reward_seconds(5 * 60)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.REAL_PROCESS_BLOCKING)

        blocked = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )
        service.start_high_access(5, "play one match")
        allowed = service.run_real_process_blocking_scan_cycle(
            process_names=["steam.exe"]
        )

    assert [attempt.target for attempt in blocked] == ["steam.exe"]
    assert blocked[0].action_taken == "terminate_requested"
    assert allowed == []
    assert terminated == ["steam.exe"]


def test_planned_use_website_pass_hosts_release_requires_trusted_browser(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            hosts_blocker=HostsBlocker(hosts_path=tmp_path / "hosts"),
        )
        service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        rule = service.add_rule("site", "reddit.com", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        service.start_planned_use_pass(rule.id, "planned website access", 5 * 60)

        untrusted_preview = service.preview_hosts_blocking_dry_run()
        untrusted_release = service.get_website_high_release_status(now=now)
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        trusted_preview = service.preview_hosts_blocking_dry_run()
        trusted_release = service.get_website_high_release_status(now=now)

    assert untrusted_preview.blocked_domains == ["reddit.com", "www.reddit.com"]
    assert untrusted_release.status == "held_closed"
    assert trusted_preview.blocked_domains == []
    assert trusted_release.status == "allowed"


def test_unmanaged_browser_guard_only_runs_for_trusted_website_release(
    tmp_path,
    test_settings,
) -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    terminated: list[str] = []

    def fake_terminate(target: str) -> subprocess.CompletedProcess[str]:
        terminated.append(target)
        return subprocess.CompletedProcess(["taskkill", "/IM", target], 0)

    with initialize_database(tmp_path / "selfboss.db") as connection:
        service = _make_service(
            connection,
            test_settings,
            now=lambda: now,
            process_blocker=ProcessBlocker(termination_runner=fake_terminate),
            hosts_blocker=HostsBlocker(hosts_path=tmp_path / "hosts"),
        )
        service.create_task(title="Required main", kind=TaskKind.MAIN)
        service.start_day()
        service.day_state.add_reward_seconds(5 * 60)
        service.add_rule("site", "reddit.com", allow_from_level="high")
        service.set_enforcement_mode(EnforcementMode.REAL_HOSTS_BLOCKING)
        untrusted = service.run_unmanaged_browser_guard_cycle(
            process_names=["msedge.exe", "firefox.exe", "chrome.exe"]
        )
        _write_browser_heartbeat(test_settings, now, incognito_allowed=True)
        service.start_high_access(5, "planned website access")
        trusted = service.run_unmanaged_browser_guard_cycle(
            process_names=["msedge.exe", "firefox.exe", "chrome.exe"]
        )
        repeated = service.run_unmanaged_browser_guard_cycle(
            process_names=["msedge.exe", "firefox.exe", "chrome.exe"]
        )

    assert untrusted == []
    assert [result.target for result in trusted] == ["msedge.exe", "firefox.exe"]
    assert [result.action for result in trusted] == [
        "terminate_requested",
        "terminate_requested",
    ]
    assert repeated == []
    assert terminated == ["msedge.exe", "firefox.exe"]


def _write_browser_heartbeat(
    settings,
    now: datetime,
    *,
    incognito_allowed,
    age_seconds: int = 30,
    browser: str = "chrome",
    browser_blocking: str = "active",
    browser_blocking_available=True,
    dnr_supported=True,
    dnr_session_rule_count=1,
    dnr_last_update_status="active",
    youtube_spa_content_script_seen=True,
) -> None:
    heartbeat_path = settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app": "SelfBoss",
        "protocol_version": 1,
        "browser": browser,
        "extension_connected": True,
        "browser_blocking": browser_blocking,
        "incognito_allowed": incognito_allowed,
        "last_heartbeat_at": (now - timedelta(seconds=age_seconds)).isoformat(),
        "source": "native_host",
    }
    if browser_blocking_available is not None:
        payload["browser_blocking_available"] = browser_blocking_available
    if dnr_supported is not None:
        payload["dnr_supported"] = dnr_supported
    if dnr_session_rule_count is not None:
        payload["dnr_session_rule_count"] = dnr_session_rule_count
    if dnr_last_update_status is not None:
        payload["dnr_last_update_status"] = dnr_last_update_status
    if youtube_spa_content_script_seen is not None:
        payload["youtube_spa_content_script_seen"] = youtube_spa_content_script_seen
    heartbeat_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _make_service(
    connection,
    settings,
    now=None,
    *,
    soft_start_enabled: bool | None = False,
    process_blocker: ProcessBlocker | None = None,
    hosts_blocker: HostsBlocker | None = None,
) -> SelfBossAppService:
    service = SelfBossAppService(
        settings=settings,
        tasks=TaskRepository(connection),
        day_state=DayStateRepository(connection),
        rewards=RewardLedgerRepository(connection),
        high_sessions=HighSessionRepository(connection),
        rules=RuleRepository(connection),
        now_provider=now,
        process_blocker=process_blocker,
        hosts_blocker=hosts_blocker,
    )
    if (
        soft_start_enabled is not None
        and service.day_state.get().day_started_at is None
    ):
        service.set_soft_start_enabled(soft_start_enabled)
    return service


def _create_required_main(service: SelfBossAppService) -> None:
    service.create_task(title="Required main", kind=TaskKind.MAIN)


def _start_active_day_low(service: SelfBossAppService):
    main = service.create_task(title="Required main", kind=TaskKind.MAIN)
    service.start_day()
    return main


def _complete_personal_trial_qa(service: SelfBossAppService) -> None:
    for key, _label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS:
        service.set_personal_trial_qa_item(key, True)


def _assert_rule_rejected(
    service: SelfBossAppService,
    rule_type: str,
    target: str,
) -> None:
    try:
        service.add_rule(rule_type, target)
    except ValueError:
        return
    raise AssertionError(f"{rule_type} rule target should be rejected: {target}")
