"""Read-only runtime and safety settings tab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from selfboss.config import is_production_app_mode
from selfboss.core.models import AppSettings
from selfboss.core.use_cases import (
    DAILY_RECREATION_CAP_MAX_MINUTES,
    DAILY_RECREATION_CAP_MIN_MINUTES,
    SOFT_START_LOCKED_AFTER_START_DAY,
    SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY,
    SelfBossAppService,
)
from selfboss.platform.browser_setup import (
    BrowserSetupActionResult,
    BrowserSetupRegistrar,
    LOOPGUARD_CHROME_EXTENSION_ID,
    open_chrome_extensions_page,
    open_extension_folder,
    normalize_extension_id,
)
from selfboss.packaging_support import (
    app_resource_root,
    browser_extension_folder,
    recovery_scripts_folder,
)
from selfboss.ui.components import (
    CardFrame,
    make_muted_label,
    make_page_content,
    reset_layout,
)
from selfboss.ui.style import MEDIUM_GAP, SETTINGS_MAX_WIDTH, common_stylesheet
from selfboss.ui.theme import modern_common_stylesheet
from selfboss.ui.widgets import (
    configure_pill,
    make_status_row,
    make_subpanel,
    set_button_role,
    set_card_role,
)
from selfboss.ui.window_chrome import (
    apply_dark_window_chrome,
    prepare_dialog_window,
)


class BrowserSetupDialog(QDialog):
    """Beginner-friendly Chrome extension setup steps."""

    def __init__(
        self,
        *,
        extension_folder: Path,
        open_chrome_extensions_page_action: Callable[[], BrowserSetupActionResult],
        open_extension_folder_action: Callable[[Path], BrowserSetupActionResult],
        refresh_status_action: Callable[[], str] | None = None,
        repair_native_host_action: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.extension_folder = extension_folder
        self.open_chrome_extensions_page_action = open_chrome_extensions_page_action
        self.open_extension_folder_action = open_extension_folder_action
        self.refresh_status_action = refresh_status_action
        self.repair_native_host_action = repair_native_host_action
        self.setWindowTitle("Set up Chrome")
        prepare_dialog_window(self)
        self.setObjectName("BrowserSetupDialog")
        self.setStyleSheet(_settings_stylesheet())

        layout = QVBoxLayout(self)
        reset_layout(layout)
        layout.setSpacing(MEDIUM_GAP)

        self.intro_label = QLabel(
            "Chrome setup is required before LoopGuard can start a day."
        )
        self.intro_label.setObjectName("BrowserSetupIntro")
        self.intro_label.setWordWrap(True)
        self.folder_label = make_muted_label(
            f"Extension folder: {extension_folder}"
        )
        _make_selectable_diagnostic(self.folder_label, extension_folder)
        self.steps_label = QLabel(
            "1. Open Chrome extensions\n"
            "2. Open the bundled extension folder\n"
            "3. In Chrome, enable Developer mode, click Load unpacked, and "
            "select that folder\n"
            "4. Return to LoopGuard and refresh status\n"
            "5. Use Repair connection only if Native Host does not connect"
        )
        self.steps_label.setObjectName("BrowserSetupSteps")
        self.steps_label.setWordWrap(True)
        self.open_extension_folder_button = QPushButton("Open extension folder")
        self.copy_extension_folder_button = QPushButton("Copy extension folder path")
        self.copy_extension_folder_button.setObjectName(
            "copyExtensionFolderPathButton"
        )
        self.open_chrome_extensions_button = QPushButton(
            "Open Chrome extensions"
        )
        self.repair_native_host_button = QPushButton("Repair connection")
        self.refresh_status_button = QPushButton("Refresh status")
        self.status_label = make_muted_label("")
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)

        action_panel = QWidget()
        action_layout = QVBoxLayout(action_panel)
        reset_layout(action_layout)
        action_layout.setSpacing(MEDIUM_GAP)
        for button in (
            self.open_chrome_extensions_button,
            self.open_extension_folder_button,
            self.copy_extension_folder_button,
            self.repair_native_host_button,
            self.refresh_status_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            action_layout.addWidget(button)

        self.open_extension_folder_button.clicked.connect(self._open_extension_folder)
        self.copy_extension_folder_button.clicked.connect(
            self._copy_extension_folder_path
        )
        self.open_chrome_extensions_button.clicked.connect(
            self._open_chrome_extensions_page
        )
        self.repair_native_host_button.clicked.connect(self._repair_native_host)
        self.refresh_status_button.clicked.connect(self._refresh_status)
        self.repair_native_host_button.setEnabled(repair_native_host_action is not None)
        self.refresh_status_button.setEnabled(refresh_status_action is not None)

        layout.addWidget(self.intro_label)
        layout.addWidget(self.folder_label)
        layout.addWidget(self.steps_label)
        layout.addWidget(action_panel)
        layout.addWidget(self.status_label)
        layout.addWidget(self.button_box)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_dark_window_chrome()

    def _apply_dark_window_chrome(self) -> None:
        apply_dark_window_chrome(self)

    def _open_chrome_extensions_page(self) -> None:
        self._show_action_result(self.open_chrome_extensions_page_action())

    def _open_extension_folder(self) -> None:
        self._show_action_result(
            self.open_extension_folder_action(self.extension_folder)
        )

    def _copy_extension_folder_path(self) -> None:
        QApplication.clipboard().setText(str(self.extension_folder))
        self.status_label.setText("Copied extension folder path.")

    def _repair_native_host(self) -> None:
        if self.repair_native_host_action is None:
            return
        self.status_label.setText(self.repair_native_host_action())

    def _refresh_status(self) -> None:
        if self.refresh_status_action is None:
            return
        self.status_label.setText(self.refresh_status_action())

    def _show_action_result(self, result: BrowserSetupActionResult) -> None:
        if result.copy_text:
            QApplication.clipboard().setText(result.copy_text)
        self.status_label.setText(result.reason)


class SettingsPage(QWidget):
    """Read-only safety and runtime status page."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        service: SelfBossAppService,
        browser_setup_registrar: BrowserSetupRegistrar | None = None,
        open_chrome_extensions_page_action: Callable[
            [], BrowserSetupActionResult
        ] | None = None,
        open_extension_folder_action: Callable[
            [Path], BrowserSetupActionResult
        ] | None = None,
        production_mode: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.service = service
        self.production_mode = (
            is_production_app_mode()
            if production_mode is None
            else production_mode
        )
        self.browser_setup_registrar = (
            browser_setup_registrar or BrowserSetupRegistrar(repo_root=_repo_root())
        )
        self.open_chrome_extensions_page_action = (
            open_chrome_extensions_page_action or open_chrome_extensions_page
        )
        self.open_extension_folder_action = (
            open_extension_folder_action or open_extension_folder
        )
        self._soft_start_idle_help = (
            "After Start Day, LoopGuard waits this long before tasks can be "
            "completed and rewards can be earned."
        )
        self._daily_recreation_cap_idle_help = (
            "Maximum total HIGH time allocated per day. Refunded time does not "
            "restore this cap."
        )
        self._configuration_backup_helper_text = (
            "Exports local rules and settings only. It does not export "
            "browsing history or logs."
        )
        self._configuration_backup_active_day_lock_text = (
            "Available before Start Day or after closing the day."
        )
        self._recovery_idle_note = (
            "Use only if blocking breaks or LoopGuard cannot recover normally."
        )
        self._recovery_active_day_note = (
            "Emergency scripts remain available in the app folder."
        )

        self.setObjectName("settingsPage")
        self.setStyleSheet(_settings_stylesheet())
        self.browser_setup_refresh_timer = QTimer(self)
        self.browser_setup_refresh_timer.setObjectName("browserSetupRefreshTimer")
        self.browser_setup_refresh_timer.setInterval(3000)
        self.browser_setup_refresh_timer.timeout.connect(
            self._refresh_browser_setup_controls
        )
        outer_layout = QVBoxLayout(self)
        reset_layout(outer_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("settingsScrollArea")
        self.scroll_area.viewport().setObjectName("settingsScrollViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        outer_layout.addWidget(self.scroll_area)

        shell, content, layout = make_page_content(
            "settingsContent",
            max_width=SETTINGS_MAX_WIDTH,
        )
        self.content_widget = content
        self.scroll_area.setWidget(shell)

        runtime_card, runtime_layout, self.runtime_card_title_label = _build_card(
            "Runtime and Safety"
        )
        self.runtime_card = runtime_card
        set_card_role(runtime_card, "compact")
        self.title_label = self.runtime_card_title_label
        self.test_mode_label = QLabel("Test Mode: ON / Locked")
        self.test_mode_label.setObjectName("settingsTestModeBadge")
        self.test_mode_label.setWordWrap(True)
        self.test_mode_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        self.safe_mode_label = QLabel(
            f"Safe Mode: {'ON' if settings.safe_mode else 'OFF'}"
        )
        self.recovery_mode_label = QLabel(
            f"Recovery Mode: {'ON' if settings.recovery_mode else 'OFF'}"
        )
        configure_pill(
            self.safe_mode_label,
            "success" if settings.safe_mode else "neutral",
        )
        configure_pill(
            self.recovery_mode_label,
            "warning" if settings.recovery_mode else "neutral",
        )
        self.safe_mode_label.setWordWrap(True)
        self.recovery_mode_label.setWordWrap(True)
        self.safety_note_label = make_muted_label(
            "Test Mode is locked on until recovery, background monitoring, "
            "and real enforcement checks are complete."
        )
        runtime_layout.addWidget(
            make_subpanel(
                self.test_mode_label,
                make_status_row("Safe", self.safe_mode_label),
                make_status_row("Recovery", self.recovery_mode_label),
                self.safety_note_label,
                role="compact",
            )
        )
        (
            advanced_diagnostics_card,
            advanced_diagnostics_layout,
            self.advanced_diagnostics_card_title_label,
        ) = _build_card("Advanced Diagnostics")
        self.advanced_diagnostics_card = advanced_diagnostics_card
        set_card_role(advanced_diagnostics_card, "secondary")
        self.advanced_diagnostics_toggle_button = QPushButton(
            "Show advanced diagnostics"
        )
        self.advanced_diagnostics_toggle_button.setObjectName(
            "advancedDiagnosticsToggleButton"
        )
        set_button_role(self.advanced_diagnostics_toggle_button, "quiet")
        self.advanced_diagnostics_hint_label = make_muted_label(
            "Detailed readiness and troubleshooting information."
        )
        self.advanced_diagnostics_panel = QWidget()
        self.advanced_diagnostics_panel.setObjectName("advancedDiagnosticsPanel")
        self.advanced_diagnostics_panel.setVisible(False)
        self.advanced_diagnostics_panel_layout = QVBoxLayout(
            self.advanced_diagnostics_panel
        )
        reset_layout(self.advanced_diagnostics_panel_layout)
        self.advanced_diagnostics_panel_layout.setSpacing(MEDIUM_GAP)
        self.advanced_diagnostics_toggle_button.clicked.connect(
            self._toggle_advanced_diagnostics
        )
        self.advanced_diagnostics_toggle_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        advanced_diagnostics_layout.addWidget(
            make_subpanel(
                self.advanced_diagnostics_hint_label,
                self.advanced_diagnostics_toggle_button,
                role="compact",
            )
        )
        advanced_diagnostics_layout.addWidget(self.advanced_diagnostics_panel)

        data_card, data_layout, self.data_card_title_label = _build_card("Diagnostics")
        set_card_role(data_card, "list")
        self.data_path_label = make_muted_label("App data folder: Local LoopGuard data")
        self.database_path_label = make_muted_label(
            f"Database: {settings.db_path.name}"
        )
        self.diagnostics_hint_label = make_muted_label("Hover to see full path.")
        self.diagnostics_status_label = make_muted_label("")
        self.open_app_data_folder_button = QPushButton("Open app data folder")
        self.open_database_folder_button = QPushButton("Open database folder")
        self.open_app_data_folder_button.setObjectName("openAppDataFolderButton")
        self.open_database_folder_button.setObjectName("openDatabaseFolderButton")
        for button in (
            self.open_app_data_folder_button,
            self.open_database_folder_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            set_button_role(button, "quiet")
        _make_selectable_diagnostic(self.data_path_label, settings.data_dir)
        _make_selectable_diagnostic(self.database_path_label, settings.db_path)
        diagnostics_action_row = QHBoxLayout()
        reset_layout(diagnostics_action_row)
        diagnostics_action_row.setSpacing(MEDIUM_GAP)
        diagnostics_action_row.addWidget(self.open_app_data_folder_button)
        diagnostics_action_row.addWidget(self.open_database_folder_button)
        diagnostics_action_row.addStretch(1)
        self.open_app_data_folder_button.clicked.connect(self._open_app_data_folder)
        self.open_database_folder_button.clicked.connect(self._open_database_folder)
        data_layout.addWidget(self.data_path_label)
        data_layout.addWidget(self.database_path_label)
        data_layout.addWidget(self.diagnostics_hint_label)
        data_layout.addLayout(diagnostics_action_row)
        data_layout.addWidget(self.diagnostics_status_label)
        self.advanced_diagnostics_panel_layout.addWidget(data_card)

        (
            configuration_backup_card,
            configuration_backup_layout,
            self.configuration_backup_card_title_label,
        ) = _build_card("Configuration Backup")
        set_card_role(configuration_backup_card, "control")
        self.configuration_backup_helper_label = make_muted_label(
            self._configuration_backup_helper_text
        )
        self.configuration_backup_helper_label.setObjectName(
            "settingsConfigurationBackupHelperLabel"
        )
        self.configuration_backup_status_label = make_muted_label("")
        self.configuration_backup_status_label.setObjectName(
            "settingsConfigurationBackupStatusLabel"
        )
        self.export_configuration_button = QPushButton("Export configuration")
        self.import_configuration_button = QPushButton("Import configuration")
        self.export_configuration_button.setObjectName("exportConfigurationButton")
        self.import_configuration_button.setObjectName("importConfigurationButton")
        set_button_role(self.export_configuration_button, "primary")
        set_button_role(self.import_configuration_button, "quiet")
        configuration_backup_action_row = QHBoxLayout()
        reset_layout(configuration_backup_action_row)
        configuration_backup_action_row.setSpacing(MEDIUM_GAP)
        configuration_backup_action_row.addWidget(self.export_configuration_button)
        configuration_backup_action_row.addWidget(self.import_configuration_button)
        configuration_backup_action_row.addStretch(1)
        self.export_configuration_button.clicked.connect(self._export_configuration)
        self.import_configuration_button.clicked.connect(self._import_configuration)
        configuration_backup_layout.addWidget(
            make_subpanel(
                self.configuration_backup_helper_label,
                _panel_from_layout(configuration_backup_action_row),
                role="metric",
            )
        )
        configuration_backup_layout.addWidget(self.configuration_backup_status_label)

        (
            browser_setup_card,
            browser_setup_layout,
            self.browser_setup_card_title_label,
        ) = _build_card("Browser Setup")
        if self.production_mode:
            self.browser_setup_card_title_label.setText("Chrome setup")
        set_card_role(browser_setup_card, "control")
        self.browser_setup_chrome_status_label = configure_pill(QLabel("Unknown"))
        self.browser_setup_incognito_status_label = configure_pill(
            QLabel("Unknown"),
            "neutral",
        )
        self.browser_setup_native_host_status_label = configure_pill(
            QLabel("Unknown"),
            "neutral",
        )
        self.browser_setup_dnr_status_label = configure_pill(QLabel("Unknown"))
        self.browser_setup_youtube_status_label = configure_pill(
            QLabel("Unknown"),
            "neutral",
        )
        self.browser_setup_status_label = make_muted_label("")
        self.browser_setup_status_label.setObjectName("settingsBrowserSetupStatusLabel")
        self.browser_setup_status_label.setVisible(False)
        self.browser_setup_folder_label = make_muted_label(
            "Extension folder: browser_extension/chrome_mv3"
        )
        self.browser_setup_folder_label.setObjectName("settingsBrowserSetupFolderLabel")
        self.browser_setup_instruction_label = make_muted_label(
            "Chrome setup is required for precise website blocking."
        )
        self.browser_setup_instruction_label.setObjectName(
            "settingsBrowserSetupInstructionLabel"
        )
        self.browser_setup_extension_id_label = QLabel("Chrome extension ID")
        self.browser_setup_extension_id_label.setObjectName(
            "settingsBrowserSetupExtensionIdLabel"
        )
        self.browser_setup_extension_id_input = QLineEdit()
        self.browser_setup_extension_id_input.setObjectName(
            "settingsBrowserSetupExtensionIdInput"
        )
        self.browser_setup_extension_id_input.setPlaceholderText(
            "Optional custom 32-character extension ID"
        )
        self.browser_setup_extension_id_help_label = make_muted_label(
            "Advanced/custom repair only. Normal setup does not require copying "
            "an extension ID."
        )
        self.browser_setup_edge_label = make_muted_label("Edge: planned.")
        self.browser_setup_next_action_label = make_muted_label("")
        self.browser_setup_next_action_label.setObjectName(
            "settingsBrowserSetupNextActionLabel"
        )
        self.browser_setup_status_note_label = make_muted_label("")
        self._browser_setup_intro_skipped_this_session = False
        self.browser_setup_intro_label = make_muted_label(
            "Chrome setup is required before Start Day. LoopGuard guides you "
            "through loading the bundled extension manually; Chrome is not "
            "modified silently."
        )
        self.browser_setup_intro_label.setObjectName(
            "settingsBrowserSetupIntroLabel"
        )
        self.browser_setup_intro_future_label = make_muted_label(
            "Next step: Set up Chrome. Then return here and refresh status. "
            "Use Repair connection only if Native Host stays disconnected."
        )
        self.browser_setup_intro_future_label.setObjectName(
            "settingsBrowserSetupIntroFutureLabel"
        )
        self.setup_chrome_button = QPushButton("Set up Chrome")
        self.open_chrome_extensions_button = QPushButton(
            "Open Chrome extensions page"
        )
        self.open_extension_folder_button = QPushButton("Open extension folder")
        self.refresh_browser_setup_button = QPushButton("Refresh status")
        self.skip_browser_setup_intro_button = QPushButton("Skip for now")
        self.dismiss_browser_setup_intro_button = QPushButton("Don't show again")
        self.repair_native_host_button = QPushButton("Repair connection")
        self.unregister_native_host_button = QPushButton("Unregister native host")
        self.manual_browser_setup_toggle_button = QPushButton("Show manual setup")
        self.setup_chrome_button.setObjectName("setupChromeButton")
        self.open_chrome_extensions_button.setObjectName(
            "settingsOpenChromeExtensionsButton"
        )
        self.open_extension_folder_button.setObjectName(
            "settingsOpenExtensionFolderButton"
        )
        self.refresh_browser_setup_button.setObjectName("refreshBrowserSetupButton")
        self.skip_browser_setup_intro_button.setObjectName(
            "skipBrowserSetupIntroButton"
        )
        self.dismiss_browser_setup_intro_button.setObjectName(
            "dismissBrowserSetupIntroButton"
        )
        self.repair_native_host_button.setObjectName("repairNativeHostButton")
        self.unregister_native_host_button.setObjectName("unregisterNativeHostButton")
        self.manual_browser_setup_toggle_button.setObjectName(
            "manualBrowserSetupToggleButton"
        )
        self.repair_native_host_button.setToolTip(
            "Repair the local Chrome connection after confirmation."
        )
        self.unregister_native_host_button.setToolTip(
            "Remove LoopGuard native host registration for Chrome from HKCU."
        )
        set_button_role(self.setup_chrome_button, "primary")
        set_button_role(self.open_chrome_extensions_button, "quiet")
        set_button_role(self.open_extension_folder_button, "quiet")
        set_button_role(self.refresh_browser_setup_button, "quiet")
        set_button_role(self.skip_browser_setup_intro_button, "quiet")
        set_button_role(self.dismiss_browser_setup_intro_button, "quiet")
        set_button_role(self.repair_native_host_button, "quiet")
        set_button_role(self.unregister_native_host_button, "danger")
        set_button_role(self.manual_browser_setup_toggle_button, "quiet")
        _make_selectable_diagnostic(
            self.browser_setup_folder_label,
            _browser_extension_folder(),
        )
        browser_setup_action_column = QVBoxLayout()
        reset_layout(browser_setup_action_column)
        browser_setup_action_column.setSpacing(MEDIUM_GAP)
        for button in (
            self.setup_chrome_button,
            self.open_chrome_extensions_button,
            self.open_extension_folder_button,
            self.refresh_browser_setup_button,
            self.repair_native_host_button,
            self.manual_browser_setup_toggle_button,
        ):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            browser_setup_action_column.addWidget(button)
        self.manual_browser_setup_panel = make_subpanel(
            self.browser_setup_extension_id_label,
            self.browser_setup_extension_id_input,
            self.browser_setup_extension_id_help_label,
            self.unregister_native_host_button,
            role="metric",
        )
        self.manual_browser_setup_panel.setObjectName("manualBrowserSetupPanel")
        self.manual_browser_setup_panel.setVisible(False)
        browser_setup_intro_action_row = QHBoxLayout()
        reset_layout(browser_setup_intro_action_row)
        browser_setup_intro_action_row.setSpacing(MEDIUM_GAP)
        browser_setup_intro_action_row.addWidget(self.skip_browser_setup_intro_button)
        browser_setup_intro_action_row.addWidget(
            self.dismiss_browser_setup_intro_button
        )
        browser_setup_intro_action_row.addStretch(1)
        self.browser_setup_intro_panel = make_subpanel(
            self.browser_setup_intro_label,
            self.browser_setup_intro_future_label,
            _panel_from_layout(browser_setup_intro_action_row),
            role="metric",
        )
        self.browser_setup_intro_panel.setObjectName("browserSetupIntroPanel")
        self.setup_chrome_button.clicked.connect(self._show_browser_setup_dialog)
        self.open_chrome_extensions_button.clicked.connect(
            self._open_chrome_extensions_page
        )
        self.open_extension_folder_button.clicked.connect(self._open_extension_folder)
        self.refresh_browser_setup_button.clicked.connect(self.refresh)
        self.skip_browser_setup_intro_button.clicked.connect(
            self._skip_browser_setup_intro
        )
        self.dismiss_browser_setup_intro_button.clicked.connect(
            self._dismiss_browser_setup_intro
        )
        self.manual_browser_setup_toggle_button.clicked.connect(
            self._toggle_manual_browser_setup
        )
        self.repair_native_host_button.clicked.connect(
            self._repair_chrome_native_host
        )
        self.unregister_native_host_button.clicked.connect(
            self._unregister_chrome_native_host
        )
        self.browser_setup_extension_id_input.textChanged.connect(
            self._refresh_browser_setup_registration_state
        )
        browser_setup_layout.addWidget(
            make_subpanel(
                make_status_row("Chrome", self.browser_setup_chrome_status_label),
                make_status_row("Incognito", self.browser_setup_incognito_status_label),
                make_status_row(
                    "Native host",
                    self.browser_setup_native_host_status_label,
                ),
                make_status_row("DNR", self.browser_setup_dnr_status_label),
                make_status_row("YouTube", self.browser_setup_youtube_status_label),
                self.browser_setup_next_action_label,
                _panel_from_layout(browser_setup_action_column),
                role="compact",
            )
        )
        browser_setup_layout.addWidget(self.browser_setup_intro_panel)
        browser_setup_layout.addWidget(
            make_subpanel(
                self.browser_setup_folder_label,
                self.browser_setup_instruction_label,
                self.browser_setup_edge_label,
                self.manual_browser_setup_panel,
                role="metric",
            )
        )
        browser_setup_layout.addWidget(self.browser_setup_status_note_label)
        readiness_card, readiness_layout, self.readiness_card_title_label = _build_card(
            "Enforcement Readiness"
        )
        self.readiness_card = readiness_card
        set_card_role(readiness_card, "list")
        self.enforcement_mode_label = QLabel("")
        self.enforcement_mode_label.setObjectName("settingsEnforcementModeLabel")
        self.enforcement_mode_label.setWordWrap(True)
        self.enforcement_mode_input = QComboBox()
        self.enforcement_mode_input.setObjectName("settingsEnforcementModeCombo")
        self.enforcement_mode_input.addItem("Preview Only", "preview_only")
        self.enforcement_mode_input.addItem("Armed Dry Run", "armed_dry_run")
        self.enforcement_mode_input.addItem(
            "Real Process Blocking", "real_process_blocking"
        )
        self.enforcement_mode_input.addItem("Real Hosts Blocking", "real_hosts_blocking")
        self.enforcement_mode_input.addItem("Full Enforcement", "full_enforcement")
        self.next_enforcement_step_label = make_muted_label("")
        self.next_enforcement_step_label.setObjectName(
            "settingsNextEnforcementStepLabel"
        )
        self.real_enforcement_label = QLabel("Real Enforcement: Locked / Not Ready")
        self.background_monitor_label = QLabel("Background monitor/service: Not Ready")
        self.browser_connector_label = QLabel("Browser connector: Not Ready")
        self.hosts_readiness_detail_label = make_muted_label(
            "Hosts readiness audit: Not Ready. Websites are not blocked yet."
        )
        self.browser_integration_status_label = make_muted_label(
            "Chrome Extension: Disconnected. Browser HIGH safety: Not ready."
        )
        self.browser_integration_status_label.setObjectName(
            "settingsBrowserIntegrationStatusLabel"
        )
        self.blocking_adapters_label = make_muted_label(
            "Blocking adapters: dry-run locked / deferred"
        )
        self.armed_dry_run_note_label = make_muted_label(
            "Armed Dry Run monitors matching app rules and logs would-block "
            "decisions without blocking. Real process blocking closes explicit "
            "blocked app rules only. Real hosts blocking writes only LoopGuard "
            "managed hosts entries for domain rules. Hosts blocking uses exact "
            "domains; bare domains also include www. It does not block URL "
            "paths, and exact subdomains may need their own rule. Browser redirect "
            "blocking controls only extension-connected browser sessions. Browser "
            "HIGH safety: Partial, not Trusted. Incognito is controlled only if "
            "the extension is allowed there, and other browsers are not "
            "browser-controlled yet. Website HIGH requires trusted Chrome "
            "extension control; if Incognito is not allowed, website HIGH stays "
            "blocked at hosts level. Other browsers are closed while website "
            "HIGH is active. Firewall blocking is not implemented. Hosts changes may require "
            "browser refresh, closing existing tabs, or manual DNS cache flush "
            "(ipconfig /flushdns). LoopGuard does not flush DNS automatically. "
            "URLs are sent only to the local native host; there is no telemetry, "
            "cloud, network service, or localhost HTTP API. "
            "Manual hosts test: confirm Real Hosts Blocking is active, confirm "
            "the target appears under LoopGuard markers in hosts, run ping "
            "domain.com, expect 127.0.0.1, then test in Incognito or after "
            "closing existing tabs/browser. Full Enforcement combines explicit "
            "app process blocking with website domain hosts blocking; it does "
            "not add URL/path, browser policy, firewall, or DNS flush behavior. "
            "Full Enforcement manual QA: use a disposable app such as notepad.exe "
            "with a HIGH app rule in LOW, ping reddit.com and www.reddit.com for "
            "127.0.0.1, remember normal Chrome may need refresh/tab close/Incognito, "
            "and switch to Preview Only to clear LoopGuard hosts markers. "
            "Safe Mode and Recovery Mode disable enforcement. "
            "Protected system and LoopGuard processes are never closed. Test "
            "safely with a disposable app such as notepad.exe first."
        )
        self.armed_dry_run_note_label.setObjectName("settingsArmedDryRunNoteLabel")
        for label in (
            self.enforcement_mode_label,
            self.real_enforcement_label,
            self.background_monitor_label,
            self.browser_connector_label,
            self.hosts_readiness_detail_label,
            self.browser_integration_status_label,
        ):
            label.setWordWrap(True)
        mode_row = QHBoxLayout()
        reset_layout(mode_row)
        mode_row.setSpacing(MEDIUM_GAP)
        mode_row.addWidget(QLabel("Mode"))
        mode_row.addWidget(self.enforcement_mode_input)
        mode_row.addStretch(1)
        readiness_layout.addWidget(self.enforcement_mode_label)
        readiness_layout.addLayout(mode_row)
        readiness_layout.addWidget(self.next_enforcement_step_label)
        readiness_layout.addWidget(self.real_enforcement_label)
        readiness_layout.addWidget(self.background_monitor_label)
        readiness_layout.addWidget(self.browser_connector_label)
        readiness_layout.addWidget(self.hosts_readiness_detail_label)
        readiness_layout.addWidget(self.browser_integration_status_label)
        readiness_layout.addWidget(self.blocking_adapters_label)
        readiness_layout.addWidget(self.armed_dry_run_note_label)
        self.advanced_diagnostics_panel_layout.addWidget(readiness_card)

        (
            personal_readiness_card,
            personal_readiness_layout,
            self.personal_readiness_card_title_label,
        ) = _build_card("Personal-use readiness")
        self.personal_readiness_card = personal_readiness_card
        set_card_role(personal_readiness_card, "list")
        self.personal_readiness_verdict_label = QLabel(
            "Ready for personal trial: Not ready"
        )
        self.personal_readiness_verdict_label.setObjectName(
            "settingsPersonalReadinessVerdictLabel"
        )
        self.personal_readiness_desktop_label = make_muted_label(
            "Desktop blocking: Not ready"
        )
        self.personal_readiness_desktop_label.setObjectName(
            "settingsPersonalReadinessDesktopLabel"
        )
        self.personal_readiness_browser_label = make_muted_label(
            "Browser blocking: Not ready"
        )
        self.personal_readiness_browser_label.setObjectName(
            "settingsPersonalReadinessBrowserLabel"
        )
        self.personal_readiness_recovery_label = make_muted_label(
            "Recovery/Safety: Not ready"
        )
        self.personal_readiness_recovery_label.setObjectName(
            "settingsPersonalReadinessRecoveryLabel"
        )
        self.personal_readiness_manual_qa_label = make_muted_label(
            "Manual QA: Not verified manually"
        )
        self.personal_readiness_manual_qa_label.setObjectName(
            "settingsPersonalReadinessManualQaLabel"
        )
        for label in (
            self.personal_readiness_verdict_label,
            self.personal_readiness_desktop_label,
            self.personal_readiness_browser_label,
            self.personal_readiness_recovery_label,
            self.personal_readiness_manual_qa_label,
        ):
            label.setWordWrap(True)
        personal_readiness_layout.addWidget(self.personal_readiness_verdict_label)
        personal_readiness_layout.addWidget(self.personal_readiness_desktop_label)
        personal_readiness_layout.addWidget(self.personal_readiness_browser_label)
        personal_readiness_layout.addWidget(self.personal_readiness_recovery_label)
        personal_readiness_layout.addWidget(self.personal_readiness_manual_qa_label)
        self.advanced_diagnostics_panel_layout.addWidget(personal_readiness_card)

        (
            personal_trial_qa_card,
            personal_trial_qa_layout,
            self.personal_trial_qa_card_title_label,
        ) = _build_card("Personal Trial QA")
        self.personal_trial_qa_card = personal_trial_qa_card
        set_card_role(personal_trial_qa_card, "list")
        self.personal_trial_qa_verdict_label = QLabel("Personal Trial QA: Not ready")
        self.personal_trial_qa_verdict_label.setObjectName(
            "settingsPersonalTrialQaVerdictLabel"
        )
        self.personal_trial_qa_verdict_label.setWordWrap(True)
        self.personal_trial_qa_note_label = make_muted_label(
            "Reset and re-run after setup/enforcement changes."
        )
        self.personal_trial_qa_note_label.setObjectName(
            "settingsPersonalTrialQaNoteLabel"
        )
        self.personal_trial_qa_checkboxes: dict[str, QCheckBox] = {}
        personal_trial_qa_layout.addWidget(self.personal_trial_qa_verdict_label)
        for item in self.service.get_personal_trial_qa_checklist().items:
            checkbox = QCheckBox(item.label)
            checkbox.setObjectName(
                "settingsPersonalTrialQa" + _object_name_suffix(item.key)
            )
            checkbox.toggled.connect(
                lambda checked, key=item.key: self._set_personal_trial_qa_item(
                    key,
                    checked,
                )
            )
            self.personal_trial_qa_checkboxes[item.key] = checkbox
            personal_trial_qa_layout.addWidget(checkbox)
        self.reset_personal_trial_qa_button = QPushButton("Reset QA checklist")
        self.reset_personal_trial_qa_button.setObjectName(
            "resetPersonalTrialQaButton"
        )
        set_button_role(self.reset_personal_trial_qa_button, "quiet")
        self.reset_personal_trial_qa_button.clicked.connect(
            self._reset_personal_trial_qa_checklist
        )
        personal_trial_qa_layout.addWidget(self.personal_trial_qa_note_label)
        personal_trial_qa_layout.addWidget(self.reset_personal_trial_qa_button)
        self.advanced_diagnostics_panel_layout.addWidget(personal_trial_qa_card)

        recovery_card, recovery_layout, self.recovery_card_title_label = _build_card(
            "Emergency recovery"
        )
        set_card_role(recovery_card, "secondary")
        self.recovery_scripts_label = QLabel(
            "Recovery scripts: "
            f"{_status_for_path(_repo_root() / 'scripts' / 'recovery_status.ps1')}"
        )
        self.recovery_adapter_label = QLabel("Recovery adapter: Available")
        self.recovery_scripts_label.setWordWrap(True)
        self.recovery_adapter_label.setWordWrap(True)
        self.open_recovery_folder_button = QPushButton("Open recovery folder")
        self.open_recovery_folder_button.setObjectName("openRecoveryFolderButton")
        set_button_role(self.open_recovery_folder_button, "quiet")
        self.open_recovery_folder_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.open_recovery_folder_button.clicked.connect(self._open_recovery_folder)
        self.safe_actions_label = make_muted_label(self._recovery_idle_note)
        self.recovery_status_label = make_muted_label("")
        self.recovery_status_label.setObjectName("settingsRecoveryStatusLabel")
        recovery_action_row = QHBoxLayout()
        reset_layout(recovery_action_row)
        recovery_action_row.setSpacing(MEDIUM_GAP)
        recovery_action_row.addWidget(self.open_recovery_folder_button)
        recovery_action_row.addStretch(1)
        recovery_layout.addWidget(
            make_subpanel(
                make_status_row("Scripts", self.recovery_scripts_label),
                make_status_row("Adapter", self.recovery_adapter_label),
                _panel_from_layout(recovery_action_row),
                self.safe_actions_label,
                role="compact",
            )
        )
        recovery_layout.addWidget(self.recovery_status_label)

        (
            strictness_card,
            strictness_layout,
            self.surrender_strictness_card_title_label,
        ) = _build_card("Surrender Strictness")
        set_card_role(strictness_card, "settings")
        self.surrender_strictness_label = QLabel("Surrender strictness")
        self.surrender_strictness_label.setWordWrap(True)
        self.surrender_strictness_input = QComboBox()
        self.surrender_strictness_input.addItem("LOW", "low")
        self.surrender_strictness_input.addItem("MEDIUM", "medium")
        self.surrender_strictness_input.addItem("HIGH", "high")
        self.surrender_strictness_help_label = make_muted_label(
            "LOW = surrender after 3h. MEDIUM = surrender after 6h. "
            "HIGH = surrender after 9h."
        )
        strictness_row = QHBoxLayout()
        reset_layout(strictness_row)
        strictness_row.setSpacing(MEDIUM_GAP)
        strictness_row.addWidget(self.surrender_strictness_label)
        strictness_row.addWidget(self.surrender_strictness_input)
        strictness_row.addStretch(1)
        strictness_layout.addWidget(
            make_subpanel(
                _panel_from_layout(strictness_row),
                self.surrender_strictness_help_label,
                role="metric",
            )
        )
        current_strictness = self.service.get_surrender_strictness()
        self.surrender_strictness_input.setCurrentIndex(
            self.surrender_strictness_input.findData(current_strictness)
        )
        self.surrender_strictness_input.currentIndexChanged.connect(
            self._set_surrender_strictness
        )
        self.enforcement_mode_input.currentIndexChanged.connect(
            self._set_enforcement_mode
        )

        (
            soft_start_card,
            soft_start_layout,
            self.soft_start_card_title_label,
        ) = _build_card("Soft Start")
        set_card_role(soft_start_card, "settings")
        self.soft_start_enabled_input = QCheckBox("Enable Soft Start")
        self.soft_start_duration_label = QLabel("Soft Start duration")
        self.soft_start_duration_label.setWordWrap(True)
        self.soft_start_duration_input = QSpinBox()
        self.soft_start_duration_input.setRange(0, 60)
        self.soft_start_duration_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.soft_start_duration_input.setKeyboardTracking(False)
        self.soft_start_duration_input.setMinimumWidth(72)
        self.soft_start_duration_input.setMaximumWidth(96)
        self.soft_start_minutes_label = QLabel("minutes")
        self.soft_start_minutes_label.setWordWrap(False)
        self.soft_start_help_label = make_muted_label(self._soft_start_idle_help)
        soft_start_enabled_row = QHBoxLayout()
        reset_layout(soft_start_enabled_row)
        soft_start_enabled_row.setSpacing(MEDIUM_GAP)
        soft_start_enabled_row.addWidget(self.soft_start_enabled_input)
        soft_start_enabled_row.addStretch(1)
        soft_start_row = QHBoxLayout()
        reset_layout(soft_start_row)
        soft_start_row.setSpacing(MEDIUM_GAP)
        soft_start_row.addWidget(self.soft_start_duration_label)
        soft_start_row.addWidget(self.soft_start_duration_input)
        soft_start_row.addWidget(self.soft_start_minutes_label)
        soft_start_row.addStretch(1)
        soft_start_layout.addWidget(
            make_subpanel(
                _panel_from_layout(soft_start_enabled_row),
                _panel_from_layout(soft_start_row),
                self.soft_start_help_label,
                role="metric",
            )
        )
        self.soft_start_enabled_input.toggled.connect(self._set_soft_start_enabled)
        self.soft_start_duration_input.valueChanged.connect(
            self._set_soft_start_duration
        )

        (
            daily_recreation_cap_card,
            daily_recreation_cap_layout,
            self.daily_recreation_cap_card_title_label,
        ) = _build_card("Daily Recreation Cap")
        set_card_role(daily_recreation_cap_card, "settings")
        self.daily_recreation_cap_label = QLabel("Daily Recreation cap")
        self.daily_recreation_cap_label.setWordWrap(True)
        self.daily_recreation_cap_input = QSpinBox()
        self.daily_recreation_cap_input.setRange(
            DAILY_RECREATION_CAP_MIN_MINUTES,
            DAILY_RECREATION_CAP_MAX_MINUTES,
        )
        self.daily_recreation_cap_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.daily_recreation_cap_input.setKeyboardTracking(False)
        self.daily_recreation_cap_input.setMinimumWidth(72)
        self.daily_recreation_cap_input.setMaximumWidth(96)
        self.daily_recreation_cap_minutes_label = QLabel("minutes")
        self.daily_recreation_cap_minutes_label.setWordWrap(False)
        self.daily_recreation_cap_help_label = make_muted_label(
            self._daily_recreation_cap_idle_help
        )
        daily_recreation_cap_row = QHBoxLayout()
        reset_layout(daily_recreation_cap_row)
        daily_recreation_cap_row.setSpacing(MEDIUM_GAP)
        daily_recreation_cap_row.addWidget(self.daily_recreation_cap_label)
        daily_recreation_cap_row.addWidget(self.daily_recreation_cap_input)
        daily_recreation_cap_row.addWidget(self.daily_recreation_cap_minutes_label)
        daily_recreation_cap_row.addStretch(1)
        daily_recreation_cap_layout.addWidget(
            make_subpanel(
                _panel_from_layout(daily_recreation_cap_row),
                self.daily_recreation_cap_help_label,
                role="metric",
            )
        )
        layout.addLayout(_settings_card_row(runtime_card, browser_setup_card))
        layout.addLayout(
            _settings_card_row(configuration_backup_card, daily_recreation_cap_card)
        )
        layout.addLayout(_settings_card_row(strictness_card, soft_start_card))
        layout.addLayout(_settings_card_row(recovery_card, advanced_diagnostics_card))

        self.daily_recreation_cap_input.valueChanged.connect(
            self._set_daily_recreation_cap_minutes
        )
        self._refresh_surrender_strictness_controls()
        self._refresh_soft_start_controls()
        self._refresh_daily_recreation_cap_controls()
        self._refresh_configuration_backup_controls()
        self._refresh_recovery_controls()
        self._refresh_enforcement_controls()
        self._refresh_browser_setup_controls()
        self._refresh_personal_trial_qa_controls()
        self._apply_production_visibility()

        layout.addStretch(1)

    def showEvent(self, event) -> None:
        self.refresh()
        self.browser_setup_refresh_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self.browser_setup_refresh_timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        """Refresh Settings status from the service."""
        self._refresh_enforcement_controls()
        self._refresh_browser_setup_controls()
        self._refresh_surrender_strictness_controls()
        self._refresh_soft_start_controls()
        self._refresh_daily_recreation_cap_controls()
        self._refresh_configuration_backup_controls()
        self._refresh_recovery_controls()
        self._refresh_personal_trial_qa_controls()
        self._apply_production_visibility()

    def _apply_production_visibility(self) -> None:
        if not self.production_mode:
            return
        self.runtime_card.setVisible(False)
        self.advanced_diagnostics_card.setVisible(False)
        self.readiness_card.setVisible(False)
        self.personal_readiness_card.setVisible(False)
        self.personal_trial_qa_card.setVisible(False)

    def _toggle_advanced_diagnostics(self) -> None:
        visible = self.advanced_diagnostics_panel.isHidden()
        self.advanced_diagnostics_panel.setVisible(visible)
        self.advanced_diagnostics_toggle_button.setText(
            "Hide advanced diagnostics" if visible else "Show advanced diagnostics"
        )

    def _set_surrender_strictness(self) -> None:
        data = self.surrender_strictness_input.currentData()
        if isinstance(data, str):
            try:
                self.service.set_surrender_strictness(data)
            except ValueError as error:
                self.surrender_strictness_help_label.setText(str(error))
                self._refresh_surrender_strictness_controls()

    def _set_enforcement_mode(self) -> None:
        data = self.enforcement_mode_input.currentData()
        if not isinstance(data, str):
            return
        try:
            self.service.set_enforcement_mode(data)
        except ValueError as error:
            self.next_enforcement_step_label.setText(str(error))
        self._refresh_enforcement_controls()

    def _set_soft_start_enabled(self, enabled: bool) -> None:
        try:
            self.service.set_soft_start_enabled(enabled)
        except ValueError as error:
            self.soft_start_help_label.setText(str(error))
            self._refresh_soft_start_controls()

    def _set_soft_start_duration(self, minutes: int) -> None:
        try:
            self.service.set_soft_start_duration_minutes(minutes)
        except ValueError as error:
            self.soft_start_help_label.setText(str(error))
            self._refresh_soft_start_controls()

    def _set_daily_recreation_cap_minutes(self, minutes: int) -> None:
        try:
            self.service.set_daily_recreation_cap_minutes(minutes)
        except ValueError as error:
            self.daily_recreation_cap_help_label.setText(str(error))
            self._refresh_daily_recreation_cap_controls()

    def _set_personal_trial_qa_item(self, step_key: str, checked: bool) -> None:
        try:
            self.service.set_personal_trial_qa_item(step_key, checked)
        except ValueError as error:
            self.personal_trial_qa_verdict_label.setText(str(error))
        self._refresh_personal_trial_qa_controls()
        self._refresh_enforcement_controls()

    def _reset_personal_trial_qa_checklist(self) -> None:
        self.service.reset_personal_trial_qa_checklist()
        self._refresh_personal_trial_qa_controls()
        self._refresh_enforcement_controls()

    def _refresh_personal_trial_qa_controls(self) -> None:
        checklist = self.service.get_personal_trial_qa_checklist()
        self.personal_trial_qa_verdict_label.setText(
            "Personal Trial QA: "
            f"{_readable_personal_trial_qa_status(checklist.status)} - "
            f"{checklist.message}"
        )
        for item in checklist.items:
            checkbox = self.personal_trial_qa_checkboxes.get(item.key)
            if checkbox is None:
                continue
            blocked = checkbox.blockSignals(True)
            try:
                checkbox.setChecked(item.checked)
            finally:
                checkbox.blockSignals(blocked)

    def _refresh_soft_start_controls(self) -> None:
        snapshot = self.service.dashboard_snapshot()
        enabled_blocked = self.soft_start_enabled_input.blockSignals(True)
        duration_blocked = self.soft_start_duration_input.blockSignals(True)
        try:
            self.soft_start_enabled_input.setChecked(
                self.service.get_soft_start_enabled()
            )
            self.soft_start_duration_input.setValue(
                self.service.get_soft_start_duration_minutes()
            )
        finally:
            self.soft_start_enabled_input.blockSignals(enabled_blocked)
            self.soft_start_duration_input.blockSignals(duration_blocked)

        editable = not snapshot.day_started
        tooltip = "" if editable else SOFT_START_LOCKED_AFTER_START_DAY
        for widget in (
            self.soft_start_enabled_input,
            self.soft_start_duration_input,
            self.soft_start_duration_label,
            self.soft_start_minutes_label,
        ):
            widget.setEnabled(editable)
            widget.setToolTip(tooltip)
        self.soft_start_help_label.setText(
            self._soft_start_idle_help
            if editable
            else SOFT_START_LOCKED_AFTER_START_DAY
        )

    def _refresh_daily_recreation_cap_controls(self) -> None:
        snapshot = self.service.dashboard_snapshot()
        blocked = self.daily_recreation_cap_input.blockSignals(True)
        try:
            self.daily_recreation_cap_input.setValue(
                self.service.get_daily_recreation_cap_minutes()
            )
        finally:
            self.daily_recreation_cap_input.blockSignals(blocked)

        editable = not snapshot.day_started or snapshot.day_closed
        tooltip = "" if editable else SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY
        for widget in (
            self.daily_recreation_cap_input,
            self.daily_recreation_cap_label,
            self.daily_recreation_cap_minutes_label,
        ):
            widget.setEnabled(editable)
            widget.setToolTip(tooltip)
        self.daily_recreation_cap_help_label.setText(
            self._daily_recreation_cap_idle_help
            if editable
            else SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY
        )

    def _refresh_configuration_backup_controls(self) -> None:
        snapshot = self.service.dashboard_snapshot()
        editable = not snapshot.day_started or snapshot.day_closed
        tooltip = "" if editable else self._configuration_backup_active_day_lock_text
        for button in (
            self.export_configuration_button,
            self.import_configuration_button,
        ):
            button.setEnabled(editable)
            button.setToolTip(tooltip)
        if editable:
            self.configuration_backup_helper_label.setText(
                self._configuration_backup_helper_text
            )
            if (
                self.configuration_backup_status_label.text()
                == self._configuration_backup_active_day_lock_text
            ):
                self.configuration_backup_status_label.setText("")
            return
        self.configuration_backup_helper_label.setText(
            self._configuration_backup_helper_text
        )
        self.configuration_backup_status_label.setText(
            self._configuration_backup_active_day_lock_text
        )

    def _refresh_recovery_controls(self) -> None:
        snapshot = self.service.dashboard_snapshot()
        active_day = snapshot.day_started and not snapshot.day_closed
        self.safe_actions_label.setText(
            self._recovery_active_day_note if active_day else self._recovery_idle_note
        )

    def _refresh_surrender_strictness_controls(self) -> None:
        snapshot = self.service.dashboard_snapshot()
        current_strictness = self.service.get_surrender_strictness()
        blocked = self.surrender_strictness_input.blockSignals(True)
        try:
            index = self.surrender_strictness_input.findData(current_strictness)
            if index >= 0:
                self.surrender_strictness_input.setCurrentIndex(index)
        finally:
            self.surrender_strictness_input.blockSignals(blocked)

        editable = not snapshot.day_started or snapshot.day_closed
        tooltip = "" if editable else SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY
        self.surrender_strictness_input.setEnabled(editable)
        self.surrender_strictness_input.setToolTip(tooltip)
        self.surrender_strictness_label.setToolTip(tooltip)
        self.surrender_strictness_help_label.setText(
            (
                "LOW = surrender after 3h. MEDIUM = surrender after 6h. "
                "HIGH = surrender after 9h."
            )
            if editable
            else SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY
        )

    def _refresh_enforcement_controls(self) -> None:
        status = self.service.get_enforcement_status()
        selected_index = self.enforcement_mode_input.findData(
            status.selected_mode.value
        )
        if selected_index < 0:
            selected_index = self.enforcement_mode_input.findData("preview_only")
        blocked = self.enforcement_mode_input.blockSignals(True)
        try:
            self.enforcement_mode_input.setCurrentIndex(selected_index)
        finally:
            self.enforcement_mode_input.blockSignals(blocked)
        for option in status.mode_options:
            index = self.enforcement_mode_input.findData(option.mode.value)
            item = self.enforcement_mode_input.model().item(index) if index >= 0 else None
            if item is not None:
                item.setEnabled(option.enabled)
                self.enforcement_mode_input.setItemData(
                    index,
                    option.reason,
                    Qt.ItemDataRole.ToolTipRole,
                )

        self.test_mode_label.setText("Test Mode: ON / Locked")
        self.safe_mode_label.setText(
            f"Safe Mode: {'ON' if self.settings.safe_mode else 'OFF'}"
        )
        self.recovery_mode_label.setText(
            f"Recovery Mode: {'ON' if self.settings.recovery_mode else 'OFF'}"
        )
        self.enforcement_mode_label.setText(
            "Current mode: "
            f"{_readable_mode(status.effective_mode.value)}"
        )
        self.next_enforcement_step_label.setText(status.next_step)
        self.background_monitor_label.setText(
            _readiness_status_line("Real Process Blocking", status.process_readiness)
        )
        self.browser_connector_label.setText(
            _readiness_status_line("Real Hosts Blocking", status.hosts_readiness)
        )
        self.hosts_readiness_detail_label.setText(
            _hosts_readiness_detail(status.hosts_readiness)
        )
        release_status = self.service.get_website_high_release_status()
        self.browser_integration_status_label.setText(
            _browser_integration_detail(
                self.service.get_browser_integration_status(),
                release_status,
            )
        )
        self.real_enforcement_label.setText(
            _readiness_status_line("Full Enforcement", status.full_readiness)
        )
        self.blocking_adapters_label.setText(
            "Missing readiness: " + _missing_readiness_summary(status)
        )
        checklist = self.service.get_personal_use_readiness_checklist()
        self.personal_readiness_verdict_label.setText(
            "Ready for personal trial: "
            f"{_readable_personal_readiness_verdict(checklist.verdict)}. "
            f"{checklist.summary}"
        )
        self.personal_readiness_desktop_label.setText(
            "Desktop blocking: " + _personal_readiness_group_line(
                checklist,
                ("Enforcement mode", "Process blocking", "Hosts blocking"),
            )
        )
        self.personal_readiness_browser_label.setText(
            "Browser blocking: " + _personal_readiness_group_line(
                checklist,
                (
                    "Browser extension",
                    "Incognito",
                    "DNR",
                    "YouTube SPA detector",
                    "Browser path rules",
                    "Browser attempt logging",
                ),
            )
        )
        self.personal_readiness_recovery_label.setText(
            "Recovery/Safety: " + _personal_readiness_group_line(
                checklist,
                ("Recovery/Safe Mode",),
            )
        )
        self.personal_readiness_manual_qa_label.setText(
            f"Manual QA: {checklist.manual_qa_status}"
        )

    def _open_app_data_folder(self) -> None:
        self._open_diagnostic_folder(self.settings.data_dir)

    def _open_database_folder(self) -> None:
        self._open_diagnostic_folder(self.settings.db_path.parent)

    def _open_recovery_folder(self) -> None:
        folder = _recovery_scripts_folder()
        if not folder.exists():
            self.recovery_status_label.setText(f"Folder not found: {folder}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.recovery_status_label.setText(
            f"Opened: {folder}" if opened else f"Could not open: {folder}"
        )

    def _show_browser_setup_dialog(self) -> None:
        dialog = BrowserSetupDialog(
            extension_folder=_browser_extension_folder(),
            open_chrome_extensions_page_action=self.open_chrome_extensions_page_action,
            open_extension_folder_action=self.open_extension_folder_action,
            refresh_status_action=self._browser_setup_dialog_status,
            repair_native_host_action=self._repair_chrome_native_host,
            parent=self,
        )
        dialog.exec()

    def show_browser_setup_guidance(self) -> None:
        """Focus the production browser setup guidance without mutating Chrome."""
        self._browser_setup_intro_skipped_this_session = False
        self.refresh()
        self.browser_setup_status_note_label.setText(
            "Use Set up Chrome to load the bundled extension manually, then "
            "refresh status."
        )
        self._show_browser_setup_dialog()

    def _browser_setup_dialog_status(self) -> str:
        self.refresh()
        return (
            _browser_setup_status_line(self.service.get_browser_integration_status())
            + " Next action: "
            + _browser_setup_next_action(self.service.get_browser_integration_status())
        )

    def _open_chrome_extensions_page(self) -> None:
        self._show_browser_setup_action_result(
            self.open_chrome_extensions_page_action()
        )

    def _open_extension_folder(self) -> None:
        self._show_browser_setup_action_result(
            self.open_extension_folder_action(_browser_extension_folder())
        )

    def _show_browser_setup_action_result(
        self,
        result: BrowserSetupActionResult,
    ) -> None:
        if result.copy_text:
            QApplication.clipboard().setText(result.copy_text)
        self.browser_setup_status_note_label.setText(result.reason)

    def _skip_browser_setup_intro(self) -> None:
        self._browser_setup_intro_skipped_this_session = True
        self.browser_setup_intro_panel.setVisible(False)
        self.browser_setup_status_note_label.setText(
            "Browser setup guidance skipped for this session."
        )

    def _dismiss_browser_setup_intro(self) -> None:
        self.service.mark_browser_setup_intro_seen()
        self.browser_setup_intro_panel.setVisible(False)
        self.browser_setup_status_note_label.setText(
            "Browser setup guidance will stay available here."
        )

    def _toggle_manual_browser_setup(self) -> None:
        visible = self.manual_browser_setup_panel.isHidden()
        self.manual_browser_setup_panel.setVisible(visible)
        self.manual_browser_setup_toggle_button.setText(
            "Hide manual setup" if visible else "Show manual setup"
        )
        self._refresh_browser_setup_registration_state()

    def _repair_chrome_native_host(self) -> str:
        custom_id = self.browser_setup_extension_id_input.text().strip()
        extension_id = (
            custom_id
            if custom_id and not self.manual_browser_setup_panel.isHidden()
            else LOOPGUARD_CHROME_EXTENSION_ID
        )
        try:
            normalized_extension_id = normalize_extension_id(extension_id)
        except ValueError as error:
            self.browser_setup_status_note_label.setText(str(error))
            self._refresh_browser_setup_registration_state()
            return str(error)
        answer = QMessageBox.question(
            self,
            "Repair connection",
            "Repair LoopGuard's Chrome connection under HKCU?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.browser_setup_status_note_label.setText(
                "Native host repair cancelled."
            )
            return "Native host repair cancelled."

        result = self.browser_setup_registrar.repair_native_host(
            browser="chrome",
            extension_id=normalized_extension_id,
        )
        self.browser_setup_status_note_label.setText(result.reason)
        self._refresh_browser_setup_registration_state()
        return result.reason

    def _unregister_chrome_native_host(self) -> None:
        answer = QMessageBox.question(
            self,
            "Unregister native host",
            "Remove LoopGuard native host registration for Chrome from HKCU?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.browser_setup_status_note_label.setText(
                "Native host unregister cancelled."
            )
            return

        result = self.browser_setup_registrar.unregister_native_host(browser="chrome")
        self.browser_setup_status_note_label.setText(result.reason)
        self._refresh_browser_setup_registration_state()

    def _open_diagnostic_folder(self, folder: Path) -> None:
        if not folder.exists():
            self.diagnostics_status_label.setText(f"Folder not found: {folder}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self.diagnostics_status_label.setText(
            f"Opened: {folder}" if opened else f"Could not open: {folder}"
        )

    def _export_configuration(self) -> None:
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export configuration",
            "selfboss-configuration.json",
            "JSON files (*.json)",
        )
        if not file_name:
            return
        try:
            payload = self.service.export_configuration()
            Path(file_name).write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except (OSError, ValueError) as error:
            self.configuration_backup_status_label.setText(
                f"Export failed: {error}"
            )
            return
        self.configuration_backup_status_label.setText(
            f"Exported configuration: {Path(file_name).name}"
        )

    def _import_configuration(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import configuration",
            "",
            "JSON files (*.json)",
        )
        if not file_name:
            return
        try:
            raw_json = Path(file_name).read_text(encoding="utf-8")
            preview = self.service.preview_configuration_import(raw_json)
        except (OSError, ValueError) as error:
            self.configuration_backup_status_label.setText(
                f"Import blocked: {error}"
            )
            return

        answer = QMessageBox.question(
            self,
            "Import configuration",
            (
                "Import configuration? This will replace rules and update "
                "supported settings.\n"
                f"Rules: {preview.rule_count}\n"
                f"Settings: {preview.setting_count}"
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.configuration_backup_status_label.setText(
                "Configuration import cancelled."
            )
            return

        try:
            result = self.service.import_configuration(raw_json)
        except ValueError as error:
            self.configuration_backup_status_label.setText(
                f"Import blocked: {error}"
            )
            return
        self.configuration_backup_status_label.setText(result.message)
        self.refresh()

    def _refresh_browser_setup_controls(self) -> None:
        status = self.service.get_browser_integration_status()
        self.browser_setup_status_label.setText(_browser_setup_status_line(status))
        self.browser_setup_status_label.setToolTip(
            self.browser_setup_status_label.text()
        )
        self._refresh_browser_setup_summary(status)
        self._refresh_browser_setup_intro_controls(status)
        self.browser_setup_next_action_label.setText(
            "Next action: " + _browser_setup_next_action(status)
        )
        self._refresh_browser_setup_registration_state()

    def _refresh_browser_setup_intro_controls(self, status) -> None:
        connection = getattr(status, "connection_status", "disconnected")
        visible = (
            self.production_mode
            and not self._browser_setup_intro_skipped_this_session
            and not self.service.has_seen_browser_setup_intro()
            and connection != "connected"
        )
        self.browser_setup_intro_panel.setVisible(visible)

    def _refresh_browser_setup_summary(self, status) -> None:
        connection = getattr(status, "connection_status", "disconnected")
        incognito = getattr(status, "incognito_status", "unknown")
        native_host = getattr(status, "native_messaging_status", "unknown")
        dnr = getattr(status, "dnr_status", "unknown")
        youtube_spa = getattr(status, "youtube_spa_status", "unknown")
        _set_status_pill(
            self.browser_setup_chrome_status_label,
            _readable_browser_setup_connection(connection),
            _browser_connection_role(connection),
        )
        _set_status_pill(
            self.browser_setup_incognito_status_label,
            _readable_incognito_status(incognito),
            _incognito_role(incognito),
        )
        _set_status_pill(
            self.browser_setup_native_host_status_label,
            _readable_browser_setup_native_host(native_host),
            _native_host_role(native_host),
        )
        _set_status_pill(
            self.browser_setup_dnr_status_label,
            _readable_dnr_status(status),
            _dnr_role(dnr),
        )
        _set_status_pill(
            self.browser_setup_youtube_status_label,
            _readable_youtube_spa_status(youtube_spa),
            _youtube_spa_role(youtube_spa),
        )

    def _refresh_browser_setup_registration_state(self) -> None:
        custom_id = self.browser_setup_extension_id_input.text().strip()
        if custom_id and not self.manual_browser_setup_panel.isHidden():
            try:
                normalize_extension_id(custom_id)
            except ValueError as error:
                self.repair_native_host_button.setEnabled(False)
                self.repair_native_host_button.setToolTip(str(error))
                return
        self.repair_native_host_button.setEnabled(True)
        self.repair_native_host_button.setToolTip(
            "Repair the local Chrome connection under HKCU using the bundled "
            "extension ID."
        )


def _repo_root() -> Path:
    return app_resource_root()


def _panel_from_layout(inner_layout) -> QWidget:
    panel = QWidget()
    panel.setObjectName("SettingsInlinePanel")
    layout = QVBoxLayout(panel)
    reset_layout(layout)
    layout.addLayout(inner_layout)
    return panel


def _set_status_pill(label: QLabel, text: str, role: str) -> None:
    label.setText(text)
    label.setProperty("role", role)
    label.style().unpolish(label)
    label.style().polish(label)


def _browser_connection_role(value: str) -> str:
    if value == "connected":
        return "success"
    if value == "partial":
        return "warning"
    if value == "stale":
        return "warning"
    return "danger"


def _incognito_role(value: str) -> str:
    if value == "allowed":
        return "success"
    if value == "not_allowed":
        return "danger"
    return "neutral"


def _native_host_role(value: str) -> str:
    if value == "connected":
        return "success"
    if value == "not_connected":
        return "danger"
    return "neutral"


def _dnr_role(value: str) -> str:
    if value == "active":
        return "success"
    if value == "supported_no_rules":
        return "warning"
    if value in {"unavailable", "error"}:
        return "danger"
    return "neutral"


def _youtube_spa_role(value: str) -> str:
    if value == "seen":
        return "success"
    if value == "not_seen":
        return "warning"
    return "neutral"


def _browser_extension_folder() -> Path:
    return browser_extension_folder()


def _recovery_scripts_folder() -> Path:
    return recovery_scripts_folder()


def _status_for_path(path: Path) -> str:
    return "Available" if path.exists() else "Not found"


def _readable_personal_readiness_verdict(value: str) -> str:
    if value == "ready_for_personal_trial":
        return "Ready for personal trial"
    if value == "partial":
        return "Partial"
    return "Not ready"


def _readable_personal_trial_qa_status(value: str) -> str:
    if value == "complete":
        return "Ready"
    if value == "partial":
        return "Partial"
    return "Not ready"


def _object_name_suffix(value: str) -> str:
    words = [word for word in value.split("_") if word]
    return "".join(word[:1].upper() + word[1:] for word in words)


def _personal_readiness_group_line(checklist, labels: tuple[str, ...]) -> str:
    items = {item.label: item for item in getattr(checklist, "items", ())}
    parts = [
        f"{label}: {items[label].status}"
        for label in labels
        if label in items
    ]
    return "; ".join(parts) if parts else "Unknown"


def _readable_mode(value: str) -> str:
    return value.replace("_", " ").title()


def _readiness_status_line(label: str, group) -> str:
    state = "Ready" if group.ready else "Locked / Not Ready"
    return f"{label}: {state}"


def _missing_readiness_summary(status) -> str:
    missing: list[str] = []
    for group in (
        status.process_readiness,
        status.hosts_readiness,
        status.recovery_readiness,
    ):
        missing.extend(group.missing_items)
    return "; ".join(missing[:4]) if missing else "none"


def _browser_integration_detail(status, release_status) -> str:
    connection = _readable_mode(getattr(status, "connection_status", "disconnected"))
    native_messaging = _readable_native_messaging_status(
        getattr(status, "native_messaging_status", "unknown")
    )
    incognito = _readable_incognito_status(
        getattr(status, "incognito_status", "unknown")
    )
    dnr = _readable_dnr_status(status)
    youtube_spa = _readable_youtube_spa_status(
        getattr(status, "youtube_spa_status", "unknown")
    )
    safety = _readable_browser_high_safety(
        getattr(status, "browser_high_safety", "not_ready")
    )
    age = _format_heartbeat_age(getattr(status, "last_heartbeat_age_seconds", None))
    release = _readable_release_status(getattr(release_status, "status", "not_needed"))
    other_browsers = _readable_other_browser_status(
        getattr(release_status, "other_browsers_status", "not_needed")
    )
    return (
        f"Extension: {connection}; "
        f"Native Messaging: {native_messaging}; "
        f"Incognito: {incognito}; "
        f"DNR: {dnr}; "
        f"YouTube SPA detector: {youtube_spa}; "
        f"Last heartbeat: {age}; "
        f"Next action: {getattr(status, 'next_action', 'Reload the extension if status is stale.')}; "
        f"Browser HIGH safety: {safety}; "
        f"Website HIGH hosts release: {release}; "
        f"Other browsers: {other_browsers}. "
        "Website HIGH requires trusted Chrome extension control. If Incognito "
        "is not allowed, website HIGH stays blocked at hosts level. Other "
        "browsers are closed while website HIGH is active."
    )


def _browser_setup_status_line(status) -> str:
    connection = _readable_browser_setup_connection(
        getattr(status, "connection_status", "disconnected")
    )
    incognito = _readable_incognito_status(
        getattr(status, "incognito_status", "unknown")
    )
    native_host = _readable_browser_setup_native_host(
        getattr(status, "native_messaging_status", "unknown")
    )
    dnr = _readable_dnr_status(status)
    youtube_spa = _readable_youtube_spa_status(
        getattr(status, "youtube_spa_status", "unknown")
    )
    return (
        f"Chrome: {connection}; "
        f"Incognito: {incognito}; "
        f"Native host: {native_host}; "
        f"DNR: {dnr}; "
        f"YouTube SPA detector: {youtube_spa}."
    )


def _readable_browser_setup_connection(value: str) -> str:
    if value == "connected":
        return "Browser ready"
    if value == "partial":
        return "Extension seen"
    if value == "stale":
        return "Status unknown/stale"
    return "Browser disconnected"


def _readable_browser_setup_native_host(value: str) -> str:
    if value == "connected":
        return "Registered"
    if value == "not_connected":
        return "Not registered"
    return "Unknown"


def _browser_setup_next_action(status) -> str:
    connection = getattr(status, "connection_status", "disconnected")
    incognito = getattr(status, "incognito_status", "unknown")
    dnr = getattr(status, "dnr_status", "unknown")
    youtube_spa = getattr(status, "youtube_spa_status", "unknown")
    if connection == "disconnected":
        return (
            "Open Set up Chrome, load the bundled extension manually, then "
            "refresh status."
        )
    if connection == "stale":
        return "Reload extension."
    if incognito != "allowed":
        return "Enable Incognito."
    if dnr in {"unavailable", "error"}:
        return "Reload extension and check the DNR permission."
    if youtube_spa in {"not_seen", "unknown"}:
        return "Open YouTube once to activate the SPA detector."
    return "Run manual QA."


def _readable_native_messaging_status(value: str) -> str:
    if value == "connected":
        return "Connected"
    if value == "not_connected":
        return "Not connected"
    return "Unknown"


def _readable_dnr_status(status) -> str:
    value = getattr(status, "dnr_status", "unknown")
    count = getattr(status, "dnr_session_rule_count", None)
    error = getattr(status, "dnr_last_error", "")
    if value == "active":
        return f"Active {count} rules" if isinstance(count, int) else "Active"
    if value == "supported_no_rules":
        return "Supported but no active rules"
    if value == "unavailable":
        return "Unavailable"
    if value == "error":
        return f"Error: {error}" if error else "Error"
    return "Unknown"


def _readable_youtube_spa_status(value: str) -> str:
    if value == "seen":
        return "Seen"
    if value == "not_seen":
        return "Not seen yet"
    return "Unknown"


def _readable_browser_high_safety(value: str) -> str:
    if value == "trusted_for_chrome":
        return "Trusted for Chrome"
    return _readable_mode(value)


def _readable_release_status(value: str) -> str:
    if value == "allowed":
        return "Allowed"
    if value == "held_closed":
        return "Held closed"
    return "Not needed"


def _readable_other_browser_status(value: str) -> str:
    if value == "guard_active":
        return "Guard active"
    if value == "not_controlled":
        return "Not controlled"
    return "Not needed"


def _readable_incognito_status(value: str) -> str:
    if value == "allowed":
        return "Allowed"
    if value == "not_allowed":
        return "Not allowed"
    return "Unknown"


def _format_heartbeat_age(value: object) -> str:
    if not isinstance(value, int):
        return "none"
    if value < 60:
        return f"{value}s ago"
    minutes = value // 60
    return f"{minutes}m ago"


def _hosts_readiness_detail(group) -> str:
    checks = {check.key: check.ready for check in group.checks}
    managed = "Ready" if checks.get("managed_section_transform") else "Missing"
    backup = (
        "Ready"
        if checks.get("backup_supported") and checks.get("rollback_supported")
        else "Missing"
    )
    recovery = "Ready" if checks.get("recovery_removal_supported") else "Missing"
    return (
        "Hosts readiness audit: "
        f"Managed section: {managed}; "
        f"Backup/Rollback: {backup}; "
        f"Recovery removal: {recovery}; "
        "Admin required for real hosts writes; "
        "exact domains only; bare domains also include www; URL paths are not blocked; "
        "if a site still opens, add its exact subdomain; hosts changes may require "
        "browser refresh, closing existing tabs, or manual DNS cache flush "
        "(ipconfig /flushdns). LoopGuard does not flush DNS automatically. "
        "Browser HIGH safety: Partial, not Trusted; browser redirect blocking "
        "controls only extension-connected browser sessions. Incognito is "
        "controlled only if the extension is allowed there. Other browsers are "
        "not browser-controlled yet. Website HIGH requires trusted Chrome "
        "extension control; if Incognito is not allowed, website HIGH stays "
        "blocked at hosts level. Future hardening may require user-approved "
        "browser policy or unmanaged-browser controls. URLs are sent only to the "
        "local native host; there is no telemetry, cloud, network service, or "
        "localhost HTTP API. "
        "Manual hosts test: confirm Real Hosts Blocking is active, confirm the "
        "target appears under LoopGuard markers in hosts, run ping domain.com, "
        "expect 127.0.0.1, then test in Incognito or after closing existing "
        "tabs/browser."
    )


def _make_selectable_diagnostic(label: QLabel, path: Path) -> None:
    label.setToolTip(str(path))
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)


def _build_card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    card = CardFrame(title)
    title_label = card.title_label
    if title_label is None:
        raise RuntimeError("Settings cards require a title label.")
    return card, card.card_layout, title_label


def _settings_card_row(*cards: QFrame) -> QHBoxLayout:
    row = QHBoxLayout()
    reset_layout(row)
    row.setSpacing(MEDIUM_GAP)
    for card in cards:
        row.addWidget(card, 1)
    return row


def _settings_stylesheet() -> str:
    return (
        """
    QWidget#settingsPage {
        background: #070908;
    }
    QScrollArea#settingsScrollArea,
    QWidget#settingsContent {
        background: #070908;
    }
    QLabel#settingsTestModeBadge {
        min-height: 22px;
    }
    """
        + common_stylesheet()
        + modern_common_stylesheet()
    )
