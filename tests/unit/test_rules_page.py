from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTabWidget,
)

from selfboss.core.models import (  # noqa: E402
    AppSettings,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.core.use_cases import (  # noqa: E402
    BROWSER_HEARTBEAT_FILE_NAME,
    SelfBossAppService,
)
from selfboss.data.db import initialize_database  # noqa: E402
from selfboss.data.repositories import (  # noqa: E402
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.ui.components import CardFrame  # noqa: E402
import selfboss.ui.rules_page as rules_page_module  # noqa: E402
from selfboss.ui.rules_page import RulesPage  # noqa: E402
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


def _write_ready_browser_heartbeat(settings: AppSettings, now: datetime) -> None:
    heartbeat_path = settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "browser": "chrome",
                "context": "regular",
                "extension_connected": True,
                "browser_blocking": "active",
                "browser_blocking_available": True,
                "incognito_allowed": True,
                "dnr_supported": True,
                "dnr_session_rule_count": 1,
                "dnr_last_update_status": "active",
                "youtube_spa_content_script_seen": True,
                "last_heartbeat_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_rules_page_loads_existing_rules(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "game.exe", allow_from_level="medium")

        page = RulesPage(service)

        assert app is not None
        assert page.page_title_label.text() == "Rules"
        assert page.page_title_label.objectName() == "CardTitle"
        assert page.scroll_area.objectName() == "rulesScrollArea"
        assert page.scroll_area.widgetResizable() is True
        assert page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.findChildren(QScrollArea) == [page.scroll_area]
        cards = page.findChildren(CardFrame)
        assert len(cards) == 5
        for card in cards:
            margins = card.card_layout.contentsMargins()
            assert card.objectName() == "CardFrame"
            assert card.card_layout.spacing() == CARD_SPACING
            assert margins.left() == CARD_PADDING
            assert margins.top() == CARD_PADDING
            assert margins.right() == CARD_PADDING
            assert margins.bottom() == CARD_PADDING
            assert bool(card.card_layout.alignment() & Qt.AlignmentFlag.AlignTop)
            assert card.title_label is not None
            assert card.title_label.objectName() == "CardTitle"
        assert "Dry-run rule planning" in page.summary_label.text()
        assert page.site_card_title_label.text() == "Blocked sites"
        assert page.app_card_title_label.text() == "Blocked apps"
        assert page.site_help_label.text() == (
            "Website target can be a domain like reddit.com or a browser path "
            "pattern like youtube.com/shorts/*. Path patterns require the "
            "browser extension."
        )
        assert page.app_help_label.text() == (
            "Windows process names only, for example steam.exe or discord.exe."
        )
        assert page.rules_editor_tabs.objectName() == "rulesEditorTabs"
        assert isinstance(page.rules_editor_tabs, QTabWidget)
        assert page.rules_editor_tabs.count() == 2
        assert page.rules_editor_tabs.tabText(0) == "Websites"
        assert page.rules_editor_tabs.tabText(1) == "Apps"
        assert page.preview_card_title_label.text() == "Dry-run preview"
        assert page.attempts_card_title_label.text() == "Recent test attempts"
        assert page.low_rule_label.text() == "LOW / Focus"
        assert page.medium_rule_label.text() == "MEDIUM / Utility"
        assert page.high_rule_label.text() == "HIGH / Recreation"
        assert _table_text(page.site_rules_table, 0, 0) == "example.com"
        assert _table_text(page.site_rules_table, 0, 1) == "High Risk Escape"
        assert _table_text(page.site_rules_table, 0, 2) == "None"
        assert _table_text(page.site_rules_table, 0, 3) == "HIGH"
        assert _table_text(page.app_rules_table, 0, 0) == "game.exe"
        assert _table_text(page.app_rules_table, 0, 1) == "High Risk Escape"
        assert _table_text(page.app_rules_table, 0, 2) == "None"
        assert _table_text(page.app_rules_table, 0, 3) == "MEDIUM"
        assert "Test Mode" in page.preview_label.text()
        assert page.preview_label.objectName() == "rulesPreviewLabel"
        assert "Test Mode" in page.test_mode_label.text()
        assert page.site_input.objectName() == "websiteTargetInput"
        assert page.app_input.objectName() == "appTargetInput"
        assert page.site_input.placeholderText() == "youtube.com/shorts/*"
        assert page.app_input.placeholderText() == "steam.exe"
        assert page.site_allow_from_input.objectName() == "websiteAllowFromCombo"
        assert page.app_allow_from_input.objectName() == "appAllowFromCombo"
        assert page.site_purpose_input.objectName() == "websitePurposeCombo"
        assert page.app_purpose_input.objectName() == "appPurposeCombo"
        assert page.site_escape_family_input.objectName() == (
            "websiteEscapeFamilyCombo"
        )
        assert page.app_escape_family_input.objectName() == "appEscapeFamilyCombo"
        assert page.duplicate_rules_warning_label.objectName() == (
            "rulesDuplicateWarningLabel"
        )
        assert page.utility_leakage_warning_label.objectName() == (
            "rulesUtilityLeakageWarningLabel"
        )
        assert page.add_site_button.objectName() == "addWebsiteRuleButton"
        assert page.add_app_button.objectName() == "addAppRuleButton"
        assert page.add_starter_rules_button.objectName() == "addStarterRulesButton"
        assert page.add_starter_rules_button.text() == "Add starter rules"
        assert page.update_site_allow_from_button.objectName() == (
            "updateWebsiteRuleButton"
        )
        assert page.remove_site_button.objectName() == "removeWebsiteRuleButton"
        assert page.update_app_allow_from_button.objectName() == "updateAppRuleButton"
        assert page.remove_app_button.objectName() == "removeAppRuleButton"
        assert page.log_site_attempt_button.objectName() == "logWebsiteAttemptButton"
        assert page.log_app_attempt_button.objectName() == "logAppAttemptButton"
        assert page.planned_use_selected_rule_label.objectName() == (
            "plannedUseSelectedRuleLabel"
        )
        assert page.planned_use_helper_label.objectName() == "plannedUseHelperLabel"
        assert page.planned_use_helper_label.text() == (
            "Use passes for task-specific access. For recreation, use HIGH."
        )
        assert page.planned_use_reason_input.objectName() == "plannedUseReasonInput"
        assert page.planned_use_reason_input.placeholderText() == (
            "Reason for this task-specific access"
        )
        assert page.planned_use_duration_combo.objectName() == (
            "plannedUseDurationCombo"
        )
        assert page.start_planned_use_pass_button.objectName() == (
            "startPlannedUsePassButton"
        )
        assert page.active_planned_use_pass_label.objectName() == (
            "activePlannedUsePassLabel"
        )
        assert page.end_planned_use_pass_button.objectName() == (
            "endPlannedUsePassButton"
        )
        assert page.site_allow_from_input.count() == 3
        assert page.app_allow_from_input.count() == 3
        assert page.site_purpose_input.count() == 9
        assert page.app_purpose_input.count() == 9
        assert page.site_escape_family_input.count() == 10
        assert page.app_escape_family_input.count() == 10
        assert [
            page.planned_use_duration_combo.itemData(index)
            for index in range(page.planned_use_duration_combo.count())
        ] == [600, 900, 1500]
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == "Select a rule first."
        assert page.end_planned_use_pass_button.isEnabled() is False
        assert page.active_planned_use_pass_label.text() == (
            "Active planned-use pass: none"
        )
        assert page.website_target_input is page.site_input
        assert page.app_target_input is page.app_input
        assert page.website_allowed_from_combo is page.site_allow_from_input
        assert page.app_allowed_from_combo is page.app_allow_from_input
        assert page.website_purpose_combo is page.site_purpose_input
        assert page.app_purpose_combo is page.app_purpose_input
        assert page.website_escape_family_combo is page.site_escape_family_input
        assert page.app_escape_family_combo is page.app_escape_family_input
        assert page.add_website_rule_button is page.add_site_button
        assert page.add_app_rule_button is page.add_app_button
        assert page.log_website_attempt_button is page.log_site_attempt_button
        assert page.log_app_rule_attempt_button is page.log_app_attempt_button
        assert page.website_rules_table is page.site_rules_table
        assert isinstance(page.site_rules_table, QTableWidget)
        assert isinstance(page.app_rules_table, QTableWidget)
        assert page.site_rules_table.objectName() == "website_rules_table"
        assert page.app_rules_table.objectName() == "app_rules_table"
        assert page.site_rules_table.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.app_rules_table.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.site_rules_table.columnCount() == 5
        assert page.app_rules_table.columnCount() == 5
        assert page.rules_preview_area is not None
        assert page.recent_attempts_table.objectName() == "recentAttemptsTable"
        assert page.recent_attempts_table.columnCount() == 6
        assert page.access_attempts_table is page.recent_attempts_table
        assert page.attempt_summary_empty_label.objectName() == (
            "attemptSummaryEmptyLabel"
        )
        assert page.attempt_summary_total_label.objectName() == (
            "attemptSummaryTotalLabel"
        )
        assert page.attempt_summary_families_label.objectName() == (
            "attemptSummaryFamiliesLabel"
        )
        assert page.attempt_summary_decisions_label.objectName() == (
            "attemptSummaryDecisionsLabel"
        )
        assert page.attempt_summary_path_label.objectName() == (
            "attemptSummaryPathLabel"
        )
        assert page.attempt_summary_switching_label.objectName() == (
            "attemptSummarySwitchingLabel"
        )
        assert page.attempt_summary_helper_label.objectName() == (
            "attemptSummaryHelperLabel"
        )
        assert page.attempt_decision_filter_combo.objectName() == (
            "attemptDecisionFilterCombo"
        )
        assert page.attempt_process_filter_input.objectName() == (
            "attemptProcessFilterInput"
        )
        assert page.attempt_process_filter_input.placeholderText() == (
            "Process filter"
        )
        assert page.attempt_access_filter_combo.objectName() == (
            "attemptAccessFilterCombo"
        )
        assert [
            page.attempt_decision_filter_combo.itemData(index)
            for index in range(page.attempt_decision_filter_combo.count())
        ] == ["all", "would_block", "would_allow"]
        assert [
            page.attempt_access_filter_combo.itemData(index)
            for index in range(page.attempt_access_filter_combo.count())
        ] == ["all", "low", "medium", "high"]
        assert page.attempt_summary_empty_label.text() == (
            "No Test Mode attempts logged yet."
        )
        assert page.attempt_summary_empty_label.isVisibleTo(page) is True
        assert page.attempt_summary_helper_label.text() == (
            "No Test Mode attempts logged yet. "
            "Log a test attempt from a selected rule to see a pattern."
        )
        _assert_edit_buttons_waiting_for_selection(page)
        _assert_no_real_blocking_controls(page)


def test_rules_page_hides_dev_testing_surfaces_in_production(
    test_settings: AppSettings,
) -> None:
    QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service, production_mode=True)

        assert page.preview_card.isHidden()
        assert page.recent_attempts_card.isHidden()
        assert page.log_site_attempt_button.isHidden()
        assert page.log_app_attempt_button.isHidden()
        assert not page.add_site_button.isHidden()
        assert page.site_purpose_input.isHidden()
        assert page.site_escape_family_input.isHidden()
        assert page.app_purpose_input.isHidden()
        assert page.app_escape_family_input.isHidden()
        assert page.site_rules_table.isColumnHidden(1)
        assert page.site_rules_table.isColumnHidden(2)
        assert page.app_rules_table.isColumnHidden(1)
        assert page.app_rules_table.isColumnHidden(2)
        assert "Dry-run" not in page.summary_label.text()


def test_rules_page_adds_starter_rules_and_reports_idempotent_result(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.add_starter_rules_button.click()

        assert app is not None
        assert page.status_label.text() == "Created 15 rules, skipped 0 existing."
        assert page.site_rules_table.rowCount() == 9
        assert page.app_rules_table.rowCount() == 6
        assert [rule.target for rule in service.get_rules("site")] == [
            "youtube.com",
            "www.youtube.com",
            "youtube.com/shorts/*",
            "www.youtube.com/shorts/*",
            "m.youtube.com/shorts/*",
            "discord.com",
            "reddit.com",
            "mangadex.org",
            "mangalib.me",
        ]
        assert [rule.target for rule in service.get_rules("app")] == [
            "steam.exe",
            "steamwebhelper.exe",
            "discord.exe",
            "epicgameslauncher.exe",
            "riotclientservices.exe",
            "battlenet.exe",
        ]
        assert "youtube.com" in page.preview_blocked_sites_label.text()
        assert "steam.exe" in page.preview_blocked_apps_label.text()

        page.add_starter_rules_button.click()

        assert page.status_label.text() == "Created 0 rules, skipped 15 existing."
        assert page.site_rules_table.rowCount() == 9
        assert page.app_rules_table.rowCount() == 6
        _assert_no_real_blocking_controls(page)


def test_rules_page_adds_site_and_app_rules_with_thresholds(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_input.setText("example.com")
        page.site_purpose_input.setCurrentIndex(
            page.site_purpose_input.findData("compulsive_stimulation")
        )
        page.site_escape_family_input.setCurrentIndex(
            page.site_escape_family_input.findData("video")
        )
        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("high")
        )
        page.add_site_button.click()
        page.app_input.setText("game.exe")
        page.app_purpose_input.setCurrentIndex(
            page.app_purpose_input.findData("gateway_app")
        )
        page.app_escape_family_input.setCurrentIndex(
            page.app_escape_family_input.findData("launcher")
        )
        page.app_allow_from_input.setCurrentIndex(
            page.app_allow_from_input.findData("medium")
        )
        page.add_app_button.click()

        assert app is not None
        assert [rule.target for rule in service.get_rules("site")] == ["example.com"]
        assert [rule.target for rule in service.get_rules("app")] == ["game.exe"]
        assert service.get_rules("site")[0].allow_from_level == "high"
        assert service.get_rules("app")[0].allow_from_level == "medium"
        assert service.get_rules("site")[0].purpose == "compulsive_stimulation"
        assert service.get_rules("site")[0].escape_family == "video"
        assert service.get_rules("app")[0].purpose == "gateway_app"
        assert service.get_rules("app")[0].escape_family == "launcher"
        assert _table_text(page.site_rules_table, 0, 1) == "Compulsive Stimulation"
        assert _table_text(page.site_rules_table, 0, 2) == "Video"
        assert _table_text(page.site_rules_table, 0, 3) == "HIGH"
        assert _table_text(page.app_rules_table, 0, 1) == "Gateway App"
        assert _table_text(page.app_rules_table, 0, 2) == "Launcher"
        assert _table_text(page.app_rules_table, 0, 3) == "MEDIUM"
        assert "example.com" in page.preview_blocked_sites_label.text()
        assert "game.exe" in page.preview_blocked_apps_label.text()
        _assert_edit_buttons_waiting_for_selection(page)
        _assert_no_real_blocking_controls(page)


def test_rules_page_displays_legacy_path_variants_canonically_and_warns(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            """
            INSERT INTO rules (
                rule_type,
                target,
                enabled,
                allow_from_level,
                purpose,
                escape_family,
                created_at
            )
            VALUES (?, ?, 1, ?, ?, ?, ?)
            """,
            [
                (
                    "site",
                    "youtube.com/shorts",
                    "high",
                    "compulsive_stimulation",
                    "video",
                    now,
                ),
                (
                    "site",
                    "youtube.com/shorts/",
                    "high",
                    "compulsive_stimulation",
                    "video",
                    now,
                ),
            ],
        )
        service = _make_service(test_settings, connection)

        page = RulesPage(service)

        assert app is not None
        assert _table_text(page.site_rules_table, 0, 0) == "youtube.com/shorts/*"
        assert _table_text(page.site_rules_table, 1, 0) == "youtube.com/shorts/*"
        assert page.duplicate_rules_warning_label.text() == (
            "Duplicate-equivalent rule: youtube.com/shorts/*"
        )

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page._remove_selected_rule("site", page.site_rules_table)

        assert [rule.target for rule in service.get_rules("site")] == [
            "youtube.com/shorts/"
        ]


def test_rules_page_does_not_warn_for_path_variants_with_different_metadata(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        now = datetime.now(timezone.utc).isoformat()
        connection.executemany(
            """
            INSERT INTO rules (
                rule_type,
                target,
                enabled,
                allow_from_level,
                purpose,
                escape_family,
                created_at
            )
            VALUES (?, ?, 1, ?, ?, ?, ?)
            """,
            [
                (
                    "site",
                    "youtube.com/shorts",
                    "high",
                    "compulsive_stimulation",
                    "video",
                    now,
                ),
                (
                    "site",
                    "youtube.com/shorts/",
                    "medium",
                    "compulsive_stimulation",
                    "video",
                    now,
                ),
            ],
        )
        service = _make_service(test_settings, connection)

        page = RulesPage(service)

        assert app is not None
        assert page.duplicate_rules_warning_label.text() == ""


def test_rules_page_suggests_escape_family_for_obvious_targets(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_escape_family_input.setCurrentIndex(
            page.site_escape_family_input.findData("none")
        )
        page.site_input.setText("YouTube.COM/Shorts")
        assert page.site_escape_family_input.currentData() == "video"

        page.site_escape_family_input.setCurrentIndex(
            page.site_escape_family_input.findData("none")
        )
        page.site_input.setText("focus.example")
        assert page.site_escape_family_input.currentData() == "none"

        page.app_escape_family_input.setCurrentIndex(
            page.app_escape_family_input.findData("none")
        )
        page.app_input.setText("steam.exe")
        assert app is not None
        assert page.app_escape_family_input.currentData() == "launcher"


def test_rules_page_warns_for_obvious_escape_targets_below_high(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    warning = (
        "Utility mode warning: this looks like an escape target. HIGH is "
        "recommended."
    )
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("medium")
        )
        page.site_input.setText("youtube.com")

        assert app is not None
        assert page.utility_leakage_warning_label.text() == warning

        page.site_escape_family_input.setCurrentIndex(
            page.site_escape_family_input.findData("none")
        )
        page.site_input.setText("docs.example.com")
        assert page.utility_leakage_warning_label.text() == ""

        page.rules_editor_tabs.setCurrentWidget(page.rules_editor_tabs.widget(1))
        page.app_allow_from_input.setCurrentIndex(
            page.app_allow_from_input.findData("low")
        )
        page.app_input.setText("steam.exe")

        assert page.utility_leakage_warning_label.text() == warning


def test_rules_page_warning_does_not_block_add_or_update(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    warning = (
        "Utility mode warning: this looks like an escape target. HIGH is "
        "recommended."
    )
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("medium")
        )
        page.site_input.setText("reddit.com")

        assert app is not None
        assert page.utility_leakage_warning_label.text() == warning

        page.add_site_rule()
        rule = service.get_rules("site")[0]

        assert rule.target == "reddit.com"
        assert rule.allow_from_level == "medium"
        assert rule.escape_family == "random_browsing"

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("low")
        )

        assert page.utility_leakage_warning_label.text() == warning

        page.update_selected_site_allow_from_level()

        assert service.get_rules("site")[0].allow_from_level == "low"


def test_rules_page_warns_for_selected_existing_escape_rule(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    warning = (
        "Utility mode warning: this looks like an escape target. HIGH is "
        "recommended."
    )
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("app", "discord.exe", allow_from_level="medium")

        page = RulesPage(service)
        page.rules_editor_tabs.setCurrentWidget(page.rules_editor_tabs.widget(1))
        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)

        assert app is not None
        assert page.utility_leakage_warning_label.text() == warning


def test_rules_page_purpose_suggests_allow_from_for_new_rules(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_purpose_input.setCurrentIndex(
            page.site_purpose_input.findData("work_tool")
        )
        assert page.site_allow_from_input.currentData() == "low"

        page.site_purpose_input.setCurrentIndex(
            page.site_purpose_input.findData("gateway_app")
        )
        assert page.site_allow_from_input.currentData() == "high"

        service.add_rule("site", "example.com", allow_from_level="medium")
        page.refresh()
        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        assert page.site_allow_from_input.currentData() == "medium"

        page.site_purpose_input.setCurrentIndex(
            page.site_purpose_input.findData("work_tool")
        )

        assert app is not None
        assert page.site_allow_from_input.currentData() == "medium"
        _assert_no_real_blocking_controls(page)


def test_rules_page_preview_follows_normal_low_medium_and_high(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.add_rule("site", "low.example", allow_from_level="low")
        service.add_rule("site", "medium.example", allow_from_level="medium")
        service.add_rule("site", "high.example", allow_from_level="high")
        page = RulesPage(service)

        assert app is not None
        assert "access level: LOW" in page.preview_label.text()
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: low.example (high_risk_escape, none)"
        )
        assert page.preview_blocked_sites_label.text() == (
            "Blocked sites now: medium.example (high_risk_escape, none), "
            "high.example (high_risk_escape, none)"
        )

        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        page.refresh()

        assert "access level: MEDIUM" in page.preview_label.text()
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: low.example (high_risk_escape, none), "
            "medium.example (high_risk_escape, none)"
        )
        assert page.preview_blocked_sites_label.text() == (
            "Blocked sites now: high.example (high_risk_escape, none)"
        )

        _write_ready_browser_heartbeat(
            test_settings,
            datetime.now(timezone.utc),
        )
        service.start_high_access(5, "planned recreation")
        page.refresh()

        assert "access level: HIGH" in page.preview_label.text()
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: low.example (high_risk_escape, none), "
            "medium.example (high_risk_escape, none), "
            "high.example (high_risk_escape, none)"
        )
        assert page.preview_blocked_sites_label.text() == "Blocked sites now: None"
        _assert_no_real_blocking_controls(page)


def test_rules_page_rejects_invalid_website_rule(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_input.setText("https://youtube.com")
        page.add_site_button.click()

        assert app is not None
        assert service.get_rules("site") == []
        assert page.site_rules_table.rowCount() == 0
        assert "Website rules" in page.status_label.text()


def test_rules_page_rejects_invalid_app_rule(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.app_input.setText("youtube.com")
        page.add_app_button.click()

        assert app is not None
        assert service.get_rules("app") == []
        assert page.app_rules_table.rowCount() == 0
        assert "App rules" in page.status_label.text()


def test_rules_page_displays_normalized_lowercase_rules(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_input.setText("  YouTube.COM/Shorts  ")
        page.add_site_button.click()
        page.app_input.setText("  STEAM.EXE  ")
        page.add_app_button.click()

        assert app is not None
        assert service.get_rules("site")[0].target == "youtube.com/shorts/*"
        assert service.get_rules("app")[0].target == "steam.exe"
        assert _table_text(page.site_rules_table, 0, 0) == "youtube.com/shorts/*"
        assert _table_text(page.site_rules_table, 0, 3) == "HIGH"
        assert _table_text(page.app_rules_table, 0, 0) == "steam.exe"
        assert _table_text(page.app_rules_table, 0, 3) == "HIGH"


def test_rules_page_compacts_long_targets_with_tooltips(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    long_domain = (
        "really-long-video-site-name-with-many-segments.example-subdomain.test"
    )
    long_process = "really-long-distraction-process-name-for-testing.exe"
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = RulesPage(service)

        page.site_input.setText(long_domain)
        page.add_site_button.click()
        page.app_input.setText(long_process)
        page.add_app_button.click()

        site_item = page.site_rules_table.item(0, 0)
        app_item = page.app_rules_table.item(0, 0)

        assert app is not None
        assert site_item.text() == long_domain
        assert site_item.toolTip() == f"{long_domain} - high_risk_escape - none - HIGH"
        assert app_item.text() == long_process
        assert app_item.toolTip() == (
            f"{long_process} - high_risk_escape - none - HIGH"
        )
        assert page.site_rules_table.wordWrap() is False
        assert page.app_rules_table.wordWrap() is False


def test_rules_page_update_buttons_enable_on_selection_and_persist_changes(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "game.exe", allow_from_level="medium")
        page = RulesPage(service)

        _assert_edit_buttons_waiting_for_selection(page)
        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("medium")
        )
        page.site_purpose_input.setCurrentIndex(
            page.site_purpose_input.findData("work_tool")
        )
        page.site_escape_family_input.setCurrentIndex(
            page.site_escape_family_input.findData("fake_productivity")
        )
        assert page.update_site_allow_from_button.isEnabled() is True
        page.update_site_allow_from_button.click()
        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        page.app_allow_from_input.setCurrentIndex(
            page.app_allow_from_input.findData("low")
        )
        page.app_purpose_input.setCurrentIndex(
            page.app_purpose_input.findData("controlled_recreation")
        )
        page.app_escape_family_input.setCurrentIndex(
            page.app_escape_family_input.findData("games")
        )
        assert page.update_app_allow_from_button.isEnabled() is True
        page.update_app_allow_from_button.click()

        assert app is not None
        assert page.update_site_allow_from_button.isHidden() is False
        assert page.update_app_allow_from_button.isHidden() is False
        assert service.get_rules("site")[0].allow_from_level == "medium"
        assert service.get_rules("app")[0].allow_from_level == "low"
        assert service.get_rules("site")[0].purpose == "work_tool"
        assert service.get_rules("site")[0].escape_family == "fake_productivity"
        assert service.get_rules("app")[0].purpose == "controlled_recreation"
        assert service.get_rules("app")[0].escape_family == "games"
        assert _table_text(page.site_rules_table, 0, 1) == "Work Tool"
        assert _table_text(page.site_rules_table, 0, 2) == "Fake Productivity"
        assert _table_text(page.site_rules_table, 0, 3) == "MEDIUM"
        assert _table_text(page.app_rules_table, 0, 1) == "Controlled Recreation"
        assert _table_text(page.app_rules_table, 0, 2) == "Games"
        assert _table_text(page.app_rules_table, 0, 3) == "LOW"
        _assert_edit_buttons_waiting_for_selection(page)


def test_rules_page_remove_buttons_enable_on_selection_and_remove_rules(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("site", "example.com")
        service.add_rule("app", "game.exe")
        page = RulesPage(service)

        _assert_edit_buttons_waiting_for_selection(page)
        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        assert page.remove_site_button.isEnabled() is True
        page.remove_site_button.click()
        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        assert page.remove_app_button.isEnabled() is True
        page.remove_app_button.click()

        assert app is not None
        assert page.remove_site_button.isHidden() is False
        assert page.remove_app_button.isHidden() is False
        assert service.get_rules("site") == []
        assert service.get_rules("app") == []
        assert page.site_rules_table.rowCount() == 0
        assert page.app_rules_table.rowCount() == 0
        _assert_edit_buttons_waiting_for_selection(page)


def test_rules_page_locks_removal_and_weakening_during_active_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "game.exe", allow_from_level="medium")
        service.start_day()
        page = RulesPage(service)

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)

        assert app is not None
        assert page.update_site_allow_from_button.isEnabled() is False
        assert "Rules are locked during an active day" in (
            page.update_site_allow_from_button.toolTip()
        )
        assert page.remove_site_button.isEnabled() is False
        assert "Rules are locked during an active day" in (
            page.remove_site_button.toolTip()
        )

        page._remove_selected_rule("site", page.site_rules_table)
        assert "Rules are locked during an active day" in page.status_label.text()
        assert [rule.target for rule in service.get_rules("site")] == ["example.com"]

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("medium")
        )
        page._update_selected_allow_from_level(
            "site",
            page.site_rules_table,
            page.site_allow_from_input,
            page.site_purpose_input,
            page.site_escape_family_input,
        )
        assert "Rules are locked during an active day" in page.status_label.text()
        assert service.get_rules("site")[0].allow_from_level == "high"

        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        page.app_allow_from_input.setCurrentIndex(
            page.app_allow_from_input.findData("high")
        )
        assert page.update_app_allow_from_button.isEnabled() is False
        page._update_selected_allow_from_level(
            "app",
            page.app_rules_table,
            page.app_allow_from_input,
            page.app_purpose_input,
            page.app_escape_family_input,
        )

        assert "Rules are locked during an active day" in page.status_label.text()
        assert service.get_rules("app")[0].allow_from_level == "medium"


def test_rules_page_logs_manual_attempt_for_selected_rule(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        page = RulesPage(service)

        _assert_edit_buttons_waiting_for_selection(page)
        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        assert page.log_site_attempt_button.isEnabled() is True
        page.log_site_attempt_button.click()

        attempts = service.list_recent_access_attempts()

        assert app is not None
        assert len(attempts) == 1
        assert attempts[0].target == "youtube.com"
        assert attempts[0].decision == "would_block_in_current_mode"
        assert attempts[0].purpose == "compulsive_stimulation"
        assert attempts[0].escape_family == "video"
        assert page.attempt_summary_empty_label.isVisibleTo(page) is False
        assert page.attempt_summary_total_label.text() == "Today's attempts: 1"
        assert page.attempt_summary_families_label.text() == (
            "Top escape families: Video 1"
        )
        assert page.attempt_summary_decisions_label.text() == (
            "Decisions: Would Block In Current Mode 1"
        )
        assert page.attempt_summary_path_label.text() == (
            "Today's family path: video"
        )
        assert page.attempt_summary_switching_label.text() == (
            "Possible switching: No"
        )
        assert page.attempt_summary_helper_label.text() == (
            "Recent attempts are concentrated in one escape family: video. "
            "Check whether this family should stay HIGH during Focus/Utility."
        )
        assert page.recent_attempts_table.rowCount() == 1
        assert _table_text(page.recent_attempts_table, 0, 1) == "youtube.com"
        assert _table_text(page.recent_attempts_table, 0, 2) == (
            "Would Block In Current Mode"
        )
        assert _table_text(page.recent_attempts_table, 0, 3) == (
            "Compulsive Stimulation"
        )
        assert _table_text(page.recent_attempts_table, 0, 4) == "Video"
        assert _table_text(page.recent_attempts_table, 0, 5) == "LOW"
        _assert_edit_buttons_waiting_for_selection(page)
        _assert_no_real_blocking_controls(page)


def test_rules_page_starts_ends_planned_use_pass_and_logs_pass_decision(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule(
            "site",
            "youtube.com",
            allow_from_level="high",
            purpose="compulsive_stimulation",
            escape_family="video",
        )
        page = RulesPage(service)

        assert app is not None
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == "Select a rule first."

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)

        assert page.planned_use_selected_rule_label.text() == (
            "Planned-use pass target: youtube.com (website)"
        )
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == (
            "Enter a planned-use reason."
        )

        page.planned_use_reason_input.setText("Watch one PySide6 tutorial")

        assert page.start_planned_use_pass_button.isEnabled() is True
        assert page.start_planned_use_pass_button.toolTip() == (
            "Temporarily allow only the selected rule target."
        )

        page.start_planned_use_pass_button.click()
        active_pass = service.get_active_planned_use_pass()

        assert active_pass is not None
        assert active_pass.target == "youtube.com"
        assert active_pass.reason == "Watch one PySide6 tutorial"
        assert page.status_label.text() == "Started planned-use pass: youtube.com"
        assert page.active_planned_use_pass_label.text().startswith(
            "Active planned-use pass: youtube.com (website)"
        )
        assert "until" in page.active_planned_use_pass_label.text()
        assert "Watch one PySide6 tutorial" in page.active_planned_use_pass_label.text()
        assert page.end_planned_use_pass_button.isEnabled() is True
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: youtube.com "
            "(compulsive_stimulation, video, planned-use pass)"
        )
        assert page.preview_blocked_sites_label.text() == "Blocked sites now: None"

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.log_site_attempt_button.click()

        attempts = service.list_recent_access_attempts()
        assert attempts[0].decision == "allowed_by_planned_use_pass"
        assert _table_text(page.recent_attempts_table, 0, 2) == (
            "Allowed by pass"
        )

        page.end_planned_use_pass_button.click()

        assert service.get_active_planned_use_pass() is None
        assert page.status_label.text() == "Ended planned-use pass: youtube.com"
        assert page.active_planned_use_pass_label.text() == (
            "Active planned-use pass: none"
        )
        assert page.preview_blocked_sites_label.text() == (
            "Blocked sites now: youtube.com (compulsive_stimulation, video)"
        )
        assert page.end_planned_use_pass_button.isEnabled() is False
        _assert_no_real_blocking_controls(page)


def test_rules_page_planned_use_signal_updates_do_not_crash(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("site", "youtube.com", allow_from_level="high")
        service.add_rule("app", "steam.exe", allow_from_level="high")
        page = RulesPage(service)

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.planned_use_reason_input.setText("Watch one PySide6 tutorial")

        assert app is not None
        assert page.start_planned_use_pass_button.isEnabled() is True

        page.rules_editor_tabs.setCurrentIndex(1)

        assert page.planned_use_selected_rule_label.text() == (
            "Planned-use pass: select a rule first."
        )
        assert page.start_planned_use_pass_button.isEnabled() is False
        assert page.start_planned_use_pass_button.toolTip() == "Select a rule first."

        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        page.planned_use_reason_input.setText("Use Steam for one declared test")

        assert page.planned_use_selected_rule_label.text() == (
            "Planned-use pass target: steam.exe (app)"
        )
        assert page.start_planned_use_pass_button.isEnabled() is True
        _assert_no_real_blocking_controls(page)


def test_rules_page_stale_disabled_rule_selection_cannot_start_planned_use_pass(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        rule = service.add_rule("site", "youtube.com", allow_from_level="high")
        page = RulesPage(service)

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.planned_use_reason_input.setText("Watch one PySide6 tutorial")
        assert page.start_planned_use_pass_button.isEnabled() is True

        with connection:
            connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (rule.id,))
        page.start_planned_use_pass_button.click()

        assert app is not None
        assert "Disabled rules cannot use planned-use passes" in page.status_label.text()
        assert service.get_active_planned_use_pass() is None
        assert page.active_planned_use_pass_label.text() == (
            "Active planned-use pass: none"
        )
        _assert_no_real_blocking_controls(page)


def test_rules_page_attempt_summary_updates_with_family_switching(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule(
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
            purpose="gateway_app",
            escape_family="games",
        )
        page = RulesPage(service)

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.log_site_attempt_button.click()
        page.rules_editor_tabs.setCurrentIndex(1)
        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        page.log_app_attempt_button.click()

        assert app is not None
        assert page.attempt_summary_total_label.text() == "Today's attempts: 2"
        assert page.attempt_summary_families_label.text() == (
            "Top escape families: Games 1, Video 1"
        )
        assert page.attempt_summary_decisions_label.text() == (
            "Decisions: Would Block In Current Mode 2"
        )
        assert page.attempt_summary_path_label.text() == (
            "Today's family path: video -> games"
        )
        assert page.attempt_summary_switching_label.text() == (
            "Possible switching: Yes"
        )
        assert page.attempt_summary_helper_label.text() == (
            "Possible escape switching detected: recent attempts moved across "
            "2 families: video -> games. "
            "Consider returning to the anchor task, using earned Recreation, or "
            "entering Recovery if the day is breaking."
        )
        assert page.recent_attempts_table.rowCount() == 2
        _assert_no_real_blocking_controls(page)


def test_rules_page_filters_recent_armed_dry_run_attempts(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.add_rule("app", "steam.exe", allow_from_level="high")
        service.add_rule("app", "chat.exe", allow_from_level="medium")
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.set_enforcement_mode("armed_dry_run")
        service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe", "chat.exe"]
        )
        now = now + timedelta(seconds=61)
        _complete_task_after_claim_delay(service, main.id)
        service.run_armed_dry_run_process_scan_cycle(
            process_names=["steam.exe", "chat.exe"]
        )
        page = RulesPage(service)

        assert app is not None
        assert page.recent_attempts_table.rowCount() == 4

        page.attempt_decision_filter_combo.setCurrentIndex(
            page.attempt_decision_filter_combo.findData("would_allow")
        )

        assert page.recent_attempts_table.rowCount() == 1
        assert _table_text(page.recent_attempts_table, 0, 1) == "chat.exe"
        assert _table_text(page.recent_attempts_table, 0, 2) == "Would allow"

        page.attempt_decision_filter_combo.setCurrentIndex(
            page.attempt_decision_filter_combo.findData("all")
        )
        page.attempt_process_filter_input.setText("steam")

        assert page.recent_attempts_table.rowCount() == 2
        assert _table_text(page.recent_attempts_table, 0, 1) == "steam.exe"
        assert _table_text(page.recent_attempts_table, 1, 1) == "steam.exe"

        page.attempt_process_filter_input.clear()
        page.attempt_access_filter_combo.setCurrentIndex(
            page.attempt_access_filter_combo.findData("medium")
        )

        assert page.recent_attempts_table.rowCount() == 2
        assert _table_text(page.recent_attempts_table, 0, 5) == "MEDIUM"
        assert _table_text(page.recent_attempts_table, 1, 5) == "MEDIUM"


def test_rules_page_shows_real_process_action_status(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=lambda: now)
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

        page = RulesPage(service)

        assert app is not None
        assert page.recent_attempts_table.rowCount() == 1
        assert _table_text(page.recent_attempts_table, 0, 1) == "steam.exe"
        assert _table_text(page.recent_attempts_table, 0, 2) == (
            "Terminate requested"
        )
        assert _table_text(page.recent_attempts_table, 0, 2) != "Would block"
        _assert_no_real_blocking_controls(page)


def test_rules_page_shows_real_process_pass_allowed_status_and_local_time(
    test_settings: AppSettings,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 8, 13, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(
        rules_page_module,
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

        page = RulesPage(service)

        assert app is not None
        assert page.recent_attempts_table.rowCount() == 1
        assert _table_text(page.recent_attempts_table, 0, 0) == "2026-05-08 16:09"
        assert _table_text(page.recent_attempts_table, 0, 1) == "notepad.exe"
        assert _table_text(page.recent_attempts_table, 0, 2) == "Allowed by pass"


def test_rules_page_recent_attempts_default_to_today(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        yesterday = service.add_rule(
            "app",
            "discord.exe",
            allow_from_level="high",
            purpose="high_risk_escape",
            escape_family="chat",
        )
        today = service.add_rule(
            "app",
            "steam.exe",
            allow_from_level="high",
            purpose="gateway_app",
            escape_family="launcher",
        )
        service.log_manual_rule_attempt(yesterday.id)

        now = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
        service.log_manual_rule_attempt(today.id)
        page = RulesPage(service)

        assert app is not None
        assert page.recent_attempts_table.rowCount() == 1
        assert _table_text(page.recent_attempts_table, 0, 1) == "steam.exe"
        assert page.attempt_summary_total_label.text() == "Today's attempts: 1"
        assert page.attempt_summary_path_label.text() == (
            "Today's family path: launcher"
        )
        assert len(service.list_recent_access_attempts(today_only=False)) == 2


def test_rules_page_preview_shows_surrender_pauses_restrictions(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection, now=current_now)
        service.add_rule("site", "example.com", allow_from_level="high")
        service.add_rule("app", "game.exe", allow_from_level="medium")
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        now = start + timedelta(hours=6)
        service.activate_surrender()
        page = RulesPage(service)

        assert app is not None
        assert "Surrender active" in page.preview_label.text()
        assert "Test Mode" in page.preview_label.text()
        assert "Effective mode:" in page.preview_label.text()
        assert "restriction state: SURRENDER" in page.preview_label.text()
        assert page.preview_blocked_sites_label.text() == "Blocked sites now: None"
        assert page.preview_blocked_apps_label.text() == "Blocked apps now: None"
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: example.com (high_risk_escape, none)"
        )
        assert page.preview_allowed_apps_label.text() == (
            "Allowed apps now: game.exe (high_risk_escape, none)"
        )
        assert page.add_site_button.isEnabled() is True
        assert page.add_app_button.isEnabled() is True

        page.site_rules_table.selectRow(0)
        page.site_rules_table.setCurrentCell(0, 0)
        page.site_allow_from_input.setCurrentIndex(
            page.site_allow_from_input.findData("low")
        )
        assert page.update_site_allow_from_button.isEnabled() is False
        assert "Rules are locked during an active day" in (
            page.update_site_allow_from_button.toolTip()
        )
        page._update_selected_allow_from_level(
            "site",
            page.site_rules_table,
            page.site_allow_from_input,
            page.site_purpose_input,
            page.site_escape_family_input,
        )
        assert "Rules are locked during an active day" in page.status_label.text()
        assert service.get_rules("site")[0].allow_from_level == "high"

        page.app_rules_table.selectRow(0)
        page.app_rules_table.setCurrentCell(0, 0)
        assert page.remove_app_button.isEnabled() is False
        page._remove_selected_rule("app", page.app_rules_table)
        assert "Rules are locked during an active day" in page.status_label.text()
        assert [rule.target for rule in service.get_rules("app")] == ["game.exe"]

        page.site_input.setText("future.example")
        page.add_site_button.click()
        assert [rule.target for rule in service.get_rules("site")] == [
            "example.com",
            "future.example",
        ]
        assert page.preview_blocked_sites_label.text() == "Blocked sites now: None"
        assert "future.example" in page.preview_allowed_sites_label.text()
        _assert_no_real_blocking_controls(page)


def test_rules_page_preview_shows_bad_day_medium_baseline(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.add_rule("site", "medium.example", allow_from_level="medium")
        service.add_rule("site", "high.example", allow_from_level="high")
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.activate_bad_day_mode()
        page = RulesPage(service)

        assert app is not None
        assert "restriction state: BAD DAY" in page.preview_label.text()
        assert page.preview_allowed_sites_label.text() == (
            "Allowed sites now: medium.example (high_risk_escape, none)"
        )
        assert page.preview_blocked_sites_label.text() == (
            "Blocked sites now: high.example (high_risk_escape, none)"
        )
        _assert_no_real_blocking_controls(page)


def _assert_edit_buttons_waiting_for_selection(page: RulesPage) -> None:
    for button in (
        page.update_site_allow_from_button,
        page.remove_site_button,
        page.log_site_attempt_button,
        page.update_app_allow_from_button,
        page.remove_app_button,
        page.log_app_attempt_button,
    ):
        assert button.isHidden() is False
        assert not button.isEnabled()
        assert button.toolTip() == "Select a rule first."


def _table_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _assert_no_real_blocking_controls(page: RulesPage) -> None:
    forbidden_phrases = (
        "real blocking",
        "real enforcement",
        "enable blocking",
        "enable enforcement",
        "apply blocking",
    )
    for button in page.findChildren(QPushButton):
        text = button.text().lower()
        if button.isEnabled():
            assert not any(phrase in text for phrase in forbidden_phrases)


def _make_service(
    settings: AppSettings,
    connection,
    now=None,
    *,
    soft_start_enabled: bool | None = False,
) -> SelfBossAppService:
    service = SelfBossAppService(
        settings=settings,
        tasks=TaskRepository(connection),
        day_state=DayStateRepository(connection),
        rewards=RewardLedgerRepository(connection),
        high_sessions=HighSessionRepository(connection),
        rules=RuleRepository(connection),
        now_provider=now,
    )
    if soft_start_enabled is not None:
        service.set_soft_start_enabled(soft_start_enabled)
    return service
