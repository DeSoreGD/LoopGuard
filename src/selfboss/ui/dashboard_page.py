"""Dashboard tab for the LoopGuard UI shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from selfboss.config import is_production_app_mode
from selfboss.core.models import Task, TaskStatus
from selfboss.core.use_cases import (
    DAY_CLOSE_REVIEW_NORMAL,
    DAY_CLOSE_REVIEW_RECOVERY,
    HighAccessOptions,
    START_DAY_BROWSER_REQUIRED,
    SelfBossAppService,
    format_attempt_local_time,
)
from selfboss.ui.components import (
    CardFrame,
    make_muted_label,
    make_value_label,
    make_page_content,
    reset_layout,
    top_aligned,
)
from selfboss.ui.style import (
    CONTROL_HEIGHT,
    GRID_SPACING,
    SMALL_GAP,
    common_stylesheet,
)
from selfboss.ui.theme import modern_common_stylesheet
from selfboss.ui.widgets import (
    configure_pill,
    make_section_title,
    make_status_row,
    make_subpanel,
    set_button_role,
    set_card_role,
)
from selfboss.ui.window_chrome import prepare_dialog_window

DASHBOARD_PRODUCT_MAX_WIDTH = 1440


class StartHighAccessDialog(QDialog):
    """Choose how much wallet time to spend on HIGH access."""

    def __init__(
        self,
        options: HighAccessOptions,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.options = options
        self.setWindowTitle("Start HIGH access")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        self.wallet_label = QLabel(
            f"You have {format_reward_time(options.available_seconds)} available."
        )
        self.wallet_label.setWordWrap(True)
        layout.addWidget(self.wallet_label)
        self.unavailable_reason_label = QLabel(options.unavailable_reason)
        self.unavailable_reason_label.setWordWrap(True)
        self.unavailable_reason_label.setVisible(bool(options.unavailable_reason))
        layout.addWidget(self.unavailable_reason_label)

        self.duration_group = QButtonGroup(self)
        self.duration_buttons: list[QRadioButton] = []
        form = QFormLayout()
        first_enabled_button: QRadioButton | None = None
        for option in options.options:
            button = QRadioButton(option.label)
            button.setEnabled(option.enabled)
            button.setProperty("minutes", option.minutes)
            self.duration_group.addButton(button)
            self.duration_buttons.append(button)
            form.addRow("", button)
            if option.enabled and first_enabled_button is None:
                first_enabled_button = button

        self.custom_duration_button = QRadioButton("Custom minutes")
        self.custom_duration_button.setEnabled(
            min(options.available_seconds, options.daily_remaining_seconds) >= 60
            and options.can_start_high
        )
        self.custom_minutes_input = QSpinBox()
        self.custom_minutes_input.setMinimum(1)
        self.custom_minutes_input.setMaximum(
            max(
                1,
                min(
                    options.available_seconds // 60,
                    options.max_session_minutes,
                    options.daily_remaining_seconds // 60,
                ),
            )
        )
        self.custom_minutes_input.setMinimumWidth(110)
        self.custom_minutes_input.setEnabled(self.custom_duration_button.isEnabled())
        self.duration_group.addButton(self.custom_duration_button)
        form.addRow(self.custom_duration_button, self.custom_minutes_input)
        layout.addLayout(form)

        self.intent_input = QLineEdit()
        self.intent_input.setObjectName("highIntentInput")
        self.intent_input.setPlaceholderText("What will you use HIGH for?")
        self.intent_error_label = make_muted_label("")
        self.intent_error_label.setObjectName("highIntentErrorLabel")
        self.intent_error_label.setVisible(False)
        layout.addWidget(self.intent_input)
        layout.addWidget(self.intent_error_label)

        if first_enabled_button is not None:
            first_enabled_button.setChecked(True)
        elif self.custom_duration_button.isEnabled():
            self.custom_duration_button.setChecked(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("Start HIGH access")
        ok_button.setEnabled(
            options.can_start_high
            and self.duration_group.checkedButton() is not None
        )
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def selected_minutes(self) -> int:
        """Return the selected HIGH access duration."""
        checked = self.duration_group.checkedButton()
        if checked is self.custom_duration_button:
            return self.custom_minutes_input.value()
        if checked is None:
            return 0
        minutes = checked.property("minutes")
        return minutes if isinstance(minutes, int) else 0

    def selected_intent(self) -> str:
        """Return the declared HIGH access intent."""
        return " ".join(self.intent_input.text().split())

    def _accept_if_valid(self) -> None:
        if len("".join(self.selected_intent().split())) < 5:
            self.intent_error_label.setText(
                "Enter at least 5 characters describing your HIGH intent."
            )
            self.intent_error_label.setVisible(True)
            return
        self.intent_error_label.setVisible(False)
        self.accept()


class DashboardPage(QWidget):
    """Dashboard page bound to the application service."""

    high_access_dialog_class = StartHighAccessDialog

    def __init__(
        self,
        service: SelfBossAppService,
        *,
        on_day_started: Callable[[], None] | None = None,
        production_mode: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.on_day_started = on_day_started
        self.production_mode = (
            is_production_app_mode()
            if production_mode is None
            else production_mode
        )

        self.setObjectName("dashboardPage")
        self.setStyleSheet(_dashboard_stylesheet())
        outer_layout = QVBoxLayout(self)
        reset_layout(outer_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("dashboardScrollArea")
        self.scroll_area.viewport().setObjectName("dashboardScrollViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer_layout.addWidget(self.scroll_area)

        shell, content, layout = make_page_content(
            "dashboardContent",
            max_width=DASHBOARD_PRODUCT_MAX_WIDTH,
        )
        self.content_widget = content
        self.scroll_area.setWidget(shell)

        hero_row = QHBoxLayout()
        reset_layout(hero_row)
        hero_row.setSpacing(GRID_SPACING)
        top_aligned(hero_row)
        controls_row = QHBoxLayout()
        reset_layout(controls_row)
        controls_row.setSpacing(GRID_SPACING)
        top_aligned(controls_row)
        recovery_row = QHBoxLayout()
        reset_layout(recovery_row)
        recovery_row.setSpacing(GRID_SPACING)
        top_aligned(recovery_row)

        day_card, day_layout, self.day_card_title_label = _build_card("Today")
        day_card.setObjectName("DashboardHeroCard")
        set_card_role(day_card, "hero")
        _tighten_release_card_layout(day_layout)
        day_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.main_card_title_label = self.day_card_title_label
        self.day_state_pill_label = QLabel()
        self.day_state_pill_label.setObjectName("DashboardStatusPill")
        configure_pill(self.day_state_pill_label, "focus")
        self.day_state_pill_label.setObjectName("DashboardStatusPill")
        self.day_state_pill_label.setWordWrap(True)
        self.day_status_label = make_value_label("")
        self.day_status_label.setObjectName("DashboardHeroValue")
        self.soft_start_status_label = make_muted_label("")
        self.soft_start_hint_label = make_muted_label("")
        self.task_counts_label = QLabel()
        self.task_counts_label.setVisible(False)
        self.planned_task_counts_label = make_muted_label("")
        self.unplanned_task_counts_label = make_muted_label("")
        self.day_ended_summary_label = make_muted_label("")
        self.day_ended_summary_label.setObjectName("dayEndedSummaryLabel")
        self.rest_token_label = make_muted_label("")
        self.rest_token_hint_label = make_muted_label(
            "Earned rest is only available before Start Day."
        )
        self.rest_token_hint_label.setObjectName("restTokenHintLabel")
        _configure_expanding_labels(
            self.day_card_title_label,
            self.day_status_label,
            self.soft_start_status_label,
            self.soft_start_hint_label,
            self.planned_task_counts_label,
            self.unplanned_task_counts_label,
            self.day_ended_summary_label,
            self.rest_token_label,
            self.rest_token_hint_label,
        )
        self.start_day_button = QPushButton("Start Day")
        self.start_day_button.setObjectName("startDayButton")
        self.use_rest_token_button = QPushButton("Use Rest Day")
        self.use_rest_token_button.setObjectName("useRestTokenButton")
        self.end_day_button = QPushButton("End Day")
        self.end_day_button.setObjectName("endDayButton")
        self.recovery_close_today_button = QPushButton("Recovery Close Today")
        self.recovery_close_today_button.setObjectName("recoveryCloseTodayButton")
        _configure_action_button(self.start_day_button)
        _configure_action_button(self.use_rest_token_button)
        _configure_action_button(self.end_day_button)
        _configure_action_button(self.recovery_close_today_button)
        set_button_role(self.start_day_button, "primary")
        set_button_role(self.use_rest_token_button, "quiet")
        set_button_role(self.end_day_button, "quiet")
        set_button_role(self.recovery_close_today_button, "danger")
        self.start_day_button.clicked.connect(self.start_day)
        self.use_rest_token_button.clicked.connect(self.use_rest_token)
        self.end_day_button.clicked.connect(self.end_day)
        self.recovery_close_today_button.clicked.connect(self.recovery_close_today)
        day_layout.addWidget(self.day_state_pill_label)
        day_layout.addWidget(self.day_status_label)
        day_layout.addWidget(self.soft_start_status_label)
        day_layout.addWidget(self.soft_start_hint_label)
        day_layout.addWidget(
            make_subpanel(
                make_status_row("Planned", self.planned_task_counts_label),
                make_status_row("Unplanned", self.unplanned_task_counts_label),
                make_status_row("Rest Token", self.rest_token_label),
                self.rest_token_hint_label,
                role="metric",
            )
        )
        day_layout.addWidget(self.day_ended_summary_label)

        access_card, access_layout, self.access_card_title_label = _build_card("Access")
        set_card_role(access_card, "control")
        _configure_metric_card(access_card)
        self.access_mode_metric_label = make_value_label("")
        self.access_mode_metric_label.setObjectName("accessModeMetricLabel")
        self.access_mode_metric_label.setWordWrap(True)
        self.access_level_label = QLabel()
        self.access_level_label.setObjectName("DashboardStatusPill")
        configure_pill(self.access_level_label, "focus")
        self.access_level_label.setObjectName("DashboardStatusPill")
        self.access_level_label.setWordWrap(True)
        self.high_timer_label = make_muted_label("")
        self.high_status_label = make_muted_label("")
        self.high_intent_label = make_muted_label("")
        self.high_intent_label.setObjectName("highIntentLabel")
        access_layout.addWidget(
            make_subpanel(
                self.access_mode_metric_label,
                self.access_level_label,
                make_status_row("High", self.high_timer_label),
                make_status_row("State", self.high_status_label),
                self.high_intent_label,
                role="metric",
            )
        )

        reward_card, reward_layout, self.reward_card_title_label = _build_card(
            "Recreation budget"
        )
        set_card_role(reward_card, "control")
        _tighten_release_card_layout(reward_layout)
        reward_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.reward_balance_label = make_value_label("")
        self.reward_balance_label.setObjectName("DashboardMetric")
        self.reward_wallet_label = make_muted_label("")
        self.spend_status_label = make_muted_label("")
        self.recreation_budget_progress = QProgressBar()
        self.recreation_budget_progress.setObjectName("recreationBudgetProgress")
        self.recreation_budget_progress.setRange(0, 100)
        self.recreation_budget_progress.setValue(0)
        self.recreation_budget_progress.setTextVisible(False)
        self.start_high_access_button = QPushButton("Start HIGH access")
        self.end_high_access_button = QPushButton("End HIGH access")
        self.start_high_access_button.setObjectName("startHighButton")
        self.end_high_access_button.setObjectName("endHighButton")
        _configure_action_button(self.start_high_access_button)
        _configure_action_button(self.end_high_access_button)
        set_button_role(self.start_high_access_button, "primary")
        set_button_role(self.end_high_access_button, "quiet")
        self.start_high_access_button.clicked.connect(self.open_high_access_dialog)
        self.end_high_access_button.clicked.connect(self.end_high_access)
        reward_actions = QHBoxLayout()
        reset_layout(reward_actions)
        reward_actions.setSpacing(SMALL_GAP)
        reward_actions.addWidget(self.start_high_access_button)
        reward_actions.addWidget(self.end_high_access_button)
        reward_actions.addStretch(1)
        reward_layout.addWidget(
            make_subpanel(
                self.reward_balance_label,
                self.recreation_budget_progress,
                self.reward_wallet_label,
                role="metric",
            )
        )
        reward_layout.addLayout(reward_actions)
        reward_layout.addWidget(self.spend_status_label)

        self.main_task_status_label = QLabel()
        self.main_task_status_label.setObjectName("DashboardStatusPill")
        configure_pill(self.main_task_status_label, "neutral")
        self.main_task_status_label.setObjectName("DashboardStatusPill")
        self.main_task_status_label.setWordWrap(True)
        self.main_task_label = QLabel()
        self.main_task_label.setObjectName("mainTaskTitle")
        self.main_task_label.setWordWrap(True)
        self.main_task_hint_label = make_muted_label("")
        day_layout.addWidget(
            make_subpanel(
                self.main_task_status_label,
                self.main_task_label,
                self.main_task_hint_label,
                role="metric",
            )
        )
        day_action_row = QHBoxLayout()
        reset_layout(day_action_row)
        day_action_row.setSpacing(SMALL_GAP)
        day_action_row.addWidget(self.start_day_button)
        day_action_row.addWidget(self.use_rest_token_button)
        day_action_row.addWidget(self.end_day_button)
        day_action_row.addWidget(self.recovery_close_today_button)
        day_action_row.addStretch(1)
        day_layout.addLayout(day_action_row)

        safety_card, safety_layout, self.safety_card_title_label = _build_card(
            "Blocking"
        )
        set_card_role(safety_card, "compact")
        self.test_mode_label = QLabel()
        self.test_mode_label.setObjectName("testModeBadge")
        self.test_mode_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        self.test_mode_label.setWordWrap(True)
        self.test_mode_explanation_label = make_muted_label(
            "Preview only. No system blocking is active."
        )
        self.test_mode_explanation_label.setObjectName("testModeExplanationLabel")
        self.safe_mode_label = QLabel()
        self.recovery_mode_label = QLabel()
        configure_pill(self.safe_mode_label, "success")
        configure_pill(self.recovery_mode_label, "success")
        self.safe_mode_label.setWordWrap(True)
        self.recovery_mode_label.setWordWrap(True)
        self.enforcement_mode_label = make_muted_label("")
        self.enforcement_mode_label.setObjectName("enforcementModeLabel")
        self.real_blocking_status_label = make_muted_label("")
        self.real_blocking_status_label.setObjectName("realBlockingStatusLabel")
        self.websites_status_label = make_muted_label("")
        self.websites_status_label.setObjectName("websitesStatusLabel")
        self.browser_status_label = make_muted_label("")
        self.browser_status_label.setObjectName("dashboardBrowserStatusLabel")
        self.enforcement_next_step_label = make_muted_label("")
        self.enforcement_next_step_label.setObjectName("enforcementNextStepLabel")
        self.enforcement_next_step_label.setVisible(False)
        (
            pass_card,
            pass_layout,
            self.planned_use_pass_card_title_label,
        ) = _build_card("Planned-use pass")
        self.planned_use_pass_card = pass_card
        set_card_role(pass_card, "compact")
        self.active_planned_use_pass_title_label = make_section_title("Quick pass")
        self.active_planned_use_pass_title_label.setObjectName(
            "activePlannedUsePassTitleLabel"
        )
        self.active_planned_use_pass_title_label.setWordWrap(True)
        self.planned_use_pass_helper_label = make_muted_label(
            "Use this for task-specific access. For recreation, use HIGH."
        )
        self.planned_use_pass_helper_label.setObjectName(
            "plannedUsePassHelperLabel"
        )
        self.planned_use_pass_rule_combo = QComboBox()
        self.planned_use_pass_rule_combo.setObjectName("plannedUsePassRuleCombo")
        self.planned_use_pass_reason_input = QLineEdit()
        self.planned_use_pass_reason_input.setObjectName(
            "plannedUsePassReasonInput"
        )
        self.planned_use_pass_reason_input.setPlaceholderText(
            "Reason for this task-specific access"
        )
        self.planned_use_pass_duration_combo = QComboBox()
        self.planned_use_pass_duration_combo.setObjectName(
            "plannedUsePassDurationCombo"
        )
        for minutes in (10, 15, 25):
            self.planned_use_pass_duration_combo.addItem(
                f"{minutes} min",
                minutes * 60,
            )
        self.start_planned_use_pass_button = QPushButton("Start pass")
        self.start_planned_use_pass_button.setObjectName(
            "startPlannedUsePassButton"
        )
        self.end_planned_use_pass_button = QPushButton("End pass")
        self.end_planned_use_pass_button.setObjectName("endPlannedUsePassButton")
        self.planned_use_pass_reason_input.setMinimumHeight(CONTROL_HEIGHT)
        self.planned_use_pass_duration_combo.setMinimumHeight(CONTROL_HEIGHT)
        self.planned_use_pass_duration_combo.setFixedWidth(110)
        _configure_action_button(self.start_planned_use_pass_button)
        _configure_action_button(self.end_planned_use_pass_button)
        set_button_role(self.start_planned_use_pass_button, "primary")
        set_button_role(self.end_planned_use_pass_button, "quiet")
        self.planned_use_pass_rule_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_quick_planned_use_pass_action_state()
        )
        self.planned_use_pass_reason_input.textChanged.connect(
            lambda _text: self._refresh_quick_planned_use_pass_action_state()
        )
        self.start_planned_use_pass_button.clicked.connect(
            self.start_planned_use_pass
        )
        self.end_planned_use_pass_button.clicked.connect(
            self.end_planned_use_pass
        )
        self.active_planned_use_pass_detail_label = make_muted_label("")
        self.active_planned_use_pass_detail_label.setObjectName(
            "activePlannedUsePassDetailLabel"
        )
        self.active_planned_use_pass_reason_label = make_muted_label("")
        self.active_planned_use_pass_reason_label.setObjectName(
            "activePlannedUsePassReasonLabel"
        )
        (
            escape_card,
            escape_layout,
            self.recent_escapes_card_title_label,
        ) = _build_card("Recent escapes")
        set_card_role(escape_card, "secondary")
        self.recent_escape_pattern_title_label = make_section_title("Pattern")
        self.recent_escape_pattern_title_label.setObjectName(
            "recentEscapePatternTitleLabel"
        )
        self.recent_escape_pattern_title_label.setWordWrap(True)
        self.recent_escape_pattern_explanation_label = make_muted_label("")
        self.recent_escape_pattern_explanation_label.setObjectName(
            "recentEscapePatternExplanationLabel"
        )
        self.recent_escape_pattern_next_action_label = make_muted_label("")
        self.recent_escape_pattern_next_action_label.setObjectName(
            "recentEscapePatternNextActionLabel"
        )
        self.recent_escape_pattern_meta_label = make_muted_label("")
        self.recent_escape_pattern_meta_label.setObjectName(
            "recentEscapePatternMetaLabel"
        )
        self.browser_escape_title_label = make_section_title("Browser")
        self.browser_escape_title_label.setObjectName("browserEscapeTitleLabel")
        self.browser_escape_title_label.setWordWrap(True)
        self.browser_escape_summary_label = make_muted_label("")
        self.browser_escape_summary_label.setObjectName("browserEscapeSummaryLabel")
        self.dry_run_attempts_title_label = make_section_title("Process")
        self.dry_run_attempts_title_label.setObjectName("dryRunAttemptsTitleLabel")
        self.dry_run_attempts_title_label.setWordWrap(True)
        self.dry_run_attempts_label = make_muted_label("")
        self.dry_run_attempts_label.setObjectName("dryRunAttemptsLabel")
        self.test_mode_row = make_status_row("Test Mode", self.test_mode_label)
        self.blocking_summary_row = make_status_row(
            "Blocking",
            self.test_mode_explanation_label,
        )
        self.enforcement_mode_row = make_status_row("Mode", self.enforcement_mode_label)
        self.apps_status_row = make_status_row("Apps", self.real_blocking_status_label)
        self.sites_status_row = make_status_row("Sites", self.websites_status_label)
        self.browser_status_row = make_status_row("Browser", self.browser_status_label)
        self.safe_mode_row = make_status_row("Safe", self.safe_mode_label)
        self.recovery_mode_row = make_status_row("Recovery", self.recovery_mode_label)
        safety_layout.addWidget(
            make_subpanel(
                self.test_mode_row,
                self.blocking_summary_row,
                self.enforcement_mode_row,
                self.apps_status_row,
                self.sites_status_row,
                self.browser_status_row,
                self.safe_mode_row,
                self.recovery_mode_row,
                role="compact",
            )
        )
        safety_layout.addWidget(self.enforcement_next_step_label)
        pass_layout.addWidget(self.active_planned_use_pass_title_label)
        pass_layout.addWidget(self.planned_use_pass_helper_label)
        planned_use_pass_row = QHBoxLayout()
        reset_layout(planned_use_pass_row)
        planned_use_pass_row.setSpacing(SMALL_GAP)
        planned_use_pass_row.addWidget(self.planned_use_pass_rule_combo, 2)
        planned_use_pass_row.addWidget(self.planned_use_pass_reason_input, 2)
        planned_use_pass_row.addWidget(self.planned_use_pass_duration_combo)
        planned_use_pass_row.addWidget(self.start_planned_use_pass_button)
        planned_use_pass_row.addWidget(self.end_planned_use_pass_button)
        pass_layout.addLayout(planned_use_pass_row)
        pass_layout.addWidget(
            make_subpanel(
                self.active_planned_use_pass_detail_label,
                self.active_planned_use_pass_reason_label,
                role="compact",
            )
        )
        escape_layout.addWidget(
            make_subpanel(
                self.recent_escape_pattern_title_label,
                self.recent_escape_pattern_explanation_label,
                self.recent_escape_pattern_next_action_label,
                self.recent_escape_pattern_meta_label,
                role="metric",
            )
        )
        escape_layout.addWidget(
            make_subpanel(
                self.browser_escape_title_label,
                self.browser_escape_summary_label,
                role="metric",
            )
        )
        escape_layout.addWidget(
            make_subpanel(
                self.dry_run_attempts_title_label,
                self.dry_run_attempts_label,
                role="metric",
            )
        )

        (
            placeholders_card,
            placeholders_layout,
            self.placeholders_card_title_label,
        ) = _build_card("Recovery actions")
        set_card_role(placeholders_card, "secondary")
        self.placeholder_note_label = QLabel(
            "Surrender and Bad Day Mode are safe app-state controls."
        )
        self.placeholder_note_label.setWordWrap(True)
        self.surrender_strictness_label = make_muted_label("")
        self.surrender_button = QPushButton("Surrender unavailable")
        self.bad_day_button = QPushButton("Bad Day unavailable")
        self.surrender_button.setObjectName("activateSurrenderButton")
        self.bad_day_button.setObjectName("activateBadDayButton")
        _configure_action_button(self.surrender_button)
        _configure_action_button(self.bad_day_button)
        set_button_role(self.surrender_button, "danger")
        set_button_role(self.bad_day_button, "quiet")
        self.surrender_button.clicked.connect(self.activate_surrender)
        self.bad_day_button.clicked.connect(self.activate_bad_day_mode)
        self.surrender_button.setToolTip("App-state only; no blocker changes run.")
        self.bad_day_button.setToolTip("App-state only; no blocker changes run.")
        self.bad_day_status_label = make_muted_label("")
        placeholder_row = QHBoxLayout()
        reset_layout(placeholder_row)
        placeholder_row.setSpacing(SMALL_GAP)
        placeholder_row.addWidget(self.surrender_button)
        placeholder_row.addWidget(self.bad_day_button)
        placeholder_row.addStretch(1)
        placeholders_layout.addWidget(
            make_subpanel(
                self.surrender_strictness_label,
                self.placeholder_note_label,
                self.bad_day_status_label,
                role="danger",
            )
        )
        placeholders_layout.addLayout(placeholder_row)

        metrics_column = QVBoxLayout()
        reset_layout(metrics_column)
        metrics_column.setSpacing(GRID_SPACING)
        metrics_column.addWidget(access_card, 1)
        metrics_column.addWidget(reward_card, 1)
        hero_row.addWidget(day_card, 5)
        hero_row.addLayout(metrics_column, 3)
        controls_row.addWidget(safety_card, 1)
        controls_row.addWidget(pass_card, 1)
        recovery_row.addWidget(escape_card, 1)
        recovery_row.addWidget(placeholders_card, 1)
        if self.production_mode:
            self.test_mode_row.setVisible(False)
            self.enforcement_mode_row.setVisible(False)
            self.safe_mode_row.setVisible(False)
            self.recovery_mode_row.setVisible(False)
            self.enforcement_next_step_label.setVisible(False)
            escape_card.setVisible(False)

        layout.addLayout(hero_row)
        layout.addLayout(controls_row)
        layout.addLayout(recovery_row)
        layout.addStretch(1)
        self.planned_use_pass_card.setVisible(not self.production_mode)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()

        self.refresh()

    def refresh(self) -> None:
        """Refresh dashboard labels from the application service."""
        snapshot = self.service.dashboard_snapshot()
        surrender_active = getattr(
            snapshot,
            "surrender_active_today",
            snapshot.surrender_active,
        )
        bad_day_active = getattr(snapshot, "bad_day_active_today", False)
        day_status_text = (
            "Day ended"
            if snapshot.day_closed
            else "Surrendered"
            if surrender_active
            else snapshot.day_status_label
        )
        self.day_status_label.setText(day_status_text)
        self.day_state_pill_label.setText(day_status_text.upper())
        self.day_state_pill_label.setProperty(
            "role",
            "success"
            if snapshot.day_closed
            else "danger"
            if surrender_active
            else "focus",
        )
        _refresh_widget_style(self.day_state_pill_label)
        self._refresh_soft_start(snapshot)
        self.task_counts_label.setText(
            "Tasks today: "
            f"planned {snapshot.planned_task_count} total / "
            f"{snapshot.planned_pending_count} pending / "
            f"{snapshot.planned_done_count} done; "
            f"unplanned {snapshot.unplanned_task_count} total / "
            f"{snapshot.unplanned_pending_count} pending / "
            f"{snapshot.unplanned_done_count} done"
        )
        self.planned_task_counts_label.setText(
            "Planned "
            f"{snapshot.planned_done_count}/{snapshot.planned_task_count} done "
            f"- {snapshot.planned_pending_count} pending"
        )
        self.unplanned_task_counts_label.setText(
            "Unplanned "
            f"{snapshot.unplanned_done_count}/{snapshot.unplanned_task_count} done "
            f"- {snapshot.unplanned_pending_count} pending"
        )
        start_day_unavailable_reason = self._start_day_unavailable_reason(snapshot)
        self.start_day_button.setEnabled(start_day_unavailable_reason == "")
        self.start_day_button.setToolTip(
            ""
            if start_day_unavailable_reason == ""
            else (
                start_day_unavailable_reason
                or "The day is already started."
            )
        )
        self.rest_token_label.setText(f"{snapshot.rest_token_count}/1 available")
        self.rest_token_hint_label.setVisible(
            snapshot.rest_token_count > 0 or snapshot.can_use_rest_token
        )
        self.use_rest_token_button.setVisible(
            snapshot.rest_token_count > 0
            and not snapshot.day_started
            and not snapshot.day_closed
        )
        self.use_rest_token_button.setEnabled(snapshot.can_use_rest_token)
        self.use_rest_token_button.setToolTip(
            ""
            if snapshot.can_use_rest_token
            else snapshot.rest_token_unavailable_reason
        )
        self.end_day_button.setVisible(
            snapshot.day_started and not snapshot.day_closed
        )
        self.end_day_button.setText(snapshot.end_day_confirm_label)
        self.end_day_button.setEnabled(snapshot.can_end_day)
        self.end_day_button.setToolTip(
            (
                "End Day confirmation is available after the review delay."
                if snapshot.end_day_pending and snapshot.end_day_remaining_seconds > 0
                else ""
                if snapshot.can_end_day
                else snapshot.end_day_unavailable_reason
            )
        )
        self.recovery_close_today_button.setVisible(
            not self.production_mode and snapshot.day_started and not snapshot.day_closed
        )
        self.recovery_close_today_button.setEnabled(
            snapshot.can_recovery_close_today
        )
        self.recovery_close_today_button.setToolTip(
            ""
            if snapshot.can_recovery_close_today
            else snapshot.recovery_close_today_unavailable_reason
        )
        self.day_ended_summary_label.setVisible(snapshot.day_closed)
        self.day_ended_summary_label.setText(
            f"Day ended. {snapshot.day_summary_label}"
            if snapshot.day_closed
            else (
                "End Day requested. Confirm in "
                f"{_format_remaining(snapshot.end_day_remaining_seconds)}."
                if snapshot.end_day_pending and snapshot.end_day_remaining_seconds > 0
                else ""
            )
        )
        self.day_ended_summary_label.setVisible(
            snapshot.day_closed
            or (snapshot.end_day_pending and snapshot.end_day_remaining_seconds > 0)
        )
        if surrender_active:
            self.access_mode_metric_label.setText("Surrender")
            self.access_level_label.setText("SURRENDER")
            self.access_level_label.setProperty("role", "danger")
            _refresh_widget_style(self.access_level_label)
            self.high_timer_label.setText("Restrictions paused for today.")
            self.high_status_label.setText(
                f"Underlying access: {_access_display_label(snapshot.access_level.value)}"
            )
        elif snapshot.high_active:
            self.access_mode_metric_label.setText("Recreation")
            self.access_level_label.setText("HIGH")
            self.access_level_label.setProperty("role", "recreation")
            _refresh_widget_style(self.access_level_label)
            self.high_timer_label.setText(
                "HIGH remaining: "
                f"{_format_remaining(snapshot.high_remaining_seconds)}"
            )
            self.high_status_label.setText(
                "Bad Day baseline resumes after HIGH."
                if bad_day_active
                else "Recreation active. Time is counting down."
            )
        elif bad_day_active:
            self.access_mode_metric_label.setText("Utility")
            self.access_level_label.setText("MEDIUM")
            self.access_level_label.setProperty("role", "utility")
            _refresh_widget_style(self.access_level_label)
            self.high_timer_label.setText("HIGH remaining: inactive")
            self.high_status_label.setText("Bad Day Mode baseline.")
        else:
            self.access_mode_metric_label.setText(
                _access_metric_label(snapshot.access_level.value)
            )
            self.access_level_label.setText(
                _access_level_badge(snapshot.access_level.value)
            )
            self.access_level_label.setProperty(
                "role",
                _access_display_role(snapshot.access_level.value),
            )
            _refresh_widget_style(self.access_level_label)
            self.high_timer_label.setText(
                "HIGH remaining: "
                f"{_format_remaining(snapshot.high_remaining_seconds)}"
                if snapshot.high_active
                else "HIGH remaining: inactive"
            )
            self.high_status_label.setText(
                "Recreation active. Time is counting down."
                if snapshot.high_active
                else "Recreation inactive."
            )
        self.high_intent_label.setVisible(snapshot.high_active)
        if snapshot.high_active and snapshot.high_intent:
            self.high_intent_label.setText(
                f"HIGH intent: {_short_dashboard_text(snapshot.high_intent)}"
            )
        elif snapshot.high_active:
            self.high_intent_label.setText("HIGH intent: not recorded")
        else:
            self.high_intent_label.setText("")
        self.reward_balance_label.setText(
            f"Available: {format_reward_time(snapshot.reward_balance_seconds)}"
        )
        cap_seconds = max(1, snapshot.high_daily_cap_minutes * 60)
        progress = min(
            100,
            int((snapshot.high_daily_used_seconds / cap_seconds) * 100),
        )
        self.recreation_budget_progress.setValue(progress)
        self.recreation_budget_progress.setToolTip(
            "Used today: "
            f"{snapshot.high_daily_used_seconds // 60} / "
            f"{snapshot.high_daily_cap_minutes} min"
        )
        recreation_today = (
            "Recreation today: "
            "used "
            f"{snapshot.high_daily_used_seconds // 60} / "
            f"{snapshot.high_daily_cap_minutes} min."
        )
        if surrender_active:
            self.reward_wallet_label.setText(
                "HIGH access is not needed while Surrender is active."
            )
        elif snapshot.high_daily_cap_reached:
            self.reward_wallet_label.setText(
                "Available for HIGH: "
                f"{format_reward_time(snapshot.reward_balance_seconds)}. "
                "Recreation cap reached for today. "
                f"{recreation_today}"
            )
        elif snapshot.high_cooldown_active:
            self.reward_wallet_label.setText(
                "Available for HIGH: "
                f"{format_reward_time(snapshot.reward_balance_seconds)}. "
                "Recreation cooldown: "
                f"{_format_cooldown_minutes(snapshot.high_cooldown_remaining_seconds)} "
                "remaining. "
                f"{recreation_today}"
            )
        else:
            self.reward_wallet_label.setText(
                "Available for HIGH: "
                f"{format_reward_time(snapshot.reward_balance_seconds)}. "
                f"{recreation_today}"
            )
        self.start_high_access_button.setEnabled(
            snapshot.reward_balance_seconds >= 60
            and not snapshot.high_active
            and not surrender_active
            and not snapshot.day_closed
            and not snapshot.high_daily_cap_reached
            and not snapshot.high_cooldown_active
        )
        self._refresh_high_button_tooltips(snapshot, surrender_active)
        self.end_high_access_button.setEnabled(snapshot.high_active)
        self.end_high_access_button.setToolTip(
            "End HIGH access and refund remaining seconds."
            if snapshot.high_active
            else "HIGH access is inactive."
        )
        self.test_mode_label.setText(
            f"TEST MODE: {'ON' if snapshot.test_mode else 'OFF'}"
        )
        self.safe_mode_label.setText(
            f"Safe mode: {'On' if snapshot.safe_mode else 'Off'}"
        )
        self.recovery_mode_label.setText(
            f"Recovery mode: {'On' if snapshot.recovery_mode else 'Off'}"
        )
        self._refresh_enforcement_status(snapshot)
        self._refresh_active_planned_use_pass(snapshot)
        self._refresh_recent_escape_pattern(snapshot)
        self._refresh_browser_escape_summary(snapshot)
        self._refresh_dry_run_attempts(snapshot)
        main_task_display = _main_task_display(
            snapshot.main_task,
            day_started=snapshot.day_started,
        )
        self.main_task_status_label.setText(main_task_display.status)
        self.main_task_label.setText(main_task_display.title)
        self.main_task_hint_label.setText(main_task_display.hint)
        self._refresh_surrender(snapshot)
        self._refresh_bad_day(snapshot)

    def start_day(self) -> None:
        """Start today's planned work period."""
        snapshot = self.service.dashboard_snapshot()
        start_day_unavailable_reason = self._start_day_unavailable_reason(snapshot)
        if start_day_unavailable_reason:
            self.spend_status_label.setText(start_day_unavailable_reason)
            self.refresh()
            return
        if not self._confirm_start_day():
            return
        try:
            self.service.start_day()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return
        self.spend_status_label.setText("Day started")
        self.refresh()
        if self.on_day_started is not None:
            self.on_day_started()

    def use_rest_token(self) -> None:
        """Use an earned Rest Token before Start Day."""
        try:
            self.service.use_rest_token()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return
        self.spend_status_label.setText(
            "Rest Day planned. No reward or Recreation granted."
        )
        self.refresh()

    def _start_day_unavailable_reason(self, snapshot) -> str:
        if not snapshot.can_start_day:
            return (
                snapshot.start_day_unavailable_reason
                or "The day is already started."
            )
        if (
            self.production_mode
            and not snapshot.browser_integration_status.browser_blocking_ready
        ):
            return START_DAY_BROWSER_REQUIRED
        return ""

    def end_day(self) -> None:
        """End today's active work/reward loop."""
        try:
            snapshot = self.service.dashboard_snapshot()
            if not snapshot.end_day_pending:
                self.service.request_end_day()
                self.spend_status_label.setText(
                    "End Day requested. Review for 60 seconds, then confirm."
                )
                self.refresh()
                return
            if snapshot.end_day_remaining_seconds > 0:
                self.spend_status_label.setText(
                    "End Day confirmation is available in "
                    f"{_format_remaining(snapshot.end_day_remaining_seconds)}."
                )
                self.refresh()
                return
            review = self.service.get_day_close_review(DAY_CLOSE_REVIEW_NORMAL)
            self.service.confirm_end_day()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return
        self.spend_status_label.setText("Day ended")
        self.refresh()
        self._show_day_close_review(review)

    def recovery_close_today(self) -> None:
        """Close today's active loop without treating it as a completed day."""
        if not self._confirm_recovery_close_today():
            return
        review = self.service.get_day_close_review(DAY_CLOSE_REVIEW_RECOVERY)
        try:
            self.service.recovery_close_today()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return
        self.spend_status_label.setText("Recovery close completed")
        self.refresh()
        self._show_day_close_review(review)

    def open_high_access_dialog(self) -> None:
        """Open the HIGH access duration picker."""
        options = self.service.get_high_access_options()
        if not options.can_start_high:
            self.spend_status_label.setText(options.unavailable_reason)
            self.refresh()
            return
        if options.available_seconds < 60:
            self.spend_status_label.setText("No whole reward minutes available")
            self.refresh()
            return

        dialog = self.high_access_dialog_class(options, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        minutes = dialog.selected_minutes()
        intent = dialog.selected_intent()
        try:
            self.service.start_high_access(minutes, intent)
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return

        self.spend_status_label.setText(f"Started HIGH for {minutes} minutes")
        self.refresh()

    def spend_minutes(self, minutes: int, intent: str = "") -> None:
        """Compatibility shim for tests or older callers."""
        try:
            self.service.start_high_access(minutes, intent)
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return

        self.spend_status_label.setText(f"Started HIGH for {minutes} minutes")
        self.refresh()

    def end_high_access(self) -> None:
        """End HIGH access early without system side effects."""
        self.service.end_high_access()
        self.spend_status_label.setText("Ended HIGH access")
        self.refresh()

    def start_planned_use_pass(self) -> None:
        """Start a temporary pass for the selected existing rule."""
        rule_id = self.planned_use_pass_rule_combo.currentData()
        if not isinstance(rule_id, int):
            self.spend_status_label.setText("Select a rule first")
            self.refresh()
            return
        duration_seconds = self.planned_use_pass_duration_combo.currentData()
        if not isinstance(duration_seconds, int):
            duration_seconds = 15 * 60
        try:
            active_pass = self.service.start_planned_use_pass(
                rule_id,
                self.planned_use_pass_reason_input.text(),
                duration_seconds,
            )
        except (KeyError, ValueError) as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return

        self.planned_use_pass_reason_input.clear()
        self.spend_status_label.setText(
            f"Started planned-use pass: {active_pass.target}"
        )
        self.refresh()

    def end_planned_use_pass(self) -> None:
        """End the active planned-use pass if present."""
        ended = self.service.end_active_planned_use_pass()
        if ended is None:
            self.spend_status_label.setText("No active planned-use pass.")
        else:
            self.spend_status_label.setText(f"Ended planned-use pass: {ended.target}")
        self.refresh()

    def activate_surrender(self) -> None:
        """Activate app-state-only surrender when the delay has elapsed."""
        try:
            self.service.activate_surrender()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return

        self.spend_status_label.setText("Surrender active for today")
        self.refresh()

    def activate_bad_day_mode(self) -> None:
        """Activate safe current-day Bad Day Mode."""
        try:
            self.service.activate_bad_day_mode()
        except ValueError as error:
            self.spend_status_label.setText(str(error))
            self.refresh()
            return

        self.spend_status_label.setText("Bad Day Mode active for today")
        self.refresh()

    def _refresh_surrender(self, snapshot) -> None:
        self.surrender_strictness_label.setText(
            "Surrender strictness: "
            f"{snapshot.surrender_strictness.upper()} "
            f"({_format_surrender_delay(snapshot.surrender_delay_seconds)})"
        )
        surrender_active = getattr(
            snapshot,
            "surrender_active_today",
            snapshot.surrender_active,
        )
        if snapshot.day_closed:
            self.placeholder_note_label.setText("Day ended.")
            self.surrender_button.setText("Surrender unavailable")
            self.surrender_button.setEnabled(False)
            self.surrender_button.setToolTip(
                "Surrender is unavailable after End Day."
            )
            return

        if snapshot.soft_start_active:
            self.placeholder_note_label.setText(
                "Surrender unavailable during Soft Start."
            )
            self.surrender_button.setText("Surrender unavailable")
            self.surrender_button.setEnabled(False)
            self.surrender_button.setToolTip(
                "Surrender is unavailable during Soft Start."
            )
            return

        if surrender_active:
            self.placeholder_note_label.setText("Surrender active for today.")
            self.surrender_button.setText("Surrender active")
            self.surrender_button.setEnabled(False)
            self.surrender_button.setToolTip("Surrender is already active today.")
            return

        if not snapshot.day_started:
            self.placeholder_note_label.setText(
                "Surrender unavailable until day starts."
            )
            self.surrender_button.setText("Surrender unavailable")
            self.surrender_button.setEnabled(False)
            self.surrender_button.setToolTip(
                "Start the day before activating Surrender."
            )
            return

        if not snapshot.surrender_available:
            self.placeholder_note_label.setText(
                "Surrender available in "
                f"{_format_surrender_remaining(snapshot.surrender_remaining_seconds)}."
            )
            self.surrender_button.setText("Surrender unavailable")
            self.surrender_button.setEnabled(False)
            self.surrender_button.setToolTip(
                "Surrender available in "
                f"{_format_surrender_remaining(snapshot.surrender_remaining_seconds)}."
            )
            return

        self.placeholder_note_label.setText("Surrender is available now.")
        self.surrender_button.setText("Activate Surrender")
        self.surrender_button.setEnabled(True)
        self.surrender_button.setToolTip("Activate safe app-state Surrender.")

    def _refresh_bad_day(self, snapshot) -> None:
        bad_day_active = getattr(snapshot, "bad_day_active_today", False)
        surrender_active = getattr(
            snapshot,
            "surrender_active_today",
            snapshot.surrender_active,
        )
        if snapshot.day_closed:
            self.bad_day_status_label.setText(
                "Bad Day Mode unavailable after End Day."
            )
            self.bad_day_button.setText("Bad Day unavailable")
            self.bad_day_button.setEnabled(False)
            self.bad_day_button.setToolTip(
                "Bad Day Mode is unavailable after End Day."
            )
            return

        if snapshot.soft_start_active:
            self.bad_day_status_label.setText(
                "Bad Day Mode unavailable during Soft Start."
            )
            self.bad_day_button.setText("Bad Day unavailable")
            self.bad_day_button.setEnabled(False)
            self.bad_day_button.setToolTip(
                "Bad Day Mode is unavailable during Soft Start."
            )
            return

        if surrender_active:
            self.bad_day_status_label.setText(
                "Bad Day Mode is overridden by Surrender."
            )
            self.bad_day_button.setText("Bad Day overridden")
            self.bad_day_button.setEnabled(False)
            self.bad_day_button.setToolTip(
                "Bad Day Mode is overridden by Surrender."
            )
            return

        if not snapshot.day_started:
            self.bad_day_status_label.setText(
                "Bad Day Mode unavailable until day starts."
            )
            self.bad_day_button.setText("Bad Day unavailable")
            self.bad_day_button.setEnabled(False)
            self.bad_day_button.setToolTip(
                "Start the day before activating Bad Day Mode."
            )
            return

        if bad_day_active:
            self.bad_day_status_label.setText(
                "Bad Day Mode active: MEDIUM baseline for today. "
                "HIGH still requires reward."
            )
            self.bad_day_button.setText("Bad Day active")
            self.bad_day_button.setEnabled(False)
            self.bad_day_button.setToolTip("Bad Day Mode is already active today.")
            return

        self.bad_day_status_label.setText(
            "Bad Day Mode available: MEDIUM baseline for today."
        )
        self.bad_day_button.setText("Activate Bad Day Mode")
        self.bad_day_button.setEnabled(True)
        self.bad_day_button.setToolTip("Activate safe app-state Bad Day Mode.")

    def _confirm_start_day(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Start Day?")
        prepare_dialog_window(dialog)
        dialog.setText(
            "Starting the day locks your current plan. Tasks added after this "
            "will be Unplanned and will not earn rewards or unlock MEDIUM. "
            "Make sure your MAIN task is ready."
        )
        start_button = dialog.addButton(
            "Start Day",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is start_button

    def _confirm_recovery_close_today(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Recovery Close Today?")
        prepare_dialog_window(dialog)
        dialog.setText(
            "Close today without MAIN completion? This stops today's earning "
            "and returns you to planning. It does not count as a completed day."
        )
        close_button = dialog.addButton(
            "Recovery Close Today",
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        return dialog.clickedButton() is close_button

    def _show_day_close_review(self, summary) -> None:
        dialog = QMessageBox(self)
        dialog.setWindowTitle(summary.title)
        prepare_dialog_window(dialog)
        dialog.setText(_day_close_review_text(summary))
        dialog.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def _refresh_high_button_tooltips(self, snapshot, surrender_active: bool) -> None:
        if self.start_high_access_button.isEnabled():
            self.start_high_access_button.setToolTip(
                "Spend reward time to start HIGH access."
            )
            return

        if snapshot.day_closed:
            tooltip = "HIGH access is unavailable after End Day."
        elif surrender_active:
            tooltip = "HIGH access is not needed while Surrender is active."
        elif snapshot.high_active:
            tooltip = "HIGH access is already active."
        elif snapshot.high_daily_cap_reached:
            tooltip = "Recreation cap reached for today."
        elif snapshot.high_cooldown_active:
            tooltip = (
                "Recreation cooldown: "
                f"{_format_cooldown_minutes(snapshot.high_cooldown_remaining_seconds)} "
                "remaining."
            )
        elif snapshot.reward_balance_seconds < 60:
            tooltip = "Earn at least 1 minute of reward time to start HIGH."
        else:
            tooltip = "HIGH access is unavailable."
        self.start_high_access_button.setToolTip(tooltip)

    def _refresh_soft_start(self, snapshot) -> None:
        if not snapshot.soft_start_enabled or snapshot.soft_start_duration_minutes == 0:
            self.soft_start_status_label.setText("Soft Start: OFF")
            self.soft_start_hint_label.setText("")
            return

        if not snapshot.day_started:
            self.soft_start_status_label.setText(
                "Soft Start: "
                f"{snapshot.soft_start_duration_minutes}m after Start Day"
            )
            self.soft_start_hint_label.setText(
                "After Start Day, task completion waits for the buffer."
            )
            return

        if snapshot.soft_start_active:
            self.soft_start_status_label.setText("Soft Start active")
            self.soft_start_hint_label.setText(
                "Tasks unlock in "
                f"{_format_soft_start_remaining(snapshot.soft_start_remaining_seconds)}. "
                "Relax now; rewards unlock after the buffer."
            )
            return

        self.soft_start_status_label.setText("Soft Start complete")
        self.soft_start_hint_label.setText("")

    def _refresh_recent_escape_pattern(self, snapshot) -> None:
        summary = snapshot.recent_attempt_summary
        self.recent_escape_pattern_explanation_label.setText(
            _recent_pattern_summary(summary)
        )
        self.recent_escape_pattern_next_action_label.setText(
            f"Next action: {summary.suggested_next_action}"
        )
        self.recent_escape_pattern_meta_label.setText(
            "Attempts: "
            f"{summary.total_attempts} · "
            f"Families: {_attempt_family_path(summary.recent_family_sequence)} · "
            "Switching: "
            f"{'Yes' if summary.possible_switching_detected else 'No'}"
        )

    def _refresh_browser_escape_summary(self, snapshot) -> None:
        summary = snapshot.browser_escape_summary
        if not summary.has_attempts or summary.last_attempt is None:
            self.browser_escape_summary_label.setText("Browser: none today.")
            return

        top_targets = "; ".join(
            f"{target.display_target} - {target.count}"
            for target in summary.top_targets
        )
        last_attempt = summary.last_attempt
        last_parts = [
            _privacy_safe_dashboard_text(getattr(last_attempt, "target", "")),
            _browser_attempt_kind_label(last_attempt),
            _browser_attempt_action_label(last_attempt),
            _short_attempt_time(last_attempt.occurred_at),
        ]
        last_text = " - ".join(part for part in last_parts if part)
        top_text = f" Top: {top_targets}." if top_targets else ""
        self.browser_escape_summary_label.setText(
            f"{summary.message}{top_text} Last: {last_text}."
        )

    def _refresh_dry_run_attempts(self, snapshot) -> None:
        summary = snapshot.dry_run_process_summary
        attempts = summary.latest_attempts
        if not attempts:
            self.dry_run_attempts_label.setText("Process: none today.")
            return
        real_active = (
            snapshot.enforcement_status.effective_mode.value
            in {"real_process_blocking", "full_enforcement"}
        )
        if summary.today_would_block_count:
            if real_active:
                action_mode = (
                    "Full Enforcement"
                    if snapshot.enforcement_status.effective_mode.value
                    == "full_enforcement"
                    else "Real Process Blocking"
                )
                summary_line = (
                    f"{action_mode} acted on "
                    f"{summary.today_would_block_count} blocked app attempts today."
                )
            else:
                summary_line = (
                    "Armed Dry Run saw "
                    f"{summary.today_would_block_count} would-block app attempts today."
                )
        else:
            summary_line = "No blocked app attempts recorded today."
        if summary.last_would_block_target:
            if real_active:
                latest_real_action = next(
                    (
                        attempt.action_taken
                        for attempt in attempts
                        if attempt.source == "real_process_blocking_process"
                        and attempt.decision == "would_block"
                    ),
                    "",
                )
                action_text = (
                    f" - {_attempt_action_label(latest_real_action)}"
                    if latest_real_action
                    else ""
                )
                summary_line += (
                    " Last blocked app today: "
                    f"{summary.last_would_block_target}{action_text}."
                )
            else:
                summary_line += (
                    f" Last blocked app: {summary.last_would_block_target}."
                )
        summary_line += f" {summary.real_blocking_note}"
        parts = [
            (
                f"{_short_attempt_time(attempt.occurred_at)} "
                f"{attempt.target}: {_attempt_status_label(attempt)} "
                f"at {attempt.access_level_at_attempt.upper()}"
            )
            for attempt in attempts[:3]
        ]
        recent_text = "; ".join(parts[:2])
        self.dry_run_attempts_label.setText(
            f"{summary_line} Recent: {recent_text}"
        )

    def _refresh_enforcement_status(self, snapshot) -> None:
        status = snapshot.enforcement_status
        active_day = snapshot.day_started and not snapshot.day_closed
        if status.effective_mode.value == "real_process_blocking":
            self.test_mode_explanation_label.setText(
                "Blocking: apps active."
                if active_day
                else "Blocking armed. Starts when day starts."
            )
        elif status.effective_mode.value == "real_hosts_blocking":
            self.test_mode_explanation_label.setText(
                "Blocking: websites active."
                if active_day
                else "Blocking armed. Starts when day starts."
            )
        elif status.effective_mode.value == "full_enforcement":
            self.test_mode_explanation_label.setText(
                "Blocking: apps + websites."
                if active_day
                else "Blocking armed. Starts when day starts."
            )
        else:
            self.test_mode_explanation_label.setText(
                "Blocking: not active."
                if self.production_mode
                else "Blocking: preview only."
            )
        if self.production_mode and not snapshot.browser_integration_status.browser_blocking_ready:
            self.test_mode_explanation_label.setText("Chrome setup required.")
        self.enforcement_mode_label.setText(
            "Enforcement mode: "
            f"{_readable_label(status.effective_mode.value)}"
        )
        if status.effective_mode.value == "real_process_blocking":
            real_blocking_text = "Active"
        elif status.effective_mode.value == "real_hosts_blocking":
            real_blocking_text = "Inactive"
        elif status.effective_mode.value == "full_enforcement":
            real_blocking_text = "Active"
        else:
            real_blocking_text = "Inactive"
        if not active_day and real_blocking_text == "Active":
            real_blocking_text = "Armed"
        self.real_blocking_status_label.setText(f"Apps: {real_blocking_text}")
        hosts_status = snapshot.hosts_blocking_status
        browser_status = snapshot.browser_integration_status
        release_status = snapshot.website_high_release_status
        release_note = _dashboard_website_release_note(release_status)
        trial_note = (
            "" if self.production_mode
            else _dashboard_personal_trial_summary(
                self.service.get_personal_use_readiness_checklist()
            )
        )
        sites_text = _dashboard_websites_summary(hosts_status).rstrip(".")
        browser_text = _dashboard_browser_summary(browser_status).strip()
        full_web_status = (
            f"{sites_text}. {release_note}{browser_text} {trial_note}"
        ).strip()
        self.websites_status_label.setText(
            f"{sites_text}. {browser_text} {trial_note}".strip()
        )
        self.websites_status_label.setToolTip(full_web_status)
        self.browser_status_label.setText(browser_text.rstrip("."))
        self.browser_status_label.setToolTip(full_web_status)
        self.enforcement_next_step_label.setText(
            "" if self.production_mode else status.next_step
        )
        self.enforcement_next_step_label.setToolTip(status.next_step)

        self.safe_mode_label.setProperty(
            "role",
            "warning" if snapshot.safe_mode else "success",
        )
        self.recovery_mode_label.setProperty(
            "role",
            "warning" if snapshot.recovery_mode else "success",
        )
        _refresh_widget_style(self.safe_mode_label)
        _refresh_widget_style(self.recovery_mode_label)

    def _refresh_active_planned_use_pass(self, snapshot) -> None:
        self._refresh_planned_use_pass_rule_choices()
        active_pass = snapshot.active_planned_use_pass
        if active_pass is None:
            self.active_planned_use_pass_detail_label.setText(
                "No active planned-use pass."
            )
            self.active_planned_use_pass_reason_label.setText("")
            self.active_planned_use_pass_reason_label.setVisible(False)
            self._refresh_quick_planned_use_pass_action_state(active_pass=None)
            return

        remaining = _format_soft_start_remaining(
            snapshot.active_planned_use_pass_remaining_seconds
        )
        reason = _short_dashboard_text(active_pass.reason)
        target_type = _planned_use_target_type_label(
            str(getattr(active_pass, "target_type", ""))
        )
        self.active_planned_use_pass_detail_label.setText(
            f"{active_pass.target} ({target_type}) - {remaining} left"
        )
        self.active_planned_use_pass_reason_label.setText(
            f"Reason: {reason}"
        )
        self.active_planned_use_pass_reason_label.setVisible(True)
        self._refresh_quick_planned_use_pass_action_state(active_pass=active_pass)

    def _refresh_planned_use_pass_rule_choices(self) -> None:
        current_rule_id = self.planned_use_pass_rule_combo.currentData()
        self.planned_use_pass_rule_combo.clear()
        for rule_type, rule_type_label in (("site", "Website"), ("app", "App")):
            for rule in self.service.get_rules(rule_type):
                self.planned_use_pass_rule_combo.addItem(
                    (
                        f"{rule_type_label}: {rule.target} - "
                        f"{rule.allow_from_level.upper()} - "
                        f"{_readable_label(rule.escape_family)}"
                    ),
                    rule.id,
                )
        if self.planned_use_pass_rule_combo.count() == 0:
            self.planned_use_pass_rule_combo.addItem("No eligible rules yet", None)
            return
        if isinstance(current_rule_id, int):
            index = self.planned_use_pass_rule_combo.findData(current_rule_id)
            if index >= 0:
                self.planned_use_pass_rule_combo.setCurrentIndex(index)

    def _refresh_quick_planned_use_pass_action_state(
        self,
        active_pass: object | None = None,
    ) -> None:
        if active_pass is None:
            active_pass = self.service.get_active_planned_use_pass()
        has_active_pass = active_pass is not None
        has_rule = isinstance(self.planned_use_pass_rule_combo.currentData(), int)
        has_reason = bool(self.planned_use_pass_reason_input.text().strip())
        can_start = has_rule and has_reason and not has_active_pass

        self.planned_use_pass_rule_combo.setEnabled(not has_active_pass and has_rule)
        self.planned_use_pass_reason_input.setEnabled(not has_active_pass and has_rule)
        self.planned_use_pass_duration_combo.setEnabled(not has_active_pass and has_rule)
        self.start_planned_use_pass_button.setEnabled(can_start)
        self.start_planned_use_pass_button.setToolTip(
            "Start a temporary pass for the selected rule target."
            if can_start
            else _planned_use_pass_unavailable_reason(
                has_rule=has_rule,
                has_reason=has_reason,
                has_active_pass=has_active_pass,
            )
        )
        self.end_planned_use_pass_button.setEnabled(has_active_pass)
        self.end_planned_use_pass_button.setToolTip(
            "End the active planned-use pass."
            if has_active_pass
            else "No active planned-use pass."
        )


class _MainTaskDisplay:
    def __init__(self, *, status: str, title: str, hint: str) -> None:
        self.status = status
        self.title = title
        self.hint = hint


def _main_task_display(
    task: Task | None,
    *,
    day_started: bool = False,
) -> _MainTaskDisplay:
    if task is None:
        if day_started:
            return _MainTaskDisplay(
                status="UNAVAILABLE",
                title="No planned MAIN task for this active day.",
                hint=(
                    "This day was started without a planned MAIN task. "
                    "MAIN unlock is unavailable for this day."
                ),
            )
        return _MainTaskDisplay(
            status="EMPTY",
            title="No planned MAIN task yet.",
            hint="Create one before Start Day.",
        )
    if task.status is TaskStatus.DONE:
        return _MainTaskDisplay(
            status="COMPLETED",
            title=f"{task.title} — completed",
            hint="Today's planned MAIN task is complete.",
        )
    if task.completion_claimed_at:
        return _MainTaskDisplay(
            status="CLAIM PENDING",
            title=f"{task.title} - completion claim pending",
            hint="Confirm Done after the delay to unlock MEDIUM access.",
        )
    return _MainTaskDisplay(
        status=task.status.value.upper(),
        title=f"{task.title} — {task.status.value}",
        hint="Completing today's planned MAIN task unlocks MEDIUM access.",
    )


def _main_task_text(task: Task | None) -> str:
    return _main_task_display(task).title


def _build_card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    card = CardFrame(title)
    title_label = card.title_label
    if title_label is None:
        raise RuntimeError("Dashboard cards require a title label.")
    return card, card.card_layout, title_label


def _dashboard_stylesheet() -> str:
    return (
        """
    QWidget#dashboardPage {
        background: #0b0d0c;
    }
    QScrollArea#dashboardScrollArea,
    QWidget#dashboardContent {
        background: #0b0d0c;
        border: none;
    }
    QPushButton#activateSurrenderButton:disabled,
    QPushButton#activateBadDayButton:disabled {
        color: #667065;
        background: #151915;
        border: 1px solid #273027;
        padding: 3px 10px;
    }
    """
        + common_stylesheet()
        + modern_common_stylesheet()
    )


def _configure_action_button(button: QPushButton) -> None:
    button.setMinimumHeight(CONTROL_HEIGHT)
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


def _configure_metric_card(card: QFrame) -> None:
    card.setMinimumHeight(220)
    card.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Expanding,
    )


def _tighten_release_card_layout(layout: QVBoxLayout) -> None:
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)
    top_aligned(layout)


def _configure_expanding_labels(*labels: QLabel) -> None:
    for label in labels:
        label.setWordWrap(True)
        label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )


def _format_remaining(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _format_surrender_remaining(seconds: int) -> str:
    total_minutes = max(1, (max(0, seconds) + 59) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours <= 0:
        return f"{minutes}m"
    return f"{hours}h {minutes}m"


def _format_surrender_delay(seconds: int) -> str:
    hours = max(0, seconds) // 3600
    return f"{hours}h"


def _format_soft_start_remaining(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    if minutes <= 0:
        return f"{remaining_seconds}s"
    if remaining_seconds == 0:
        return f"{minutes}m"
    return f"{minutes}m {remaining_seconds}s"


def _attempt_family_path(families: list[str]) -> str:
    if not families:
        return "none"
    return " -> ".join(family.replace("_", " ") for family in families)


def _readable_label(value: str) -> str:
    return value.replace("_", " ").title()


def _access_display_label(value: str) -> str:
    labels = {
        "low": "LOW / Focus",
        "medium": "MEDIUM / Utility",
        "high": "HIGH / Recreation",
    }
    return labels.get(value.lower(), value.upper())


def _access_metric_label(value: str) -> str:
    labels = {
        "low": "Focus",
        "medium": "Utility",
        "high": "Recreation",
    }
    return labels.get(value.lower(), value.title())


def _access_level_badge(value: str) -> str:
    labels = {
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
    }
    return labels.get(value.lower(), value.upper())


def _access_display_role(value: str) -> str:
    roles = {
        "low": "focus",
        "medium": "utility",
        "high": "recreation",
    }
    return roles.get(value.lower(), "neutral")


def _refresh_widget_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _planned_use_target_type_label(target_type: str) -> str:
    if target_type == "site":
        return "website"
    if target_type == "app":
        return "app"
    return "target"


def _planned_use_pass_unavailable_reason(
    *,
    has_rule: bool,
    has_reason: bool,
    has_active_pass: bool,
) -> str:
    if has_active_pass:
        return "Another planned-use pass is already active."
    if not has_rule:
        return "Add an enabled rule first."
    if not has_reason:
        return "Enter a planned-use reason."
    return "Planned-use pass is unavailable."


def _day_close_review_text(summary) -> str:
    lines = [
        f"MAIN completed: {'Yes' if summary.main_completed else 'No'}",
        (
            "Planned tasks: "
            f"{summary.planned_done_count} / {summary.planned_task_count} done"
        ),
        (
            "Unplanned tasks: "
            f"{summary.unplanned_done_count} / {summary.unplanned_task_count} done"
        ),
        f"Recreation used: {format_reward_time(summary.recreation_used_seconds)}",
    ]
    if summary.recent_attempt_count:
        attempts = "attempt" if summary.recent_attempt_count == 1 else "attempts"
        attempt_line = f"Escape attempts: {summary.recent_attempt_count} {attempts}"
        if summary.recent_family_path:
            attempt_line += f" - {summary.recent_family_path}"
        lines.append(attempt_line)
    else:
        lines.append("Escape attempts: none today")
    if summary.active_planned_use_pass_target:
        target_type = _planned_use_target_type_label(
            str(summary.active_planned_use_pass_type or "")
        )
        lines.append(
            "Planned-use pass: "
            f"{summary.active_planned_use_pass_target} ({target_type}) ended"
        )
    lines.append(f"Next action: {summary.next_action}")
    return "\n".join(lines)


def _dashboard_browser_summary(status) -> str:
    connection = getattr(status, "connection_status", "disconnected")
    native_host = getattr(status, "native_host_status", "not_connected")
    prepared = getattr(status, "native_host_prepared_status", "unknown")
    if getattr(status, "browser_blocking_ready", False):
        return "Browser: Browser ready."
    if connection == "partial" and native_host != "connected":
        return "Browser: Extension seen, native host missing."
    if prepared == "prepared" and connection != "connected":
        return "Browser: Native host prepared, extension not connected."
    if connection == "stale":
        return "Browser: Status unknown/stale."
    if connection == "partial":
        return "Browser: Status unknown/stale."
    return "Browser: Browser disconnected."


def _dashboard_websites_summary(status) -> str:
    if getattr(status, "status", "") == "permission_denied":
        return "Sites: Permission issue."
    if getattr(status, "status", "") == "armed_idle":
        return "Sites: Blocking armed. Starts when day starts."
    if getattr(status, "active", False):
        count = getattr(status, "blocked_domain_count", 0)
        return f"Sites: Active ({count} domains)."
    return "Sites: Inactive."


def _dashboard_website_release_note(status) -> str:
    release = _readable_release_status(getattr(status, "status", "not_needed"))
    if release == "Held closed":
        return "Website HIGH held closed. "
    return ""


def _dashboard_personal_trial_summary(checklist) -> str:
    verdict = getattr(checklist, "verdict", "not_ready")
    if verdict == "ready_for_personal_trial":
        label = "verified"
    elif verdict == "partial":
        label = "partial"
    else:
        label = "not verified"
    return f"Trial: {label}."


def _readable_release_status(value: str) -> str:
    if value == "allowed":
        return "Allowed"
    if value == "held_closed":
        return "Held closed"
    return "Not needed"


def _readable_incognito_status(value: str) -> str:
    if value == "allowed":
        return "Allowed"
    if value == "not_allowed":
        return "Not allowed"
    return "Unknown"


def _attempt_action_label(value: str) -> str:
    labels = {
        "none": "None",
        "terminate_requested": "Terminate requested",
        "skipped_protected": "Skipped protected",
        "access_denied": "Access denied",
        "failed": "Failed",
        "not_found": "Not found",
    }
    return labels.get(value, _readable_label(value))


def _attempt_decision_label(value: str) -> str:
    labels = {
        "would_block": "Would block",
        "would_allow": "Would allow",
        "would_block_in_current_mode": "Would block",
        "allowed_by_planned_use_pass": "Allowed by pass",
    }
    return labels.get(value, _readable_label(value))


def _attempt_status_label(attempt) -> str:
    if getattr(attempt, "enforcement_mode", "") == "real_process_blocking":
        action_taken = getattr(attempt, "action_taken", "none")
        if action_taken and action_taken != "none":
            return _attempt_action_label(action_taken)
        if getattr(attempt, "decision", "") == "would_allow":
            return "Allowed"
    return _attempt_decision_label(getattr(attempt, "decision", ""))


def _short_attempt_time(value: str) -> str:
    return format_attempt_local_time(value, include_date=False)


def _browser_attempt_kind_label(attempt) -> str:
    for attribute in ("path_kind", "url_family", "matched_scope"):
        value = getattr(attempt, attribute, "")
        if value and value not in {"unknown", "none", "generic_site"}:
            return _privacy_safe_dashboard_text(str(value))
    return ""


def _browser_attempt_action_label(attempt) -> str:
    action_taken = getattr(attempt, "action_taken", "")
    decision = getattr(attempt, "decision", "")
    if action_taken == "browser_redirect":
        return "blocked"
    if action_taken == "evaluation_only":
        return "evaluation only"
    if decision == "would_block":
        return "blocked"
    if decision == "allowed_by_planned_use_pass":
        return "allowed by pass"
    if decision == "would_allow":
        return "allowed"
    return _readable_label(decision)


def _privacy_safe_dashboard_text(value: str, *, fallback: str = "unknown") -> str:
    clean_value = " ".join(value.strip().lower().split())
    had_scheme = "://" in clean_value
    if "://" in clean_value:
        clean_value = clean_value.split("://", 1)[1]
    if had_scheme:
        clean_value = clean_value.split("/", 1)[0]
    clean_value = clean_value.split("?", 1)[0].split("#", 1)[0]
    if not clean_value or "://" in clean_value:
        return fallback
    return _short_dashboard_text(clean_value, max_chars=64)


def _recent_pattern_summary(summary) -> str:
    if summary.total_attempts == 0:
        return "No attempts logged today."
    family_path = _attempt_family_path(summary.recent_family_sequence)
    if summary.possible_switching_detected:
        return f"Possible switching: {family_path}"
    if summary.recent_family_sequence:
        return f"Recent family: {family_path}"
    return summary.pattern_explanation


def _short_dashboard_text(value: str, *, max_chars: int = 48) -> str:
    clean_value = " ".join(value.split())
    if len(clean_value) <= max_chars:
        return clean_value
    return clean_value[: max_chars - 3].rstrip() + "..."


def format_reward_time(seconds: int) -> str:
    """Format reward seconds as compact minutes/seconds copy."""
    safe_seconds = max(0, seconds)
    minutes, remaining_seconds = divmod(safe_seconds, 60)
    if minutes == 0 and remaining_seconds == 0:
        return "0m"
    if minutes == 0:
        return f"{remaining_seconds}s"
    if remaining_seconds == 0:
        return f"{minutes}m"
    return f"{minutes}m {remaining_seconds}s"


def _format_cooldown_minutes(seconds: int) -> str:
    return f"{max(1, (max(0, seconds) + 59) // 60)}m"
