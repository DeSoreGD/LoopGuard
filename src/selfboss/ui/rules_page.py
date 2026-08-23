"""Rules tab for the LoopGuard UI shell."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from selfboss.config import is_production_app_mode
from selfboss.core.use_cases import (
    ESCAPE_FAMILY_OPTIONS,
    RULE_PURPOSE_OPTIONS,
    SelfBossAppService,
    canonical_rule_target_for_display,
    format_attempt_local_time,
    recommended_allow_from_level_for_purpose,
    rule_duplicate_equivalence_key,
    suggest_escape_family_for_rule,
    utility_leakage_warning_for_rule,
)
from selfboss.ui.components import (
    CardFrame,
    make_muted_label,
    make_page_content,
    make_value_label,
    reset_layout,
    top_aligned,
)
from selfboss.ui.style import (
    CONTROL_HEIGHT,
    MEDIUM_GAP,
    PAGE_SPACING,
    SMALL_GAP,
    TABLE_PAGE_MAX_WIDTH,
    common_stylesheet,
)
from selfboss.ui.theme import modern_common_stylesheet
from selfboss.ui.widgets import (
    configure_pill,
    make_subpanel,
    set_button_role,
    set_card_role,
)


_RULE_STORED_TARGET_ROLE = Qt.ItemDataRole.UserRole + 1


class RulesPage(QWidget):
    """Dry-run rules editor with no blocker side effects."""

    def __init__(
        self,
        service: SelfBossAppService,
        *,
        production_mode: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.production_mode = (
            is_production_app_mode()
            if production_mode is None
            else production_mode
        )

        self.setObjectName("rulesPage")
        self.setStyleSheet(_rules_stylesheet())

        root_layout = QVBoxLayout(self)
        reset_layout(root_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("rulesScrollArea")
        self.scroll_area.viewport().setObjectName("rulesScrollViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        root_layout.addWidget(self.scroll_area)

        shell, content, content_layout = make_page_content(
            "rulesContent",
            max_width=TABLE_PAGE_MAX_WIDTH,
        )
        self.content_widget = content
        self.scroll_area.setWidget(shell)

        content_layout.addWidget(self._build_intro_card())
        content_layout.addWidget(self._build_editor_tabs())
        content_layout.addWidget(self._build_preview_card())
        content_layout.addWidget(self._build_recent_attempts_card())
        content_layout.addStretch(1)
        self._apply_production_visibility()

        self.refresh()

    def refresh(self) -> None:
        """Refresh stored rules and dry-run preview."""
        site_rules = self.service.get_rules("site")
        app_rules = self.service.get_rules("app")
        self._load_rules("site", self.site_rules_table, site_rules)
        self._load_rules("app", self.app_rules_table, app_rules)
        self._refresh_duplicate_rule_warning(site_rules, app_rules)
        self._refresh_utility_leakage_warning()
        preview = self.service.preview_blocking()
        site_rules_by_target = {rule.target: rule for rule in site_rules}
        app_rules_by_target = {rule.target: rule for rule in app_rules}

        if preview.restriction_state == "surrender":
            effective_mode = "restriction state: SURRENDER"
        elif preview.restriction_state == "bad_day":
            effective_mode = "restriction state: BAD DAY (MEDIUM baseline)"
        else:
            effective_mode = f"access level: {preview.access_level.value.upper()}"

        self.preview_label.setText(
            f"Effective mode: {effective_mode}\n{preview.message}"
        )
        self.preview_blocked_sites_label.setText(
            "Blocked sites now: "
            + _format_preview_targets(
                preview.blocked_sites,
                site_rules_by_target,
                preview.active_planned_use_pass,
            )
        )
        self.preview_blocked_apps_label.setText(
            "Blocked apps now: "
            + _format_preview_targets(
                preview.blocked_apps,
                app_rules_by_target,
                preview.active_planned_use_pass,
            )
        )
        self.preview_allowed_sites_label.setText(
            "Allowed sites now: "
            + _format_preview_targets(
                preview.allowed_sites,
                site_rules_by_target,
                preview.active_planned_use_pass,
            )
        )
        self.preview_allowed_apps_label.setText(
            "Allowed apps now: "
            + _format_preview_targets(
                preview.allowed_apps,
                app_rules_by_target,
                preview.active_planned_use_pass,
            )
        )
        self._load_recent_attempts()
        self._update_planned_use_pass_state(preview.active_planned_use_pass)

    def _apply_production_visibility(self) -> None:
        if not self.production_mode:
            return
        self.preview_card.setVisible(False)
        self.recent_attempts_card.setVisible(False)
        self.log_site_attempt_button.setVisible(False)
        self.log_app_attempt_button.setVisible(False)

    def add_site_rule(self) -> None:
        """Add a site rule from the site input."""
        self._add_rule(
            "site",
            self.site_input,
            self.site_allow_from_input,
            self.site_purpose_input,
            self.site_escape_family_input,
        )

    def add_app_rule(self) -> None:
        """Add an app rule from the app input."""
        self._add_rule(
            "app",
            self.app_input,
            self.app_allow_from_input,
            self.app_purpose_input,
            self.app_escape_family_input,
        )

    def add_starter_rules(self) -> None:
        """Add missing starter dry-run rules."""
        result = self.service.add_starter_rule_presets()
        message = (
            f"Created {result.created_count} rules, "
            f"skipped {result.skipped_existing_count} existing."
        )
        if result.failed_presets:
            message += f" Failed {len(result.failed_presets)} presets."
        self.status_label.setText(message)
        self.refresh()

    def remove_selected_site_rule(self) -> None:
        """Remove the selected site rule."""
        self._remove_selected_rule("site", self.site_rules_table)

    def remove_selected_app_rule(self) -> None:
        """Remove the selected app rule."""
        self._remove_selected_rule("app", self.app_rules_table)

    def update_selected_site_allow_from_level(self) -> None:
        """Update the selected site rule threshold."""
        self._update_selected_allow_from_level(
            "site",
            self.site_rules_table,
            self.site_allow_from_input,
            self.site_purpose_input,
            self.site_escape_family_input,
        )

    def update_selected_app_allow_from_level(self) -> None:
        """Update the selected app rule threshold."""
        self._update_selected_allow_from_level(
            "app",
            self.app_rules_table,
            self.app_allow_from_input,
            self.app_purpose_input,
            self.app_escape_family_input,
        )

    def log_selected_site_attempt(self) -> None:
        """Log a manual Test Mode attempt for the selected site rule."""
        self._log_selected_attempt(self.site_rules_table)

    def log_selected_app_attempt(self) -> None:
        """Log a manual Test Mode attempt for the selected app rule."""
        self._log_selected_attempt(self.app_rules_table)

    def _build_intro_card(self) -> QFrame:
        intro_card, intro_layout, self.page_title_label = _build_card("Rules")
        set_card_role(intro_card, "compact")
        intro_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.summary_label = make_muted_label(
            "Dry-run rule planning. Test Mode is active and no system blocking runs."
        )
        self.test_mode_label = QLabel("Preview only — Test Mode. No system changes.")
        self.test_mode_label.setObjectName("rulesTestModeBadge")
        self.test_mode_label.setWordWrap(True)
        self.test_mode_label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        if self.production_mode:
            self.summary_label.setText(
                "Rules shape Focus, Utility, and Recreation boundaries."
            )
            self.test_mode_label.setVisible(False)
        self.low_rule_label = make_muted_label("LOW / Focus")
        self.medium_rule_label = make_muted_label("MEDIUM / Utility")
        self.high_rule_label = make_muted_label("HIGH / Recreation")
        for label, role in (
            (self.low_rule_label, "focus"),
            (self.medium_rule_label, "neutral"),
            (self.high_rule_label, "danger"),
        ):
            configure_pill(label, role)
        self.status_label = make_muted_label("")
        self.status_label.setObjectName("rulesStatusMessage")
        self.duplicate_rules_warning_label = make_muted_label("")
        self.duplicate_rules_warning_label.setObjectName(
            "rulesDuplicateWarningLabel"
        )
        self.utility_leakage_warning_label = make_muted_label("")
        self.utility_leakage_warning_label.setObjectName(
            "rulesUtilityLeakageWarningLabel"
        )
        self.add_starter_rules_button = QPushButton("Add starter rules")
        self.add_starter_rules_button.setObjectName("addStarterRulesButton")
        _configure_buttons(self.add_starter_rules_button)
        set_button_role(self.add_starter_rules_button, "primary")
        self.add_starter_rules_button.clicked.connect(self.add_starter_rules)

        intro_top_row = QHBoxLayout()
        reset_layout(intro_top_row)
        intro_top_row.setSpacing(SMALL_GAP)
        intro_top_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        intro_top_row.addWidget(self.summary_label, 1)
        if not self.production_mode:
            intro_top_row.addWidget(self.test_mode_label)
        intro_top_row.addWidget(self.add_starter_rules_button)
        level_row = QHBoxLayout()
        reset_layout(level_row)
        level_row.setSpacing(SMALL_GAP)
        level_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for label in (
            self.low_rule_label,
            self.medium_rule_label,
            self.high_rule_label,
        ):
            level_row.addWidget(label)
        level_row.addStretch(1)
        intro_strip = QVBoxLayout()
        reset_layout(intro_strip)
        intro_strip.setSpacing(SMALL_GAP)
        intro_strip.addLayout(intro_top_row)
        intro_strip.addLayout(level_row)
        intro_layout.addLayout(intro_strip)
        intro_layout.addWidget(self.status_label)
        intro_layout.addWidget(self.utility_leakage_warning_label)
        intro_layout.addWidget(self.duplicate_rules_warning_label)
        return intro_card

    def _build_editor_tabs(self) -> QTabWidget:
        self.rules_editor_tabs = QTabWidget()
        self.rules_editor_tabs.setObjectName("rulesEditorTabs")
        self.rules_editor_tabs.setDocumentMode(True)
        self.rules_editor_tabs.addTab(self._build_site_tab(), "Websites")
        self.rules_editor_tabs.addTab(self._build_app_tab(), "Apps")
        self.rules_editor_tabs.currentChanged.connect(
            lambda _index: self._update_planned_use_pass_state()
        )
        self.rules_editor_tabs.currentChanged.connect(
            lambda _index: self._refresh_utility_leakage_warning()
        )
        return self.rules_editor_tabs

    def _build_site_tab(self) -> QWidget:
        tab = _build_tab_page()
        card, layout, self.site_card_title_label = _build_card("Blocked sites")
        set_card_role(card, "control")
        tab.layout().addWidget(card)

        self.site_help_label = make_muted_label(
            "Website target can be a domain like reddit.com or a browser path "
            "pattern like youtube.com/shorts/*. Path patterns require the "
            "browser extension."
        )
        self.site_input = QLineEdit()
        self.site_input.setObjectName("websiteTargetInput")
        self.site_input.setPlaceholderText("youtube.com/shorts/*")
        self.site_allow_from_input = _build_allow_from_combo()
        self.site_allow_from_input.setObjectName("websiteAllowFromCombo")
        self.site_purpose_input = _build_rule_purpose_combo()
        self.site_purpose_input.setObjectName("websitePurposeCombo")
        self.site_escape_family_input = _build_escape_family_combo()
        self.site_escape_family_input.setObjectName("websiteEscapeFamilyCombo")
        if self.production_mode:
            self.site_purpose_input.setVisible(False)
            self.site_escape_family_input.setVisible(False)
        self.add_site_button = QPushButton("Add rule")
        self.add_site_button.setObjectName("addWebsiteRuleButton")
        self.site_rules_table = _build_rules_table("website_rules_table")
        self.site_rules_list = self.site_rules_table
        self.update_site_allow_from_button = QPushButton("Update Allow from")
        self.remove_site_button = QPushButton("Remove rule")
        self.log_site_attempt_button = QPushButton("Log test attempt")
        self.update_site_allow_from_button.setObjectName("updateWebsiteRuleButton")
        self.remove_site_button.setObjectName("removeWebsiteRuleButton")
        self.log_site_attempt_button.setObjectName("logWebsiteAttemptButton")
        _configure_rule_controls(
            self.site_input,
            self.site_allow_from_input,
            self.site_purpose_input,
            self.site_escape_family_input,
            self.add_site_button,
            self.update_site_allow_from_button,
            self.remove_site_button,
            self.log_site_attempt_button,
        )
        self.add_site_button.clicked.connect(self.add_site_rule)
        self.site_purpose_input.currentIndexChanged.connect(
            self._suggest_site_allow_from_for_new_rule
        )
        self.site_input.textChanged.connect(
            self._suggest_site_escape_family_for_new_rule
        )
        self.site_input.textChanged.connect(
            lambda _text: self._refresh_utility_leakage_warning()
        )
        self.site_allow_from_input.currentIndexChanged.connect(
            lambda _index: self._refresh_utility_leakage_warning()
        )
        self.site_escape_family_input.currentIndexChanged.connect(
            lambda _index: self._refresh_utility_leakage_warning()
        )
        self.update_site_allow_from_button.clicked.connect(
            self.update_selected_site_allow_from_level
        )
        self.remove_site_button.clicked.connect(self.remove_selected_site_rule)
        self.log_site_attempt_button.clicked.connect(self.log_selected_site_attempt)
        self.site_rules_table.itemSelectionChanged.connect(
            self._update_site_rule_action_state
        )
        self._update_site_rule_action_state()

        self.website_target_input = self.site_input
        self.website_allowed_from_combo = self.site_allow_from_input
        self.website_purpose_combo = self.site_purpose_input
        self.website_escape_family_combo = self.site_escape_family_input
        self.add_website_rule_button = self.add_site_button
        self.website_rules_table = self.site_rules_table
        self.log_website_attempt_button = self.log_site_attempt_button
        if self.production_mode:
            self.site_rules_table.setColumnHidden(1, True)
            self.site_rules_table.setColumnHidden(2, True)

        layout.addWidget(self.site_help_label)
        layout.addWidget(
            _build_form_panel(
                _build_rule_form_row(
                    "Target",
                    self.site_input,
                    self.site_purpose_input,
                    self.site_escape_family_input,
                    self.site_allow_from_input,
                    self.add_site_button,
                )
            )
        )
        layout.addWidget(self.site_rules_table)
        layout.addLayout(
            _build_action_row(
                self.update_site_allow_from_button,
                self.remove_site_button,
                self.log_site_attempt_button,
            )
        )
        return tab

    def _build_app_tab(self) -> QWidget:
        tab = _build_tab_page()
        card, layout, self.app_card_title_label = _build_card("Blocked apps")
        set_card_role(card, "control")
        tab.layout().addWidget(card)

        self.app_help_label = make_muted_label(
            "Windows process names only, for example steam.exe or discord.exe."
        )
        self.app_input = QLineEdit()
        self.app_input.setObjectName("appTargetInput")
        self.app_input.setPlaceholderText("steam.exe")
        self.app_allow_from_input = _build_allow_from_combo()
        self.app_allow_from_input.setObjectName("appAllowFromCombo")
        self.app_purpose_input = _build_rule_purpose_combo()
        self.app_purpose_input.setObjectName("appPurposeCombo")
        self.app_escape_family_input = _build_escape_family_combo()
        self.app_escape_family_input.setObjectName("appEscapeFamilyCombo")
        if self.production_mode:
            self.app_purpose_input.setVisible(False)
            self.app_escape_family_input.setVisible(False)
        self.add_app_button = QPushButton("Add rule")
        self.add_app_button.setObjectName("addAppRuleButton")
        self.app_rules_table = _build_rules_table("app_rules_table")
        self.app_rules_list = self.app_rules_table
        self.update_app_allow_from_button = QPushButton("Update Allow from")
        self.remove_app_button = QPushButton("Remove rule")
        self.log_app_attempt_button = QPushButton("Log test attempt")
        self.update_app_allow_from_button.setObjectName("updateAppRuleButton")
        self.remove_app_button.setObjectName("removeAppRuleButton")
        self.log_app_attempt_button.setObjectName("logAppAttemptButton")
        _configure_rule_controls(
            self.app_input,
            self.app_allow_from_input,
            self.app_purpose_input,
            self.app_escape_family_input,
            self.add_app_button,
            self.update_app_allow_from_button,
            self.remove_app_button,
            self.log_app_attempt_button,
        )
        self.add_app_button.clicked.connect(self.add_app_rule)
        self.app_purpose_input.currentIndexChanged.connect(
            self._suggest_app_allow_from_for_new_rule
        )
        self.app_input.textChanged.connect(
            self._suggest_app_escape_family_for_new_rule
        )
        self.app_input.textChanged.connect(
            lambda _text: self._refresh_utility_leakage_warning()
        )
        self.app_allow_from_input.currentIndexChanged.connect(
            lambda _index: self._refresh_utility_leakage_warning()
        )
        self.app_escape_family_input.currentIndexChanged.connect(
            lambda _index: self._refresh_utility_leakage_warning()
        )
        self.update_app_allow_from_button.clicked.connect(
            self.update_selected_app_allow_from_level
        )
        self.remove_app_button.clicked.connect(self.remove_selected_app_rule)
        self.log_app_attempt_button.clicked.connect(self.log_selected_app_attempt)
        self.app_rules_table.itemSelectionChanged.connect(
            self._update_app_rule_action_state
        )
        self._update_app_rule_action_state()

        self.app_target_input = self.app_input
        self.app_allowed_from_combo = self.app_allow_from_input
        self.app_purpose_combo = self.app_purpose_input
        self.app_escape_family_combo = self.app_escape_family_input
        self.add_app_rule_button = self.add_app_button
        self.log_app_rule_attempt_button = self.log_app_attempt_button
        if self.production_mode:
            self.app_rules_table.setColumnHidden(1, True)
            self.app_rules_table.setColumnHidden(2, True)

        layout.addWidget(self.app_help_label)
        layout.addWidget(
            _build_form_panel(
                _build_rule_form_row(
                    "Target",
                    self.app_input,
                    self.app_purpose_input,
                    self.app_escape_family_input,
                    self.app_allow_from_input,
                    self.add_app_button,
                )
            )
        )
        layout.addWidget(self.app_rules_table)
        layout.addLayout(
            _build_action_row(
                self.update_app_allow_from_button,
                self.remove_app_button,
                self.log_app_attempt_button,
            )
        )
        return tab

    def _build_preview_card(self) -> QFrame:
        preview_card, preview_layout, self.preview_card_title_label = _build_card(
            "Dry-run preview"
        )
        set_card_role(preview_card, "secondary")
        self.preview_card = preview_card
        self.rules_preview_area = preview_card
        self.preview_label = make_value_label("")
        self.preview_label.setObjectName("rulesPreviewLabel")
        self.preview_blocked_sites_label = make_muted_label("")
        self.preview_blocked_apps_label = make_muted_label("")
        self.preview_allowed_sites_label = make_muted_label("")
        self.preview_allowed_apps_label = make_muted_label("")
        self.planned_use_selected_rule_label = make_muted_label(
            "Planned-use pass: select a rule first."
        )
        self.planned_use_selected_rule_label.setObjectName(
            "plannedUseSelectedRuleLabel"
        )
        self.planned_use_helper_label = make_muted_label(
            "Use passes for task-specific access. For recreation, use HIGH."
        )
        self.planned_use_helper_label.setObjectName("plannedUseHelperLabel")
        self.planned_use_reason_input = QLineEdit()
        self.planned_use_reason_input.setObjectName("plannedUseReasonInput")
        self.planned_use_reason_input.setPlaceholderText(
            "Reason for this task-specific access"
        )
        self.planned_use_duration_combo = QComboBox()
        self.planned_use_duration_combo.setObjectName("plannedUseDurationCombo")
        for minutes in (10, 15, 25):
            self.planned_use_duration_combo.addItem(f"{minutes} min", minutes * 60)
        self.start_planned_use_pass_button = QPushButton("Start planned-use pass")
        self.start_planned_use_pass_button.setObjectName(
            "startPlannedUsePassButton"
        )
        self.active_planned_use_pass_label = make_muted_label("")
        self.active_planned_use_pass_label.setObjectName("activePlannedUsePassLabel")
        self.end_planned_use_pass_button = QPushButton("End pass")
        self.end_planned_use_pass_button.setObjectName("endPlannedUsePassButton")
        self.planned_use_reason_input.setMinimumHeight(CONTROL_HEIGHT)
        self.planned_use_reason_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.planned_use_duration_combo.setMinimumHeight(CONTROL_HEIGHT)
        self.planned_use_duration_combo.setFixedWidth(110)
        _configure_buttons(
            self.start_planned_use_pass_button,
            self.end_planned_use_pass_button,
        )
        self.planned_use_reason_input.textChanged.connect(
            lambda _text: self._update_planned_use_pass_state()
        )
        self.start_planned_use_pass_button.clicked.connect(
            self.start_planned_use_pass
        )
        self.end_planned_use_pass_button.clicked.connect(
            self.end_planned_use_pass
        )
        self.preview_sites_label = self.preview_blocked_sites_label
        self.preview_apps_label = self.preview_blocked_apps_label
        for label in (
            self.preview_label,
            self.preview_blocked_sites_label,
            self.preview_blocked_apps_label,
            self.preview_allowed_sites_label,
            self.preview_allowed_apps_label,
        ):
            label.setWordWrap(True)
        preview_summary_row = QHBoxLayout()
        reset_layout(preview_summary_row)
        preview_summary_row.setSpacing(SMALL_GAP)
        preview_summary_row.addWidget(self.preview_label, 1)
        preview_layout.addWidget(
            _build_layout_panel(preview_summary_row, role="compact")
        )
        preview_layout.addWidget(
            make_subpanel(
                self.preview_blocked_sites_label,
                self.preview_blocked_apps_label,
                self.preview_allowed_sites_label,
                self.preview_allowed_apps_label,
                role="compact",
            )
        )
        preview_layout.addWidget(
            make_subpanel(
                self.planned_use_helper_label,
                self.planned_use_selected_rule_label,
                role="compact",
            )
        )
        planned_use_row = QHBoxLayout()
        reset_layout(planned_use_row)
        planned_use_row.setSpacing(SMALL_GAP)
        planned_use_row.addWidget(self.planned_use_reason_input, 1)
        planned_use_row.addWidget(self.planned_use_duration_combo)
        planned_use_row.addWidget(self.start_planned_use_pass_button)
        planned_use_row.addWidget(self.end_planned_use_pass_button)
        preview_layout.addWidget(_build_layout_panel(planned_use_row, role="compact"))
        preview_layout.addWidget(self.active_planned_use_pass_label)
        return preview_card

    def _build_recent_attempts_card(self) -> QFrame:
        attempts_card, attempts_layout, self.attempts_card_title_label = _build_card(
            "Recent test attempts"
        )
        set_card_role(attempts_card, "secondary")
        self.recent_attempts_card = attempts_card
        self.attempt_summary_empty_label = make_muted_label("")
        self.attempt_summary_empty_label.setObjectName("attemptSummaryEmptyLabel")
        self.attempt_summary_total_label = make_muted_label("")
        self.attempt_summary_total_label.setObjectName("attemptSummaryTotalLabel")
        self.attempt_summary_families_label = make_muted_label("")
        self.attempt_summary_families_label.setObjectName("attemptSummaryFamiliesLabel")
        self.attempt_summary_decisions_label = make_muted_label("")
        self.attempt_summary_decisions_label.setObjectName("attemptSummaryDecisionsLabel")
        self.attempt_summary_path_label = make_muted_label("")
        self.attempt_summary_path_label.setObjectName("attemptSummaryPathLabel")
        self.attempt_summary_switching_label = make_muted_label("")
        self.attempt_summary_switching_label.setObjectName("attemptSummarySwitchingLabel")
        self.attempt_summary_helper_label = make_muted_label("")
        self.attempt_summary_helper_label.setObjectName("attemptSummaryHelperLabel")
        self.attempt_decision_filter_combo = QComboBox()
        self.attempt_decision_filter_combo.setObjectName("attemptDecisionFilterCombo")
        self.attempt_decision_filter_combo.addItem("All dry-run decisions", "all")
        self.attempt_decision_filter_combo.addItem("Would block", "would_block")
        self.attempt_decision_filter_combo.addItem("Allowed", "would_allow")
        self.attempt_process_filter_input = QLineEdit()
        self.attempt_process_filter_input.setObjectName("attemptProcessFilterInput")
        self.attempt_process_filter_input.setPlaceholderText("Process filter")
        self.attempt_access_filter_combo = QComboBox()
        self.attempt_access_filter_combo.setObjectName("attemptAccessFilterCombo")
        self.attempt_access_filter_combo.addItem("All levels", "all")
        self.attempt_access_filter_combo.addItem("LOW", "low")
        self.attempt_access_filter_combo.addItem("MEDIUM", "medium")
        self.attempt_access_filter_combo.addItem("HIGH", "high")
        self.attempt_decision_filter_combo.currentIndexChanged.connect(
            lambda _index: self._load_recent_attempts()
        )
        self.attempt_process_filter_input.textChanged.connect(
            lambda _text: self._load_recent_attempts()
        )
        self.attempt_access_filter_combo.currentIndexChanged.connect(
            lambda _index: self._load_recent_attempts()
        )
        self.recent_attempts_table = _build_recent_attempts_table()
        self.access_attempts_table = self.recent_attempts_table
        for label in (
            self.attempt_summary_empty_label,
            self.attempt_summary_total_label,
            self.attempt_summary_families_label,
            self.attempt_summary_decisions_label,
            self.attempt_summary_path_label,
            self.attempt_summary_switching_label,
            self.attempt_summary_helper_label,
        ):
            label.setWordWrap(True)
        attempts_layout.addWidget(self.attempt_summary_empty_label)
        self.attempt_summary_detail_panel = make_subpanel(
            self.attempt_summary_total_label,
            self.attempt_summary_families_label,
            self.attempt_summary_decisions_label,
            self.attempt_summary_path_label,
            self.attempt_summary_switching_label,
            self.attempt_summary_helper_label,
            role="compact",
        )
        attempts_layout.addWidget(self.attempt_summary_detail_panel)
        filter_row = QHBoxLayout()
        reset_layout(filter_row)
        filter_row.setSpacing(SMALL_GAP)
        filter_row.addWidget(self.attempt_decision_filter_combo)
        filter_row.addWidget(self.attempt_process_filter_input, 1)
        filter_row.addWidget(self.attempt_access_filter_combo)
        self.attempt_filter_panel = _build_layout_panel(filter_row, role="compact")
        attempts_layout.addWidget(self.attempt_filter_panel)
        attempts_layout.addWidget(self.recent_attempts_table)
        return attempts_card

    def _add_rule(
        self,
        rule_type: str,
        input_widget: QLineEdit,
        allow_from_input: QComboBox,
        purpose_input: QComboBox,
        escape_family_input: QComboBox,
    ) -> None:
        try:
            rule = self.service.add_rule(
                rule_type,
                input_widget.text(),
                allow_from_level=_selected_combo_data(allow_from_input, "high"),
                purpose=_selected_combo_data(purpose_input, "high_risk_escape"),
                escape_family=_selected_combo_data(escape_family_input, "none"),
            )
        except ValueError as error:
            self.status_label.setText(str(error))
            return

        input_widget.clear()
        self.status_label.setText(
            f"Added {rule.rule_type}: {rule.target} - {rule.allow_from_level.upper()}"
        )
        self.refresh()

    def _update_site_rule_action_state(self) -> None:
        self._sync_selected_rule_inputs(
            "site",
            self.site_rules_table,
            self.site_allow_from_input,
            self.site_purpose_input,
            self.site_escape_family_input,
        )
        self._update_rule_action_state(
            self.site_rules_table,
            self.update_site_allow_from_button,
            self.remove_site_button,
            self.log_site_attempt_button,
        )
        self._refresh_utility_leakage_warning()

    def _update_app_rule_action_state(self) -> None:
        self._sync_selected_rule_inputs(
            "app",
            self.app_rules_table,
            self.app_allow_from_input,
            self.app_purpose_input,
            self.app_escape_family_input,
        )
        self._update_rule_action_state(
            self.app_rules_table,
            self.update_app_allow_from_button,
            self.remove_app_button,
            self.log_app_attempt_button,
        )
        self._refresh_utility_leakage_warning()

    def _update_rule_action_state(
        self,
        table: QTableWidget,
        update_button: QPushButton,
        remove_button: QPushButton,
        log_button: QPushButton,
    ) -> None:
        has_selection = _selected_target_from_table(table) is not None
        rules_locked = self._rules_locked_for_active_day()
        update_button.setEnabled(has_selection and not rules_locked)
        remove_button.setEnabled(has_selection and not rules_locked)
        log_button.setEnabled(has_selection)
        update_button.setToolTip(
            "Rules are locked during an active day. You can add stricter rules or edit tomorrow."
            if has_selection and rules_locked
            else "Update the selected rule allow-from level."
            if has_selection and not rules_locked
            else "Select a rule first."
        )
        remove_button.setToolTip(
            "Rules are locked during an active day. You can add stricter rules or edit tomorrow."
            if has_selection and rules_locked
            else "Remove the selected rule."
            if has_selection and not rules_locked
            else "Select a rule first."
        )
        log_button.setToolTip(
            "Log a manual Test Mode access attempt for the selected rule."
            if has_selection
            else "Select a rule first."
        )
        self._update_planned_use_pass_state()

    def _rules_locked_for_active_day(self) -> bool:
        snapshot = self.service.dashboard_snapshot()
        return snapshot.day_started and not snapshot.day_closed

    def _remove_selected_rule(self, rule_type: str, table: QTableWidget) -> None:
        target = _selected_target_from_table(table)
        if target is None:
            self.status_label.setText("Select a rule first")
            return

        try:
            self.service.remove_rule(rule_type, target)
        except ValueError as error:
            self.status_label.setText(str(error))
            self.refresh()
            return
        self.status_label.setText(f"Removed {rule_type}: {target}")
        self.refresh()

    def _log_selected_attempt(self, table: QTableWidget) -> None:
        rule_id = _selected_rule_id_from_table(table)
        if rule_id is None:
            self.status_label.setText("Select a rule first")
            return

        try:
            attempt = self.service.log_manual_rule_attempt(rule_id)
        except (KeyError, ValueError) as error:
            self.status_label.setText(str(error))
            return

        self.status_label.setText(
            f"Logged test attempt: {attempt.target} - "
            f"{_metadata_display(attempt.decision)}"
        )
        self.refresh()

    def start_planned_use_pass(self) -> None:
        """Start a temporary Test Mode pass for the selected rule."""
        rule_id = self._selected_rule_id_for_active_tab()
        if rule_id is None:
            self.status_label.setText("Select a rule first")
            self._update_planned_use_pass_state()
            return

        try:
            active_pass = self.service.start_planned_use_pass(
                rule_id,
                self.planned_use_reason_input.text(),
                _selected_combo_int(self.planned_use_duration_combo, 900),
            )
        except (KeyError, ValueError) as error:
            self.status_label.setText(str(error))
            self._update_planned_use_pass_state()
            return

        self.planned_use_reason_input.clear()
        self.status_label.setText(f"Started planned-use pass: {active_pass.target}")
        self.refresh()

    def end_planned_use_pass(self) -> None:
        """End the active Test Mode planned-use pass."""
        ended = self.service.end_active_planned_use_pass()
        if ended is None:
            self.status_label.setText("No active planned-use pass.")
        else:
            self.status_label.setText(f"Ended planned-use pass: {ended.target}")
        self.refresh()

    def _update_planned_use_pass_state(self, active_pass: object | None = None) -> None:
        if not hasattr(self, "start_planned_use_pass_button"):
            return
        if active_pass is not None and not hasattr(active_pass, "target"):
            active_pass = None
        if active_pass is None:
            active_pass = self.service.get_active_planned_use_pass()

        has_selection = self._selected_rule_id_for_active_tab() is not None
        selected_label = self._selected_rule_label_for_active_tab()
        selected_rule_type = self._selected_rule_type_for_active_tab()
        has_reason = bool(self.planned_use_reason_input.text().strip())
        has_active_pass = active_pass is not None
        self.planned_use_selected_rule_label.setText(
            "Planned-use pass target: "
            f"{selected_label} ({_planned_use_target_type_label(selected_rule_type)})"
            if has_selection
            else "Planned-use pass: select a rule first."
        )
        self.start_planned_use_pass_button.setEnabled(
            has_selection and has_reason and not has_active_pass
        )
        self.start_planned_use_pass_button.setToolTip(
            "Temporarily allow only the selected rule target."
            if self.start_planned_use_pass_button.isEnabled()
            else _planned_use_unavailable_reason(
                has_selection=has_selection,
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
        self.active_planned_use_pass_label.setText(
            _format_active_planned_use_pass(active_pass)
        )

    def _selected_rule_id_for_active_tab(self) -> int | None:
        if self.rules_editor_tabs.currentIndex() == 1:
            return _selected_rule_id_from_table(self.app_rules_table)
        return _selected_rule_id_from_table(self.site_rules_table)

    def _selected_rule_type_for_active_tab(self) -> str:
        return "app" if self.rules_editor_tabs.currentIndex() == 1 else "site"

    def _selected_rule_label_for_active_tab(self) -> str:
        table = (
            self.app_rules_table
            if self.rules_editor_tabs.currentIndex() == 1
            else self.site_rules_table
        )
        return _selected_target_from_table(table) or ""

    def _update_selected_allow_from_level(
        self,
        rule_type: str,
        table: QTableWidget,
        allow_from_input: QComboBox,
        purpose_input: QComboBox,
        escape_family_input: QComboBox,
    ) -> None:
        target = _selected_target_from_table(table)
        if target is None:
            self.status_label.setText("Select a rule first")
            return

        try:
            rule = self.service.update_rule_allow_from_level(
                rule_type,
                target,
                _selected_combo_data(allow_from_input, "high"),
                purpose=_selected_combo_data(purpose_input, "high_risk_escape"),
                escape_family=_selected_combo_data(escape_family_input, "none"),
            )
        except (KeyError, ValueError) as error:
            self.status_label.setText(str(error))
            self.refresh()
            return
        self.status_label.setText(
            f"Updated {rule.rule_type}: {rule.target} - "
            f"{rule.allow_from_level.upper()}"
        )
        self.refresh()

    def _load_rules(
        self,
        rule_type: str,
        table: QTableWidget,
        rules: list[object],
    ) -> None:
        table.setRowCount(0)
        for row_index, rule in enumerate(rules):
            table.insertRow(row_index)
            display_target = canonical_rule_target_for_display(rule_type, rule.target)
            full_text = (
                f"{display_target} - {rule.purpose} - {rule.escape_family} - "
                f"{rule.allow_from_level.upper()}"
            )
            if display_target != rule.target:
                full_text = f"{full_text} (stored: {rule.target})"
            target_item = _build_table_item(display_target, full_text)
            target_item.setData(Qt.ItemDataRole.UserRole, rule.id)
            target_item.setData(_RULE_STORED_TARGET_ROLE, rule.target)
            purpose_item = _build_table_item(
                _metadata_display(rule.purpose),
                full_text,
            )
            family_item = _build_table_item(
                _metadata_display(rule.escape_family),
                full_text,
            )
            level_item = _build_table_item(rule.allow_from_level.upper(), full_text)
            enabled_item = _build_table_item("Yes" if rule.enabled else "No", full_text)
            table.setItem(row_index, 0, target_item)
            table.setItem(row_index, 1, purpose_item)
            table.setItem(row_index, 2, family_item)
            table.setItem(row_index, 3, level_item)
            table.setItem(row_index, 4, enabled_item)
        if rule_type == "site":
            self._update_site_rule_action_state()
        else:
            self._update_app_rule_action_state()

    def _refresh_duplicate_rule_warning(
        self,
        site_rules: list[object],
        app_rules: list[object],
    ) -> None:
        seen: dict[tuple[str, str, str, str, str, bool], str] = {}
        duplicate_targets: list[str] = []
        for rule in (*site_rules, *app_rules):
            key = rule_duplicate_equivalence_key(rule)
            display_target = canonical_rule_target_for_display(
                getattr(rule, "rule_type", ""),
                getattr(rule, "target", ""),
            )
            if key in seen:
                if display_target not in duplicate_targets:
                    duplicate_targets.append(display_target)
            else:
                seen[key] = display_target

        if not duplicate_targets:
            self.duplicate_rules_warning_label.setText("")
            return

        if len(duplicate_targets) == 1:
            message = f"Duplicate-equivalent rule: {duplicate_targets[0]}"
        else:
            message = "Duplicate-equivalent rules: " + "; ".join(
                duplicate_targets[:3]
            )
        self.duplicate_rules_warning_label.setText(message)

    def _refresh_utility_leakage_warning(self) -> None:
        warning = self._utility_leakage_warning_for_active_editor()
        self.utility_leakage_warning_label.setText(warning)

    def _utility_leakage_warning_for_active_editor(self) -> str:
        if self.rules_editor_tabs.currentIndex() == 1:
            return self._utility_leakage_warning_for_editor(
                "app",
                self.app_rules_table,
                self.app_input,
                self.app_allow_from_input,
                self.app_escape_family_input,
            )
        return self._utility_leakage_warning_for_editor(
            "site",
            self.site_rules_table,
            self.site_input,
            self.site_allow_from_input,
            self.site_escape_family_input,
        )

    def _utility_leakage_warning_for_editor(
        self,
        rule_type: str,
        table: QTableWidget,
        target_input: QLineEdit,
        allow_from_input: QComboBox,
        escape_family_input: QComboBox,
    ) -> str:
        target = _selected_target_from_table(table) or target_input.text()
        if not target.strip():
            return ""
        return utility_leakage_warning_for_rule(
            rule_type,
            target,
            _selected_combo_data(allow_from_input, "high"),
            _selected_combo_data(escape_family_input, "none"),
        )

    def _load_recent_attempts(self) -> None:
        decision_filter = _selected_combo_data(
            self.attempt_decision_filter_combo,
            "all",
        )
        access_filter = _selected_combo_data(self.attempt_access_filter_combo, "all")
        process_filter = self.attempt_process_filter_input.text()
        attempts = self.service.list_recent_dry_run_process_attempts(
            limit=10,
            decision=decision_filter,
            process_query=process_filter,
            access_level=access_filter,
        )
        if (
            decision_filter == "all"
            and access_filter == "all"
            and not process_filter.strip()
        ):
            attempts = self.service.list_recent_access_attempts(limit=10)
        self._load_attempt_summary()
        self.recent_attempts_table.setRowCount(0)
        for row_index, attempt in enumerate(attempts):
            self.recent_attempts_table.insertRow(row_index)
            full_text = (
                f"{attempt.occurred_at} - {attempt.target} - {attempt.decision} - "
                f"{attempt.purpose} - {attempt.escape_family} - "
                f"{attempt.access_level_at_attempt.upper()} - "
                f"{attempt.enforcement_mode} - {attempt.action_taken}"
            )
            items = (
                _build_table_item(_short_attempt_time(attempt.occurred_at), full_text),
                _build_table_item(attempt.target, full_text),
                _build_table_item(_attempt_status_display(attempt), full_text),
                _build_table_item(_metadata_display(attempt.purpose), full_text),
                _build_table_item(_metadata_display(attempt.escape_family), full_text),
                _build_table_item(attempt.access_level_at_attempt.upper(), full_text),
            )
            for column, item in enumerate(items):
                self.recent_attempts_table.setItem(row_index, column, item)
        has_any_attempts = self.recent_attempts_table.rowCount() > 0
        self.recent_attempts_table.setVisible(has_any_attempts)

    def _load_attempt_summary(self) -> None:
        summary = self.service.get_recent_attempt_summary(limit=20)
        has_attempts = summary.total_attempts > 0
        self.attempt_summary_empty_label.setVisible(not has_attempts)
        self.attempt_summary_detail_panel.setVisible(has_attempts)
        self.attempt_filter_panel.setVisible(has_attempts)
        self.attempt_summary_total_label.setVisible(has_attempts)
        self.attempt_summary_families_label.setVisible(has_attempts)
        self.attempt_summary_decisions_label.setVisible(has_attempts)
        self.attempt_summary_path_label.setVisible(has_attempts)
        self.attempt_summary_switching_label.setVisible(has_attempts)
        self.attempt_summary_helper_label.setVisible(True)
        self.attempt_summary_helper_label.setText(
            _format_attempt_helper_text(summary)
        )
        if not has_attempts:
            self.attempt_summary_empty_label.setText(
                "No Test Mode attempts logged yet."
            )
            return

        self.attempt_summary_total_label.setText(
            f"Today's attempts: {summary.total_attempts}"
        )
        self.attempt_summary_families_label.setText(
            "Top escape families: "
            + _format_count_summary(summary.by_escape_family)
        )
        self.attempt_summary_decisions_label.setText(
            "Decisions: " + _format_count_summary(summary.by_decision)
        )
        self.attempt_summary_path_label.setText(
            "Today's family path: "
            + (
                " -> ".join(summary.recent_family_sequence)
                if summary.recent_family_sequence
                else "None"
            )
        )
        self.attempt_summary_switching_label.setText(
            "Possible switching: "
            + ("Yes" if summary.possible_switching_detected else "No")
        )

    def _sync_selected_rule_inputs(
        self,
        rule_type: str,
        table: QTableWidget,
        allow_from_input: QComboBox,
        purpose_input: QComboBox,
        escape_family_input: QComboBox,
    ) -> None:
        target = _selected_target_from_table(table)
        if target is None:
            return
        selected_rule = next(
            (
                rule
                for rule in self.service.get_rules(rule_type)
                if rule.target == target
            ),
            None,
        )
        if selected_rule is None:
            return

        blockers = (
            QSignalBlocker(allow_from_input),
            QSignalBlocker(purpose_input),
            QSignalBlocker(escape_family_input),
        )
        try:
            _set_combo_data(allow_from_input, selected_rule.allow_from_level)
            _set_combo_data(purpose_input, selected_rule.purpose)
            _set_combo_data(escape_family_input, selected_rule.escape_family)
        finally:
            del blockers

    def _suggest_site_allow_from_for_new_rule(self) -> None:
        self._suggest_allow_from_for_new_rule(
            self.site_rules_table,
            self.site_purpose_input,
            self.site_allow_from_input,
        )

    def _suggest_app_allow_from_for_new_rule(self) -> None:
        self._suggest_allow_from_for_new_rule(
            self.app_rules_table,
            self.app_purpose_input,
            self.app_allow_from_input,
        )

    def _suggest_site_escape_family_for_new_rule(self) -> None:
        self._suggest_escape_family_for_new_rule(
            "site",
            self.site_rules_table,
            self.site_input,
            self.site_escape_family_input,
        )

    def _suggest_app_escape_family_for_new_rule(self) -> None:
        self._suggest_escape_family_for_new_rule(
            "app",
            self.app_rules_table,
            self.app_input,
            self.app_escape_family_input,
        )

    def _suggest_allow_from_for_new_rule(
        self,
        table: QTableWidget,
        purpose_input: QComboBox,
        allow_from_input: QComboBox,
    ) -> None:
        if _selected_target_from_table(table) is not None:
            return
        purpose = _selected_combo_data(purpose_input, "high_risk_escape")
        _set_combo_data(
            allow_from_input,
            recommended_allow_from_level_for_purpose(purpose),
        )

    def _suggest_escape_family_for_new_rule(
        self,
        rule_type: str,
        table: QTableWidget,
        target_input: QLineEdit,
        escape_family_input: QComboBox,
    ) -> None:
        if _selected_target_from_table(table) is not None:
            return
        if _selected_combo_data(escape_family_input, "none") != "none":
            return
        suggestion = suggest_escape_family_for_rule(rule_type, target_input.text())
        if suggestion != "none":
            _set_combo_data(escape_family_input, suggestion)


def _build_allow_from_combo() -> QComboBox:
    combo = QComboBox()
    combo.addItem("LOW / Focus", "low")
    combo.addItem("MEDIUM / Utility", "medium")
    combo.addItem("HIGH / Recreation", "high")
    combo.setCurrentIndex(combo.findData("high"))
    return combo


def _build_rule_purpose_combo() -> QComboBox:
    combo = QComboBox()
    for purpose in RULE_PURPOSE_OPTIONS:
        combo.addItem(_metadata_display(purpose), purpose)
    combo.setCurrentIndex(combo.findData("high_risk_escape"))
    return combo


def _build_escape_family_combo() -> QComboBox:
    combo = QComboBox()
    for family in ESCAPE_FAMILY_OPTIONS:
        combo.addItem(_metadata_display(family), family)
    combo.setCurrentIndex(combo.findData("none"))
    return combo


def _build_tab_page() -> QWidget:
    tab = QWidget()
    layout = QVBoxLayout(tab)
    reset_layout(layout)
    layout.setSpacing(PAGE_SPACING)
    top_aligned(layout)
    return tab


def _build_rules_table(object_name: str) -> QTableWidget:
    table = QTableWidget(0, 5)
    table.setObjectName(object_name)
    table.setHorizontalHeaderLabels(
        ["Target", "Purpose", "Escape family", "Allowed from", "Enabled"]
    )
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setMinimumHeight(150)
    table.setMaximumHeight(210)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(30)
    horizontal_header = table.horizontalHeader()
    horizontal_header.setStretchLastSection(False)
    horizontal_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    horizontal_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(1, 172)
    table.setColumnWidth(2, 150)
    table.setColumnWidth(3, 118)
    table.setColumnWidth(4, 74)
    return table


def _build_recent_attempts_table() -> QTableWidget:
    table = QTableWidget(0, 6)
    table.setObjectName("recentAttemptsTable")
    table.setHorizontalHeaderLabels(
        ["Time", "Target", "Decision", "Purpose", "Escape family", "Access"]
    )
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setMinimumHeight(92)
    table.setMaximumHeight(140)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(30)
    horizontal_header = table.horizontalHeader()
    horizontal_header.setStretchLastSection(False)
    horizontal_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    horizontal_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    horizontal_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(0, 136)
    table.setColumnWidth(2, 178)
    table.setColumnWidth(3, 160)
    table.setColumnWidth(4, 140)
    table.setColumnWidth(5, 80)
    return table


def _build_table_item(text: str, tooltip: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setToolTip(tooltip)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _selected_target_from_table(table: QTableWidget) -> str | None:
    row = table.currentRow()
    if row < 0:
        return None
    item = table.item(row, 0)
    if item is None:
        return None
    stored_target = item.data(_RULE_STORED_TARGET_ROLE)
    if isinstance(stored_target, str) and stored_target:
        return stored_target
    return item.text()


def _selected_rule_id_from_table(table: QTableWidget) -> int | None:
    row = table.currentRow()
    if row < 0:
        return None
    item = table.item(row, 0)
    if item is None:
        return None
    value = item.data(Qt.ItemDataRole.UserRole)
    return value if isinstance(value, int) else None


def _configure_rule_controls(
    target_input: QLineEdit,
    allow_from_input: QComboBox,
    purpose_input: QComboBox,
    escape_family_input: QComboBox,
    *buttons: QPushButton,
) -> None:
    target_input.setMinimumHeight(CONTROL_HEIGHT)
    target_input.setMinimumWidth(140)
    target_input.setSizePolicy(
        QSizePolicy.Policy.Expanding,
        QSizePolicy.Policy.Fixed,
    )
    allow_from_input.setMinimumHeight(CONTROL_HEIGHT)
    allow_from_input.setFixedWidth(110)
    purpose_input.setMinimumHeight(CONTROL_HEIGHT)
    purpose_input.setFixedWidth(178)
    escape_family_input.setMinimumHeight(CONTROL_HEIGHT)
    escape_family_input.setFixedWidth(158)
    for button in buttons:
        button.setMinimumHeight(CONTROL_HEIGHT)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    if buttons:
        set_button_role(buttons[0], "primary")
    for button in buttons[1:]:
        role = "danger" if "Remove" in button.text() else "quiet"
        set_button_role(button, role)


def _build_rule_form_row(
    target_label_text: str,
    target_input: QLineEdit,
    purpose_input: QComboBox,
    escape_family_input: QComboBox,
    allow_from_input: QComboBox,
    add_button: QPushButton,
) -> QVBoxLayout:
    form = QVBoxLayout()
    reset_layout(form)
    form.setSpacing(SMALL_GAP)

    target_row = QHBoxLayout()
    reset_layout(target_row)
    target_row.setSpacing(MEDIUM_GAP)

    target_group = _build_labeled_control(target_label_text, target_input)
    target_row.addLayout(target_group, 1)
    target_row.addWidget(add_button, 0, Qt.AlignmentFlag.AlignBottom)

    metadata_row = QHBoxLayout()
    reset_layout(metadata_row)
    metadata_row.setSpacing(MEDIUM_GAP)
    allow_group = _build_labeled_control("Allowed from", allow_from_input)
    if not purpose_input.isHidden():
        metadata_row.addLayout(_build_labeled_control("Purpose", purpose_input))
    if not escape_family_input.isHidden():
        metadata_row.addLayout(
            _build_labeled_control("Escape family", escape_family_input)
        )
    metadata_row.addLayout(allow_group)
    metadata_row.addStretch(1)

    form.addLayout(target_row)
    form.addLayout(metadata_row)
    return form


def _build_labeled_control(label_text: str, widget: QWidget) -> QVBoxLayout:
    group = QVBoxLayout()
    reset_layout(group)
    group.setSpacing(SMALL_GAP)
    label = QLabel(label_text)
    label.setObjectName("rulesFieldLabel")
    label.setWordWrap(False)
    group.addWidget(label)
    group.addWidget(widget)
    return group


def _build_layout_panel(
    inner_layout: QHBoxLayout | QVBoxLayout,
    *,
    role: str = "metric",
) -> QFrame:
    panel = QFrame()
    panel.setObjectName("SubPanel")
    panel.setProperty("role", role)
    layout = QVBoxLayout(panel)
    reset_layout(layout)
    layout.addLayout(inner_layout)
    return panel


def _build_form_panel(form_layout: QVBoxLayout) -> QFrame:
    return _build_layout_panel(form_layout, role="compact")


def _build_action_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    reset_layout(row)
    row.setSpacing(SMALL_GAP)
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


def _configure_buttons(*buttons: QPushButton) -> None:
    for button in buttons:
        button.setMinimumHeight(CONTROL_HEIGHT)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        role = "primary" if "Start" in button.text() else "quiet"
        set_button_role(button, role)


def _selected_combo_data(combo: QComboBox, fallback: str) -> str:
    data = combo.currentData()
    return data if isinstance(data, str) else fallback


def _selected_combo_int(combo: QComboBox, fallback: int) -> int:
    data = combo.currentData()
    return data if isinstance(data, int) else fallback


def _set_combo_data(combo: QComboBox, value: str) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _metadata_display(value: str) -> str:
    return value.replace("_", " ").title()


def _attempt_action_display(value: str) -> str:
    labels = {
        "none": "None",
        "terminate_requested": "Terminate requested",
        "skipped_protected": "Skipped protected",
        "access_denied": "Access denied",
        "failed": "Failed",
        "not_found": "Not found",
    }
    return labels.get(value, _metadata_display(value))


def _attempt_decision_display(value: str) -> str:
    labels = {
        "would_block": "Would block",
        "would_allow": "Would allow",
        "allowed_by_planned_use_pass": "Allowed by pass",
    }
    return labels.get(value, _metadata_display(value))


def _attempt_status_display(attempt: object) -> str:
    if getattr(attempt, "enforcement_mode", "") == "real_process_blocking":
        action_taken = getattr(attempt, "action_taken", "none")
        if action_taken and action_taken != "none":
            return _attempt_action_display(action_taken)
        if getattr(attempt, "decision", "") == "would_allow":
            return "Allowed"
    return _attempt_decision_display(getattr(attempt, "decision", ""))


def _format_count_summary(counts: dict[str, int]) -> str:
    if not counts:
        return "None"
    return ", ".join(
        f"{_metadata_display(key)} {value}"
        for key, value in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    )


def _format_attempt_helper_text(summary: object) -> str:
    explanation = getattr(summary, "pattern_explanation", "")
    next_action = getattr(summary, "suggested_next_action", "")
    if explanation and next_action:
        return f"{explanation} {next_action}"
    return explanation or next_action


def _short_attempt_time(value: str) -> str:
    return format_attempt_local_time(value)


def _format_preview_targets(
    targets: list[str],
    rules_by_target: dict[str, object],
    active_pass: object | None = None,
) -> str:
    if not targets:
        return "None"
    return ", ".join(
        _format_preview_target(target, rules_by_target, active_pass)
        for target in targets
    )


def _format_preview_target(
    target: str,
    rules_by_target: dict[str, object],
    active_pass: object | None = None,
) -> str:
    rule = rules_by_target.get(target)
    if rule is None:
        return target
    purpose = getattr(rule, "purpose", "high_risk_escape")
    escape_family = getattr(rule, "escape_family", "none")
    if _pass_matches_preview_target(active_pass, rule):
        return f"{target} ({purpose}, {escape_family}, planned-use pass)"
    return f"{target} ({purpose}, {escape_family})"


def _pass_matches_preview_target(active_pass: object | None, rule: object) -> bool:
    if active_pass is None:
        return False
    return getattr(active_pass, "rule_id", None) == getattr(rule, "id", None)


def _format_active_planned_use_pass(active_pass: object | None) -> str:
    if active_pass is None:
        return "Active planned-use pass: none"
    target_type = _planned_use_target_type_label(
        str(getattr(active_pass, "target_type", ""))
    )
    return (
        "Active planned-use pass: "
        f"{getattr(active_pass, 'target', '')} ({target_type}) - "
        f"until {_short_attempt_time(getattr(active_pass, 'expires_at', ''))} - "
        f"{getattr(active_pass, 'reason', '')}"
    )


def _planned_use_target_type_label(target_type: str) -> str:
    if target_type == "site":
        return "website"
    if target_type == "app":
        return "app"
    return "target"


def _planned_use_unavailable_reason(
    *,
    has_selection: bool,
    has_reason: bool,
    has_active_pass: bool,
) -> str:
    if has_active_pass:
        return "Another planned-use pass is already active."
    if not has_selection:
        return "Select a rule first."
    if not has_reason:
        return "Enter a planned-use reason."
    return "Planned-use pass is unavailable."


def _build_card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    card = CardFrame(title)
    title_label = card.title_label
    if title_label is None:
        raise RuntimeError("Rules cards require a title label.")
    return card, card.card_layout, title_label


def _rules_stylesheet() -> str:
    return (
        """
    QLabel#rulesTestModeBadge {
        border-radius: 999px;
        padding: 4px 10px;
    }
    """
        + common_stylesheet()
        + modern_common_stylesheet()
    )
