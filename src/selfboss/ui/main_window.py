"""Main window for the LoopGuard UI shell."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from selfboss.config import is_production_app_mode
from selfboss.core.models import AppSettings
from selfboss.core.use_cases import SelfBossAppService
from selfboss.ui.dashboard_page import DashboardPage
from selfboss.ui.rules_page import RulesPage
from selfboss.ui.settings_page import SettingsPage
from selfboss.ui.style import SIDEBAR_WIDTH, common_stylesheet
from selfboss.ui.tasks_page import TasksPage
from selfboss.ui.theme import modern_common_stylesheet
from selfboss.ui.window_chrome import (
    apply_dark_window_chrome,
    apply_loopguard_window_icon,
    prepare_dialog_window,
)


class MainWindow(QMainWindow):
    """Main LoopGuard window with sidebar navigation."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        service: SelfBossAppService,
        close_to_tray: bool = True,
        production_mode: bool | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self.close_to_tray = close_to_tray
        self.production_mode = (
            is_production_app_mode()
            if production_mode is None
            else production_mode
        )
        self.tray_controller: Any = None
        self.database_connection: Any = None

        self.setWindowTitle("LoopGuard")
        apply_loopguard_window_icon(self)
        self.resize(1180, 760)
        self.setStyleSheet(_app_stylesheet())

        self.dashboard_page = DashboardPage(
            service,
            on_day_started=self.refresh_task_flow,
            production_mode=self.production_mode,
        )
        self.tasks_page = TasksPage(service, on_tasks_changed=self.refresh_dashboard)
        self.rules_page = RulesPage(
            service,
            production_mode=self.production_mode,
        )
        self.settings_page = SettingsPage(
            settings,
            service=service,
            production_mode=self.production_mode,
        )

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("contentStack")
        self.content_stack.addWidget(self.dashboard_page)
        self.content_stack.addWidget(self.tasks_page)
        self.content_stack.addWidget(self.rules_page)
        self.content_stack.addWidget(self.settings_page)

        self.sidebar_buttons: dict[str, QPushButton] = {}
        self.sidebar_button_group = QButtonGroup(self)
        self.sidebar_button_group.setExclusive(True)

        sidebar = self._build_sidebar()
        shell = QWidget()
        shell.setObjectName("appShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(sidebar)

        content_shell = QWidget()
        content_shell.setObjectName("contentShell")
        content_layout = QVBoxLayout(content_shell)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        header = self._build_header()
        content_layout.addWidget(header)
        content_layout.addWidget(self.content_stack, 1)
        shell_layout.addWidget(content_shell, 1)

        self.setCentralWidget(shell)
        self._select_page("Dashboard")
        self.armed_dry_run_scan_timer = QTimer(self)
        self.armed_dry_run_scan_timer.setObjectName("armedDryRunScanTimer")
        self.armed_dry_run_scan_timer.setInterval(5000)
        self.armed_dry_run_scan_timer.timeout.connect(
            self._run_enforcement_scan_cycle
        )
        self.armed_dry_run_scan_timer.start()
        QTimer.singleShot(0, self._apply_dark_window_chrome)
        QTimer.singleShot(0, self._maybe_show_browser_setup_intro_prompt)

    def show_and_raise(self) -> None:
        """Restore the window from tray/minimized state and request focus."""
        state = self.windowState()
        if self.isMinimized() or state & Qt.WindowState.WindowMinimized:
            self.showNormal()
        else:
            self.show()
        self.setWindowState(
            (state & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive
        )
        self.raise_()
        self.activateWindow()
        handle = self.windowHandle()
        if handle is not None:
            handle.requestActivate()

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 18, 16, 18)
        layout.setSpacing(8)

        title = QLabel("LoopGuard")
        title.setObjectName("sidebarTitle")
        title.setWordWrap(True)
        subtitle = QLabel("Local escape control")
        subtitle.setObjectName("sidebarSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        for index, name in enumerate(("Dashboard", "Tasks", "Rules", "Settings")):
            button = QPushButton(name)
            button.setObjectName("sidebarButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, page=name: self._select_page(page))
            self.sidebar_buttons[name] = button
            self.sidebar_button_group.addButton(button, index)
            layout.addWidget(button)

        layout.addStretch(1)
        footer = QLabel(
            "Local protection"
            if self.production_mode
            else "Test Mode ON"
        )
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setToolTip(
            "LoopGuard runs locally on this computer."
            if self.production_mode
            else "Test Mode is locked on."
        )
        layout.addWidget(footer)
        return sidebar

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("appHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(28, 18, 28, 14)
        layout.setSpacing(2)
        self.header_title_label = QLabel("Dashboard")
        self.header_title_label.setObjectName("appHeaderTitle")
        self.header_title_label.setWordWrap(True)
        self.header_subtitle_label = QLabel("Today, access, and recovery at a glance")
        self.header_subtitle_label.setObjectName("appHeaderSubtitle")
        self.header_subtitle_label.setWordWrap(True)
        layout.addWidget(self.header_title_label)
        layout.addWidget(self.header_subtitle_label)
        return header

    def _select_page(self, name: str) -> None:
        pages = {
            "Dashboard": self.dashboard_page,
            "Tasks": self.tasks_page,
            "Rules": self.rules_page,
            "Settings": self.settings_page,
        }
        page = pages[name]
        self.content_stack.setCurrentWidget(page)
        self.sidebar_buttons[name].setChecked(True)
        self._refresh_header(name)
        self._refresh_selected_page(name)

    def _refresh_header(self, name: str) -> None:
        subtitles = {
            "Dashboard": "Today, access, and recovery at a glance",
            "Tasks": "Plan anchors and claim completed work",
            "Rules": "Shape focus, utility, and recreation boundaries",
            "Settings": "Local setup, safety, and recovery controls",
        }
        self.header_title_label.setText(name)
        self.header_subtitle_label.setText(subtitles[name])

    def _refresh_selected_page(self, name: str) -> None:
        if name == "Dashboard":
            self.dashboard_page.refresh()
        elif name == "Tasks":
            self.tasks_page.refresh_tasks()
        elif name == "Rules":
            self.rules_page.refresh()
        elif name == "Settings":
            self.settings_page.refresh()

    def refresh_dashboard(self) -> None:
        """Refresh dashboard values from the application service."""
        self.dashboard_page.refresh()

    def refresh_task_flow(self) -> None:
        """Refresh task and dashboard pages after day-level changes."""
        self.tasks_page.refresh_tasks()
        self.dashboard_page.refresh()

    def _run_enforcement_scan_cycle(self) -> None:
        try:
            attempts = [
                *self.service.run_armed_dry_run_process_scan_cycle(),
                *self.service.run_real_process_blocking_scan_cycle(),
            ]
            hosts_result = self.service.run_real_hosts_blocking_cycle()
            unmanaged_browser_results = (
                self.service.run_unmanaged_browser_guard_cycle()
            )
            high_events = self.service.collect_high_notification_events()
        except Exception:
            return
        for event in high_events:
            self._notify_high_event(event)
        if not attempts and hosts_result is None and not unmanaged_browser_results:
            if not high_events:
                return
        self.dashboard_page.refresh()
        if self.content_stack.currentWidget() is self.rules_page:
            self.rules_page.refresh()
        if self.content_stack.currentWidget() is self.settings_page:
            self.settings_page.refresh()

    def _notify_high_event(self, event) -> None:
        notifier = getattr(self.tray_controller, "notify_high_event", None)
        if callable(notifier):
            notifier(event.title, event.message)
            return
        icon = getattr(self.tray_controller, "icon", None)
        if icon is not None and icon.isVisible():
            icon.showMessage(
                event.title,
                event.message,
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            return
        self.dashboard_page.spend_status_label.setText(event.message)

    def _maybe_show_browser_setup_intro_prompt(self) -> None:
        if (
            not self.production_mode
            or self.service.has_seen_browser_setup_intro()
            or not self.service.should_show_browser_setup_intro()
        ):
            return
        choice = self._show_browser_setup_intro_prompt()
        if choice == "setup":
            self._select_page("Settings")
            self.settings_page.show_browser_setup_guidance()
            return
        if choice == "dismiss":
            self.service.mark_browser_setup_intro_seen()
            self.settings_page.refresh()

    def _show_browser_setup_intro_prompt(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("Set up Chrome")
        prepare_dialog_window(box)
        box.setText(
            "Chrome setup is required before LoopGuard can start a day. The "
            "extension is bundled, but Chrome will not be changed silently."
        )
        box.setInformativeText(
            "LoopGuard will guide you through loading the extension and will ask "
            "before repairing the native host connection."
        )
        setup_button = box.addButton(
            "Set up now",
            QMessageBox.ButtonRole.AcceptRole,
        )
        skip_button = box.addButton(
            "Skip for now",
            QMessageBox.ButtonRole.RejectRole,
        )
        dismiss_button = box.addButton(
            "Don't show again",
            QMessageBox.ButtonRole.ActionRole,
        )
        box.setDefaultButton(setup_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is setup_button:
            return "setup"
        if clicked is dismiss_button:
            return "dismiss"
        return "skip"

    def disable_close_to_tray(self) -> None:
        """Allow the window to close normally."""
        self.close_to_tray = False

    def showEvent(self, event: QShowEvent) -> None:
        """Reapply native titlebar styling once the window handle exists."""
        super().showEvent(event)
        self._apply_dark_window_chrome()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide instead of closing when close-to-tray is enabled."""
        if self.close_to_tray:
            event.ignore()
            self.hide()
            self._notify_still_running()
            return

        super().closeEvent(event)

    def _notify_still_running(self) -> None:
        """Show a neutral tray notice when the window is hidden."""
        notifier = getattr(self.tray_controller, "notify_still_running", None)
        if callable(notifier):
            notifier()

    def _apply_dark_window_chrome(self) -> None:
        apply_dark_window_chrome(self)


def _app_stylesheet() -> str:
    return (
        """
    QMainWindow {
        background: #0b0d0c;
    }
    QWidget#appShell {
        background: #0b0d0c;
    }
    QStackedWidget#contentStack {
        background: #0b0d0c;
        padding: 0;
    }
    QLabel {
        color: #f1f5ef;
        font-size: 13px;
    }
    QPushButton {
        background: #171c18;
        border: 1px solid #273027;
        border-radius: 9px;
        min-height: 30px;
        padding: 4px 12px;
        color: #f1f5ef;
        font-weight: 600;
    }
    QTableWidget,
    QListWidget,
    QLineEdit,
    QComboBox,
    QSpinBox {
        background: #0b0d0c;
        border: 1px solid #273027;
        border-radius: 9px;
        min-height: 30px;
        padding: 3px 8px;
        color: #f1f5ef;
    }
    """
        + common_stylesheet()
        + modern_common_stylesheet()
    )
