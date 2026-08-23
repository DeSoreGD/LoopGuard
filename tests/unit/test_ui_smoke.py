from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QFrame,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
)

from selfboss.core.models import (  # noqa: E402
    AppSettings,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.core.use_cases import SelfBossAppService  # noqa: E402
from selfboss.data.db import initialize_database  # noqa: E402
from selfboss.data.repositories import (  # noqa: E402
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.platform.browser_setup import (  # noqa: E402
    BrowserSetupActionResult,
    LOOPGUARD_CHROME_EXTENSION_ID,
    NativeHostRegistrationResult,
)
from selfboss.platform.hosts_blocker import HostsBlocker  # noqa: E402
import selfboss.ui.settings_page as settings_page_module  # noqa: E402
from selfboss.ui.components import (  # noqa: E402
    CardFrame,
    make_page_content,
    make_badge,
    make_muted_label,
    make_value_label,
)
from selfboss.ui.main_window import MainWindow  # noqa: E402
from selfboss.ui.dashboard_page import DASHBOARD_PRODUCT_MAX_WIDTH  # noqa: E402
from selfboss.ui.settings_page import BrowserSetupDialog, SettingsPage  # noqa: E402
from selfboss.ui.style import (  # noqa: E402
    CARD_PADDING,
    CARD_SPACING,
    PAGE_MARGIN,
    SETTINGS_MAX_WIDTH,
    SIDEBAR_WIDTH,
    TABLE_MAX_HEIGHT,
    TABLE_PAGE_MAX_WIDTH,
)
from selfboss.ui.theme import modern_common_stylesheet  # noqa: E402
from selfboss.ui.tray import TrayController  # noqa: E402


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


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        app_home=tmp_path,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "selfboss.db",
        log_dir=tmp_path / "logs",
        test_mode=True,
        recovery_mode=False,
        safe_mode=False,
    )


def test_shared_card_helpers_use_layout_tokens() -> None:
    app = QApplication.instance() or QApplication([])
    card = CardFrame("Card title", "Longer helper text")
    margins = card.card_layout.contentsMargins()
    theme_qss = modern_common_stylesheet()

    assert app is not None
    assert card.objectName() == "CardFrame"
    assert margins.left() == CARD_PADDING
    assert margins.top() == CARD_PADDING
    assert margins.right() == CARD_PADDING
    assert margins.bottom() == CARD_PADDING
    assert card.card_layout.spacing() == CARD_SPACING
    assert bool(card.card_layout.alignment() & Qt.AlignmentFlag.AlignTop)
    assert card.title_label is not None
    assert card.title_label.objectName() == "CardTitle"
    assert card.title_label.wordWrap() is True
    assert card.subtitle_label is not None
    assert card.subtitle_label.objectName() == "MutedText"
    assert card.subtitle_label.wordWrap() is True

    muted = make_muted_label("muted")
    value = make_value_label("value")
    badge = make_badge("badge")

    assert muted.objectName() == "MutedText"
    assert muted.wordWrap() is True
    assert value.objectName() == "ValueText"
    assert value.wordWrap() is True
    assert badge.objectName() == "Badge"
    assert badge.property("variant") == "neutral"
    assert badge.wordWrap() is True
    assert "SelfBoss Modern Theme v1" in theme_qss
    assert "QFrame#CardFrame" in theme_qss
    assert "QFrame#DashboardHeroCard" in theme_qss
    assert "QLabel#Badge" in theme_qss
    assert "QLabel#DashboardStatusPill" in theme_qss
    assert "QProgressBar#recreationBudgetProgress" in theme_qss
    assert "QFrame#appHeader" in theme_qss
    assert "QWidget#dashboardContentShell" in theme_qss
    assert "QWidget#dashboardScrollViewport" in theme_qss
    assert "QAbstractScrollArea::viewport" in theme_qss
    assert "QScrollBar:vertical" in theme_qss
    assert "QPushButton" in theme_qss

    shell, content, layout = make_page_content("exampleContent")
    assert shell.objectName() == "exampleContentShell"
    assert content.objectName() == "exampleContent"
    assert content.maximumWidth() == TABLE_PAGE_MAX_WIDTH
    assert content.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert layout.contentsMargins().left() == PAGE_MARGIN


def test_main_window_still_constructs_with_dashboard_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        window = MainWindow(
            settings=settings,
            service=service,
        )

        assert app is not None
        assert not hasattr(window, "tabs")
        assert window.findChildren(QTabWidget) == [window.rules_page.rules_editor_tabs]
        assert list(window.sidebar_buttons) == [
            "Dashboard",
            "Tasks",
            "Rules",
            "Settings",
        ]
        sidebar = window.findChild(QFrame, "sidebar")
        header = window.findChild(QFrame, "appHeader")
        assert sidebar is not None
        assert sidebar.width() == SIDEBAR_WIDTH
        assert header is not None
        assert window.header_title_label.text() == "Dashboard"
        assert window.header_subtitle_label.text() == (
            "Today, access, and recovery at a glance"
        )
        assert "SelfBoss Modern Theme v1" in window.styleSheet()
        assert window.content_stack.currentWidget() is window.dashboard_page
        assert window.sidebar_buttons["Dashboard"].isChecked()
        assert window.dashboard_page.test_mode_label.text() == "TEST MODE: ON"
        assert window.dashboard_page.scroll_area.objectName() == "dashboardScrollArea"
        assert window.dashboard_page.scroll_area.viewport().objectName() == (
            "dashboardScrollViewport"
        )
        assert window.armed_dry_run_scan_timer.objectName() == "armedDryRunScanTimer"
        assert window.armed_dry_run_scan_timer.interval() == 5000
        assert window.armed_dry_run_scan_timer.isActive() is True
        assert window.dashboard_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.tasks_page.scroll_area.objectName() == "tasksScrollArea"
        assert window.tasks_page.scroll_area.viewport().objectName() == (
            "tasksScrollViewport"
        )
        assert window.tasks_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.tasks_page.tasks_table.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.rules_page.scroll_area.viewport().objectName() == (
            "rulesScrollViewport"
        )
        assert window.settings_page.scroll_area.viewport().objectName() == (
            "settingsScrollViewport"
        )
        assert window.dashboard_page.content_widget.maximumWidth() == (
            DASHBOARD_PRODUCT_MAX_WIDTH
        )
        assert window.tasks_page.content_widget.maximumWidth() == TABLE_PAGE_MAX_WIDTH
        assert window.rules_page.content_widget.maximumWidth() == TABLE_PAGE_MAX_WIDTH
        assert window.settings_page.content_widget.maximumWidth() == SETTINGS_MAX_WIDTH
        assert window.tasks_page.tasks_table.maximumHeight() == TABLE_MAX_HEIGHT
        assert window.tasks_page.tasks_table.columnWidth(1) >= 90
        assert window.tasks_page.tasks_table.columnWidth(4) >= 130
        assert not window.rules_page.update_site_allow_from_button.isHidden()
        assert not window.rules_page.update_site_allow_from_button.isEnabled()
        assert window.rules_page.update_site_allow_from_button.toolTip() == (
            "Select a rule first."
        )

        def fail_scan():
            raise RuntimeError("scan failed")

        monkeypatch.setattr(service, "run_armed_dry_run_process_scan_cycle", fail_scan)
        window._run_enforcement_scan_cycle()


def test_main_window_enforcement_scan_refreshes_settings_page(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        window = MainWindow(settings=settings, service=service)
        window.sidebar_buttons["Settings"].click()

        assert app is not None
        assert hasattr(window.settings_page, "refresh")
        window._run_enforcement_scan_cycle()

        assert window.content_stack.currentWidget() is window.settings_page


def test_main_window_enforcement_scan_sends_high_notifications_once(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    class RecordingTray:
        def __init__(self) -> None:
            self.high_events: list[tuple[str, str]] = []

        def notify_high_event(self, title: str, message: str) -> None:
            self.high_events.append((title, message))

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection, now=current_now)
        service.day_state.add_reward_seconds(15 * 60)
        service.start_high_access(15, "watch one tutorial")
        window = MainWindow(settings=settings, service=service)
        tray = RecordingTray()
        window.tray_controller = tray

        now = start + timedelta(minutes=10)
        window._run_enforcement_scan_cycle()
        window._run_enforcement_scan_cycle()

        assert app is not None
        assert tray.high_events == [
            ("HIGH ending soon", "Recreation ends in 5m.")
        ]


def test_main_window_close_hides_to_tray_and_keeps_timer_active(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)

    class RecordingTray:
        def __init__(self) -> None:
            self.notifications = 0

        def notify_still_running(self) -> None:
            self.notifications += 1

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        window = MainWindow(settings=settings, service=service)
        tray = RecordingTray()
        window.tray_controller = tray

        window.show()
        app.processEvents()
        closed = window.close()
        app.processEvents()

        assert app is not None
        assert closed is False
        assert window.isHidden()
        assert window.close_to_tray is True
        assert window.armed_dry_run_scan_timer.isActive() is True
        assert tray.notifications == 1


def test_main_window_close_hides_during_active_enforcement_without_stopping_timer(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        service.set_enforcement_mode("armed_dry_run")
        window = MainWindow(settings=settings, service=service)

        window.show()
        app.processEvents()
        closed = window.close()
        app.processEvents()

        assert app is not None
        assert closed is False
        assert window.isHidden()
        assert window.armed_dry_run_scan_timer.isActive() is True


def test_main_window_sidebar_switches_pages(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        window = MainWindow(
            settings=settings,
            service=make_service(settings, connection),
        )

        expected_pages = {
            "Dashboard": window.dashboard_page,
            "Tasks": window.tasks_page,
            "Rules": window.rules_page,
            "Settings": window.settings_page,
        }

        assert app is not None
        for name, page in expected_pages.items():
            window.sidebar_buttons[name].click()
            assert window.content_stack.currentWidget() is page
            assert window.sidebar_buttons[name].isChecked()
            assert window.header_title_label.text() == name


def test_main_window_pages_survive_resize_cycle(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        service.create_task(
            title=("Long task title " * 8).strip(),
            kind=TaskKind.MAIN,
            allowed_url="https://example.test/" + "long-path/" * 8,
        )
        service.add_rule(
            "site",
            "really-long-video-site-name-with-many-segments.example-subdomain.test",
        )
        service.add_rule(
            "app",
            "really-long-distraction-process-name-for-testing.exe",
        )
        window = MainWindow(settings=settings, service=service)
        window.resize(900, 680)
        window.show()
        app.processEvents()

        for width, height in ((620, 520), (760, 420), (1000, 680)):
            window.resize(width, height)
            app.processEvents()
            for name in ("Dashboard", "Tasks", "Rules", "Settings"):
                window.sidebar_buttons[name].click()
                app.processEvents()

        window.showFullScreen()
        app.processEvents()
        window.showNormal()
        window.resize(760, 520)
        app.processEvents()

        assert app is not None
        assert window.dashboard_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.tasks_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.rules_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.settings_page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )


def test_sidebar_navigation_refreshes_rules_after_surrender(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection, now=current_now)
        service.add_rule("site", "example.com", allow_from_level="high")
        task = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, task.id)
        window = MainWindow(settings=settings, service=service)

        assert app is not None
        assert "example.com" in window.rules_page.preview_blocked_sites_label.text()

        now = start + timedelta(hours=6)
        service.activate_surrender()
        window.sidebar_buttons["Rules"].click()

        assert window.rules_page.preview_blocked_sites_label.text() == (
            "Blocked sites now: None"
        )
        assert "example.com" in window.rules_page.preview_allowed_sites_label.text()
        assert "Surrender active" in window.rules_page.preview_label.text()


def test_sidebar_navigation_refreshes_dashboard_after_bad_day(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        window = MainWindow(settings=settings, service=service)

        assert app is not None
        assert window.dashboard_page.access_mode_metric_label.text() == "Focus"
        assert window.dashboard_page.access_level_label.text() == "LOW"

        window.sidebar_buttons["Rules"].click()
        service.activate_bad_day_mode()
        window.sidebar_buttons["Dashboard"].click()

        assert window.dashboard_page.access_mode_metric_label.text() == "Utility"
        assert window.dashboard_page.access_level_label.text() == "MEDIUM"
        assert window.dashboard_page.high_status_label.text() == (
            "Bad Day Mode baseline."
        )
        assert window.dashboard_page.bad_day_button.text() == "Bad Day active"


def test_settings_page_is_read_only_safety_status(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection, soft_start_enabled=None)
        page = SettingsPage(settings, service=service)
        buttons = page.findChildren(QPushButton)

        assert app is not None
        assert page.scroll_area.objectName() == "settingsScrollArea"
        assert page.scroll_area.widgetResizable() is True
        assert page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.findChildren(QScrollArea) == [page.scroll_area]
        assert page.runtime_card_title_label.text() == "Runtime and Safety"
        assert page.data_card_title_label.text() == "Diagnostics"
        assert page.configuration_backup_card_title_label.text() == (
            "Configuration Backup"
        )
        assert page.browser_setup_card_title_label.text() == "Browser Setup"
        assert page.browser_setup_refresh_timer.objectName() == (
            "browserSetupRefreshTimer"
        )
        assert page.browser_setup_refresh_timer.interval() == 3000
        assert page.browser_setup_refresh_timer.isActive() is False
        assert page.advanced_diagnostics_card_title_label.text() == (
            "Advanced Diagnostics"
        )
        assert page.advanced_diagnostics_toggle_button.text() == (
            "Show advanced diagnostics"
        )
        assert page.advanced_diagnostics_panel.isHidden() is True
        assert page.readiness_card_title_label.text() == "Enforcement Readiness"
        assert page.personal_readiness_card_title_label.text() == (
            "Personal-use readiness"
        )
        assert page.personal_trial_qa_card_title_label.text() == "Personal Trial QA"
        page.advanced_diagnostics_toggle_button.click()
        assert page.advanced_diagnostics_panel.isHidden() is False
        assert page.advanced_diagnostics_toggle_button.text() == (
            "Hide advanced diagnostics"
        )
        assert page.recovery_card_title_label.text() == "Emergency recovery"
        assert page.surrender_strictness_card_title_label.text() == (
            "Surrender Strictness"
        )
        assert page.soft_start_card_title_label.text() == "Soft Start"
        assert page.runtime_card_title_label.objectName() == "CardTitle"
        assert page.findChildren(CardFrame)
        assert page.test_mode_label.text() == "Test Mode: ON / Locked"
        assert page.enforcement_mode_label.objectName() == (
            "settingsEnforcementModeLabel"
        )
        assert page.enforcement_mode_label.text() == "Current mode: Preview Only"
        assert page.enforcement_mode_input.objectName() == (
            "settingsEnforcementModeCombo"
        )
        assert page.enforcement_mode_input.count() == 5
        assert page.enforcement_mode_input.itemData(0) == "preview_only"
        assert page.enforcement_mode_input.itemData(1) == "armed_dry_run"
        assert page.enforcement_mode_input.itemData(2) == "real_process_blocking"
        assert page.enforcement_mode_input.itemData(3) == "real_hosts_blocking"
        assert page.enforcement_mode_input.itemData(4) == "full_enforcement"
        assert page.next_enforcement_step_label.objectName() == (
            "settingsNextEnforcementStepLabel"
        )
        assert page.next_enforcement_step_label.text() == (
            "Next available mode: Armed Dry Run. Real Process Blocking, "
            "Real Hosts Blocking, and Full Enforcement are available."
        )
        assert page.real_enforcement_label.text() == "Full Enforcement: Ready"
        assert page.data_path_label.text() == "App data folder: Local LoopGuard data"
        assert page.data_path_label.toolTip() == str(settings.data_dir)
        assert page.database_path_label.text() == f"Database: {settings.db_path.name}"
        assert page.database_path_label.toolTip() == str(settings.db_path)
        assert page.diagnostics_hint_label.text() == "Hover to see full path."
        assert page.open_app_data_folder_button.text() == "Open app data folder"
        assert page.open_database_folder_button.text() == "Open database folder"
        assert page.open_recovery_folder_button.text() == "Open recovery folder"
        assert page.export_configuration_button.text() == "Export configuration"
        assert page.import_configuration_button.text() == "Import configuration"
        assert page.export_configuration_button.isEnabled() is True
        assert page.import_configuration_button.isEnabled() is True
        assert page.configuration_backup_helper_label.text() == (
            "Exports local rules and settings only. It does not export "
            "browsing history or logs."
        )
        combined_configuration_backup = " ".join(
            (
                page.configuration_backup_card_title_label.text(),
                page.configuration_backup_helper_label.text(),
                page.export_configuration_button.text(),
                page.import_configuration_button.text(),
                page.configuration_backup_status_label.text(),
            )
        ).lower()
        assert "cloud" not in combined_configuration_backup
        assert "sync" not in combined_configuration_backup
        assert "analytics" not in combined_configuration_backup
        assert "Chrome: Browser disconnected" in page.browser_setup_status_label.text()
        assert "Incognito: Unknown" in page.browser_setup_status_label.text()
        assert "Native host: Not registered" in page.browser_setup_status_label.text()
        assert "DNR: Unknown" in page.browser_setup_status_label.text()
        assert "YouTube SPA detector: Unknown" in (
            page.browser_setup_status_label.text()
        )
        assert page.browser_setup_folder_label.text() == (
            "Extension folder: browser_extension/chrome_mv3"
        )
        assert page.browser_setup_folder_label.toolTip().endswith(
            "browser_extension\\chrome_mv3"
        )
        assert page.browser_setup_instruction_label.text() == (
            "Chrome setup is required for precise website blocking."
        )
        assert LOOPGUARD_CHROME_EXTENSION_ID not in (
            page.browser_setup_instruction_label.text()
        )
        assert page.browser_setup_extension_id_label.text() == "Chrome extension ID"
        assert page.browser_setup_extension_id_input.placeholderText() == (
            "Optional custom 32-character extension ID"
        )
        assert "Advanced/custom repair only" in (
            page.browser_setup_extension_id_help_label.text()
        )
        assert "Normal setup does not require copying an extension ID" in (
            page.browser_setup_extension_id_help_label.text()
        )
        assert page.browser_setup_edge_label.text() == "Edge: planned."
        assert page.browser_setup_next_action_label.text() == (
            "Next action: Open Set up Chrome, load the bundled extension "
            "manually, then refresh status."
        )
        assert page.setup_chrome_button.text() == "Set up Chrome"
        assert page.refresh_browser_setup_button.text() == "Refresh status"
        assert page.open_chrome_extensions_button.text() == (
            "Open Chrome extensions page"
        )
        assert page.open_extension_folder_button.text() == "Open extension folder"
        assert page.skip_browser_setup_intro_button.text() == "Skip for now"
        assert page.dismiss_browser_setup_intro_button.text() == "Don't show again"
        assert page.repair_native_host_button.text() == "Repair connection"
        assert page.manual_browser_setup_toggle_button.text() == "Show manual setup"
        assert page.manual_browser_setup_panel.isHidden() is True
        assert page.unregister_native_host_button.text() == "Unregister native host"
        assert page.repair_native_host_button.isEnabled() is True
        assert page.unregister_native_host_button.isEnabled() is True
        assert "Repair the local Chrome connection" in (
            page.repair_native_host_button.toolTip()
        )
        page.browser_setup_extension_id_input.setText("not-valid")
        assert page.repair_native_host_button.isEnabled() is True
        page.manual_browser_setup_toggle_button.click()
        assert page.manual_browser_setup_panel.isHidden() is False
        assert page.repair_native_host_button.isEnabled() is False
        page.browser_setup_extension_id_input.setText(
            "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP"
        )
        assert page.repair_native_host_button.isEnabled() is True
        combined_browser_setup = " ".join(
            (
                page.browser_setup_status_label.text(),
                page.browser_setup_folder_label.text(),
                page.browser_setup_instruction_label.text(),
                page.browser_setup_extension_id_label.text(),
                page.browser_setup_extension_id_input.text(),
                page.browser_setup_extension_id_help_label.text(),
                page.browser_setup_edge_label.text(),
                page.browser_setup_next_action_label.text(),
                page.browser_setup_status_note_label.text(),
            )
        ).lower()
        assert "automatic install" not in combined_browser_setup
        assert "silent install" not in combined_browser_setup
        assert "force install" not in combined_browser_setup
        assert "chrome web store" not in combined_browser_setup
        assert "developer account" not in combined_browser_setup
        assert "paid registration" not in combined_browser_setup
        assert "future install" not in combined_browser_setup
        assert "registered successfully" not in combined_browser_setup
        assert "Real Process Blocking: Ready" in page.background_monitor_label.text()
        assert "Real Hosts Blocking: Ready" in page.browser_connector_label.text()
        assert "Managed section: Ready" in page.hosts_readiness_detail_label.text()
        assert "Backup/Rollback: Ready" in page.hosts_readiness_detail_label.text()
        assert "Recovery removal: Ready" in page.hosts_readiness_detail_label.text()
        assert "Admin required for real hosts writes" in (
            page.hosts_readiness_detail_label.text()
        )
        assert "DNS cache flush" in page.hosts_readiness_detail_label.text()
        assert "bare domains also include www" in page.hosts_readiness_detail_label.text()
        assert "URL paths are not blocked" in page.hosts_readiness_detail_label.text()
        assert "ipconfig /flushdns" in page.hosts_readiness_detail_label.text()
        assert "Manual hosts test" in page.hosts_readiness_detail_label.text()
        assert "ping domain.com" in page.hosts_readiness_detail_label.text()
        assert "expect 127.0.0.1" in page.hosts_readiness_detail_label.text()
        assert "Incognito" in page.hosts_readiness_detail_label.text()
        assert "Browser HIGH safety: Partial, not Trusted" in (
            page.hosts_readiness_detail_label.text()
        )
        assert "extension-connected browser sessions" in (
            page.hosts_readiness_detail_label.text()
        )
        assert "Other browsers are not browser-controlled yet" in (
            page.hosts_readiness_detail_label.text()
        )
        assert "no telemetry, cloud, network service, or localhost HTTP API" in (
            page.hosts_readiness_detail_label.text()
        )
        assert page.browser_integration_status_label.objectName() == (
            "settingsBrowserIntegrationStatusLabel"
        )
        assert "Extension: Disconnected" in (
            page.browser_integration_status_label.text()
        )
        assert "Native Messaging: Not connected" in (
            page.browser_integration_status_label.text()
        )
        assert "Last heartbeat: none" in page.browser_integration_status_label.text()
        assert "Incognito: Unknown" in page.browser_integration_status_label.text()
        assert "DNR: Unknown" in page.browser_integration_status_label.text()
        assert "YouTube SPA detector: Unknown" in (
            page.browser_integration_status_label.text()
        )
        assert "Next action: Register the native host or reload the browser extension." in (
            page.browser_integration_status_label.text()
        )
        assert "Browser HIGH safety: Not Ready" in (
            page.browser_integration_status_label.text()
        )
        assert "Website HIGH hosts release: Not needed" in (
            page.browser_integration_status_label.text()
        )
        assert "Other browsers: Not needed" in (
            page.browser_integration_status_label.text()
        )
        assert "Trusted" not in page.browser_integration_status_label.text()
        assert "Missing recovery path to disable persisted real enforcement modes." in (
            page.blocking_adapters_label.text()
        )
        assert page.personal_readiness_verdict_label.objectName() == (
            "settingsPersonalReadinessVerdictLabel"
        )
        assert "Ready for personal trial: Not ready" in (
            page.personal_readiness_verdict_label.text()
        )
        assert "Desktop blocking: Enforcement mode: Preview Only" in (
            page.personal_readiness_desktop_label.text()
        )
        assert "Process blocking: Ready" in (
            page.personal_readiness_desktop_label.text()
        )
        assert "Hosts blocking: Ready" in (
            page.personal_readiness_desktop_label.text()
        )
        assert "Browser extension: Disconnected" in (
            page.personal_readiness_browser_label.text()
        )
        assert "Manual QA: 0/9 verified" in (
            page.personal_readiness_manual_qa_label.text()
        )
        assert page.personal_trial_qa_verdict_label.text() == (
            "Personal Trial QA: Not ready - 0/9 verified"
        )
        assert page.personal_trial_qa_note_label.text() == (
            "Reset and re-run after setup/enforcement changes."
        )
        assert page.reset_personal_trial_qa_button.text() == "Reset QA checklist"
        assert [
            checkbox.text()
            for checkbox in page.personal_trial_qa_checkboxes.values()
        ] == [
            "Chrome extension loaded",
            "Native host repair/register verified",
            "Browser heartbeat connected",
            "Incognito allowed",
            "YouTube SPA detector seen",
            "Shorts/path rule tested",
            "Process blocking tested with disposable app rule",
            "Hosts blocking tested with disposable domain rule",
            "Recovery/Safe Mode location understood",
        ]
        assert all(
            not checkbox.isChecked()
            for checkbox in page.personal_trial_qa_checkboxes.values()
        )
        combined_personal_readiness = " ".join(
            (
                page.personal_readiness_verdict_label.text(),
                page.personal_readiness_desktop_label.text(),
                page.personal_readiness_browser_label.text(),
                page.personal_readiness_recovery_label.text(),
                page.personal_readiness_manual_qa_label.text(),
                page.personal_trial_qa_verdict_label.text(),
                page.personal_trial_qa_note_label.text(),
            )
        ).lower()
        assert "production ready" not in combined_personal_readiness
        assert "tamper-proof" not in combined_personal_readiness
        assert "https://" not in combined_personal_readiness
        assert "?v=" not in combined_personal_readiness
        assert "telemetry" not in combined_personal_readiness
        assert "cloud" not in combined_personal_readiness
        assert "network" not in combined_personal_readiness
        assert page.armed_dry_run_note_label.objectName() == (
            "settingsArmedDryRunNoteLabel"
        )
        assert "logs would-block decisions without blocking" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Real hosts blocking writes only LoopGuard" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Full Enforcement combines explicit app process blocking" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Full Enforcement manual QA" in page.armed_dry_run_note_label.text()
        assert "notepad.exe" in page.armed_dry_run_note_label.text()
        assert "ping reddit.com and www.reddit.com" in (
            page.armed_dry_run_note_label.text()
        )
        assert "switch to Preview Only to clear LoopGuard hosts markers" in (
            page.armed_dry_run_note_label.text()
        )
        assert "bare domains also include www" in (
            page.armed_dry_run_note_label.text()
        )
        assert "LoopGuard does not flush DNS automatically" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Manual hosts test" in page.armed_dry_run_note_label.text()
        assert "ping domain.com" in page.armed_dry_run_note_label.text()
        assert "expect 127.0.0.1" in page.armed_dry_run_note_label.text()
        assert "Incognito" in page.armed_dry_run_note_label.text()
        assert "Browser HIGH safety: Partial, not Trusted" in (
            page.armed_dry_run_note_label.text()
        )
        assert "extension-connected browser sessions" in (
            page.armed_dry_run_note_label.text()
        )
        assert "other browsers are not browser-controlled yet" in (
            page.armed_dry_run_note_label.text()
        )
        assert "no telemetry, cloud, network service, or localhost HTTP API" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Firewall blocking is not implemented" in (
            page.armed_dry_run_note_label.text()
        )
        assert "DNS cache flush" in page.armed_dry_run_note_label.text()
        assert "Safe Mode and Recovery Mode disable enforcement" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Protected system and LoopGuard processes are never closed" in (
            page.armed_dry_run_note_label.text()
        )
        assert "Test Mode is locked on" in page.safety_note_label.text()
        assert page.safe_actions_label.text() == (
            "Use only if blocking breaks or LoopGuard cannot recover normally."
        )
        assert page.open_recovery_folder_button.isEnabled() is True
        assert page.surrender_strictness_input.count() == 3
        assert page.surrender_strictness_input.currentData() == "medium"
        assert "LOW = surrender after 3h" in page.surrender_strictness_help_label.text()
        assert page.soft_start_enabled_input.text() == "Enable Soft Start"
        assert page.soft_start_enabled_input.isChecked() is True
        assert page.soft_start_duration_input.minimum() == 0
        assert page.soft_start_duration_input.maximum() == 60
        assert page.soft_start_duration_input.value() == 15
        assert page.soft_start_duration_input.suffix() == ""
        assert page.soft_start_minutes_label.text() == "minutes"
        assert page.soft_start_duration_input.buttonSymbols() == (
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        assert page.soft_start_duration_input.keyboardTracking() is False
        assert "rewards can be earned" in page.soft_start_help_label.text()
        assert page.daily_recreation_cap_card_title_label.text() == (
            "Daily Recreation Cap"
        )
        assert page.daily_recreation_cap_label.text() == "Daily Recreation cap"
        assert page.daily_recreation_cap_input.minimum() == 15
        assert page.daily_recreation_cap_input.maximum() == 300
        assert page.daily_recreation_cap_input.value() == 90
        assert page.daily_recreation_cap_minutes_label.text() == "minutes"
        assert page.daily_recreation_cap_input.buttonSymbols() == (
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        assert page.daily_recreation_cap_input.keyboardTracking() is False
        assert "Refunded time does not restore this cap" in (
            page.daily_recreation_cap_help_label.text()
        )

        page.surrender_strictness_input.setCurrentIndex(
            page.surrender_strictness_input.findData("high")
        )
        page.enforcement_mode_input.setCurrentIndex(
            page.enforcement_mode_input.findData("armed_dry_run")
        )
        page.soft_start_enabled_input.setChecked(False)
        page.soft_start_duration_input.setValue(20)
        page.daily_recreation_cap_input.setValue(120)

        assert service.get_surrender_strictness() == "high"
        assert service.get_enforcement_mode().value == "armed_dry_run"
        assert service.get_soft_start_enabled() is False
        assert service.get_soft_start_duration_minutes() == 20
        assert service.get_daily_recreation_cap_minutes() == 120
        assert {button.text() for button in buttons} == {
            "Open app data folder",
            "Open database folder",
            "Open recovery folder",
            "Export configuration",
            "Import configuration",
            "Hide advanced diagnostics",
            "Set up Chrome",
            "Open Chrome extensions page",
            "Open extension folder",
                "Refresh status",
                "Skip for now",
                "Don't show again",
                "Repair connection",
                "Unregister native host",
                "Hide manual setup",
                "Reset QA checklist",
            }
        assert [
            line_edit
            for line_edit in page.findChildren(QLineEdit)
            if line_edit.objectName() == "settingsBrowserSetupExtensionIdInput"
        ] == [page.browser_setup_extension_id_input]
        assert page.soft_start_enabled_input in page.findChildren(QCheckBox)
        for checkbox in page.personal_trial_qa_checkboxes.values():
            assert checkbox in page.findChildren(QCheckBox)
        assert set(page.findChildren(QSpinBox)) == {
            page.soft_start_duration_input,
            page.daily_recreation_cap_input,
        }
        _assert_no_unsafe_settings_controls(page)


def test_settings_page_hides_dev_diagnostics_in_production(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(settings, service=service, production_mode=True)

        assert page.runtime_card.isHidden()
        assert page.advanced_diagnostics_card.isHidden()
        assert page.readiness_card.isHidden()
        assert page.personal_readiness_card.isHidden()
        assert page.personal_trial_qa_card.isHidden()
        assert page.browser_setup_card_title_label.text() == "Chrome setup"
        assert not page.setup_chrome_button.isHidden()


def test_settings_production_browser_setup_intro_can_be_dismissed(
    tmp_path: Path,
) -> None:
    QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(settings, service=service, production_mode=True)

        assert page.browser_setup_intro_panel.isHidden() is False
        intro_text = " ".join(
            (
                page.browser_setup_intro_label.text(),
                page.browser_setup_intro_future_label.text(),
                page.open_chrome_extensions_button.text(),
                page.open_extension_folder_button.text(),
                page.refresh_browser_setup_button.text(),
                page.repair_native_host_button.text(),
            )
        ).lower()
        assert "bundled extension" in intro_text
        assert "chrome is not modified silently" in intro_text
        assert "repair connection only" in intro_text
        assert "chrome web store" not in intro_text
        assert "developer account" not in intro_text
        assert "paid registration" not in intro_text
        assert "future install" not in intro_text
        assert "silent install" not in intro_text
        assert "force install" not in intro_text

        page.skip_browser_setup_intro_button.click()

        assert page.browser_setup_intro_panel.isHidden() is True
        assert service.has_seen_browser_setup_intro() is False

        reloaded = SettingsPage(settings, service=service, production_mode=True)
        assert reloaded.browser_setup_intro_panel.isHidden() is False

        reloaded.dismiss_browser_setup_intro_button.click()

        assert service.has_seen_browser_setup_intro() is True
        dismissed = SettingsPage(settings, service=service, production_mode=True)
        assert dismissed.browser_setup_intro_panel.isHidden() is True


def test_loopguard_inno_spec_has_optional_native_host_task_only() -> None:
    spec_text = (Path.cwd() / "packaging" / "installer" / "LoopGuard.iss").read_text(
        encoding="utf-8"
    )
    lower_spec = spec_text.lower()

    assert "Prepare Chrome connection for LoopGuard" in spec_text
    assert "Register Chrome native host for LoopGuard" not in spec_text
    assert "LoopGuardNativeHost.exe" in spec_text
    assert "com.selfboss.native_host" in spec_text
    assert LOOPGUARD_CHROME_EXTENSION_ID in spec_text
    assert "Software\\Google\\Chrome\\NativeMessagingHosts" in spec_text
    assert "hkcu" in lower_spec
    assert "extensioninstallforcelist" not in lower_spec
    assert "software\\policies" not in lower_spec
    assert "force-install" not in lower_spec
    assert "chrome policy" not in lower_spec


def test_main_window_production_browser_setup_prompt_paths(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        window = MainWindow(
            settings=settings,
            service=service,
            production_mode=True,
        )
        window.armed_dry_run_scan_timer.stop()
        wizard_calls: list[str] = []
        window.settings_page._show_browser_setup_dialog = (  # type: ignore[method-assign]
            lambda: wizard_calls.append("wizard")
        )

        window._show_browser_setup_intro_prompt = lambda: "setup"  # type: ignore[method-assign]
        window._maybe_show_browser_setup_intro_prompt()

        assert wizard_calls == ["wizard"]
        assert window.content_stack.currentWidget() is window.settings_page
        assert "Browser setup guidance is ready" not in (
            window.settings_page.browser_setup_status_note_label.text()
        )
        assert "Use Set up Chrome" in (
            window.settings_page.browser_setup_status_note_label.text()
        )
        assert service.has_seen_browser_setup_intro() is False

        window._show_browser_setup_intro_prompt = lambda: "skip"  # type: ignore[method-assign]
        window._maybe_show_browser_setup_intro_prompt()
        assert service.has_seen_browser_setup_intro() is False

        window._show_browser_setup_intro_prompt = lambda: "dismiss"  # type: ignore[method-assign]
        window._maybe_show_browser_setup_intro_prompt()
        assert service.has_seen_browser_setup_intro() is True


def test_settings_soft_start_controls_lock_after_start_day(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        main = service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = SettingsPage(settings, service=service)

        assert app is not None
        assert page.surrender_strictness_input.isEnabled() is False
        assert page.surrender_strictness_input.currentData() == "medium"
        assert page.surrender_strictness_input.toolTip() == (
            "Locked after Start Day. Change it before tomorrow's Start Day."
        )
        assert page.surrender_strictness_label.toolTip() == (
            "Locked after Start Day. Change it before tomorrow's Start Day."
        )
        assert page.surrender_strictness_help_label.text() == (
            "Locked after Start Day. Change it before tomorrow's Start Day."
        )
        assert page.soft_start_enabled_input.isEnabled() is False
        assert page.soft_start_duration_input.isEnabled() is False
        assert page.soft_start_minutes_label.isEnabled() is False
        assert page.soft_start_duration_input.toolTip() == (
            "Soft Start can only be changed before Start Day. "
            "Changes apply to the next day/session."
        )
        assert "Soft Start can only be changed before Start Day" in (
            page.soft_start_help_label.text()
        )
        assert page.daily_recreation_cap_input.isEnabled() is False
        assert page.daily_recreation_cap_minutes_label.isEnabled() is False
        assert page.daily_recreation_cap_input.toolTip() == (
            "Locked after Start Day. Change it before tomorrow's Start Day."
        )
        assert page.daily_recreation_cap_help_label.text() == (
            "Locked after Start Day. Change it before tomorrow's Start Day."
        )
        assert page.export_configuration_button.isEnabled() is False
        assert page.import_configuration_button.isEnabled() is False
        assert page.configuration_backup_status_label.text() == (
            "Available before Start Day or after closing the day."
        )
        assert page.open_recovery_folder_button.isEnabled() is True
        assert page.safe_actions_label.text() == (
            "Emergency scripts remain available in the app folder."
        )

        _complete_task_after_claim_delay(service, main.id)
        service.end_day()
        page.refresh()

        assert page.surrender_strictness_input.isEnabled() is True
        assert page.surrender_strictness_input.toolTip() == ""
        assert "LOW = surrender after 3h" in page.surrender_strictness_help_label.text()
        assert page.daily_recreation_cap_input.isEnabled() is True
        assert page.daily_recreation_cap_input.toolTip() == ""
        assert "Refunded time does not restore this cap" in (
            page.daily_recreation_cap_help_label.text()
        )
        assert page.export_configuration_button.isEnabled() is True
        assert page.import_configuration_button.isEnabled() is True
        assert page.safe_actions_label.text() == (
            "Use only if blocking breaks or LoopGuard cannot recover normally."
        )


def test_settings_personal_trial_qa_persists_and_resets(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(settings, service=service)

        page.personal_trial_qa_checkboxes["chrome_extension_loaded"].setChecked(True)
        page.refresh()

        assert app is not None
        assert page.personal_trial_qa_checkboxes["chrome_extension_loaded"].isChecked()
        assert page.personal_trial_qa_verdict_label.text() == (
            "Personal Trial QA: Partial - 1/9 verified"
        )
        assert "Manual QA: 1/9 verified" in (
            page.personal_readiness_manual_qa_label.text()
        )

        reloaded = SettingsPage(settings, service=make_service(settings, connection))
        assert reloaded.personal_trial_qa_checkboxes[
            "chrome_extension_loaded"
        ].isChecked()

        reloaded.reset_personal_trial_qa_button.click()

        assert all(
            not checkbox.isChecked()
            for checkbox in reloaded.personal_trial_qa_checkboxes.values()
        )
        assert reloaded.personal_trial_qa_verdict_label.text() == (
            "Personal Trial QA: Not ready - 0/9 verified"
        )


def test_settings_shows_populated_browser_diagnostics(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    now = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    heartbeat_path = settings.data_dir / "browser_heartbeat.json"
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
                "dnr_session_rule_count": 3,
                "dnr_last_update_status": "active",
                "youtube_spa_content_script_seen": True,
                "last_heartbeat_at": (now - timedelta(seconds=30)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection, now=lambda: now)
        page = SettingsPage(settings, service=service)
        text = page.browser_integration_status_label.text()

        assert app is not None
        assert "Extension: Connected" in text
        assert "Native Messaging: Connected" in text
        assert "Incognito: Allowed" in text
        assert "DNR: Active 3 rules" in text
        assert "YouTube SPA detector: Seen" in text
        assert "Last heartbeat: 30s ago" in text
        assert "Next action: Browser integration looks connected." in text
        assert "https://" not in text
        assert "domains" not in text.lower()
        assert "Chrome: Browser ready" in page.browser_setup_status_label.text()
        assert "Incognito: Allowed" in page.browser_setup_status_label.text()
        assert "Native host: Registered" in page.browser_setup_status_label.text()
        assert "DNR: Active 3 rules" in page.browser_setup_status_label.text()
        assert "YouTube SPA detector: Seen" in page.browser_setup_status_label.text()
        assert page.browser_setup_next_action_label.text() == (
            "Next action: Run manual QA."
        )
        assert "Browser blocking: Browser extension: Connected" in (
            page.personal_readiness_browser_label.text()
        )
        assert "Incognito: Allowed" in page.personal_readiness_browser_label.text()
        assert "DNR: Active 3 rules" in page.personal_readiness_browser_label.text()
        assert "YouTube SPA detector: Seen" in (
            page.personal_readiness_browser_label.text()
        )


def test_browser_setup_dialog_shows_steps_and_uses_injected_openers(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    extension_folder = tmp_path / "browser_extension" / "chrome_mv3"
    extension_folder.mkdir(parents=True)
    calls: list[str] = []

    def open_chrome() -> BrowserSetupActionResult:
        calls.append("chrome")
        return BrowserSetupActionResult(
            ok=False,
            reason="Open Chrome and paste: chrome://extensions",
            copy_text="chrome://extensions",
        )

    def open_folder(path: Path) -> BrowserSetupActionResult:
        calls.append(f"folder:{path}")
        return BrowserSetupActionResult(
            ok=True,
            reason=f"Opened extension folder: {path}",
        )

    dialog = BrowserSetupDialog(
        extension_folder=extension_folder,
        open_chrome_extensions_page_action=open_chrome,
        open_extension_folder_action=open_folder,
        repair_native_host_action=lambda: calls.append("repair") or "Repair opened",
        refresh_status_action=lambda: calls.append("refresh") or "Status refreshed",
    )

    assert app is not None
    assert dialog.windowTitle() == "Set up Chrome"
    assert dialog.windowIcon().isNull() is False
    assert str(extension_folder) in dialog.folder_label.text()
    assert "Chrome setup is required" in dialog.intro_label.text()
    for step in (
        "1. Open Chrome extensions",
        "2. Open the bundled extension folder",
        "3. In Chrome, enable Developer mode",
        "4. Return to LoopGuard and refresh status",
        "5. Use Repair connection only",
        "enable Developer mode",
        "Load unpacked",
        "Repair connection",
    ):
        assert step in dialog.steps_label.text()
    assert "extension ID" not in dialog.steps_label.text()
    assert LOOPGUARD_CHROME_EXTENSION_ID not in dialog.steps_label.text()

    dialog.open_extension_folder_button.click()
    assert dialog.status_label.text() == f"Opened extension folder: {extension_folder}"
    dialog.copy_extension_folder_button.click()
    assert dialog.status_label.text() == "Copied extension folder path."
    assert QApplication.clipboard().text() == str(extension_folder)
    dialog.open_chrome_extensions_button.click()

    dialog.repair_native_host_button.click()
    assert dialog.status_label.text() == "Repair opened"
    dialog.refresh_status_button.click()
    assert dialog.status_label.text() == "Status refreshed"

    assert calls == [
        "folder:" + str(extension_folder),
        "chrome",
        "repair",
        "refresh",
    ]
    assert QApplication.clipboard().text() == "chrome://extensions"


def test_settings_repairs_and_unregisters_native_host_only_after_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    registrar = RecordingBrowserSetupRegistrar()
    questions: list[str] = []

    def accept_registration(_parent, _title, message):
        questions.append(message)
        return settings_page_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(
        settings_page_module.QMessageBox,
        "question",
        accept_registration,
    )

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(
            settings,
            service=service,
            browser_setup_registrar=registrar,
        )

        page.refresh_browser_setup_button.click()
        assert registrar.repair_calls == []
        assert registrar.unregister_calls == []

        page.browser_setup_extension_id_input.setText("not-valid")
        assert page.repair_native_host_button.isEnabled() is True
        page.repair_native_host_button.click()
        assert registrar.repair_calls == [
            ("chrome", LOOPGUARD_CHROME_EXTENSION_ID)
        ]
        assert registrar.unregister_calls == []

        page.manual_browser_setup_toggle_button.click()
        assert page.repair_native_host_button.isEnabled() is False
        page.browser_setup_extension_id_input.setText(
            "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP"
        )
        assert page.repair_native_host_button.isEnabled() is True
        page.repair_native_host_button.click()

        assert page.unregister_native_host_button.isEnabled() is True
        page.unregister_native_host_button.click()

        assert app is not None
        assert questions == [
            "Repair LoopGuard's Chrome connection under HKCU?",
            "Repair LoopGuard's Chrome connection under HKCU?",
            "Remove LoopGuard native host registration for Chrome from HKCU?",
        ]
        assert registrar.repair_calls == [
            ("chrome", LOOPGUARD_CHROME_EXTENSION_ID),
            ("chrome", "abcdefghijklmnopabcdefghijklmnop")
        ]
        assert registrar.unregister_calls == ["chrome"]
        assert page.browser_setup_status_note_label.text() == (
            "Native host unregistered. Browser integration will be disconnected "
            "until registered again."
        )


def test_settings_cancelled_native_host_actions_do_not_call_registrar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    registrar = RecordingBrowserSetupRegistrar()

    monkeypatch.setattr(
        settings_page_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: settings_page_module.QMessageBox.StandardButton.No,
    )

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(
            settings,
            service=service,
            browser_setup_registrar=registrar,
        )
        page.browser_setup_extension_id_input.setText(
            "abcdefghijklmnopabcdefghijklmnop"
        )
        page.repair_native_host_button.click()
        assert page.browser_setup_status_note_label.text() == (
            "Native host repair cancelled."
        )
        page.unregister_native_host_button.click()

        assert app is not None
        assert registrar.repair_calls == []
        assert registrar.unregister_calls == []
        assert page.browser_setup_status_note_label.text() == (
            "Native host unregister cancelled."
        )


def test_settings_diagnostic_folder_buttons_open_folders(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    opened_paths: list[str] = []

    def fake_open_url(url) -> bool:
        opened_paths.append(url.toLocalFile())
        return True

    monkeypatch.setattr(
        settings_page_module.QDesktopServices,
        "openUrl",
        fake_open_url,
    )

    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        page = SettingsPage(settings, service=service)

        page.open_app_data_folder_button.click()
        page.open_database_folder_button.click()
        page.open_recovery_folder_button.click()

        assert app is not None
        assert [Path(path) for path in opened_paths] == [
            settings.data_dir,
            settings.db_path.parent,
            Path.cwd() / "scripts",
        ]
        assert page.diagnostics_status_label.text() == f"Opened: {settings.db_path.parent}"
        assert page.recovery_status_label.text() == f"Opened: {Path.cwd() / 'scripts'}"


def test_tray_controller_has_required_actions_and_reopens_window(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        window = MainWindow(
            settings=settings,
            service=make_service(settings, connection),
        )
        tray = TrayController(window=window, settings=settings, app=app)

        window.hide()
        tray.show_window()

        assert tray.action_texts() == [
            "Show LoopGuard",
            "Run in Test Mode",
            "Recovery Status",
            "Exit LoopGuard...",
        ]
        assert window.isVisible()

        production_window = MainWindow(
            settings=settings,
            service=make_service(settings, connection),
            production_mode=True,
        )
        production_tray = TrayController(
            window=production_window,
            settings=settings,
            app=app,
        )

        assert production_tray.action_texts() == [
            "Show LoopGuard",
            "Exit LoopGuard...",
        ]


def test_tray_exit_prompts_when_monitoring_state_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    hosts_path = tmp_path / "hosts"
    hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    with initialize_database(settings.db_path) as connection:
        service = make_service(
            settings,
            connection,
            hosts_blocker=HostsBlocker(hosts_path=hosts_path),
        )
        service.add_rule("site", "reddit.com", allow_from_level="high")
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.set_enforcement_mode("full_enforcement")
        window = MainWindow(settings=settings, service=service)
        tray = TrayController(window=window, settings=settings, app=app)
        reviews = []
        calls = {"confirm": 0, "exit": 0}

        def cancel_exit() -> str:
            calls["confirm"] += 1
            return "cancel"

        def show_selfboss() -> str:
            calls["confirm"] += 1
            return "show"

        def recovery_close() -> str:
            calls["confirm"] += 1
            return "recovery_close"

        def exit_now() -> None:
            calls["exit"] += 1

        monkeypatch.setattr(tray, "_confirm_exit_monitoring_warning", cancel_exit)
        monkeypatch.setattr(tray, "_exit_app_now", exit_now)
        window.dashboard_page._confirm_recovery_close_today = lambda: True
        window.dashboard_page._show_day_close_review = lambda summary: reviews.append(
            summary
        )

        tray.exit_app()

        assert calls == {"confirm": 1, "exit": 0}
        assert window.close_to_tray is True

        window.hide()
        monkeypatch.setattr(tray, "_confirm_exit_monitoring_warning", show_selfboss)
        tray.exit_app()

        assert calls == {"confirm": 2, "exit": 0}
        assert window.isVisible()

        monkeypatch.setattr(tray, "_confirm_exit_monitoring_warning", recovery_close)
        tray.exit_app()

        assert calls == {"confirm": 3, "exit": 1}
        assert reviews and reviews[0].close_type == "recovery_close"
        assert "reddit.com" not in hosts_path.read_text(encoding="utf-8")


def test_tray_exit_prompts_for_active_day_and_skips_prompt_when_inactive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    settings = make_settings(tmp_path)
    with initialize_database(settings.db_path) as connection:
        service = make_service(settings, connection)
        window = MainWindow(settings=settings, service=service)
        tray = TrayController(window=window, settings=settings, app=app)
        calls = {"confirm": 0, "exit": 0}

        def cancel_exit() -> str:
            calls["confirm"] += 1
            return "cancel"

        def exit_now() -> None:
            calls["exit"] += 1

        monkeypatch.setattr(tray, "_confirm_exit_monitoring_warning", cancel_exit)
        monkeypatch.setattr(tray, "_exit_app_now", exit_now)

        tray.exit_app()

        assert calls == {"confirm": 0, "exit": 1}

        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        tray.exit_app()

        assert calls == {"confirm": 0, "exit": 2}

        service.set_enforcement_mode("armed_dry_run")
        tray.exit_app()

        assert calls == {"confirm": 1, "exit": 2}


def _assert_no_unsafe_settings_controls(page: SettingsPage) -> None:
    forbidden_phrases = (
        "disable test mode",
        "test mode off",
        "enable real blocking",
        "enable real enforcement",
        "admin",
        "hosts",
        "firewall",
        "kill process",
        "system",
    )
    for button in page.findChildren(QPushButton):
        if button.isEnabled():
            text = button.text().lower()
            assert not any(phrase in text for phrase in forbidden_phrases)


class RecordingBrowserSetupRegistrar:
    def __init__(self) -> None:
        self.repair_calls: list[tuple[str, str]] = []
        self.unregister_calls: list[str] = []

    def register_native_host(
        self,
        *,
        browser: str,
        extension_id: str | None = None,
    ) -> NativeHostRegistrationResult:
        return self.repair_native_host(browser=browser, extension_id=extension_id)

    def repair_native_host(
        self,
        *,
        browser: str,
        extension_id: str | None = None,
    ) -> NativeHostRegistrationResult:
        self.repair_calls.append((browser, extension_id or LOOPGUARD_CHROME_EXTENSION_ID))
        return NativeHostRegistrationResult(
            ok=True,
            reason=(
                "Native host registered. Reload the extension, "
                "then Refresh status."
            ),
        )

    def unregister_native_host(
        self,
        *,
        browser: str,
    ) -> NativeHostRegistrationResult:
        self.unregister_calls.append(browser)
        return NativeHostRegistrationResult(
            ok=True,
            reason=(
                "Native host unregistered. Browser integration will be disconnected "
                "until registered again."
            ),
        )


def make_service(
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
