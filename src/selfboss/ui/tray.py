"""System tray integration for the LoopGuard UI shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from selfboss.core.models import AppSettings, EnforcementMode
from selfboss.ui.main_window import MainWindow
from selfboss.ui.window_chrome import loopguard_app_icon

_STILL_RUNNING_MESSAGE = (
    "LoopGuard is still running. Use the tray menu to reopen or exit."
)
_ACTIVE_DAY_EXIT_WARNING = (
    "LoopGuard is protecting an active day. Close the day or use Emergency "
    "Recovery if something is broken."
)


class TrayController:
    """Own the tray icon and menu actions."""

    def __init__(
        self,
        *,
        window: MainWindow,
        settings: AppSettings,
        app: QApplication,
    ) -> None:
        self.window = window
        self.settings = settings
        self.app = app

        self.menu = QMenu()
        self.show_action = QAction("Show LoopGuard", self.menu)
        self.test_mode_action = QAction("Run in Test Mode", self.menu)
        self.recovery_status_action = QAction("Recovery Status", self.menu)
        self.exit_action = QAction("Exit LoopGuard...", self.menu)

        self.test_mode_action.setCheckable(True)
        self.test_mode_action.setChecked(settings.test_mode)
        self.test_mode_action.setEnabled(False)

        self.show_action.triggered.connect(self.show_window)
        self.recovery_status_action.triggered.connect(self.show_recovery_status)
        self.exit_action.triggered.connect(self.exit_app)

        self.menu.addAction(self.show_action)
        if not window.production_mode:
            self.menu.addAction(self.test_mode_action)
        if not window.production_mode:
            self.menu.addAction(self.recovery_status_action)
        self.menu.addSeparator()
        self.menu.addAction(self.exit_action)

        self.icon = QSystemTrayIcon(_create_icon(), self.app)
        self.icon.setToolTip("LoopGuard")
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)

    def show(self) -> None:
        """Show the tray icon."""
        self.icon.show()

    def show_window(self) -> None:
        """Show and raise the main window."""
        self.window.show_and_raise()

    def notify_still_running(self) -> None:
        """Show a short tray notification after close-to-tray."""
        if not self.icon.isVisible():
            return
        self.icon.showMessage(
            "LoopGuard is still running",
            _STILL_RUNNING_MESSAGE,
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def show_recovery_status(self) -> None:
        """Show the current placeholder recovery status."""
        status = "Recovery mode is active." if self.settings.recovery_mode else (
            "Recovery mode is available but not active."
        )
        QMessageBox.information(self.window, "Recovery Status", status)

    def exit_app(self) -> None:
        """Exit the application without close-to-tray interception."""
        if self._exit_needs_confirmation():
            decision = self._confirm_exit_monitoring_warning()
            if decision == "show":
                self.show_window()
                return
            if decision == "recovery_close" and not self.window.production_mode:
                self._recovery_close_today_then_exit_if_closed()
                return
            if decision != "exit":
                return
        self._exit_app_now()

    def _exit_needs_confirmation(self) -> bool:
        service = self.window.service
        if self.settings.safe_mode or self.settings.recovery_mode:
            return False
        snapshot = service.dashboard_snapshot()
        enforcement_status = service.get_enforcement_status()
        active_enforcement_selected = (
            enforcement_status.selected_mode is not EnforcementMode.PREVIEW_ONLY
            or enforcement_status.effective_mode is not EnforcementMode.PREVIEW_ONLY
        )
        return (
            (snapshot.day_started and not snapshot.day_closed and active_enforcement_selected)
            or snapshot.high_active
            or snapshot.active_planned_use_pass is not None
        )

    def _confirm_exit_monitoring_warning(self) -> str:
        dialog = QMessageBox(self.window)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Exit LoopGuard")
        dialog.setText(_ACTIVE_DAY_EXIT_WARNING)
        cancel_button = dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        show_button = dialog.addButton(
            "Show LoopGuard",
            QMessageBox.ButtonRole.ActionRole,
        )
        recovery_close_button = None
        if not self.window.production_mode:
            recovery_close_button = dialog.addButton(
                "Recovery Close Today",
                QMessageBox.ButtonRole.AcceptRole,
            )
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        clicked = dialog.clickedButton()
        if recovery_close_button is not None and clicked is recovery_close_button:
            return "recovery_close"
        if clicked is show_button:
            return "show"
        return "cancel"

    def _recovery_close_today_then_exit_if_closed(self) -> None:
        self.show_window()
        self.window.dashboard_page.recovery_close_today()
        self.window.refresh_task_flow()
        if not self.window.service.is_active_day():
            self._exit_app_now()

    def _exit_app_now(self) -> None:
        """Exit immediately after any required safety confirmation."""
        self.window.disable_close_to_tray()
        self.icon.hide()
        self.app.quit()

    def action_texts(self) -> list[str]:
        """Return non-separator menu action labels for smoke tests."""
        return [action.text() for action in self.menu.actions() if not action.isSeparator()]

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_window()


def _create_icon() -> QIcon:
    """Create a small in-memory tray icon."""
    icon = loopguard_app_icon()
    if not icon.isNull():
        return icon
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.GlobalColor.darkCyan)
    painter.setPen(Qt.GlobalColor.white)
    painter.drawEllipse(2, 2, 28, 28)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "LG")
    painter.end()

    return QIcon(pixmap)
