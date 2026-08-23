from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QScrollArea,
    QSizePolicy,
)

from selfboss.core.models import (  # noqa: E402
    AppSettings,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.core.use_cases import (  # noqa: E402
    HIGH_COOLDOWN_SECONDS,
    HIGH_DAILY_MAX_MINUTES,
    SelfBossAppService,
    START_DAY_BROWSER_REQUIRED,
)
from selfboss.data.db import initialize_database  # noqa: E402
from selfboss.data.repositories import (  # noqa: E402
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.platform.hosts_blocker import HostsBlocker  # noqa: E402
from selfboss.ui.components import CardFrame  # noqa: E402
import selfboss.ui.dashboard_page as dashboard_page_module  # noqa: E402
from selfboss.ui.dashboard_page import DashboardPage, format_reward_time  # noqa: E402
from selfboss.ui.style import CARD_PADDING, CARD_SPACING  # noqa: E402


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


def test_dashboard_shows_main_task_guidance(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
        service = _make_service(test_settings, connection, now=lambda: now)
        page = DashboardPage(service)
        production_page = DashboardPage(service, production_mode=True)

        assert app is not None
        assert production_page.planned_use_pass_card.isHidden()
        assert page.scroll_area.objectName() == "dashboardScrollArea"
        assert page.scroll_area.widgetResizable() is True
        assert page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.findChildren(QScrollArea) == [page.scroll_area]
        assert page.day_card_title_label.text() == "Today"
        assert page.access_card_title_label.text() == "Access"
        assert page.reward_card_title_label.text() == "Recreation budget"
        assert page.main_card_title_label is page.day_card_title_label
        assert page.safety_card_title_label.text() == "Blocking"
        assert page.planned_use_pass_card_title_label.text() == "Planned-use pass"
        assert page.recent_escapes_card_title_label.text() == "Recent escapes"
        assert page.placeholders_card_title_label.text() == "Recovery actions"
        cards = page.findChildren(CardFrame)
        assert len(cards) == 7
        for card in cards:
            margins = card.card_layout.contentsMargins()
            assert card.objectName() in {"CardFrame", "DashboardHeroCard"}
            if card.title_label in (
                page.day_card_title_label,
                page.reward_card_title_label,
            ):
                assert card.card_layout.spacing() == 8
                assert margins.left() == 14
                assert margins.top() == 12
                assert margins.right() == 14
                assert margins.bottom() == 12
            else:
                assert card.card_layout.spacing() == CARD_SPACING
                assert margins.left() == CARD_PADDING
                assert margins.top() == CARD_PADDING
                assert margins.right() == CARD_PADDING
                assert margins.bottom() == CARD_PADDING
            assert bool(card.card_layout.alignment() & Qt.AlignmentFlag.AlignTop)
            assert card.title_label is not None
            assert card.title_label.objectName() == "CardTitle"
            assert card.title_label.wordWrap() is True
        assert page.day_status_label.text() == "Planning"
        assert page.day_status_label.objectName() == "DashboardHeroValue"
        assert page.day_state_pill_label.objectName() == "DashboardStatusPill"
        assert page.day_state_pill_label.text() == "PLANNING"
        for label in (
            page.day_status_label,
            page.soft_start_status_label,
            page.soft_start_hint_label,
            page.planned_task_counts_label,
            page.unplanned_task_counts_label,
            page.day_ended_summary_label,
        ):
            assert label.wordWrap() is True
            assert label.sizePolicy().horizontalPolicy() == (
                QSizePolicy.Policy.Expanding
            )
        assert page.planned_task_counts_label.text() == (
            "Planned 0/0 done - 0 pending"
        )
        assert page.unplanned_task_counts_label.text() == (
            "Unplanned 0/0 done - 0 pending"
        )
        assert page.start_day_button.text() == "Start Day"
        assert page.start_day_button.objectName() == "startDayButton"
        assert page.start_day_button.isEnabled() is False
        assert page.rest_token_label.text() == "0/1 available"
        assert page.use_rest_token_button.text() == "Use Rest Day"
        assert page.use_rest_token_button.objectName() == "useRestTokenButton"
        assert page.use_rest_token_button.isHidden() is True
        assert page.end_day_button.text() == "End Day"
        assert page.end_day_button.objectName() == "endDayButton"
        assert page.end_day_button.isHidden() is True
        assert page.recovery_close_today_button.text() == "Recovery Close Today"
        assert page.recovery_close_today_button.objectName() == (
            "recoveryCloseTodayButton"
        )
        assert page.recovery_close_today_button.isHidden() is True
        assert page.day_ended_summary_label.objectName() == "dayEndedSummaryLabel"
        assert page.day_ended_summary_label.isHidden() is True
        assert page.start_day_button.toolTip() == (
            "Add a planned MAIN task before starting the day."
        )

        assert page.access_mode_metric_label.text() == "Focus"
        assert page.access_mode_metric_label.objectName() == "accessModeMetricLabel"
        assert page.access_level_label.text() == "LOW"
        assert page.access_level_label.objectName() == "DashboardStatusPill"
        assert page.reward_balance_label.text() == "Available: 0m"
        assert page.reward_balance_label.objectName() == "DashboardMetric"
        assert page.recreation_budget_progress.objectName() == (
            "recreationBudgetProgress"
        )
        assert page.recreation_budget_progress.value() == 0
        assert page.high_timer_label.text() == "HIGH remaining: inactive"
        assert page.high_intent_label.objectName() == "highIntentLabel"
        assert page.high_intent_label.isVisible() is False
        assert page.start_high_access_button.text() == "Start HIGH access"
        assert page.start_high_access_button.objectName() == "startHighButton"
        assert page.end_high_access_button.text() == "End HIGH access"
        assert page.end_high_access_button.objectName() == "endHighButton"
        assert page.test_mode_label.text() == "TEST MODE: ON"
        assert page.test_mode_label.objectName() == "testModeBadge"
        assert page.test_mode_explanation_label.text() == (
            "Blocking: preview only."
        )
        assert page.test_mode_explanation_label.objectName() == (
            "testModeExplanationLabel"
        )
        assert page.safe_mode_label.text() == "Safe mode: Off"
        assert page.recovery_mode_label.text() == "Recovery mode: Off"
        assert page.enforcement_mode_label.objectName() == "enforcementModeLabel"
        assert page.enforcement_mode_label.text() == (
            "Enforcement mode: Preview Only"
        )
        assert page.real_blocking_status_label.objectName() == (
            "realBlockingStatusLabel"
        )
        assert page.real_blocking_status_label.text() == (
            "Apps: Inactive"
        )
        assert page.websites_status_label.objectName() == "websitesStatusLabel"
        assert page.websites_status_label.text() == (
            "Sites: Inactive. Browser: Browser disconnected. "
            "Trial: not verified."
        )
        _assert_dashboard_safety_is_compact(page)
        assert page.enforcement_next_step_label.objectName() == (
            "enforcementNextStepLabel"
        )
        assert page.enforcement_next_step_label.text() == (
            "Next available mode: Armed Dry Run. Real Process Blocking, "
            "Real Hosts Blocking, and Full Enforcement are available."
        )
        assert page.dry_run_attempts_title_label.objectName() == (
            "dryRunAttemptsTitleLabel"
        )
        assert page.dry_run_attempts_title_label.text() == "Process"
        assert page.dry_run_attempts_label.objectName() == "dryRunAttemptsLabel"
        assert page.dry_run_attempts_label.text() == "Process: none today."
        assert page.active_planned_use_pass_title_label.text() == "Quick pass"
        assert page.active_planned_use_pass_title_label.objectName() == (
            "activePlannedUsePassTitleLabel"
        )
        assert page.planned_use_pass_helper_label.objectName() == (
            "plannedUsePassHelperLabel"
        )
        assert page.planned_use_pass_helper_label.text() == (
            "Use this for task-specific access. For recreation, use HIGH."
        )
        assert page.planned_use_pass_rule_combo.objectName() == (
            "plannedUsePassRuleCombo"
        )
        assert page.planned_use_pass_rule_combo.itemText(0) == (
            "No eligible rules yet"
        )
        assert page.planned_use_pass_reason_input.objectName() == (
            "plannedUsePassReasonInput"
        )
        assert page.planned_use_pass_reason_input.placeholderText() == (
            "Reason for this task-specific access"
        )
        assert page.planned_use_pass_duration_combo.objectName() == (
            "plannedUsePassDurationCombo"
        )
        assert [
            page.planned_use_pass_duration_combo.itemData(index)
            for index in range(page.planned_use_pass_duration_combo.count())
        ] == [600, 900, 1500]
        assert page.start_planned_use_pass_button.objectName() == (
            "startPlannedUsePassButton"
        )
        assert page.start_planned_use_pass_button.text() == "Start pass"
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == (
            "Add an enabled rule first."
        )
        assert page.end_planned_use_pass_button.objectName() == (
            "endPlannedUsePassButton"
        )
        assert page.end_planned_use_pass_button.text() == "End pass"
        assert page.end_planned_use_pass_button.isEnabled() is False
        assert page.active_planned_use_pass_detail_label.objectName() == (
            "activePlannedUsePassDetailLabel"
        )
        assert page.active_planned_use_pass_title_label.isHidden() is False
        assert page.active_planned_use_pass_detail_label.isHidden() is False
        assert page.active_planned_use_pass_detail_label.text() == (
            "No active planned-use pass."
        )
        assert page.active_planned_use_pass_reason_label.objectName() == (
            "activePlannedUsePassReasonLabel"
        )
        assert page.active_planned_use_pass_reason_label.isHidden() is True
        assert page.recent_escape_pattern_title_label.text() == (
            "Pattern"
        )
        assert page.recent_escape_pattern_title_label.objectName() == (
            "recentEscapePatternTitleLabel"
        )
        assert page.recent_escape_pattern_explanation_label.objectName() == (
            "recentEscapePatternExplanationLabel"
        )
        assert page.recent_escape_pattern_next_action_label.objectName() == (
            "recentEscapePatternNextActionLabel"
        )
        assert page.recent_escape_pattern_meta_label.objectName() == (
            "recentEscapePatternMetaLabel"
        )
        assert page.recent_escape_pattern_explanation_label.text() == (
            "No attempts logged today."
        )
        assert page.recent_escape_pattern_next_action_label.text() == (
            "Next action: Log a test attempt from a selected rule to see a pattern."
        )
        assert page.recent_escape_pattern_meta_label.text() == (
            "Attempts: 0 · Families: none · Switching: No"
        )
        assert page.browser_escape_title_label.text() == "Browser"
        assert page.browser_escape_title_label.objectName() == (
            "browserEscapeTitleLabel"
        )
        assert page.browser_escape_summary_label.objectName() == (
            "browserEscapeSummaryLabel"
        )
        assert page.browser_escape_summary_label.text() == (
            "Browser: none today."
        )
        assert page.main_task_label.text() == "No planned MAIN task yet."
        assert page.main_task_hint_label.text() == "Create one before Start Day."
        assert page.surrender_strictness_label.text() == (
            "Surrender strictness: MEDIUM (6h)"
        )
        assert page.surrender_button.text() == "Surrender unavailable"
        assert page.surrender_button.objectName() == "activateSurrenderButton"
        assert page.placeholder_note_label.text() == (
            "Surrender unavailable until day starts."
        )
        assert page.bad_day_button.text() == "Bad Day unavailable"
        assert page.bad_day_button.objectName() == "activateBadDayButton"
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode unavailable until day starts."
        )
        assert not page.surrender_button.isEnabled()
        assert not page.bad_day_button.isEnabled()

        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        page.refresh()
        assert page.planned_task_counts_label.text() == (
            "Planned 0/1 done - 1 pending"
        )
        assert page.unplanned_task_counts_label.text() == (
            "Unplanned 0/0 done - 0 pending"
        )
        assert page.main_task_label.text() == "Main task — pending"

        assert page.start_day_button.isEnabled() is True

        service.start_day()
        page.refresh()
        assert page.end_day_button.isEnabled() is False
        assert page.end_day_button.toolTip() == (
            "End Day is available after completing today's MAIN task."
        )
        assert page.recovery_close_today_button.isHidden() is False
        assert page.recovery_close_today_button.isEnabled() is True
        assert page.recovery_close_today_button.toolTip() == ""

        service.claim_task_done(task.id)
        page.refresh()
        assert page.main_task_status_label.text() == "CLAIM PENDING"
        assert "completion claim pending" in page.main_task_label.text()
        assert page.end_day_button.isEnabled() is False

        _complete_task_after_claim_delay(service, task.id)
        page.refresh()
        assert page.access_level_label.text() == "MEDIUM"
        assert page.reward_balance_label.text() == "Available: 30m"
        assert page.end_day_button.isEnabled() is True
        assert page.end_day_button.toolTip() == ""
        assert page.recovery_close_today_button.isHidden() is False
        assert page.recovery_close_today_button.isEnabled() is True
        assert page.main_task_label.text() == "Main task — completed"

def test_production_dashboard_requires_browser_setup_before_start_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Required main", kind=TaskKind.MAIN)
        page = DashboardPage(service, production_mode=True)

        assert app is not None
        assert page.start_day_button.isEnabled() is False
        assert page.start_day_button.toolTip() == START_DAY_BROWSER_REQUIRED
        assert page.recovery_close_today_button.isHidden() is True
        assert page.test_mode_row.isHidden() is True
        assert page.safe_mode_row.isHidden() is True
        assert page.recovery_mode_row.isHidden() is True
        assert page.dry_run_attempts_label.isVisible() is False


def test_dashboard_shows_active_planned_use_pass_indicator(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        service.start_planned_use_pass(
            rule.id,
            "Watch one PySide6 tutorial",
            15 * 60,
        )
        page = DashboardPage(service)

        assert app is not None
        assert page.active_planned_use_pass_detail_label.text() == (
            "youtube.com (website) - 15m left"
        )
        assert page.active_planned_use_pass_reason_label.text() == (
            "Reason: Watch one PySide6 tutorial"
        )
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == (
            "Another planned-use pass is already active."
        )
        assert page.end_planned_use_pass_button.isEnabled() is True
        assert page.recent_escape_pattern_title_label.text() == (
            "Pattern"
        )
        assert page.recent_escape_pattern_meta_label.text() == (
            "Attempts: 0 · Families: none · Switching: No"
        )

        now = start + timedelta(minutes=5)
        page.refresh()

        assert page.active_planned_use_pass_detail_label.text() == (
            "youtube.com (website) - 10m left"
        )


def test_dashboard_quick_planned_use_pass_starts_and_ends_existing_rule(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: start)
        site_rule = service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="launcher",
        )
        page = DashboardPage(service)

        labels = [
            page.planned_use_pass_rule_combo.itemText(index)
            for index in range(page.planned_use_pass_rule_combo.count())
        ]

        assert app is not None
        assert "Website: youtube.com - HIGH - Video" in labels
        assert "App: steam.exe - HIGH - Launcher" in labels
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == (
            "Enter a planned-use reason."
        )

        page.planned_use_pass_reason_input.setText("Watch one PySide6 tutorial")

        assert page.start_planned_use_pass_button.isEnabled() is True
        assert page.start_planned_use_pass_button.toolTip() == (
            "Start a temporary pass for the selected rule target."
        )

        page.start_planned_use_pass_button.click()
        active_pass = service.get_active_planned_use_pass()

        assert active_pass is not None
        assert active_pass.rule_id == site_rule.id
        assert active_pass.target_type == "site"
        assert active_pass.target == "youtube.com"
        assert active_pass.reason == "Watch one PySide6 tutorial"
        assert service.get_rules("site")[0].allow_from_level == "high"
        assert page.spend_status_label.text() == (
            "Started planned-use pass: youtube.com"
        )
        assert page.active_planned_use_pass_detail_label.text() == (
            "youtube.com (website) - 10m left"
        )
        assert page.active_planned_use_pass_reason_label.text() == (
            "Reason: Watch one PySide6 tutorial"
        )
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.end_planned_use_pass_button.isEnabled() is True

        page.end_planned_use_pass_button.click()

        assert service.get_active_planned_use_pass() is None
        assert page.spend_status_label.text() == "Ended planned-use pass: youtube.com"
        assert page.active_planned_use_pass_detail_label.text() == (
            "No active planned-use pass."
        )
        assert page.active_planned_use_pass_reason_label.isHidden() is True


def test_dashboard_hides_planned_use_pass_indicator_after_end_or_expiry(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        page = DashboardPage(service)

        assert app is not None
        assert page.active_planned_use_pass_detail_label.text() == (
            "youtube.com (website) - 5m left"
        )

        service.end_active_planned_use_pass()
        page.refresh()

        assert page.active_planned_use_pass_detail_label.text() == (
            "No active planned-use pass."
        )
        assert page.active_planned_use_pass_reason_label.isHidden() is True

        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        page.refresh()

        assert page.active_planned_use_pass_detail_label.text() == (
            "youtube.com (website) - 5m left"
        )

        now = start + timedelta(minutes=5)
        page.refresh()

        assert page.active_planned_use_pass_detail_label.text() == (
            "No active planned-use pass."
        )
        assert page.active_planned_use_pass_reason_label.isHidden() is True


def test_dashboard_warns_for_legacy_started_day_without_main(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.day_state.start_day("2026-05-08T09:00:00+00:00")
        page = DashboardPage(service)

        assert app is not None
        assert page.main_task_status_label.text() == "UNAVAILABLE"
        assert page.main_task_label.text() == (
            "No planned MAIN task for this active day."
        )
        assert page.main_task_hint_label.text() == (
            "This day was started without a planned MAIN task. "
            "MAIN unlock is unavailable for this day."
        )


def test_dashboard_recent_escape_pattern_updates_after_logged_attempts(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = DashboardPage(service)
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
        page.refresh()

        assert app is not None
        assert page.recent_escape_pattern_explanation_label.text() == (
            "Possible switching: video -> games"
        )
        assert page.recent_escape_pattern_next_action_label.text() == (
            "Next action: Consider returning to the anchor task, using earned "
            "Recreation, or entering Recovery if the day is breaking."
        )
        assert page.recent_escape_pattern_meta_label.text() == (
            "Attempts: 2 · Families: video -> games · Switching: Yes"
        )


def test_dashboard_shows_browser_escape_summary(
    test_settings: AppSettings,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 13, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(
        dashboard_page_module,
        "format_attempt_local_time",
        lambda value, include_date=True: "16:09"
        if not include_date
        else "2026-05-08 16:09",
    )

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: now)
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
            occurred_at=(now - timedelta(minutes=1)).isoformat(),
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

        page = DashboardPage(service)
        text = page.browser_escape_summary_label.text()

        assert app is not None
        assert "2 browser attempts today." in text
        assert "Top: youtube.com/shorts/* - 2." in text
        assert "Last: www.youtube.com - youtube_shorts - blocked - 16:09." in text
        assert "secret=query" not in text
        assert "https://" not in text


def test_dashboard_shows_recent_armed_dry_run_process_attempts(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.set_enforcement_mode("armed_dry_run")
        service.run_armed_dry_run_process_scan_cycle(process_names=["steam.exe"])

        page = DashboardPage(service)

        assert app is not None
        assert "Armed Dry Run saw 1 would-block app attempts today." in (
            page.dry_run_attempts_label.text()
        )
        assert "Last blocked app: steam.exe." in page.dry_run_attempts_label.text()
        assert "logs matching app rules without blocking" in (
            page.dry_run_attempts_label.text()
        )
        assert "steam.exe" in page.dry_run_attempts_label.text()
        assert "Would block" in page.dry_run_attempts_label.text()
        assert "LOW" in page.dry_run_attempts_label.text()


def test_dashboard_shows_real_process_blocking_as_apps_only(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.set_enforcement_mode("real_process_blocking")
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

        page = DashboardPage(service)

        assert app is not None
        assert page.test_mode_explanation_label.text() == (
            "Blocking armed. Starts when day starts."
        )
        assert page.real_blocking_status_label.text() == (
            "Apps: Armed"
        )
        assert "Real Process Blocking acted on 1 blocked app attempts today." in (
            page.dry_run_attempts_label.text()
        )
        assert "Last blocked app today: steam.exe - Terminate requested." in (
            page.dry_run_attempts_label.text()
        )
        assert "steam.exe: Terminate requested at LOW" in (
            page.dry_run_attempts_label.text()
        )
        assert "steam.exe: Would block" not in page.dry_run_attempts_label.text()
        assert "Websites are not blocked yet." in page.dry_run_attempts_label.text()
        _assert_dashboard_safety_is_compact(page)


def test_dashboard_shows_real_hosts_blocking_status(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    hosts_path = test_settings.app_home / "hosts"
    hosts_blocker = HostsBlocker(hosts_path=hosts_path)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(
            test_settings,
            connection,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.set_enforcement_mode("real_hosts_blocking")

        page = DashboardPage(service)

        assert app is not None
        assert page.test_mode_explanation_label.text() == (
            "Blocking armed. Starts when day starts."
        )
        assert page.real_blocking_status_label.text() == (
            "Apps: Inactive"
        )
        assert page.websites_status_label.text() == (
            "Sites: Blocking armed. Starts when day starts. "
            "Browser: Browser disconnected. "
            "Trial: not verified."
        )
        _assert_dashboard_safety_is_compact(page)


def test_dashboard_shows_full_enforcement_as_apps_and_websites(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    hosts_path = test_settings.app_home / "hosts"
    hosts_blocker = HostsBlocker(hosts_path=hosts_path)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(
            test_settings,
            connection,
            hosts_blocker=hosts_blocker,
        )
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("site", "reddit.com", allow_from_level="high")
        service.set_enforcement_mode("full_enforcement")

        page = DashboardPage(service)

        assert app is not None
        assert page.enforcement_mode_label.text() == (
            "Enforcement mode: Full Enforcement"
        )
        assert page.real_blocking_status_label.text() == (
            "Apps: Armed"
        )
        assert page.test_mode_explanation_label.text() == (
            "Blocking armed. Starts when day starts."
        )
        assert page.websites_status_label.text() == (
            "Sites: Blocking armed. Starts when day starts. "
            "Browser: Browser disconnected. "
            "Trial: not verified."
        )
        _assert_dashboard_safety_is_compact(page)


def test_dashboard_shows_real_process_pass_allowed_status_and_local_time(
    test_settings: AppSettings,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 13, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(
        dashboard_page_module,
        "format_attempt_local_time",
        lambda value, include_date=True: "16:09"
        if not include_date
        else "2026-05-08 16:09",
    )

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: now)
        service.set_enforcement_mode("real_process_blocking")
        service.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type="app",
            target="notepad.exe",
            rule_id=None,
            access_level_at_attempt="low",
            decision="allowed_by_planned_use_pass",
            allow_from_level="high",
            source="real_process_blocking_process",
            enforcement_mode="real_process_blocking",
            action_taken="none",
        )

        page = DashboardPage(service)

        assert app is not None
        assert "16:09 notepad.exe: Allowed by pass at LOW" in (
            page.dry_run_attempts_label.text()
        )
        assert "13:09 notepad.exe" not in page.dry_run_attempts_label.text()
        assert "Terminate requested" not in page.dry_run_attempts_label.text()


def test_dashboard_attempt_summaries_ignore_yesterday_by_default(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        rule = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        service.log_manual_rule_attempt(rule.id)
        service.set_enforcement_mode("armed_dry_run")
        service.run_armed_dry_run_process_scan_cycle(process_names=["steam.exe"])

        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
        page = DashboardPage(service)

        assert app is not None
        assert page.recent_escape_pattern_explanation_label.text() == (
            "No attempts logged today."
        )
        assert page.recent_escape_pattern_meta_label.text() == (
            "Attempts: 0 · Families: none · Switching: No"
        )
        assert page.dry_run_attempts_label.text() == "Process: none today."


def test_dashboard_start_day_updates_status_and_task_counts(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    changed = []
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Planned main", kind=TaskKind.MAIN)
        page = DashboardPage(service, on_day_started=lambda: changed.append(True))
        page._confirm_start_day = lambda: True

        page.start_day_button.click()
        service.create_task(title="Unexpected task", kind=TaskKind.NORMAL)
        page.refresh()

        assert app is not None
        assert changed == [True]
        assert page.day_status_label.text() == "Day started"
        assert page.start_day_button.isEnabled() is False
        assert page.end_day_button.isHidden() is False
        assert page.end_day_button.isEnabled() is False
        assert page.end_day_button.toolTip() == (
            "End Day is available after completing today's MAIN task."
        )
        assert page.planned_task_counts_label.text() == (
            "Planned 0/1 done - 1 pending"
        )
        assert page.unplanned_task_counts_label.text() == (
            "Unplanned 0/1 done - 1 pending"
        )
        assert page.test_mode_label.text() == "TEST MODE: ON"
        assert page.bad_day_button.text() == "Activate Bad Day Mode"
        assert page.bad_day_button.isEnabled() is True
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode available: MEDIUM baseline for today."
        )


def test_dashboard_end_day_updates_status_and_summary(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
        service = _make_service(test_settings, connection, now=lambda: now)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        page = DashboardPage(service)
        shown_reviews = []
        page._show_day_close_review = lambda summary: shown_reviews.append(summary)

        page.end_day_button.click()

        assert app is not None
        assert shown_reviews == []
        assert page.day_status_label.text() == "Day started"
        assert page.end_day_button.text() == "End Day"
        assert page.spend_status_label.text() == (
            "End Day requested. Review for 60 seconds, then confirm."
        )

        now = now + timedelta(seconds=60)
        page.refresh()
        page.end_day_button.click()

        assert len(shown_reviews) == 1
        assert shown_reviews[0].close_type == "normal_end_day"
        assert shown_reviews[0].title == "Day closed"
        assert shown_reviews[0].main_completed is True
        assert page.day_status_label.text() == "Day ended"
        assert page.end_day_button.isHidden() is True
        assert page.day_ended_summary_label.isHidden() is False
        assert "Planned: 1 / 2 done." in page.day_ended_summary_label.text()
        assert "Reward balance: 30m." in page.day_ended_summary_label.text()
        assert page.start_high_access_button.isEnabled() is False
        assert page.spend_status_label.text() == "Day ended"


def test_dashboard_recovery_close_cancel_keeps_active_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = DashboardPage(service)
        page._confirm_recovery_close_today = lambda: False
        shown_reviews = []
        page._show_day_close_review = lambda summary: shown_reviews.append(summary)

        page.recovery_close_today_button.click()

        assert app is not None
        assert shown_reviews == []
        assert service.dashboard_snapshot().day_started is True
        assert service.dashboard_snapshot().day_closed is False
        assert page.day_status_label.text() == "Day started"
        assert page.recovery_close_today_button.isHidden() is False


def test_dashboard_blocked_end_day_does_not_show_review(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = DashboardPage(service)
        shown_reviews = []
        page._show_day_close_review = lambda summary: shown_reviews.append(summary)

        page.end_day()

        assert app is not None
        assert shown_reviews == []
        assert service.dashboard_snapshot().day_closed is False
        assert page.spend_status_label.text() == (
            "End Day is available after completing today's MAIN task."
        )


def test_dashboard_recovery_close_confirms_and_closes_incomplete_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = DashboardPage(service)
        page._confirm_recovery_close_today = lambda: True
        shown_reviews = []
        page._show_day_close_review = lambda summary: shown_reviews.append(summary)

        page.recovery_close_today_button.click()

        snapshot = service.dashboard_snapshot()
        assert app is not None
        assert len(shown_reviews) == 1
        assert shown_reviews[0].close_type == "recovery_close"
        assert shown_reviews[0].title == "Today closed in Recovery"
        assert shown_reviews[0].main_completed is False
        assert snapshot.day_closed is True
        assert page.day_status_label.text() == "Day ended"
        assert page.end_day_button.isHidden() is True
        assert page.recovery_close_today_button.isHidden() is True
        assert page.day_ended_summary_label.isHidden() is False
        assert "Planned: 0 / 1 done." in page.day_ended_summary_label.text()
        assert page.spend_status_label.text() == "Recovery close completed"


def test_dashboard_day_close_review_text_is_compact_and_neutral(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        review = service.get_day_close_review("recovery_close")

        text = dashboard_page_module._day_close_review_text(review)

        assert app is not None
        assert "MAIN completed: No" in text
        assert "Planned tasks: 0 / 1 done" in text
        assert "Recreation used: 0m" in text
        assert "Escape attempts: none today" in text
        assert "Planned-use pass: youtube.com (website) ended" in text
        assert "Next action: Plan a smaller anchor task next time." in text
        for blocked_word in ("failure", "relapse", "weak", "addicted", "punish"):
            assert blocked_word not in text.lower()


def test_dashboard_end_day_button_available_for_active_high(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.start_high_access(5, "planned recreation")
        page = DashboardPage(service)

        assert app is not None
        assert page.end_day_button.isHidden() is False
        assert page.end_day_button.isEnabled() is True
        assert page.end_day_button.toolTip() == ""


def test_dashboard_end_day_button_available_for_active_planned_use_pass(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        rule = service.add_rule("site", "youtube.com")
        service.start_day()
        _complete_task_after_claim_delay(service, main.id)
        service.start_planned_use_pass(rule.id, "Watch one tutorial", 5 * 60)
        page = DashboardPage(service)

        assert app is not None
        assert page.end_day_button.isHidden() is False
        assert page.end_day_button.isEnabled() is True
        assert page.end_day_button.toolTip() == ""


def test_dashboard_start_day_confirmation_cancel_keeps_planning(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = DashboardPage(service)
        page._confirm_start_day = lambda: False

        page.start_day_button.click()

        assert app is not None
        assert service.dashboard_snapshot().day_started is False
        assert page.day_status_label.text() == "Planning"
        assert page.start_day_button.isEnabled() is True


def test_dashboard_shows_soft_start_countdown_and_blocks_safe_modes(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(
            test_settings,
            connection,
            now=current_now,
            soft_start_enabled=True,
        )
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = DashboardPage(service)
        page._confirm_start_day = lambda: True

        assert app is not None
        assert page.soft_start_status_label.text() == "Soft Start: 15m after Start Day"
        assert "task completion waits" in page.soft_start_hint_label.text()

        page.start_day_button.click()

        assert page.day_status_label.text() == "Day started"
        assert page.soft_start_status_label.text() == "Soft Start active"
        assert page.soft_start_hint_label.text() == (
            "Tasks unlock in 15m. Relax now; rewards unlock after the buffer."
        )
        assert page.surrender_button.text() == "Surrender unavailable"
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_button.text() == "Bad Day unavailable"
        assert page.bad_day_button.isEnabled() is False
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode unavailable during Soft Start."
        )

        now = start + timedelta(minutes=15)
        page.refresh()

        assert page.soft_start_status_label.text() == "Soft Start complete"
        assert page.bad_day_button.text() == "Activate Bad Day Mode"
        assert page.bad_day_button.isEnabled() is True


def test_dashboard_surrender_timer_states(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = DashboardPage(service)

        assert app is not None
        assert page.surrender_button.text() == "Surrender unavailable"
        assert page.surrender_button.isEnabled() is False
        assert page.surrender_strictness_label.text() == (
            "Surrender strictness: MEDIUM (6h)"
        )
        assert page.placeholder_note_label.text() == (
            "Surrender unavailable until day starts."
        )

        service.start_day()
        now = start + timedelta(hours=5, minutes=1)
        page.refresh()

        assert page.surrender_button.text() == "Surrender unavailable"
        assert page.surrender_button.isEnabled() is False
        assert page.placeholder_note_label.text() == "Surrender available in 59m."

        now = start + timedelta(hours=6)
        page.refresh()

        assert page.surrender_button.text() == "Activate Surrender"
        assert page.surrender_button.isEnabled() is True
        assert page.placeholder_note_label.text() == "Surrender is available now."

        page.surrender_button.click()

        assert page.access_level_label.text() == "SURRENDER"
        assert page.high_timer_label.text() == "Restrictions paused for today."
        assert page.high_status_label.text() == "Underlying access: LOW / Focus"
        assert page.reward_wallet_label.text() == (
            "HIGH access is not needed while Surrender is active."
        )
        assert page.start_high_access_button.isEnabled() is False
        assert page.surrender_button.text() == "Surrender active"
        assert page.surrender_button.isEnabled() is False
        assert page.placeholder_note_label.text() == "Surrender active for today."
        assert page.spend_status_label.text() == "Surrender active for today"
        assert page.test_mode_label.text() == "TEST MODE: ON"


def test_dashboard_surrender_countdown_uses_selected_strictness(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.set_surrender_strictness("low")
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        now = start + timedelta(hours=2, minutes=1)
        page = DashboardPage(service)

        assert app is not None
        assert page.surrender_strictness_label.text() == (
            "Surrender strictness: LOW (3h)"
        )
        assert page.placeholder_note_label.text() == "Surrender available in 59m."
        assert page.surrender_button.isEnabled() is False

        now = start + timedelta(hours=3)
        page.refresh()

        assert page.surrender_button.text() == "Activate Surrender"
        assert page.surrender_button.isEnabled() is True


def test_dashboard_surrender_overrides_underlying_medium_display(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        now = start + timedelta(hours=6)
        service.activate_surrender()
        page = DashboardPage(service)

        assert app is not None
        assert page.access_level_label.text() == "SURRENDER"
        assert page.high_timer_label.text() == "Restrictions paused for today."
        assert page.high_status_label.text() == (
            "Underlying access: MEDIUM / Utility"
        )
        assert page.reward_balance_label.text() == "Available: 30m"
        assert page.reward_wallet_label.text() == (
            "HIGH access is not needed while Surrender is active."
        )
        assert page.start_high_access_button.isEnabled() is False
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.text() == "Surrender active"
        assert page.surrender_button.isEnabled() is False
        assert page.test_mode_label.text() == "TEST MODE: ON"


def test_dashboard_bad_day_mode_medium_baseline(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = DashboardPage(service)

        page.bad_day_button.click()

        assert app is not None
        assert page.access_level_label.text() == "MEDIUM"
        assert page.high_timer_label.text() == "HIGH remaining: inactive"
        assert page.high_status_label.text() == "Bad Day Mode baseline."
        assert page.reward_balance_label.text() == "Available: 0m"
        assert page.bad_day_button.text() == "Bad Day active"
        assert page.bad_day_button.isEnabled() is False
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode active: MEDIUM baseline for today. "
            "HIGH still requires reward."
        )
        assert page.spend_status_label.text() == "Bad Day Mode active for today"
        assert page.test_mode_label.text() == "TEST MODE: ON"


def test_dashboard_high_during_bad_day_keeps_high_display(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: start)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.activate_bad_day_mode()
        service.start_high_access(5, "planned recreation")
        page = DashboardPage(service)

        assert app is not None
        assert page.access_mode_metric_label.text() == "Recreation"
        assert page.access_level_label.text() == "HIGH"
        assert page.high_timer_label.text() == "HIGH remaining: 05:00"
        assert page.high_intent_label.text() == "HIGH intent: planned recreation"
        assert page.high_intent_label.isHidden() is False
        assert page.high_status_label.text() == (
            "Bad Day baseline resumes after HIGH."
        )
        assert page.start_day_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is False
        assert page.end_high_access_button.isEnabled() is True
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode active: MEDIUM baseline for today. "
            "HIGH still requires reward."
        )


def test_dashboard_high_can_start_during_bad_day_when_reward_exists(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Tiny task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.activate_bad_day_mode()
        page = DashboardPage(service)

        assert app is not None
        assert page.access_level_label.text() == "MEDIUM"
        assert page.start_day_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is True
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_button.text() == "Bad Day active"
        assert page.bad_day_button.isEnabled() is False
        assert page.reward_wallet_label.text() == (
            "Available for HIGH: 5m. Recreation today: used 0 / 90 min."
        )


def test_dashboard_surrender_overrides_bad_day_status(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.activate_bad_day_mode()
        now = start + timedelta(hours=6)
        service.activate_surrender()
        page = DashboardPage(service)

        assert app is not None
        assert page.access_level_label.text() == "SURRENDER"
        assert page.start_day_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is False
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.text() == "Surrender active"
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_status_label.text() == (
            "Bad Day Mode is overridden by Surrender."
        )
        assert page.bad_day_button.text() == "Bad Day overridden"
        assert page.bad_day_button.isEnabled() is False


def test_dashboard_refreshes_after_start_high_action(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service._now = lambda: now
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        page = DashboardPage(service)
        page.high_access_dialog_class = _accepted_high_dialog(15)

        page.start_high_access_button.click()

        assert app is not None
        assert page.start_high_access_button.text() == "Start HIGH access"
        assert hasattr(page, "spend_5_button") is False
        assert hasattr(page, "spend_15_button") is False
        assert hasattr(page, "spend_30_button") is False
        assert page.access_level_label.text() == "HIGH"
        assert page.reward_balance_label.text() == "Available: 15m"
        assert page.high_timer_label.text() == "HIGH remaining: 15:00"
        assert page.high_intent_label.text() == "HIGH intent: planned recreation"
        assert page.high_status_label.text() == (
            "Recreation active. Time is counting down."
        )
        assert page.spend_status_label.text() == "Started HIGH for 15 minutes"
        assert page.test_mode_label.text() == "TEST MODE: ON"


def test_dashboard_normal_mode_button_state_progression(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = DashboardPage(service)

        assert app is not None
        assert page.access_level_label.text() == "LOW"
        assert page.start_day_button.isEnabled() is True
        assert page.start_high_access_button.isEnabled() is False
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_button.isEnabled() is False

        service.start_day()
        page.refresh()

        assert page.access_level_label.text() == "LOW"
        assert page.start_day_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is False
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_button.isEnabled() is True

        _complete_task_after_claim_delay(service, task.id)
        page.refresh()

        assert page.access_level_label.text() == "MEDIUM"
        assert page.start_day_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is True
        assert page.end_high_access_button.isEnabled() is False
        assert page.surrender_button.isEnabled() is False
        assert page.bad_day_button.isEnabled() is True


def test_dashboard_start_high_button_disabled_without_balance(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = DashboardPage(service)

        assert app is not None
        assert page.start_high_access_button.text() == "Start HIGH access"
        assert page.start_high_access_button.isEnabled() is False
        assert page.reward_wallet_label.text() == (
            "Available for HIGH: 0m. Recreation today: used 0 / 90 min."
        )


def test_start_high_access_dialog_disables_unaffordable_choices(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Small task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )

        assert app is not None
        assert dialog.intent_input.objectName() == "highIntentInput"
        assert dialog.intent_error_label.objectName() == "highIntentErrorLabel"
        assert dialog.duration_buttons[0].isEnabled() is True
        assert dialog.duration_buttons[1].isEnabled() is False
        assert dialog.duration_buttons[2].isEnabled() is False
        assert dialog.custom_minutes_input.maximum() == 5


def test_start_high_access_dialog_rejects_missing_intent(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Small task", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )
        dialog._accept_if_valid()

        assert app is not None
        assert dialog.intent_error_label.isHidden() is False
        assert "at least 5 characters" in dialog.intent_error_label.text()


def test_start_high_access_dialog_custom_duration_is_capped(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.day_state.add_reward_seconds(90 * 60)

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )

        assert app is not None
        assert dialog.custom_minutes_input.maximum() == 45


def test_start_high_access_dialog_caps_custom_duration_by_daily_remaining(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: now)
        service.day_state.add_reward_seconds(90 * 60)
        service.high_sessions.start(
            day_date="2026-05-08",
            started_at=(now - timedelta(hours=1)).isoformat(),
            ends_at=(now - timedelta(minutes=15)).isoformat(),
            allocated_minutes=80,
            allocated_seconds=80 * 60,
            intent="earlier recreation",
        )

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )

        assert app is not None
        assert dialog.duration_buttons[0].isEnabled() is True
        assert dialog.duration_buttons[1].isEnabled() is False
        assert dialog.duration_buttons[2].isEnabled() is False
        assert dialog.custom_minutes_input.maximum() == 10


def test_dashboard_shows_configured_daily_recreation_cap(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.set_daily_recreation_cap_minutes(120)
        service.day_state.add_reward_seconds(5 * 60)

        page = DashboardPage(service)

        assert app is not None
        assert page.reward_wallet_label.text() == (
            "Available for HIGH: 5m. Recreation today: used 0 / 120 min."
        )


def test_dashboard_disables_high_when_daily_recreation_cap_reached(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: now)
        service.day_state.add_reward_seconds(5 * 60)
        service.high_sessions.start(
            day_date="2026-05-08",
            started_at=(now - timedelta(hours=2)).isoformat(),
            ends_at=(now - timedelta(minutes=30)).isoformat(),
            allocated_minutes=HIGH_DAILY_MAX_MINUTES,
            allocated_seconds=HIGH_DAILY_MAX_MINUTES * 60,
            intent="earlier recreation",
        )

        page = DashboardPage(service)

        assert app is not None
        assert page.start_high_access_button.isEnabled() is False
        assert "Recreation cap reached for today." in page.reward_wallet_label.text()
        assert "Recreation today: used 90 / 90 min." in (
            page.reward_wallet_label.text()
        )
        assert page.start_high_access_button.toolTip() == (
            "Recreation cap reached for today."
        )


def test_start_high_access_dialog_disables_all_choices_during_cooldown(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.day_state.add_reward_seconds(20 * 60)
        service.start_high_access(5, "planned recreation")
        now = now + timedelta(minutes=1)
        service.end_high_access()

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )

        assert app is not None
        assert [button.isEnabled() for button in dialog.duration_buttons] == [
            False,
            False,
            False,
        ]
        assert dialog.custom_duration_button.isEnabled() is False
        assert dialog.custom_minutes_input.isEnabled() is False
        assert "Recreation cooldown: 5m remaining." in (
            dialog.unavailable_reason_label.text()
        )


def test_start_high_access_dialog_disables_all_choices_during_surrender(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        now = start + timedelta(hours=6)
        service.activate_surrender()

        dialog = DashboardPage.high_access_dialog_class(
            service.get_high_access_options()
        )

        assert app is not None
        assert [button.isEnabled() for button in dialog.duration_buttons] == [
            False,
            False,
            False,
        ]
        assert dialog.custom_duration_button.isEnabled() is False
        assert dialog.custom_minutes_input.isEnabled() is False
        ok_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok_button.isEnabled() is False
        assert "Surrender is active" in dialog.unavailable_reason_label.text()


def test_dashboard_end_high_refreshes_refunded_balance(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(15, "planned recreation")
        now = now.replace(minute=4, second=10)
        page = DashboardPage(service)

        page.end_high_access_button.click()

        assert app is not None
        assert page.access_level_label.text() == "MEDIUM"
        assert page.reward_balance_label.text() == "Available: 25m 50s"
        assert page.end_high_access_button.isEnabled() is False
        assert page.start_high_access_button.isEnabled() is False
        assert (
            "Recreation cooldown: 5m remaining."
            in page.reward_wallet_label.text()
        )
        assert page.start_high_access_button.toolTip() == (
            "Recreation cooldown: 5m remaining."
        )


def test_dashboard_shows_persisted_high_after_service_reload(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    later = start.replace(minute=2)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: start)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        service.start_high_access(15, "planned recreation")

        reloaded = _make_service(test_settings, connection, now=lambda: later)
        page = DashboardPage(reloaded)

        assert app is not None
        assert page.access_level_label.text() == "HIGH"
        assert page.high_timer_label.text() == "HIGH remaining: 13:00"
        assert page.high_intent_label.text() == "HIGH intent: planned recreation"
        assert page.end_high_access_button.isEnabled() is True


def test_reward_time_formatter() -> None:
    assert format_reward_time(0) == "0m"
    assert format_reward_time(59) == "59s"
    assert format_reward_time(60) == "1m"
    assert format_reward_time(65) == "1m 5s"
    assert format_reward_time(1550) == "25m 50s"


def _accepted_high_dialog(minutes: int):
    class AcceptedHighDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_minutes(self) -> int:
            return minutes

        def selected_intent(self) -> str:
            return "planned recreation"

    return AcceptedHighDialog


def _assert_dashboard_safety_is_compact(page: DashboardPage) -> None:
    combined = " ".join(
        (
            page.test_mode_explanation_label.text(),
            page.real_blocking_status_label.text(),
            page.websites_status_label.text(),
            page.enforcement_next_step_label.text(),
        )
    )
    forbidden_phrases = (
        "ping shows 127.0.0.1",
        "manual DNS cache flush",
        "SelfBoss does not auto-flush DNS",
        "other browsers are not browser-controlled",
        "URL paths, firewall",
        "Incognito is controlled only if the extension is allowed there",
        "Website HIGH requires trusted Chrome extension control",
        "close existing tabs/browser",
    )
    for phrase in forbidden_phrases:
        assert phrase not in combined


def _make_service(
    settings: AppSettings,
    connection,
    now=None,
    *,
    soft_start_enabled: bool | None = False,
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
        hosts_blocker=hosts_blocker,
    )
    if (
        soft_start_enabled is not None
        and service.day_state.get().day_started_at is None
    ):
        service.set_soft_start_enabled(soft_start_enabled)
    return service
