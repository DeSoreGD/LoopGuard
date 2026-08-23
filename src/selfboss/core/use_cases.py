"""Application use cases for the local LoopGuard MVP."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

from selfboss.config import is_production_app_mode
from selfboss.core.models import (
    AccessLevel,
    AccessAttemptDecision,
    AccessAttemptRecord,
    AccessAttemptSource,
    AccessAttemptSummary,
    AccessRuntimeState,
    AppSettings,
    DayOutcomeCloseKind,
    DayState,
    DryRunProcessAttemptSummary,
    EnforcementMode,
    EnforcementModeOption,
    EnforcementReadinessCheck,
    EnforcementReadinessGroup,
    EnforcementStatus,
    EscapeFamily,
    PlannedUsePassRecord,
    RewardLedgerEntry,
    RewardLedgerEvent,
    RewardEventType,
    RulePurpose,
    Task,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.platform.hosts_blocker import (
    HostsActionResult,
    HostsBlocker,
    add_or_replace_managed_block,
    generate_hosts_entries,
    hosts_blocking_readiness_checks,
)
from selfboss.platform.process_blocker import (
    ProcessBlocker,
    is_protected_process_name,
    process_blocking_readiness_checks,
)
from selfboss.platform.recovery import recovery_readiness_checks
from selfboss.platform.test_mode import test_mode_readiness_checks
from selfboss.core.rewards import RewardService
from selfboss.core.state_machine import AccessStateMachine
from selfboss.data.repositories import (
    AccessAttemptRepository,
    AppSettingsRepository,
    DayOutcomeRepository,
    DayStateRepository,
    HighSessionRecord,
    HighSessionRepository,
    PlannedUsePassRepository,
    RewardLedgerRepository,
    RuleRecord,
    RuleRepository,
    TaskRepository,
)


SURRENDER_STRICTNESS_DELAYS = {
    "low": 3 * 60 * 60,
    "medium": 6 * 60 * 60,
    "high": 9 * 60 * 60,
}
DEFAULT_SURRENDER_STRICTNESS = "medium"
SURRENDER_DELAY_SECONDS = SURRENDER_STRICTNESS_DELAYS[DEFAULT_SURRENDER_STRICTNESS]
TASK_COMPLETION_UNAVAILABLE_AFTER_SURRENDER = (
    "Task completion is unavailable after Surrender."
)
TASK_COMPLETION_UNAVAILABLE_AFTER_END_DAY = (
    "Task completion is unavailable after End Day."
)
TASK_COMPLETION_CLAIM_DELAY_SECONDS = 3 * 60
TASK_COMPLETION_CLAIM_REQUIRED = "Claim Done before confirming completion."
TASK_COMPLETION_CLAIM_ALREADY_PENDING = (
    "Confirm or cancel the current completion claim first."
)
END_DAY_REQUIRES_STARTED_DAY = "Start Day before ending the day."
END_DAY_ALREADY_CLOSED = "Day is already ended."
END_DAY_REQUIRES_COMPLETED_MAIN = (
    "End Day is available after completing today's MAIN task."
)
END_DAY_HIGH_ACTIVE = "End active HIGH access before ending the day."
END_DAY_PLANNED_USE_ACTIVE = (
    "End active planned-use pass before ending the day."
)
END_DAY_CONFIRM_DELAY_SECONDS = 60
END_DAY_CONFIRM_WAIT_MESSAGE = (
    "End Day will be available after the 60-second review delay."
)
DAY_CLOSE_REVIEW_NORMAL = "normal_end_day"
DAY_CLOSE_REVIEW_RECOVERY = "recovery_close"
SOFT_START_LOCKED_AFTER_START_DAY = (
    "Soft Start can only be changed before Start Day. "
    "Changes apply to the next day/session."
)
SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY = (
    "Locked after Start Day. Change it before tomorrow's Start Day."
)
_SOFT_START_ACTIVE_DAY_STARTED_AT_KEY = "soft_start_active_day_started_at"
_SOFT_START_ACTIVE_DAY_ENABLED_KEY = "soft_start_active_day_enabled"
_SOFT_START_ACTIVE_DAY_DURATION_KEY = "soft_start_active_day_duration_minutes"
_PERSONAL_TRIAL_QA_SETTINGS_KEY = "personal_trial_qa_checklist_v1"
_BROWSER_SETUP_INTRO_SEEN_KEY = "browser_setup_intro_seen"
_END_DAY_PENDING_STARTED_AT_KEY = "end_day_pending_started_at"
_HIGH_NOTIFICATION_SESSION_KEY = "high_notification_session_key"
_HIGH_WARNING_SENT_KEY = "high_warning_sent"
_HIGH_END_SENT_KEY = "high_end_sent"
CONFIG_EXPORT_VERSION = 1
CONFIG_EXPORT_APP = "SelfBoss"
CONFIG_EXPORT_WARNING = (
    "Local LoopGuard configuration export. It includes rules and settings only; "
    "it does not include browsing history or logs."
)
CONFIG_IMPORT_LOCKED_AFTER_START_DAY = (
    "Configuration import is locked after Start Day. "
    "Import before tomorrow's Start Day."
)
START_DAY_BROWSER_REQUIRED = (
    "Chrome setup is required before Start Day. Open Browser Setup and connect "
    "the LoopGuard Chrome extension."
)
SUPPORTED_TASK_ALLOWED_URL_SCHEMES = {"http", "https"}
CONFIG_EXPORT_SETTING_KEYS = (
    "enforcement_mode",
    "surrender_strictness",
    "soft_start_enabled",
    "soft_start_duration_minutes",
    "daily_recreation_cap_minutes",
    _PERSONAL_TRIAL_QA_SETTINGS_KEY,
)
PERSONAL_TRIAL_QA_STEP_DEFINITIONS = (
    ("chrome_extension_loaded", "Chrome extension loaded"),
    ("native_host_verified", "Native host repair/register verified"),
    ("browser_heartbeat_connected", "Browser heartbeat connected"),
    ("incognito_allowed", "Incognito allowed"),
    ("youtube_spa_detector_seen", "YouTube SPA detector seen"),
    ("shorts_path_rule_tested", "Shorts/path rule tested"),
    (
        "process_blocking_tested",
        "Process blocking tested with disposable app rule",
    ),
    (
        "hosts_blocking_tested",
        "Hosts blocking tested with disposable domain rule",
    ),
    ("recovery_safe_mode_understood", "Recovery/Safe Mode location understood"),
)
RULE_PURPOSE_OPTIONS = tuple(purpose.value for purpose in RulePurpose)
ESCAPE_FAMILY_OPTIONS = tuple(family.value for family in EscapeFamily)
DEFAULT_RULE_PURPOSE = RulePurpose.HIGH_RISK_ESCAPE.value
DEFAULT_ESCAPE_FAMILY = EscapeFamily.NONE.value
UTILITY_LEAKAGE_WARNING = (
    "Utility mode warning: this looks like an escape target. HIGH is recommended."
)
UTILITY_LEAKAGE_ESCAPE_FAMILIES = {
    EscapeFamily.VIDEO.value,
    EscapeFamily.GAMES.value,
    EscapeFamily.SOCIAL.value,
    EscapeFamily.CHAT.value,
    EscapeFamily.READING_BINGE.value,
    EscapeFamily.RANDOM_BROWSING.value,
    EscapeFamily.LAUNCHER.value,
}
RULE_PURPOSE_DEFAULT_ALLOW_FROM_LEVEL = {
    RulePurpose.WORK_TOOL.value: AccessLevel.LOW.value,
    RulePurpose.STUDY_REFERENCE.value: AccessLevel.MEDIUM.value,
    RulePurpose.ESSENTIAL_COMMUNICATION.value: AccessLevel.MEDIUM.value,
    RulePurpose.INTENTIONAL_REST.value: AccessLevel.MEDIUM.value,
    RulePurpose.CONTROLLED_RECREATION.value: AccessLevel.HIGH.value,
    RulePurpose.HIGH_RISK_ESCAPE.value: AccessLevel.HIGH.value,
    RulePurpose.COMPULSIVE_STIMULATION.value: AccessLevel.HIGH.value,
    RulePurpose.GATEWAY_APP.value: AccessLevel.HIGH.value,
    RulePurpose.RECOVERY_SAFETY.value: AccessLevel.LOW.value,
}
PLANNED_USE_PASS_MIN_SECONDS = 5 * 60
PLANNED_USE_PASS_MAX_SECONDS = 25 * 60
PLANNED_USE_PASS_MIN_REASON_LENGTH = 8
HIGH_SESSION_MAX_MINUTES = 45
DAILY_RECREATION_CAP_DEFAULT_MINUTES = 90
DAILY_RECREATION_CAP_MIN_MINUTES = 15
DAILY_RECREATION_CAP_MAX_MINUTES = 300
HIGH_DAILY_MAX_MINUTES = DAILY_RECREATION_CAP_DEFAULT_MINUTES
HIGH_COOLDOWN_SECONDS = 5 * 60
HIGH_INTENT_MIN_NONSPACE_CHARS = 5
HIGH_DAILY_CAP_REACHED = "Recreation cap reached for today."
HIGH_BROWSER_BLOCKING_NOT_READY = (
    "Browser blocking is not ready. Reconnect Chrome before starting Recreation."
)
REST_TOKEN_EARN_STREAK_DAYS = 3
REST_TOKEN_MAX_COUNT = 1
REST_TOKEN_PRE_START_ONLY = "Earned rest is only available before Start Day."
REST_TOKEN_NONE_AVAILABLE = "No Rest Token available."
REST_TOKEN_PENDING_CLAIM = "Confirm or cancel the current completion claim first."
ARMED_DRY_RUN_PROCESS_RATE_LIMIT_SECONDS = 60
REAL_PROCESS_BLOCKING_ACTION_COOLDOWN_SECONDS = 3
ENFORCEMENT_MODE_LABELS = {
    EnforcementMode.PREVIEW_ONLY: "Preview Only",
    EnforcementMode.ARMED_DRY_RUN: "Armed Dry Run",
    EnforcementMode.REAL_PROCESS_BLOCKING: "Real Process Blocking",
    EnforcementMode.REAL_HOSTS_BLOCKING: "Real Hosts Blocking",
    EnforcementMode.FULL_ENFORCEMENT: "Full Enforcement",
}
PROCESS_ENFORCING_MODES = {
    EnforcementMode.REAL_PROCESS_BLOCKING,
    EnforcementMode.FULL_ENFORCEMENT,
}
HOSTS_ENFORCING_MODES = {
    EnforcementMode.REAL_HOSTS_BLOCKING,
    EnforcementMode.FULL_ENFORCEMENT,
}
BROWSER_HEARTBEAT_FILE_NAME = "browser_heartbeat.json"
BROWSER_HEARTBEAT_STALE_SECONDS = 120
UNMANAGED_BROWSER_PROCESS_NAMES = (
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "opera.exe",
    "opera_gx.exe",
    "vivaldi.exe",
)
UNMANAGED_BROWSER_GUARD_COOLDOWN_SECONDS = 60


@dataclass(frozen=True)
class TaskCompletionResult:
    """Result of completing a task through the application service."""

    task: Task
    day_state: DayState
    reward_entry: RewardLedgerEntry | None


@dataclass(frozen=True)
class TaskCompletionClaimResult:
    """Result of claiming a task as done before final confirmation."""

    task: Task
    available_at: str
    remaining_seconds: int


@dataclass(frozen=True)
class BrowserEscapeTargetSummary:
    """Compact count for a browser escape target shown on the dashboard."""

    display_target: str
    count: int


@dataclass(frozen=True)
class BrowserEscapeSummary:
    """Privacy-minimal summary of browser attempts logged today."""

    total_attempts: int
    last_attempt: AccessAttemptRecord | None
    top_targets: tuple[BrowserEscapeTargetSummary, ...]
    has_attempts: bool
    message: str


@dataclass(frozen=True)
class DayCloseReviewSummary:
    """Transient factual review shown after closing today's active loop."""

    close_type: str
    title: str
    main_completed: bool
    planned_done_count: int
    planned_task_count: int
    unplanned_done_count: int
    unplanned_task_count: int
    recreation_used_seconds: int
    recent_attempt_count: int
    recent_family_path: str
    active_planned_use_pass_target: str | None
    active_planned_use_pass_type: str | None
    next_action: str


@dataclass(frozen=True)
class ConfigImportPreview:
    """Summary shown before applying a local configuration import."""

    rule_count: int
    setting_count: int
    message: str


@dataclass(frozen=True)
class ConfigImportResult:
    """Summary returned after applying a local configuration import."""

    rule_count: int
    setting_count: int
    message: str


@dataclass(frozen=True)
class PersonalTrialQaItem:
    """One persisted manual QA checkbox for personal trial readiness."""

    key: str
    label: str
    checked: bool


@dataclass(frozen=True)
class PersonalTrialQaChecklist:
    """Persisted local manual QA state for personal trial readiness."""

    items: tuple[PersonalTrialQaItem, ...]
    completed_count: int
    total_count: int
    status: str
    message: str


@dataclass(frozen=True)
class DashboardSnapshot:
    """Current values displayed by the dashboard."""

    access_level: AccessLevel
    reward_balance_minutes: int
    reward_balance_seconds: int
    test_mode: bool
    safe_mode: bool
    recovery_mode: bool
    main_task: Task | None
    day_started: bool
    day_closed: bool
    day_status_label: str
    can_start_day: bool
    start_day_unavailable_reason: str
    rest_token_count: int
    can_use_rest_token: bool
    rest_token_unavailable_reason: str
    can_end_day: bool
    end_day_unavailable_reason: str
    end_day_pending: bool
    end_day_remaining_seconds: int
    end_day_confirm_label: str
    can_recovery_close_today: bool
    recovery_close_today_unavailable_reason: str
    day_summary_label: str
    planned_task_count: int
    planned_pending_count: int
    planned_done_count: int
    unplanned_task_count: int
    unplanned_pending_count: int
    unplanned_done_count: int
    high_remaining_seconds: int
    high_minutes_total: int
    high_intent: str | None
    high_active: bool
    high_daily_cap_minutes: int
    high_daily_used_seconds: int
    high_daily_remaining_seconds: int
    high_daily_cap_reached: bool
    high_cooldown_remaining_seconds: int
    high_cooldown_active: bool
    bad_day_active_today: bool
    surrender_active_today: bool
    surrender_active: bool
    effective_restriction_state: str
    surrender_available: bool
    surrender_remaining_seconds: int
    surrender_strictness: str
    surrender_delay_seconds: int
    soft_start_enabled: bool
    soft_start_active: bool
    soft_start_remaining_seconds: int
    soft_start_duration_minutes: int
    day_started_at: str | None
    day_ended_at: str | None
    recent_attempt_summary: AccessAttemptSummary
    browser_escape_summary: BrowserEscapeSummary
    active_planned_use_pass: PlannedUsePassRecord | None
    active_planned_use_pass_remaining_seconds: int
    enforcement_status: EnforcementStatus
    hosts_blocking_status: "HostsBlockingRuntimeStatus"
    browser_integration_status: "BrowserIntegrationStatus"
    website_high_release_status: "WebsiteHighReleaseStatus"
    recent_dry_run_process_attempts: list[AccessAttemptRecord]
    dry_run_process_summary: DryRunProcessAttemptSummary


@dataclass(frozen=True)
class BlockingPreview:
    """Dry-run preview of rules that would be blocked."""

    access_level: AccessLevel
    test_mode: bool
    sites: list[str]
    apps: list[str]
    blocked_sites: list[str]
    blocked_apps: list[str]
    allowed_sites: list[str]
    allowed_apps: list[str]
    message: str
    restriction_state: str
    active_planned_use_pass: PlannedUsePassRecord | None


@dataclass(frozen=True)
class HostsBlockingDryRunPreview:
    """Dry-run hosts entries that P35 could apply without writing files."""

    blocked_domains: list[str]
    hosts_entries: list[str]
    managed_section: str
    message: str


@dataclass(frozen=True)
class HostsBlockingRuntimeStatus:
    """Compact status for real hosts blocking."""

    status: str
    active: bool
    blocked_domain_count: int
    message: str
    last_action_status: str
    blocked_domain_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrowserIntegrationStatus:
    """Read-only desktop view of the browser extension/native-host heartbeat."""

    connection_status: str
    connected: bool
    extension_heartbeat_status: str
    native_host_status: str
    native_host_prepared_status: str
    browser_blocking_ready: bool
    browser: str
    context: str
    native_messaging_status: str
    incognito_status: str
    browser_blocking: str
    browser_blocking_available: str
    dnr_status: str
    dnr_session_rule_count: int | None
    dnr_last_update_status: str
    dnr_last_error: str
    youtube_spa_status: str
    extension_version: str
    last_heartbeat_at: str | None
    last_heartbeat_age_seconds: int | None
    browser_high_safety: str
    next_action: str
    message: str


@dataclass(frozen=True)
class HighNotificationEvent:
    """One non-modal HIGH session notification event."""

    event_type: str
    title: str
    message: str


@dataclass(frozen=True)
class WebsiteHighReleaseStatus:
    """Status of website hosts release during HIGH or planned-use pass access."""

    status: str
    message: str
    trusted_browser_ready: bool
    held_closed_targets: tuple[str, ...]
    other_browsers_status: str


@dataclass(frozen=True)
class PersonalUseReadinessItem:
    """One compact status row for the personal-use readiness checklist."""

    label: str
    status: str
    detail: str


@dataclass(frozen=True)
class PersonalUseReadinessChecklist:
    """Read-only checklist for a local personal trial."""

    verdict: str
    summary: str
    items: tuple[PersonalUseReadinessItem, ...]
    manual_qa_status: str


@dataclass(frozen=True)
class _SiteAccessTargets:
    blocked_sites: list[str]
    allowed_sites: list[str]
    held_closed_sites: list[str]
    trust_required_sites: list[str]


@dataclass(frozen=True)
class HighAccessOption:
    """A selectable HIGH access duration."""

    minutes: int
    label: str
    enabled: bool


@dataclass(frozen=True)
class HighAccessOptions:
    """Reward wallet state for starting HIGH access."""

    available_minutes: int
    available_seconds: int
    max_session_minutes: int
    daily_cap_minutes: int
    daily_used_seconds: int
    daily_remaining_seconds: int
    daily_cap_reached: bool
    high_cooldown_remaining_seconds: int
    high_cooldown_active: bool
    high_active: bool
    can_start_high: bool
    unavailable_reason: str
    options: list[HighAccessOption]


@dataclass(frozen=True)
class _StarterRulePreset:
    """A fixed dry-run starter rule preset."""

    rule_type: str
    target: str
    allow_from_level: str
    purpose: str
    escape_family: str


@dataclass(frozen=True)
class StarterRulePresetResult:
    """Summary of applying starter rule presets."""

    created_count: int
    skipped_existing_count: int
    failed_presets: tuple[str, ...]


STARTER_RULE_PRESETS = (
    _StarterRulePreset(
        "site",
        "youtube.com",
        AccessLevel.HIGH.value,
        RulePurpose.COMPULSIVE_STIMULATION.value,
        EscapeFamily.VIDEO.value,
    ),
    _StarterRulePreset(
        "site",
        "www.youtube.com",
        AccessLevel.HIGH.value,
        RulePurpose.COMPULSIVE_STIMULATION.value,
        EscapeFamily.VIDEO.value,
    ),
    _StarterRulePreset(
        "site",
        "youtube.com/shorts/*",
        AccessLevel.HIGH.value,
        RulePurpose.COMPULSIVE_STIMULATION.value,
        EscapeFamily.VIDEO.value,
    ),
    _StarterRulePreset(
        "site",
        "www.youtube.com/shorts/*",
        AccessLevel.HIGH.value,
        RulePurpose.COMPULSIVE_STIMULATION.value,
        EscapeFamily.VIDEO.value,
    ),
    _StarterRulePreset(
        "site",
        "m.youtube.com/shorts/*",
        AccessLevel.HIGH.value,
        RulePurpose.COMPULSIVE_STIMULATION.value,
        EscapeFamily.VIDEO.value,
    ),
    _StarterRulePreset(
        "site",
        "discord.com",
        AccessLevel.HIGH.value,
        RulePurpose.HIGH_RISK_ESCAPE.value,
        EscapeFamily.CHAT.value,
    ),
    _StarterRulePreset(
        "site",
        "reddit.com",
        AccessLevel.HIGH.value,
        RulePurpose.HIGH_RISK_ESCAPE.value,
        EscapeFamily.RANDOM_BROWSING.value,
    ),
    _StarterRulePreset(
        "site",
        "mangadex.org",
        AccessLevel.HIGH.value,
        RulePurpose.HIGH_RISK_ESCAPE.value,
        EscapeFamily.READING_BINGE.value,
    ),
    _StarterRulePreset(
        "site",
        "mangalib.me",
        AccessLevel.HIGH.value,
        RulePurpose.HIGH_RISK_ESCAPE.value,
        EscapeFamily.READING_BINGE.value,
    ),
    _StarterRulePreset(
        "app",
        "steam.exe",
        AccessLevel.HIGH.value,
        RulePurpose.GATEWAY_APP.value,
        EscapeFamily.LAUNCHER.value,
    ),
    _StarterRulePreset(
        "app",
        "steamwebhelper.exe",
        AccessLevel.HIGH.value,
        RulePurpose.GATEWAY_APP.value,
        EscapeFamily.LAUNCHER.value,
    ),
    _StarterRulePreset(
        "app",
        "discord.exe",
        AccessLevel.HIGH.value,
        RulePurpose.HIGH_RISK_ESCAPE.value,
        EscapeFamily.CHAT.value,
    ),
    _StarterRulePreset(
        "app",
        "epicgameslauncher.exe",
        AccessLevel.HIGH.value,
        RulePurpose.GATEWAY_APP.value,
        EscapeFamily.LAUNCHER.value,
    ),
    _StarterRulePreset(
        "app",
        "riotclientservices.exe",
        AccessLevel.HIGH.value,
        RulePurpose.GATEWAY_APP.value,
        EscapeFamily.LAUNCHER.value,
    ),
    _StarterRulePreset(
        "app",
        "battlenet.exe",
        AccessLevel.HIGH.value,
        RulePurpose.GATEWAY_APP.value,
        EscapeFamily.LAUNCHER.value,
    ),
)


class SelfBossAppService:
    """Coordinate repositories and domain services for the GUI."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        tasks: TaskRepository,
        day_state: DayStateRepository,
        rewards: RewardLedgerRepository,
        high_sessions: HighSessionRepository,
        rules: RuleRepository | None = None,
        access_attempts: AccessAttemptRepository | None = None,
        planned_use_passes: PlannedUsePassRepository | None = None,
        app_settings: AppSettingsRepository | None = None,
        day_outcomes: DayOutcomeRepository | None = None,
        reward_service: RewardService | None = None,
        state_machine: AccessStateMachine | None = None,
        process_blocker: ProcessBlocker | None = None,
        hosts_blocker: HostsBlocker | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.tasks = tasks
        self.day_state = day_state
        self.rewards = rewards
        self.high_sessions = high_sessions
        self.rules = rules
        self.access_attempts = access_attempts or AccessAttemptRepository(
            day_state.connection
        )
        self.planned_use_passes = (
            planned_use_passes or PlannedUsePassRepository(day_state.connection)
        )
        self.app_settings = app_settings or AppSettingsRepository(day_state.connection)
        self.day_outcomes = day_outcomes or DayOutcomeRepository(day_state.connection)
        self.reward_service = reward_service or RewardService()
        self.state_machine = state_machine or AccessStateMachine(
            reward_service=self.reward_service
        )
        self.process_blocker = process_blocker or ProcessBlocker()
        self.hosts_blocker = hosts_blocker or HostsBlocker()
        self._now_provider = now_provider
        self._high_started_at: datetime | None = None
        self._high_minutes_total = 0
        self._high_medium_unlocked = False
        self._real_process_action_attempts: dict[tuple[int, str], datetime] = {}
        self._last_hosts_signature: tuple[str, tuple[str, ...]] | None = None
        self._last_hosts_action_result: HostsActionResult | None = None
        self._suppressed_hosts_permission_signature: (
            tuple[str, tuple[str, ...]] | None
        ) = None
        self._unmanaged_browser_guard_attempts: dict[str, datetime] = {}
        self._ensure_current_day()
        self._expire_high_if_needed(self._now())

    def get_enforcement_mode(self) -> EnforcementMode:
        """Return the persisted selected enforcement mode."""
        return EnforcementMode(self.app_settings.get_enforcement_mode())

    def set_enforcement_mode(self, mode: str | EnforcementMode) -> EnforcementStatus:
        """Persist a safe enforcement mode transition after validation."""
        target_mode = _normalize_enforcement_mode(mode)
        previous_mode = self.get_enforcement_mode()
        current_status = self.get_enforcement_status(selected_mode=target_mode)

        if target_mode in {
            EnforcementMode.PREVIEW_ONLY,
            EnforcementMode.ARMED_DRY_RUN,
        }:
            self.app_settings.set_enforcement_mode(target_mode.value)
            if previous_mode in HOSTS_ENFORCING_MODES:
                self.run_real_hosts_blocking_cycle(force=True)
            return self.get_enforcement_status()

        if self.settings.safe_mode:
            raise ValueError(
                "Real enforcement modes are locked while Safe Mode is active."
            )
        if self.settings.recovery_mode:
            raise ValueError(
                "Real enforcement modes are locked while Recovery Mode is active."
            )

        required_group = _required_readiness_for_mode(current_status, target_mode)
        if required_group is not None and not required_group.ready:
            first_missing = (
                required_group.missing_items[0]
                if required_group.missing_items
                else "readiness checks are incomplete"
            )
            raise ValueError(
                f"{ENFORCEMENT_MODE_LABELS[target_mode]} is locked: {first_missing}"
            )

        self.app_settings.set_enforcement_mode(target_mode.value)
        if (
            target_mode in HOSTS_ENFORCING_MODES
            or previous_mode in HOSTS_ENFORCING_MODES
        ):
            self.run_real_hosts_blocking_cycle(force=True)
        return self.get_enforcement_status()

    def get_enforcement_status(
        self,
        selected_mode: EnforcementMode | None = None,
    ) -> EnforcementStatus:
        """Return conservative staged enforcement readiness."""
        selected = selected_mode or self.get_enforcement_mode()
        return _build_enforcement_status(
            selected_mode=selected,
            safe_mode=self.settings.safe_mode,
            recovery_mode=self.settings.recovery_mode,
        )

    def get_personal_use_readiness_checklist(
        self,
        *,
        now: datetime | None = None,
    ) -> PersonalUseReadinessChecklist:
        """Return a read-only v0.1 personal trial readiness checklist."""
        current_now = now or self._now()
        enforcement = self.get_enforcement_status()
        browser = self.get_browser_integration_status(now=current_now)
        qa_checklist = self.get_personal_trial_qa_checklist()

        items = (
            PersonalUseReadinessItem(
                label="Enforcement mode",
                status=_readable_enforcement_mode(enforcement.effective_mode),
                detail=(
                    "Selected: "
                    f"{_readable_enforcement_mode(enforcement.selected_mode)}; "
                    "Effective: "
                    f"{_readable_enforcement_mode(enforcement.effective_mode)}"
                ),
            ),
            PersonalUseReadinessItem(
                label="Process blocking",
                status=_personal_process_blocking_status(enforcement),
                detail=_readiness_detail(enforcement.process_readiness),
            ),
            PersonalUseReadinessItem(
                label="Hosts blocking",
                status=_personal_hosts_blocking_status(enforcement),
                detail=_readiness_detail(enforcement.hosts_readiness),
            ),
            PersonalUseReadinessItem(
                label="Browser extension",
                status=_personal_title_status(browser.connection_status),
                detail=browser.message,
            ),
            PersonalUseReadinessItem(
                label="Incognito",
                status=_personal_incognito_status(browser.incognito_status),
                detail="Controlled only when the extension is allowed in Incognito.",
            ),
            PersonalUseReadinessItem(
                label="DNR",
                status=_personal_dnr_status(browser),
                detail="DNR is a block-only browser hardening layer.",
            ),
            PersonalUseReadinessItem(
                label="YouTube SPA detector",
                status=_personal_youtube_spa_status(browser.youtube_spa_status),
                detail="Seen after a YouTube tab activates the content script.",
            ),
            PersonalUseReadinessItem(
                label="Browser path rules",
                status="Supported",
                detail="Browser URL path patterns are evaluated locally by the extension/native host.",
            ),
            PersonalUseReadinessItem(
                label="Browser attempt logging",
                status="Supported",
                detail="Stores hostname/rule/classification metadata only; no full URLs.",
            ),
            PersonalUseReadinessItem(
                label="Recovery/Safe Mode",
                status=(
                    "Available" if enforcement.recovery_readiness.ready else "Not ready"
                ),
                detail=_readiness_detail(enforcement.recovery_readiness),
            ),
            PersonalUseReadinessItem(
                label="Manual QA status",
                status=qa_checklist.message,
                detail="Run the personal trial QA checklist before relying on LoopGuard.",
            ),
        )
        verdict = _personal_use_readiness_verdict(
            enforcement,
            browser,
            qa_checklist,
        )
        return PersonalUseReadinessChecklist(
            verdict=verdict,
            summary=_personal_use_readiness_summary(verdict),
            items=items,
            manual_qa_status=qa_checklist.message,
        )

    def get_personal_trial_qa_checklist(self) -> PersonalTrialQaChecklist:
        """Return persisted local manual QA state for personal trial readiness."""
        stored = self.app_settings.get_value(_PERSONAL_TRIAL_QA_SETTINGS_KEY)
        states = _decode_personal_trial_qa_state(stored)
        items = tuple(
            PersonalTrialQaItem(
                key=key,
                label=label,
                checked=bool(states.get(key, False)),
            )
            for key, label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS
        )
        completed_count = sum(1 for item in items if item.checked)
        total_count = len(items)
        if completed_count == 0:
            status = "not_ready"
            message = f"0/{total_count} verified"
        elif completed_count == total_count:
            status = "complete"
            message = "All manual QA verified"
        else:
            status = "partial"
            message = f"{completed_count}/{total_count} verified"
        return PersonalTrialQaChecklist(
            items=items,
            completed_count=completed_count,
            total_count=total_count,
            status=status,
            message=message,
        )

    def set_personal_trial_qa_item(
        self,
        step_key: str,
        checked: bool,
    ) -> PersonalTrialQaChecklist:
        """Persist one manual QA checkbox value."""
        valid_keys = {key for key, _label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS}
        if step_key not in valid_keys:
            raise ValueError("Unsupported personal trial QA step")
        states = _decode_personal_trial_qa_state(
            self.app_settings.get_value(_PERSONAL_TRIAL_QA_SETTINGS_KEY)
        )
        states[step_key] = bool(checked)
        self.app_settings.set_value(
            _PERSONAL_TRIAL_QA_SETTINGS_KEY,
            _encode_personal_trial_qa_state(states),
        )
        return self.get_personal_trial_qa_checklist()

    def reset_personal_trial_qa_checklist(self) -> PersonalTrialQaChecklist:
        """Clear all persisted manual QA checkbox values."""
        self.app_settings.set_value(_PERSONAL_TRIAL_QA_SETTINGS_KEY, "{}")
        return self.get_personal_trial_qa_checklist()

    def has_seen_browser_setup_intro(self) -> bool:
        """Return whether the local browser setup intro was dismissed permanently."""
        value = self.app_settings.get_value(_BROWSER_SETUP_INTRO_SEEN_KEY)
        return value.strip().lower() == "true" if value else False

    def mark_browser_setup_intro_seen(self) -> None:
        """Persist the local browser setup intro dismissal flag."""
        self.app_settings.set_value(_BROWSER_SETUP_INTRO_SEEN_KEY, "true")

    def should_show_browser_setup_intro(self) -> bool:
        """Return whether browser setup guidance should be surfaced."""
        if self.has_seen_browser_setup_intro():
            return False
        return self.get_browser_integration_status().connection_status != "connected"

    def has_active_website_rules_requiring_browser_control(self) -> bool:
        """Return whether enabled site rules make Chrome readiness required."""
        self._ensure_current_day()
        day = self.day_state.get()
        if day.day_started_at is None or day.day_ended_at is not None:
            return False
        return any(
            rule.enabled
            for rule in self._rule_repository().list(rule_type="site")
        )

    def is_active_day(self) -> bool:
        """Return whether today's active work/reward loop is open."""
        self._ensure_current_day()
        return self._active_day_is_locked()

    def export_configuration(self) -> dict[str, object]:
        """Return a local rules/settings-only configuration export."""
        rules = [
            _rule_export_payload(rule)
            for rule in self._rule_repository().list(enabled_only=False)
        ]
        app_settings = {
            key: value
            for key in CONFIG_EXPORT_SETTING_KEYS
            if (value := self.app_settings.get_value(key)) is not None
        }
        return {
            "export_version": CONFIG_EXPORT_VERSION,
            "app": CONFIG_EXPORT_APP,
            "created_at": self._now().isoformat(),
            "warning": CONFIG_EXPORT_WARNING,
            "rules": rules,
            "app_settings": app_settings,
        }

    def preview_configuration_import(self, raw_json: str) -> ConfigImportPreview:
        """Validate a local configuration export and return its import summary."""
        rules, settings = _validated_configuration_import(raw_json)
        return ConfigImportPreview(
            rule_count=len(rules),
            setting_count=len(settings),
            message=(
                f"Ready to import {len(rules)} rules and "
                f"{len(settings)} settings."
            ),
        )

    def import_configuration(self, raw_json: str) -> ConfigImportResult:
        """Apply a validated local configuration import."""
        self._ensure_current_day()
        if self._active_day_is_locked():
            raise ValueError(CONFIG_IMPORT_LOCKED_AFTER_START_DAY)

        rules, settings = _validated_configuration_import(raw_json)
        self._rule_repository().replace_all(rules)
        for key, value in settings.items():
            self.app_settings.set_value(key, value)
        return ConfigImportResult(
            rule_count=len(rules),
            setting_count=len(settings),
            message=(
                f"Imported {len(rules)} rules and {len(settings)} settings."
            ),
        )

    def list_tasks(self) -> list[Task]:
        """Return today's tasks in repository order."""
        self._ensure_current_day()
        return self.tasks.list_for_day(self._today())

    def list_all_tasks(self) -> list[Task]:
        """Return all stored tasks for history-style access."""
        return self.tasks.list()

    def start_day(self) -> DashboardSnapshot:
        """Lock today's planned task set for reward purposes."""
        self._ensure_current_day()
        day = self.day_state.get()
        if day.day_ended_at is not None:
            return self.dashboard_snapshot()
        if day.day_started_at is None:
            if not self._has_pending_planned_main_today():
                raise ValueError("Add a planned MAIN task before starting the day.")
            if self._production_browser_setup_required():
                raise ValueError(START_DAY_BROWSER_REQUIRED)
            started_at = self._now().isoformat()
            self.day_state.start_day(started_at)
            self._capture_soft_start_for_started_day(started_at)
        return self.dashboard_snapshot()

    def get_rest_token_count(self) -> int:
        """Return the clamped earned Rest Token count."""
        return self.app_settings.get_rest_token_count()

    def use_rest_token(self) -> DashboardSnapshot:
        """Consume an earned Rest Token to take a planned rest day before Start Day."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        unavailable_reason = self._rest_token_unavailable_reason(day, now)
        if unavailable_reason:
            raise ValueError(unavailable_reason)

        self.app_settings.set_rest_token_count(self.get_rest_token_count() - 1)
        self.day_outcomes.upsert(
            day_date=self._today(),
            started_at=day.day_started_at,
            ended_at=now.isoformat(),
            close_kind=DayOutcomeCloseKind.REST,
            main_completed=False,
            rest_token_used=True,
        )
        self.day_state.end_day(now.isoformat())
        self.app_settings.set_value(_END_DAY_PENDING_STARTED_AT_KEY, "")
        self._refresh_hosts_enforcement_after_access_change()
        return self.dashboard_snapshot()

    def _production_browser_setup_required(self) -> bool:
        return (
            is_production_app_mode()
            and not self.get_browser_integration_status().browser_blocking_ready
        )

    def end_day(self) -> DashboardSnapshot:
        """Close today's active work/reward loop."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.day_started_at is None:
            raise ValueError(END_DAY_REQUIRES_STARTED_DAY)
        if day.day_ended_at is not None:
            raise ValueError(END_DAY_ALREADY_CLOSED)
        if not self._has_completed_main_today():
            raise ValueError(END_DAY_REQUIRES_COMPLETED_MAIN)
        return self._close_active_day(now, close_kind=DayOutcomeCloseKind.NORMAL)

    def request_end_day(self) -> DashboardSnapshot:
        """Start the normal End Day review delay without closing the day."""
        self._ensure_current_day()
        now = self._now()
        self._validate_normal_end_day(now)
        self.app_settings.set_value(_END_DAY_PENDING_STARTED_AT_KEY, now.isoformat())
        return self.dashboard_snapshot()

    def confirm_end_day(self) -> DashboardSnapshot:
        """Close the day after the normal End Day review delay has elapsed."""
        self._ensure_current_day()
        now = self._now()
        self._validate_normal_end_day(now)
        pending_started_at = self._end_day_pending_started_at()
        if pending_started_at is None:
            raise ValueError(END_DAY_CONFIRM_WAIT_MESSAGE)
        remaining_seconds = max(
            0,
            END_DAY_CONFIRM_DELAY_SECONDS
            - int((now - pending_started_at).total_seconds()),
        )
        if remaining_seconds > 0:
            raise ValueError(
                "End Day confirmation is available in "
                f"{_format_duration_minutes_seconds(remaining_seconds)}."
            )
        return self._close_active_day(now, close_kind=DayOutcomeCloseKind.NORMAL)

    def recovery_close_today(self) -> DashboardSnapshot:
        """Close today's active loop without requiring successful MAIN completion."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.day_started_at is None:
            raise ValueError(END_DAY_REQUIRES_STARTED_DAY)
        if day.day_ended_at is not None:
            raise ValueError(END_DAY_ALREADY_CLOSED)
        return self._close_active_day(now, close_kind=DayOutcomeCloseKind.RECOVERY)

    def get_day_close_review(self, close_type: str) -> DayCloseReviewSummary:
        """Return a neutral, transient review for a pending day close."""
        if close_type not in {DAY_CLOSE_REVIEW_NORMAL, DAY_CLOSE_REVIEW_RECOVERY}:
            raise ValueError(f"Unsupported day close review type: {close_type}")
        return _day_close_review_from_snapshot(
            close_type,
            self.dashboard_snapshot(),
        )

    def create_task(
        self,
        *,
        title: str,
        kind: TaskKind,
        reward_minutes_override: int | None = None,
        allowed_url: str | None = None,
    ) -> Task:
        """Create a task from GUI input."""
        self._ensure_current_day()
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title is required")

        clean_url = allowed_url.strip() if allowed_url else ""
        current_day = self.day_state.get()
        planning_status = (
            TaskPlanningStatus.UNPLANNED
            if current_day.day_started_at is not None
            else TaskPlanningStatus.PLANNED
        )
        if current_day.day_started_at is not None and clean_url:
            raise ValueError("URL exceptions are locked after Start Day.")
        canonical_allowed_url = (
            canonical_task_allowed_url(clean_url)
            if clean_url and planning_status is TaskPlanningStatus.PLANNED
            else ""
        )
        if planning_status is TaskPlanningStatus.UNPLANNED and kind is TaskKind.MAIN:
            raise ValueError("MAIN must be planned before Start Day.")
        reward_minutes = (
            0
            if planning_status is TaskPlanningStatus.UNPLANNED
            else reward_minutes_override or 0
        )
        task_allowed_url = (
            canonical_allowed_url or None
            if planning_status is TaskPlanningStatus.PLANNED
            else None
        )
        if reward_minutes < 0:
            raise ValueError("Reward minutes cannot be negative")

        return self.tasks.create(
            title=clean_title,
            kind=kind,
            reward_minutes=reward_minutes,
            allowed_url=task_allowed_url,
            day_date=self._today(),
            planning_status=planning_status,
        )

    def claim_task_done(self, task_id: int) -> TaskCompletionClaimResult:
        """Persist a Claim Done timestamp for a planned rewardable task."""
        task, _current_day, now = self._task_completion_context(task_id)
        if task.status is TaskStatus.DONE:
            return TaskCompletionClaimResult(
                task=task,
                available_at=task.completed_at or now.isoformat(),
                remaining_seconds=0,
            )
        if task.planning_status is not TaskPlanningStatus.PLANNED:
            raise ValueError("Unplanned tasks can be marked done directly.")
        if task.completion_available_at:
            return TaskCompletionClaimResult(
                task=task,
                available_at=task.completion_available_at,
                remaining_seconds=self.task_completion_claim_remaining_seconds(task),
            )
        pending_claim = self._pending_completion_claim_today(excluding_task_id=task.id)
        if pending_claim is not None:
            raise ValueError(TASK_COMPLETION_CLAIM_ALREADY_PENDING)

        available_at = now + timedelta(seconds=TASK_COMPLETION_CLAIM_DELAY_SECONDS)
        claimed_task = self.tasks.claim_completion(
            task.id,
            claimed_at=now.isoformat(),
            available_at=available_at.isoformat(),
        )
        return TaskCompletionClaimResult(
            task=claimed_task,
            available_at=available_at.isoformat(),
            remaining_seconds=TASK_COMPLETION_CLAIM_DELAY_SECONDS,
        )

    def confirm_task_done(self, task_id: int) -> TaskCompletionResult:
        """Confirm a claimed planned task, or directly complete an unplanned task."""
        task, current_day, now = self._task_completion_context(task_id)
        if task.status is TaskStatus.DONE:
            return TaskCompletionResult(
                task=task,
                day_state=current_day,
                reward_entry=None,
            )
        if task.planning_status is TaskPlanningStatus.PLANNED:
            if not task.completion_available_at:
                raise ValueError(TASK_COMPLETION_CLAIM_REQUIRED)
            remaining_seconds = self.task_completion_claim_remaining_seconds(task, now=now)
            if remaining_seconds > 0:
                raise ValueError(
                    "Confirm Done is available in "
                    f"{_format_duration_minutes_seconds(remaining_seconds)}."
                )

        return self._complete_task_now(task, current_day, now)

    def cancel_task_completion_claim(self, task_id: int) -> Task:
        """Cancel a pending planned-task completion claim."""
        task, _current_day, _now = self._task_completion_context(task_id)
        if task.status is TaskStatus.DONE:
            raise ValueError("Completed tasks cannot have a claim canceled.")
        if task.planning_status is not TaskPlanningStatus.PLANNED:
            raise ValueError("Unplanned tasks do not use completion claims.")
        return self.tasks.clear_completion_claim(task.id)

    def complete_task(self, task_id: int) -> TaskCompletionResult:
        """Compatibility wrapper that cannot bypass planned-task claim delay."""
        return self.confirm_task_done(task_id)

    def task_completion_claim_remaining_seconds(
        self,
        task: Task,
        *,
        now: datetime | None = None,
    ) -> int:
        """Return seconds until a claimed task can be confirmed."""
        if not task.completion_available_at:
            return 0
        reference_time = now or self._now()
        available_at = _parse_datetime(task.completion_available_at)
        return max(
            0,
            int((available_at - reference_time).total_seconds()),
        )

    def _pending_completion_claim_today(
        self,
        *,
        excluding_task_id: int | None = None,
    ) -> Task | None:
        for task in self.tasks.list_for_day(self._today()):
            if excluding_task_id is not None and task.id == excluding_task_id:
                continue
            if task.status is not TaskStatus.PENDING:
                continue
            if task.planning_status is not TaskPlanningStatus.PLANNED:
                continue
            if task.completion_claimed_at or task.completion_available_at:
                return task
        return None

    def _reset_high_notification_state(self, session: HighSessionRecord) -> None:
        self.app_settings.set_value(
            _HIGH_NOTIFICATION_SESSION_KEY,
            _high_notification_session_key(session),
        )
        self.app_settings.set_value(_HIGH_WARNING_SENT_KEY, "false")
        self.app_settings.set_value(_HIGH_END_SENT_KEY, "false")

    def _ensure_high_notification_session(self, session: HighSessionRecord) -> None:
        session_key = _high_notification_session_key(session)
        if self.app_settings.get_value(_HIGH_NOTIFICATION_SESSION_KEY) == session_key:
            return
        self._reset_high_notification_state(session)

    def delete_task(self, task_id: int) -> None:
        """Delete a task only while policy says it has no completion history."""
        self._ensure_current_day()
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.day_date != self._today():
            raise ValueError("Task is not active today")
        if task.status is TaskStatus.DONE:
            raise ValueError("Completed tasks cannot be deleted")

        if self.day_state.get().day_started_at is not None:
            raise ValueError("Tasks cannot be deleted after Start Day")

        can_delete = (
            task.planning_status is TaskPlanningStatus.PLANNED
            and task.status is TaskStatus.PENDING
        )
        if not can_delete:
            raise ValueError("Task is locked and cannot be deleted")

        self.tasks.delete(task.id)

    def get_high_access_options(self) -> HighAccessOptions:
        """Return wallet state and selectable HIGH access durations."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        high_active = day.access_level is AccessLevel.HIGH
        surrender_active = day.surrender_requested_at is not None
        day_closed = day.day_ended_at is not None
        daily_used_seconds = self._high_started_seconds_for_day(self._today())
        daily_cap_minutes = self.get_daily_recreation_cap_minutes()
        daily_remaining_seconds = self._high_daily_remaining_seconds(self._today())
        daily_cap_reached = daily_remaining_seconds <= 0
        cooldown_remaining_seconds = (
            0
            if day_closed
            else self._high_cooldown_remaining_seconds(self._today(), now)
        )
        cooldown_active = cooldown_remaining_seconds > 0 and not high_active
        browser_blocking_unready = (
            not high_active
            and not surrender_active
            and not day_closed
            and self.has_active_website_rules_requiring_browser_control()
            and not self.get_browser_integration_status(now=now).browser_blocking_ready
        )
        can_start_high = (
            not high_active
            and not surrender_active
            and not day_closed
            and not daily_cap_reached
            and not cooldown_active
            and not browser_blocking_unready
        )
        unavailable_reason = ""
        if day_closed:
            unavailable_reason = "HIGH access is unavailable after End Day"
        elif surrender_active:
            unavailable_reason = "HIGH access is not needed while Surrender is active"
        elif high_active:
            unavailable_reason = "HIGH mode is already active"
        elif browser_blocking_unready:
            unavailable_reason = HIGH_BROWSER_BLOCKING_NOT_READY
        elif daily_cap_reached:
            unavailable_reason = HIGH_DAILY_CAP_REACHED
        elif cooldown_active:
            unavailable_reason = _format_high_cooldown_message(
                cooldown_remaining_seconds
            )
        return HighAccessOptions(
            available_minutes=day.reward_balance_seconds // 60,
            available_seconds=day.reward_balance_seconds,
            max_session_minutes=HIGH_SESSION_MAX_MINUTES,
            daily_cap_minutes=daily_cap_minutes,
            daily_used_seconds=daily_used_seconds,
            daily_remaining_seconds=daily_remaining_seconds,
            daily_cap_reached=daily_cap_reached,
            high_cooldown_remaining_seconds=cooldown_remaining_seconds,
            high_cooldown_active=cooldown_active,
            high_active=high_active,
            can_start_high=can_start_high,
            unavailable_reason=unavailable_reason,
            options=[
                HighAccessOption(
                    minutes=minutes,
                    label=f"{minutes} minutes",
                    enabled=(
                        can_start_high
                        and day.reward_balance_seconds >= minutes * 60
                        and daily_remaining_seconds >= minutes * 60
                    ),
                )
                for minutes in (5, 15, 30)
            ],
        )

    def start_high_access(self, minutes: int, intent: str) -> DashboardSnapshot:
        """Start HIGH access by spending today's reward minutes."""
        if minutes <= 0:
            raise ValueError("minutes must be greater than zero")
        clean_intent = _normalize_high_intent(intent)
        if not clean_intent:
            raise ValueError("HIGH intent is required")
        if len(re.sub(r"\s+", "", clean_intent)) < HIGH_INTENT_MIN_NONSPACE_CHARS:
            raise ValueError("HIGH intent must be at least 5 characters")
        if minutes > HIGH_SESSION_MAX_MINUTES:
            raise ValueError(
                f"HIGH access cannot exceed {HIGH_SESSION_MAX_MINUTES} minutes"
            )
        self._ensure_current_day()
        now = self._now()
        requested_seconds = minutes * 60
        current_day = self._expire_high_if_needed(now)
        if current_day.surrender_requested_at is not None:
            self._reconcile_high_during_surrender(now)
            raise ValueError("HIGH access is not needed while Surrender is active")
        if current_day.day_ended_at is not None:
            raise ValueError("HIGH access is unavailable after End Day")
        if current_day.access_level is AccessLevel.HIGH:
            raise ValueError("HIGH mode is already active")
        if (
            self.has_active_website_rules_requiring_browser_control()
            and not self.get_browser_integration_status(now=now).browser_blocking_ready
        ):
            raise ValueError(HIGH_BROWSER_BLOCKING_NOT_READY)
        daily_remaining_seconds = self._high_daily_remaining_seconds(self._today())
        if requested_seconds > daily_remaining_seconds:
            if daily_remaining_seconds <= 0:
                raise ValueError(HIGH_DAILY_CAP_REACHED)
            daily_cap_minutes = self.get_daily_recreation_cap_minutes()
            raise ValueError(
                "HIGH access would exceed today's "
                f"{daily_cap_minutes}-minute Recreation cap"
            )
        cooldown_remaining_seconds = self._high_cooldown_remaining_seconds(
            self._today(),
            now,
        )
        if cooldown_remaining_seconds > 0:
            raise ValueError(_format_high_cooldown_message(cooldown_remaining_seconds))
        if current_day.reward_balance_seconds <= 0:
            raise ValueError("No reward time available")
        if requested_seconds > current_day.reward_balance_seconds:
            raise ValueError("cannot spend more reward time than available")

        balance_event = RewardLedgerEvent(
            event_type=RewardEventType.MANUAL_ADJUSTMENT,
            minutes_delta=current_day.reward_balance_seconds // 60,
            seconds_delta=current_day.reward_balance_seconds,
            occurred_at=now,
            reason="current_balance",
        )
        spend_event = self.reward_service.spend_high_seconds(
            [balance_event],
            seconds=requested_seconds,
            occurred_at=now,
        )
        self.rewards.add(
            minutes_delta=spend_event.minutes_delta,
            seconds_delta=spend_event.seconds_delta,
            reason=spend_event.reason,
            task_id=spend_event.task_id,
        )
        self.day_state.add_reward_seconds(spend_event.seconds_delta)
        self.day_state.set_access_level(AccessLevel.HIGH)

        session = self.high_sessions.start(
            day_date=self._today(),
            started_at=now.isoformat(),
            ends_at=(now + timedelta(minutes=minutes)).isoformat(),
            allocated_minutes=minutes,
            allocated_seconds=requested_seconds,
            intent=clean_intent,
        )
        self._reset_high_notification_state(session)
        self._refresh_hosts_enforcement_after_access_change()
        return self.dashboard_snapshot()

    def spend_minutes(self, amount: int, intent: str) -> DashboardSnapshot:
        """Compatibility wrapper for older callers."""
        return self.start_high_access(amount, intent)

    def end_high_access(self) -> DashboardSnapshot:
        """End HIGH access early and return to today's non-HIGH level."""
        self._ensure_current_day()
        now = self._now()
        session = self.high_sessions.active_for_day(self._today())
        if session is None:
            day = self.day_state.get()
            if day.access_level is AccessLevel.HIGH:
                self.day_state.set_access_level(self._fallback_access_level_today())
                self._refresh_hosts_enforcement_after_access_change()
            return self.dashboard_snapshot()

        remaining_seconds = self._session_remaining_seconds(session, now)
        if remaining_seconds <= 0:
            self._expire_high_if_needed(now)
            self._refresh_hosts_enforcement_after_access_change()
            return self.dashboard_snapshot()

        self.high_sessions.end(
            session.id,
            ended_at=now.isoformat(),
            reason="ended_early",
        )
        if remaining_seconds > 0:
            self.rewards.add(
                seconds_delta=remaining_seconds,
                reason="high_mode_refund",
                task_id=None,
            )
            self.day_state.add_reward_seconds(remaining_seconds)
        self.day_state.set_access_level(self._fallback_access_level_today())
        self._refresh_hosts_enforcement_after_access_change()
        return self.dashboard_snapshot()

    def collect_high_notification_events(self) -> tuple[HighNotificationEvent, ...]:
        """Return once-only non-modal HIGH warning/end notification events."""
        self._ensure_current_day()
        now = self._now()
        active_session = self.high_sessions.active_for_day(self._today())
        if active_session is not None:
            self._ensure_high_notification_session(active_session)
            threshold_seconds = high_warning_threshold_seconds(
                active_session.allocated_seconds
            )
            remaining_seconds = self._session_remaining_seconds(active_session, now)
            if remaining_seconds <= 0:
                self._expire_high_if_needed(now)
            elif (
                threshold_seconds is not None
                and remaining_seconds <= threshold_seconds
                and self.app_settings.get_value(_HIGH_WARNING_SENT_KEY) != "true"
            ):
                self.app_settings.set_value(_HIGH_WARNING_SENT_KEY, "true")
                return (
                    HighNotificationEvent(
                        event_type="warning",
                        title="HIGH ending soon",
                        message=(
                            "Recreation ends in "
                            f"{_format_duration_minutes_seconds(remaining_seconds)}."
                        ),
                    ),
                )
            else:
                return ()

        session_key = self.app_settings.get_value(_HIGH_NOTIFICATION_SESSION_KEY)
        if not session_key or self.app_settings.get_value(_HIGH_END_SENT_KEY) == "true":
            return ()
        session_id_text = session_key.split(":", 1)[0]
        if not session_id_text.isdigit():
            return ()
        session = self.high_sessions.get(int(session_id_text))
        if session is None:
            return ()
        ended = session.ended_at is not None or now >= _parse_datetime(session.ends_at)
        if not ended:
            return ()
        self.app_settings.set_value(_HIGH_END_SENT_KEY, "true")
        return (
            HighNotificationEvent(
                event_type="ended",
                title="HIGH ended",
                message="Recreation time has ended.",
            ),
        )

    def get_surrender_strictness(self) -> str:
        """Return the safe app-level surrender strictness setting."""
        return self.app_settings.get_surrender_strictness()

    def set_surrender_strictness(self, value: str) -> str:
        """Persist a safe app-level surrender strictness setting."""
        self._ensure_surrender_strictness_editable()
        return self.app_settings.set_surrender_strictness(value)

    def get_soft_start_enabled(self) -> bool:
        """Return whether Soft Start is enabled."""
        return self.app_settings.get_soft_start_enabled()

    def set_soft_start_enabled(self, enabled: bool) -> bool:
        """Persist whether Soft Start is enabled."""
        self._ensure_soft_start_settings_editable()
        return self.app_settings.set_soft_start_enabled(enabled)

    def get_soft_start_duration_minutes(self) -> int:
        """Return the configured Soft Start duration."""
        return self.app_settings.get_soft_start_duration_minutes()

    def set_soft_start_duration_minutes(self, minutes: int) -> int:
        """Persist the configured Soft Start duration."""
        self._ensure_soft_start_settings_editable()
        return self.app_settings.set_soft_start_duration_minutes(minutes)

    def get_daily_recreation_cap_minutes(self) -> int:
        """Return the configured daily Recreation cap."""
        return self.app_settings.get_daily_recreation_cap_minutes()

    def set_daily_recreation_cap_minutes(self, minutes: int) -> int:
        """Persist the daily Recreation cap while planning or closed."""
        self._ensure_daily_recreation_cap_editable()
        return self.app_settings.set_daily_recreation_cap_minutes(minutes)

    def activate_bad_day_mode(self) -> DashboardSnapshot:
        """Activate current-day Bad Day Mode without reward or task effects."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.day_started_at is None:
            raise ValueError("Start Day before activating Bad Day Mode")
        if day.day_ended_at is not None:
            raise ValueError("Bad Day Mode is unavailable after End Day")
        if self._soft_start_status(day, now).active:
            raise ValueError("Bad Day Mode is unavailable during Soft Start")
        if day.surrender_requested_at is not None:
            raise ValueError("Bad Day Mode is not needed while Surrender is active")
        if not day.bad_day_mode:
            self.day_state.activate_bad_day_mode()
        return self.dashboard_snapshot()

    def activate_surrender(self) -> DashboardSnapshot:
        """Activate app-state-only surrender after the Start Day delay."""
        self._ensure_current_day()
        now = self._now()
        day = self.day_state.get()
        delay_seconds = self._surrender_delay_seconds()
        soft_start_status = self._soft_start_status(day, now)
        surrender_status = _surrender_status(
            day,
            now,
            delay_seconds,
            start_offset_seconds=soft_start_status.duration_minutes * 60
            if soft_start_status.enabled
            else 0,
        )
        if day.day_started_at is None:
            raise ValueError("Start Day before activating surrender")
        if day.day_ended_at is not None:
            raise ValueError("Surrender is unavailable after End Day")
        if soft_start_status.active:
            raise ValueError("Surrender is unavailable during Soft Start")
        if surrender_status.active:
            self._reconcile_high_during_surrender(now)
            return self.dashboard_snapshot()
        if not surrender_status.available:
            raise ValueError("Surrender is not available yet")

        self._activate_surrender_with_high_reconciliation(now)
        return self.dashboard_snapshot()

    def dashboard_snapshot(self) -> DashboardSnapshot:
        """Return the latest dashboard values."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        else:
            day = self._apply_bad_day_baseline_if_needed(day)
        session = self.high_sessions.active_for_day(self._today())
        high_remaining_seconds = self._session_remaining_seconds(session, now)
        day_started = day.day_started_at is not None
        if not day_started and day.access_level is not AccessLevel.LOW:
            day = self.day_state.set_access_level(AccessLevel.LOW)
        today = self._today()
        today_tasks = self.tasks.list_for_day(today)
        planned_tasks = [
            task
            for task in today_tasks
            if task.planning_status is TaskPlanningStatus.PLANNED
        ]
        unplanned_tasks = [
            task
            for task in today_tasks
            if task.planning_status is TaskPlanningStatus.UNPLANNED
        ]
        planned_pending = _task_count_by_status(planned_tasks, TaskStatus.PENDING)
        planned_done = _task_count_by_status(planned_tasks, TaskStatus.DONE)
        unplanned_pending = _task_count_by_status(unplanned_tasks, TaskStatus.PENDING)
        unplanned_done = _task_count_by_status(unplanned_tasks, TaskStatus.DONE)
        has_pending_planned_main = _select_pending_main_task(planned_tasks) is not None
        day_closed = day.day_ended_at is not None
        can_start_day = not day_started and not day_closed and has_pending_planned_main
        start_day_unavailable_reason = ""
        if not day_started and day_closed:
            start_day_unavailable_reason = END_DAY_ALREADY_CLOSED
        elif not day_started and not has_pending_planned_main:
            start_day_unavailable_reason = (
                "Add a planned MAIN task before starting the day."
            )
        elif can_start_day and self._production_browser_setup_required():
            can_start_day = False
            start_day_unavailable_reason = START_DAY_BROWSER_REQUIRED
        surrender_strictness = self.get_surrender_strictness()
        surrender_delay_seconds = _surrender_delay_seconds_for(surrender_strictness)
        soft_start_status = self._soft_start_status(day, now)
        surrender_status = _surrender_status(
            day,
            now,
            surrender_delay_seconds,
            start_offset_seconds=soft_start_status.duration_minutes * 60
            if soft_start_status.enabled
            else 0,
        )
        active_planned_use_pass = self._active_planned_use_pass(now)
        recent_attempt_summary = self.get_recent_attempt_summary(limit=20)
        browser_escape_summary = self.get_browser_escape_summary(limit=50)
        enforcement_status = self.get_enforcement_status()
        dry_run_process_summary = self.get_dry_run_process_attempt_summary(limit=20)
        recent_dry_run_process_attempts = dry_run_process_summary.latest_attempts[:3]
        active_planned_use_pass_remaining_seconds = (
            max(
                0,
                int(
                    (
                        _parse_datetime(active_planned_use_pass.expires_at) - now
                    ).total_seconds()
                ),
            )
            if active_planned_use_pass is not None
            else 0
        )
        completed_main = self._has_completed_main_today()
        can_end_day = day_started and not day_closed and completed_main
        end_day_unavailable_reason = ""
        if not day_started:
            end_day_unavailable_reason = END_DAY_REQUIRES_STARTED_DAY
        elif day_closed:
            end_day_unavailable_reason = END_DAY_ALREADY_CLOSED
        elif not completed_main:
            end_day_unavailable_reason = END_DAY_REQUIRES_COMPLETED_MAIN
        end_day_pending_started_at = self._end_day_pending_started_at()
        end_day_remaining_seconds = 0
        if (
            end_day_pending_started_at is not None
            and day_started
            and not day_closed
            and completed_main
        ):
            end_day_remaining_seconds = max(
                0,
                END_DAY_CONFIRM_DELAY_SECONDS
                - int((now - end_day_pending_started_at).total_seconds()),
            )
        end_day_pending = end_day_pending_started_at is not None and can_end_day
        end_day_confirm_label = (
            "Confirm End Day"
            if end_day_pending and end_day_remaining_seconds <= 0
            else "End Day"
        )
        can_recovery_close_today = day_started and not day_closed
        recovery_close_today_unavailable_reason = ""
        if not day_started:
            recovery_close_today_unavailable_reason = END_DAY_REQUIRES_STARTED_DAY
        elif day_closed:
            recovery_close_today_unavailable_reason = END_DAY_ALREADY_CLOSED
        rest_token_count = self.get_rest_token_count()
        rest_token_unavailable_reason = self._rest_token_unavailable_reason(day, now)
        can_use_rest_token = rest_token_unavailable_reason == ""
        high_daily_used_seconds = self._high_started_seconds_for_day(self._today())
        high_daily_cap_minutes = self.get_daily_recreation_cap_minutes()
        high_daily_remaining_seconds = self._high_daily_remaining_seconds(
            self._today()
        )
        high_cooldown_remaining_seconds = (
            0
            if day_closed
            else self._high_cooldown_remaining_seconds(self._today(), now)
        )
        high_cooldown_active = (
            high_cooldown_remaining_seconds > 0
            and high_remaining_seconds <= 0
            and not day_closed
        )
        return DashboardSnapshot(
            access_level=day.access_level,
            reward_balance_minutes=day.reward_balance_seconds // 60,
            reward_balance_seconds=day.reward_balance_seconds,
            test_mode=self.settings.test_mode,
            safe_mode=self.settings.safe_mode,
            recovery_mode=self.settings.recovery_mode,
            main_task=_select_main_task(planned_tasks),
            day_started=day_started,
            day_closed=day_closed,
            day_status_label=(
                "Day ended"
                if day_closed
                else "Day started"
                if day_started
                else "Planning"
            ),
            can_start_day=can_start_day,
            start_day_unavailable_reason=start_day_unavailable_reason,
            rest_token_count=rest_token_count,
            can_use_rest_token=can_use_rest_token,
            rest_token_unavailable_reason=rest_token_unavailable_reason,
            can_end_day=can_end_day,
            end_day_unavailable_reason=end_day_unavailable_reason,
            end_day_pending=end_day_pending,
            end_day_remaining_seconds=end_day_remaining_seconds,
            end_day_confirm_label=end_day_confirm_label,
            can_recovery_close_today=can_recovery_close_today,
            recovery_close_today_unavailable_reason=(
                recovery_close_today_unavailable_reason
            ),
            day_summary_label=_day_summary_label(
                planned_done=planned_done,
                planned_total=len(planned_tasks),
                unplanned_done=unplanned_done,
                unplanned_total=len(unplanned_tasks),
                reward_balance_seconds=day.reward_balance_seconds,
                recent_attempt_summary=recent_attempt_summary,
            ),
            planned_task_count=len(planned_tasks),
            planned_pending_count=planned_pending,
            planned_done_count=planned_done,
            unplanned_task_count=len(unplanned_tasks),
            unplanned_pending_count=unplanned_pending,
            unplanned_done_count=unplanned_done,
            high_remaining_seconds=high_remaining_seconds,
            high_minutes_total=(
                session.allocated_seconds // 60 if session else 0
            ),
            high_intent=(
                session.intent.strip()
                if session and session.intent.strip()
                else None
            ),
            high_active=high_remaining_seconds > 0,
            high_daily_cap_minutes=high_daily_cap_minutes,
            high_daily_used_seconds=high_daily_used_seconds,
            high_daily_remaining_seconds=high_daily_remaining_seconds,
            high_daily_cap_reached=high_daily_remaining_seconds <= 0,
            high_cooldown_remaining_seconds=high_cooldown_remaining_seconds,
            high_cooldown_active=high_cooldown_active,
            bad_day_active_today=day.bad_day_mode and day_started,
            surrender_active_today=surrender_status.active,
            surrender_active=surrender_status.active,
            effective_restriction_state=_effective_restriction_state(
                day,
                surrender_active=surrender_status.active,
                high_active=high_remaining_seconds > 0,
            ),
            surrender_available=surrender_status.available,
            surrender_remaining_seconds=surrender_status.remaining_seconds,
            surrender_strictness=surrender_strictness,
            surrender_delay_seconds=surrender_delay_seconds,
            soft_start_enabled=soft_start_status.enabled,
            soft_start_active=soft_start_status.active,
            soft_start_remaining_seconds=soft_start_status.remaining_seconds,
            soft_start_duration_minutes=soft_start_status.duration_minutes,
            day_started_at=day.day_started_at,
            day_ended_at=day.day_ended_at,
            recent_attempt_summary=recent_attempt_summary,
            browser_escape_summary=browser_escape_summary,
            active_planned_use_pass=active_planned_use_pass,
            active_planned_use_pass_remaining_seconds=(
                active_planned_use_pass_remaining_seconds
            ),
            enforcement_status=enforcement_status,
            hosts_blocking_status=self.get_hosts_blocking_status(),
            browser_integration_status=self.get_browser_integration_status(now=now),
            website_high_release_status=self.get_website_high_release_status(now=now),
            recent_dry_run_process_attempts=recent_dry_run_process_attempts,
            dry_run_process_summary=dry_run_process_summary,
        )

    def add_rule(
        self,
        rule_type: str,
        target: str,
        allow_from_level: str | None = None,
        purpose: str = DEFAULT_RULE_PURPOSE,
        escape_family: str = DEFAULT_ESCAPE_FAMILY,
    ) -> RuleRecord:
        """Add a dry-run rule."""
        clean_type = _normalize_rule_type(rule_type)
        clean_target = _normalize_rule_target(clean_type, target)
        clean_purpose = _normalize_rule_purpose(purpose)
        clean_escape_family = _normalize_escape_family(escape_family)
        if clean_escape_family == EscapeFamily.NONE.value:
            clean_escape_family = suggest_escape_family_for_rule(
                clean_type,
                clean_target,
            )
        clean_allow_from_level = (
            recommended_allow_from_level_for_purpose(clean_purpose)
            if allow_from_level is None
            else allow_from_level
        )
        return self._rule_repository().add(
            rule_type=clean_type,
            target=clean_target,
            allow_from_level=clean_allow_from_level,
            purpose=clean_purpose,
            escape_family=clean_escape_family,
        )

    def add_starter_rule_presets(self) -> StarterRulePresetResult:
        """Create missing starter rules without overwriting existing rules."""
        repository = self._rule_repository()
        existing_keys = {
            (rule.rule_type, rule.target)
            for rule in repository.list(enabled_only=False)
        }
        created_count = 0
        skipped_existing_count = 0
        failed_presets: list[str] = []

        for preset in STARTER_RULE_PRESETS:
            try:
                clean_type = _normalize_rule_type(preset.rule_type)
                clean_target = _normalize_rule_target(clean_type, preset.target)
                key = (clean_type, clean_target)
                if key in existing_keys:
                    skipped_existing_count += 1
                    continue

                self.add_rule(
                    clean_type,
                    clean_target,
                    allow_from_level=preset.allow_from_level,
                    purpose=preset.purpose,
                    escape_family=preset.escape_family,
                )
            except ValueError as error:
                failed_presets.append(
                    f"{preset.rule_type}:{preset.target}: {error}"
                )
                continue

            existing_keys.add(key)
            created_count += 1

        return StarterRulePresetResult(
            created_count=created_count,
            skipped_existing_count=skipped_existing_count,
            failed_presets=tuple(failed_presets),
        )

    def remove_rule(self, rule_type: str, target: str) -> None:
        """Remove a dry-run rule."""
        clean_type = _normalize_rule_type(rule_type)
        clean_target, existing = self._resolve_stored_rule_target(
            clean_type,
            target,
        )
        if existing is not None and existing.enabled:
            self._reject_active_day_rule_removal()
        self._rule_repository().remove(rule_type=clean_type, target=clean_target)

    def update_rule_allow_from_level(
        self,
        rule_type: str,
        target: str,
        allow_from_level: str,
        purpose: str | None = None,
        escape_family: str | None = None,
    ) -> RuleRecord:
        """Update the access threshold and optional metadata for a dry-run rule."""
        clean_type = _normalize_rule_type(rule_type)
        clean_target, existing = self._resolve_stored_rule_target(
            clean_type,
            target,
        )
        clean_allow_from_level = _normalize_allow_from_level(allow_from_level)
        if existing is not None and existing.enabled:
            self._reject_active_day_rule_weakening(
                existing.allow_from_level,
                clean_allow_from_level,
            )
        return self._rule_repository().update_allow_from_level(
            rule_type=clean_type,
            target=clean_target,
            allow_from_level=clean_allow_from_level,
            purpose=None if purpose is None else _normalize_rule_purpose(purpose),
            escape_family=(
                None
                if escape_family is None
                else _normalize_escape_family(escape_family)
            ),
        )

    def get_rules(self, rule_type: str) -> list[RuleRecord]:
        """Return enabled dry-run rules for one rule type."""
        return self._rule_repository().list(rule_type=_normalize_rule_type(rule_type))

    def start_planned_use_pass(
        self,
        rule_id: int,
        reason: str,
        duration_seconds: int,
    ) -> PlannedUsePassRecord:
        """Start one temporary Test Mode pass for a configured rule."""
        self._ensure_current_day()
        if self.day_state.get().day_ended_at is not None:
            raise ValueError("Planned-use passes are unavailable after End Day")
        rule = self._rule_repository().get_by_id(rule_id, enabled_only=False)
        if rule is None:
            raise KeyError(f"Rule not found: {rule_id}")
        if not rule.enabled:
            raise ValueError("Disabled rules cannot use planned-use passes")

        clean_reason = reason.strip()
        if len(clean_reason) < PLANNED_USE_PASS_MIN_REASON_LENGTH:
            raise ValueError(
                "Planned-use pass reason must be at least 8 characters."
            )
        if duration_seconds < PLANNED_USE_PASS_MIN_SECONDS:
            raise ValueError("Planned-use pass duration must be at least 5 minutes.")
        if duration_seconds > PLANNED_USE_PASS_MAX_SECONDS:
            raise ValueError("Planned-use pass duration cannot exceed 25 minutes.")

        now = self._now()
        if self._active_planned_use_pass(now) is not None:
            raise ValueError("Another planned-use pass is already active.")

        expires_at = now + timedelta(seconds=duration_seconds)
        active_pass = self.planned_use_passes.add(
            rule_id=rule.id,
            target_type=rule.rule_type,
            target=rule.target,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
            reason=clean_reason,
            duration_seconds=duration_seconds,
            started_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        self._refresh_hosts_enforcement_after_access_change()
        return active_pass

    def get_active_planned_use_pass(self) -> PlannedUsePassRecord | None:
        """Return the active planned-use pass, expiring stale rows first."""
        self._ensure_current_day()
        return self._active_planned_use_pass(self._now())

    def end_active_planned_use_pass(self) -> PlannedUsePassRecord | None:
        """End the active planned-use pass if one exists."""
        self._ensure_current_day()
        now = self._now()
        self.planned_use_passes.expire_due(now.isoformat())
        ended = self.planned_use_passes.end_active(now.isoformat())
        if ended is not None:
            self._refresh_hosts_enforcement_after_access_change()
        return ended

    def list_recent_planned_use_passes(
        self,
        *,
        limit: int = 10,
    ) -> list[PlannedUsePassRecord]:
        """Return recent planned-use passes, newest first."""
        return self.planned_use_passes.list_recent(limit=limit)

    def log_manual_rule_attempt(self, rule_id: int) -> AccessAttemptRecord:
        """Record a manual Test Mode attempt for an existing rule."""
        self._ensure_current_day()
        rule = self._rule_repository().get_by_id(rule_id, enabled_only=False)
        if rule is None:
            raise KeyError(f"Rule not found: {rule_id}")
        if not rule.enabled:
            raise ValueError("Disabled rules are ignored by preview and cannot be logged")

        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
            access_level_at_attempt = day.access_level
            decision = AccessAttemptDecision.ALLOWED_NOW.value
        else:
            day = self._apply_bad_day_baseline_if_needed(day)
            access_level_at_attempt = _effective_access_level_for_rules(day)
            active_pass = self._active_planned_use_pass(now)
            if _pass_matches_rule(active_pass, rule):
                decision = AccessAttemptDecision.ALLOWED_BY_PLANNED_USE_PASS.value
            else:
                decision = (
                    AccessAttemptDecision.ALLOWED_NOW.value
                    if _is_allowed_at(access_level_at_attempt, rule)
                    else AccessAttemptDecision.WOULD_BLOCK_IN_CURRENT_MODE.value
                )

        return self.access_attempts.add(
            occurred_at=now.isoformat(),
            target_type=rule.rule_type,
            target=rule.target,
            rule_id=rule.id,
            access_level_at_attempt=access_level_at_attempt.value,
            decision=decision,
            allow_from_level=rule.allow_from_level,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
        )

    def run_armed_dry_run_process_scan_cycle(
        self,
        process_names: list[str] | tuple[str, ...] | None = None,
    ) -> list[AccessAttemptRecord]:
        """Run one safe Armed Dry Run process scan cycle."""
        self._ensure_current_day()
        now = self._now()
        status = self.get_enforcement_status()
        if status.effective_mode is not EnforcementMode.ARMED_DRY_RUN:
            return []
        if not self._active_day_is_locked():
            return []

        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        else:
            day = self._apply_bad_day_baseline_if_needed(day)

        active_processes = (
            _normalize_process_names(process_names)
            if process_names is not None
            else self.process_blocker.running_process_names()
        )
        if not active_processes:
            return []

        active_process_set = set(active_processes)
        access_level = _effective_access_level_for_rules(day)
        active_pass = self._active_planned_use_pass(now)
        recent_attempts = self.access_attempts.list_recent(limit=100)
        logged: list[AccessAttemptRecord] = []

        for rule in self._rule_repository().list(rule_type="app"):
            if rule.target not in active_process_set:
                continue
            if is_protected_process_name(rule.target):
                continue
            if _pass_matches_rule(active_pass, rule):
                decision = AccessAttemptDecision.ALLOWED_BY_PLANNED_USE_PASS.value
            else:
                decision = (
                    AccessAttemptDecision.WOULD_ALLOW.value
                    if _is_allowed_at(access_level, rule)
                    else AccessAttemptDecision.WOULD_BLOCK.value
                )
            if _process_attempt_recently_logged(
                recent_attempts,
                now=now,
                rule=rule,
                decision=decision,
                source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
            ):
                continue
            attempt = self.access_attempts.add(
                occurred_at=now.isoformat(),
                target_type=rule.rule_type,
                target=rule.target,
                rule_id=rule.id,
                access_level_at_attempt=access_level.value,
                decision=decision,
                allow_from_level=rule.allow_from_level,
                purpose=rule.purpose,
                escape_family=rule.escape_family,
                source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
                enforcement_mode=EnforcementMode.ARMED_DRY_RUN.value,
                action_taken="none",
            )
            logged.append(attempt)
            recent_attempts.insert(0, attempt)

        return logged

    def run_real_process_blocking_scan_cycle(
        self,
        process_names: list[str] | tuple[str, ...] | None = None,
    ) -> list[AccessAttemptRecord]:
        """Run one real process-blocking scan cycle for explicit app rules."""
        self._ensure_current_day()
        now = self._now()
        status = self.get_enforcement_status()
        if status.effective_mode not in PROCESS_ENFORCING_MODES:
            return []
        if not self._active_day_is_locked():
            return []

        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        else:
            day = self._apply_bad_day_baseline_if_needed(day)

        active_processes = (
            _normalize_process_names(process_names)
            if process_names is not None
            else self.process_blocker.running_process_names()
        )
        if not active_processes:
            return []

        active_process_set = set(active_processes)
        access_level = _effective_access_level_for_rules(day)
        active_pass = self._active_planned_use_pass(now)
        source = AccessAttemptSource.REAL_PROCESS_BLOCKING_PROCESS.value
        recent_attempts = self.access_attempts.list_recent(limit=100)
        logged: list[AccessAttemptRecord] = []

        for rule in self._rule_repository().list(rule_type="app"):
            if rule.target not in active_process_set:
                continue
            if _is_allowed_at(access_level, rule):
                continue

            if is_protected_process_name(rule.target):
                decision = AccessAttemptDecision.WOULD_BLOCK.value
                action_taken = "skipped_protected"
            elif _pass_matches_rule(active_pass, rule):
                decision = AccessAttemptDecision.ALLOWED_BY_PLANNED_USE_PASS.value
                action_taken = "none"
            elif self._real_process_action_recently_attempted(rule, now):
                continue
            else:
                decision = AccessAttemptDecision.WOULD_BLOCK.value
                result = self.process_blocker.terminate(
                    [rule.target],
                    active_process_names=active_processes,
                )[0]
                action_taken = result.action
                self._remember_real_process_action_attempt(rule, now)

            should_log = not _process_attempt_recently_logged(
                recent_attempts,
                now=now,
                rule=rule,
                decision=decision,
                source=source,
            )
            if not should_log:
                continue

            attempt = self.access_attempts.add(
                occurred_at=now.isoformat(),
                target_type=rule.rule_type,
                target=rule.target,
                rule_id=rule.id,
                access_level_at_attempt=access_level.value,
                decision=decision,
                allow_from_level=rule.allow_from_level,
                purpose=rule.purpose,
                escape_family=rule.escape_family,
                source=source,
                enforcement_mode=EnforcementMode.REAL_PROCESS_BLOCKING.value,
                action_taken=action_taken,
            )
            logged.append(attempt)
            recent_attempts.insert(0, attempt)

        return logged

    def run_unmanaged_browser_guard_cycle(
        self,
        process_names: list[str] | tuple[str, ...] | None = None,
    ):
        """Soft-close unmanaged browsers only while trusted website HIGH is open."""
        self._ensure_current_day()
        now = self._now()
        status = self.get_enforcement_status()
        if status.effective_mode not in HOSTS_ENFORCING_MODES:
            return []
        if not self._active_day_is_locked():
            return []
        if self.settings.safe_mode or self.settings.recovery_mode:
            return []
        release_status = self.get_website_high_release_status(now=now)
        if release_status.status != "allowed":
            return []

        active_processes = (
            _normalize_process_names(process_names)
            if process_names is not None
            else self.process_blocker.running_process_names()
        )
        active_process_set = set(active_processes)
        targets = [
            target
            for target in UNMANAGED_BROWSER_PROCESS_NAMES
            if target in active_process_set
            and not self._unmanaged_browser_guard_recently_attempted(target, now)
        ]
        if not targets:
            return []
        results = self.process_blocker.terminate(
            targets,
            active_process_names=active_processes,
        )
        for result in results:
            self._remember_unmanaged_browser_guard_attempt(result.target, now)
        return results

    def list_recent_access_attempts(
        self,
        *,
        limit: int = 10,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> list[AccessAttemptRecord]:
        """Return recent Test Mode access attempts, newest first."""
        return self._list_recent_access_attempts(
            limit=limit,
            today_only=today_only,
            occurred_on=occurred_on,
        )

    def list_recent_dry_run_process_attempts(
        self,
        *,
        limit: int = 5,
        decision: str | None = None,
        process_query: str = "",
        access_level: str | None = None,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> list[AccessAttemptRecord]:
        """Return recent Armed Dry Run process attempts, newest first."""
        if limit <= 0:
            return []
        clean_decision = _normalize_dry_run_decision_filter(decision)
        clean_access_level = _normalize_access_level_filter(access_level)
        clean_process_query = process_query.strip().lower()
        attempts = self._list_recent_access_attempts(
            limit=max(100, min(500, limit * 10)),
            source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
            today_only=today_only,
            occurred_on=occurred_on,
        )
        filtered: list[AccessAttemptRecord] = []
        for attempt in attempts:
            if not _attempt_matches_decision_filter(
                attempt.decision,
                clean_decision,
            ):
                continue
            if (
                clean_access_level is not None
                and attempt.access_level_at_attempt != clean_access_level
            ):
                continue
            if clean_process_query and clean_process_query not in attempt.target.lower():
                continue
            filtered.append(attempt)
            if len(filtered) >= limit:
                break
        return filtered

    def get_dry_run_process_attempt_summary(
        self,
        *,
        limit: int = 20,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> DryRunProcessAttemptSummary:
        """Return a compact summary of recent process enforcement attempts."""
        safe_limit = max(0, min(limit, 100))
        attempts = self.list_recent_process_enforcement_attempts(
            limit=safe_limit,
            today_only=today_only,
            occurred_on=occurred_on,
        )
        scoped_would_block = [
            attempt
            for attempt in attempts
            if attempt.decision == AccessAttemptDecision.WOULD_BLOCK.value
        ]
        last_would_block = next(
            (
                attempt.target
                for attempt in attempts
                if attempt.decision == AccessAttemptDecision.WOULD_BLOCK.value
            ),
            None,
        )
        return DryRunProcessAttemptSummary(
            total_recent_attempts=len(attempts),
            today_would_block_count=len(scoped_would_block),
            last_would_block_target=last_would_block,
            latest_attempts=attempts[: min(safe_limit, 5)],
            real_blocking_note=self._process_enforcement_note(),
        )

    def list_recent_process_enforcement_attempts(
        self,
        *,
        limit: int = 5,
        decision: str | None = None,
        process_query: str = "",
        access_level: str | None = None,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> list[AccessAttemptRecord]:
        """Return recent Armed Dry Run or real process attempts, newest first."""
        if limit <= 0:
            return []
        clean_decision = _normalize_dry_run_decision_filter(decision)
        clean_access_level = _normalize_access_level_filter(access_level)
        clean_process_query = process_query.strip().lower()
        process_sources = {
            AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
            AccessAttemptSource.REAL_PROCESS_BLOCKING_PROCESS.value,
        }
        attempts = self._list_recent_access_attempts(
            limit=max(100, min(500, limit * 10)),
            today_only=today_only,
            occurred_on=occurred_on,
        )
        filtered: list[AccessAttemptRecord] = []
        for attempt in attempts:
            if attempt.source not in process_sources:
                continue
            if not _attempt_matches_decision_filter(
                attempt.decision,
                clean_decision,
            ):
                continue
            if (
                clean_access_level is not None
                and attempt.access_level_at_attempt != clean_access_level
            ):
                continue
            if clean_process_query and clean_process_query not in attempt.target.lower():
                continue
            filtered.append(attempt)
            if len(filtered) >= limit:
                break
        return filtered

    def get_recent_attempt_summary(
        self,
        *,
        limit: int = 20,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> AccessAttemptSummary:
        """Return a read-only summary of recent Test Mode access attempts."""
        attempts = self._list_recent_access_attempts(
            limit=_clamp_summary_limit(limit),
            today_only=today_only,
            occurred_on=occurred_on,
        )
        return _summarize_access_attempts(attempts)

    def get_browser_escape_summary(
        self,
        *,
        limit: int = 50,
        today_only: bool = True,
        occurred_on: str | None = None,
    ) -> BrowserEscapeSummary:
        """Return a privacy-minimal summary of browser attempts."""
        attempts = self._list_recent_access_attempts(
            limit=_clamp_summary_limit(limit),
            source=AccessAttemptSource.BROWSER.value,
            today_only=today_only,
            occurred_on=occurred_on,
        )
        scoped_attempts = [
            attempt
            for attempt in attempts
            if attempt.decision
            in {
                AccessAttemptDecision.WOULD_BLOCK.value,
                AccessAttemptDecision.WOULD_ALLOW.value,
                AccessAttemptDecision.ALLOWED_BY_PLANNED_USE_PASS.value,
            }
        ]
        return _summarize_browser_escape_attempts(scoped_attempts)

    def _list_recent_access_attempts(
        self,
        *,
        limit: int,
        source: str | None = None,
        today_only: bool,
        occurred_on: str | None,
    ) -> list[AccessAttemptRecord]:
        if limit <= 0:
            return []
        local_day = self._attempt_occurred_on(
            today_only=today_only,
            occurred_on=occurred_on,
        )
        attempts = self.access_attempts.list_recent(
            limit=max(500, min(2000, limit * 50)),
            source=source,
        )
        if local_day is not None:
            attempts = [
                attempt
                for attempt in attempts
                if attempt_local_day(attempt.occurred_at) == local_day
            ]
        return attempts[:limit]

    def _attempt_occurred_on(
        self,
        *,
        today_only: bool,
        occurred_on: str | None,
    ) -> str | None:
        if occurred_on is not None:
            return occurred_on
        return self._today() if today_only else None

    def _process_enforcement_note(self) -> str:
        status = self.get_enforcement_status()
        if status.effective_mode is EnforcementMode.REAL_PROCESS_BLOCKING:
            return (
                "Real process blocking is active for explicit app rules. "
                "Websites are not blocked yet."
            )
        if status.effective_mode is EnforcementMode.FULL_ENFORCEMENT:
            return (
                "Full Enforcement is active for explicit app rules and website "
                "domain rules."
            )
        if status.effective_mode is EnforcementMode.ARMED_DRY_RUN:
            return "Armed Dry Run logs matching app rules without blocking."
        return "Real process blocking is inactive."

    def preview_blocking(self) -> BlockingPreview:
        """Return a dry-run preview of what would be blocked."""
        self._ensure_current_day()
        now = self._now()
        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        else:
            day = self._apply_bad_day_baseline_if_needed(day)
        rules = self._rule_repository()
        site_rules = rules.list(rule_type="site")
        app_rules = rules.list(rule_type="app")
        active_pass = self._active_planned_use_pass(now)
        if day.surrender_requested_at is not None:
            return BlockingPreview(
                access_level=day.access_level,
                test_mode=self.settings.test_mode,
                sites=[],
                apps=[],
                blocked_sites=[],
                blocked_apps=[],
                allowed_sites=[rule.target for rule in site_rules],
                allowed_apps=[rule.target for rule in app_rules],
                message=(
                    "Surrender active - restrictions paused for today. "
                    "Preview only - Test Mode. No system changes."
                ),
                restriction_state="surrender",
                active_planned_use_pass=active_pass,
            )

        preview_access_level = _effective_access_level_for_rules(day)
        blocked_sites = _blocked_targets(
            preview_access_level,
            site_rules,
            active_pass,
        )
        blocked_apps = _blocked_targets(
            preview_access_level,
            app_rules,
            active_pass,
        )
        allowed_sites = _allowed_targets(
            preview_access_level,
            site_rules,
            active_pass,
        )
        allowed_apps = _allowed_targets(
            preview_access_level,
            app_rules,
            active_pass,
        )

        return BlockingPreview(
            access_level=preview_access_level,
            test_mode=self.settings.test_mode,
            sites=blocked_sites,
            apps=blocked_apps,
            blocked_sites=blocked_sites,
            blocked_apps=blocked_apps,
            allowed_sites=allowed_sites,
            allowed_apps=allowed_apps,
            restriction_state=(
                "bad_day"
                if day.bad_day_mode and day.access_level is not AccessLevel.HIGH
                else day.access_level.value
            ),
            message="Preview only — Test Mode. No system changes.",
            active_planned_use_pass=active_pass,
        )

    def preview_hosts_blocking_dry_run(self) -> HostsBlockingDryRunPreview:
        """Return dry-run hosts entries for currently blocked website rules."""
        targets = self._site_access_targets_for_hosts(now=self._now())
        entries = generate_hosts_entries(targets.blocked_sites)
        blocked_domains = [entry.split(maxsplit=1)[1] for entry in entries]
        return HostsBlockingDryRunPreview(
            blocked_domains=blocked_domains,
            hosts_entries=entries,
            managed_section=add_or_replace_managed_block("", blocked_domains),
            message="Dry-run only. Websites are not blocked yet.",
        )

    def get_website_high_release_status(
        self,
        *,
        now: datetime | None = None,
    ) -> WebsiteHighReleaseStatus:
        """Return whether website HIGH/pass hosts release is trusted."""
        current_now = now or self._now()
        targets = self._site_access_targets_for_hosts(now=current_now)
        trusted_ready = self.is_trusted_browser_control_ready_for_website_high(
            now=current_now
        )
        if not targets.trust_required_sites:
            return WebsiteHighReleaseStatus(
                status="not_needed",
                message="Website HIGH hosts release: Not needed.",
                trusted_browser_ready=trusted_ready,
                held_closed_targets=(),
                other_browsers_status="not_needed",
            )
        if targets.held_closed_sites:
            return WebsiteHighReleaseStatus(
                status="held_closed",
                message=(
                    "Website HIGH hosts release: Held closed. Website HIGH "
                    "requires trusted Chrome extension control."
                ),
                trusted_browser_ready=False,
                held_closed_targets=tuple(targets.held_closed_sites),
                other_browsers_status="not_controlled",
            )
        return WebsiteHighReleaseStatus(
            status="allowed",
            message="Website HIGH hosts release: Allowed.",
            trusted_browser_ready=True,
            held_closed_targets=(),
            other_browsers_status="guard_active",
        )

    def run_real_hosts_blocking_cycle(
        self,
        *,
        force: bool = False,
    ) -> HostsActionResult | None:
        """Apply or clear the managed hosts section for the effective mode."""
        self._ensure_current_day()
        status = self.get_enforcement_status()
        active_day = self._active_day_is_locked()
        if status.effective_mode in HOSTS_ENFORCING_MODES and active_day:
            preview = self.preview_hosts_blocking_dry_run()
            blocked_domains = tuple(preview.blocked_domains)
            signature = ("apply", blocked_domains)
            if (
                not force
                and self._suppressed_hosts_permission_signature == signature
            ):
                return self._last_hosts_action_result
            if not force and self._last_hosts_signature == signature:
                return None
            result = (
                self.hosts_blocker.apply_real(blocked_domains)
                if blocked_domains
                else self.hosts_blocker.clear_real()
            )
        else:
            signature = ("clear", ())
            if not force and self._last_hosts_signature == signature:
                return None
            result = self.hosts_blocker.clear_real()

        self._last_hosts_action_result = result
        self._last_hosts_signature = signature
        if result.status == "permission_denied":
            self._suppressed_hosts_permission_signature = signature
        elif self._suppressed_hosts_permission_signature == signature:
            self._suppressed_hosts_permission_signature = None
        return result

    def get_hosts_blocking_status(self) -> HostsBlockingRuntimeStatus:
        """Return compact status for hosts enforcement UI."""
        enforcement_status = self.get_enforcement_status()
        preview = self.preview_hosts_blocking_dry_run()
        blocked_count = len(preview.blocked_domains)
        blocked_examples = tuple(preview.blocked_domains[:3])
        release_status = self.get_website_high_release_status()
        last_result = self._last_hosts_action_result
        last_action_status = last_result.status if last_result else ""
        active_day = self._active_day_is_locked()
        if last_action_status == "permission_denied":
            return HostsBlockingRuntimeStatus(
                status="permission_denied",
                active=False,
                blocked_domain_count=blocked_count,
                last_action_status=last_action_status,
                message=(
                    "Websites: Permission denied. LoopGuard could not update "
                    "the hosts file. Run LoopGuard as administrator for website "
                    "blocking. App/process blocking remains separate."
                ),
                blocked_domain_examples=blocked_examples,
            )
        if enforcement_status.effective_mode in HOSTS_ENFORCING_MODES and not active_day:
            return HostsBlockingRuntimeStatus(
                status="armed_idle",
                active=False,
                blocked_domain_count=blocked_count,
                last_action_status=last_action_status,
                message="Websites: Blocking armed. Starts when day starts.",
                blocked_domain_examples=blocked_examples,
            )
        if enforcement_status.effective_mode in HOSTS_ENFORCING_MODES:
            if blocked_count:
                if release_status.status == "held_closed":
                    message = (
                        "Websites: Active at hosts/DNS level. Website HIGH is "
                        "held closed because trusted browser control is not ready."
                    )
                else:
                    message = (
                        "Websites: Active at hosts/DNS level "
                        f"({blocked_count} domains)."
                    )
                return HostsBlockingRuntimeStatus(
                    status="active",
                    active=True,
                    blocked_domain_count=blocked_count,
                    last_action_status=last_action_status,
                    message=message,
                    blocked_domain_examples=blocked_examples,
                )
            return HostsBlockingRuntimeStatus(
                status="not_active",
                active=False,
                blocked_domain_count=0,
                last_action_status=last_action_status,
                message="Websites: Not active. No website rules are blocked now.",
                blocked_domain_examples=blocked_examples,
            )
        return HostsBlockingRuntimeStatus(
            status="not_active",
            active=False,
            blocked_domain_count=blocked_count,
            last_action_status=last_action_status,
            message="Websites: Not active.",
            blocked_domain_examples=blocked_examples,
        )

    def get_browser_integration_status(
        self,
        *,
        now: datetime | None = None,
    ) -> BrowserIntegrationStatus:
        """Return read-only browser extension heartbeat status."""
        heartbeat_path = self.settings.data_dir / BROWSER_HEARTBEAT_FILE_NAME
        if not heartbeat_path.exists():
            return _browser_status_disconnected("No browser heartbeat found.")

        try:
            raw = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return _browser_status_disconnected("Browser heartbeat is invalid.")
            last_heartbeat_at = raw.get("last_heartbeat_at")
            if not isinstance(last_heartbeat_at, str) or not last_heartbeat_at:
                return _browser_status_disconnected(
                    "Browser heartbeat time is unavailable."
                )
            heartbeat_time = _parse_datetime(last_heartbeat_at)
            current_time = now or self._now()
            age_seconds = max(
                0,
                int((current_time - heartbeat_time).total_seconds()),
            )
        except Exception:
            return _browser_status_disconnected("Browser heartbeat is unreadable.")

        browser = _readable_browser_name(raw.get("browser"))
        context = _readable_browser_context(raw.get("context"))
        incognito_status = _readable_incognito_status(raw.get("incognito_allowed"))
        browser_blocking = _readable_browser_blocking(raw.get("browser_blocking"))
        browser_blocking_available = _readable_bool_status(
            raw.get("browser_blocking_available")
        )
        dnr_status = _readable_dnr_status(raw)
        dnr_session_rule_count = _readable_dnr_rule_count(
            raw.get("dnr_session_rule_count")
        )
        dnr_last_update_status = _readable_compact_status(
            raw.get("dnr_last_update_status")
        )
        dnr_last_error = _readable_compact_text(raw.get("dnr_last_error"))
        youtube_spa_status = _readable_youtube_spa_status(
            raw.get("youtube_spa_content_script_seen")
        )
        extension_version = _readable_compact_text(raw.get("extension_version"))
        extension_heartbeat_status = (
            "seen" if raw.get("extension_connected") is not False else "disconnected"
        )
        heartbeat_source = raw.get("source")
        legacy_or_native_host_heartbeat = (
            heartbeat_source == "native_host" or heartbeat_source is None
        )
        native_host_status = (
            "connected"
            if legacy_or_native_host_heartbeat
            and extension_heartbeat_status == "seen"
            else "not_connected"
        )
        native_host_prepared_status = (
            "prepared" if legacy_or_native_host_heartbeat else "unknown"
        )
        browser_blocking_ready = (
            age_seconds <= BROWSER_HEARTBEAT_STALE_SECONDS
            and extension_heartbeat_status == "seen"
            and native_host_status == "connected"
            and browser == "Chrome"
            and incognito_status == "allowed"
            and browser_blocking_available == "yes"
            and dnr_status in {"active", "supported_no_rules"}
        )
        browser_high_safety = (
            "trusted_for_chrome"
            if browser_blocking_ready
            else "partial"
        )
        if age_seconds <= BROWSER_HEARTBEAT_STALE_SECONDS:
            connection_status = "connected" if browser_blocking_ready else "partial"
            next_action = _browser_diagnostics_next_action(
                connection_status=connection_status,
                incognito_status=incognito_status,
                dnr_status=dnr_status,
                dnr_session_rule_count=dnr_session_rule_count,
                dnr_last_update_status=dnr_last_update_status,
                youtube_spa_status=youtube_spa_status,
                browser_blocking_available=browser_blocking_available,
            )
            return BrowserIntegrationStatus(
                connection_status=connection_status,
                connected=browser_blocking_ready,
                extension_heartbeat_status=extension_heartbeat_status,
                native_host_status=native_host_status,
                native_host_prepared_status=native_host_prepared_status,
                browser_blocking_ready=browser_blocking_ready,
                browser=browser,
                context=context,
                native_messaging_status=native_host_status,
                incognito_status=incognito_status,
                browser_blocking=browser_blocking,
                browser_blocking_available=browser_blocking_available,
                dnr_status=dnr_status,
                dnr_session_rule_count=dnr_session_rule_count,
                dnr_last_update_status=dnr_last_update_status,
                dnr_last_error=dnr_last_error,
                youtube_spa_status=youtube_spa_status,
                extension_version=extension_version,
                last_heartbeat_at=last_heartbeat_at,
                last_heartbeat_age_seconds=age_seconds,
                browser_high_safety=browser_high_safety,
                next_action=next_action,
                message=(
                    "Browser ready."
                    if browser_blocking_ready
                    else _browser_partial_status_message(
                        extension_heartbeat_status=extension_heartbeat_status,
                        native_host_status=native_host_status,
                        native_host_prepared_status=native_host_prepared_status,
                    )
                ),
            )
        return BrowserIntegrationStatus(
            connection_status="stale",
            connected=False,
            extension_heartbeat_status="stale",
            native_host_status="not_connected",
            native_host_prepared_status=native_host_prepared_status,
            browser_blocking_ready=False,
            browser=browser,
            context=context,
            native_messaging_status="not_connected",
            incognito_status=incognito_status,
            browser_blocking=browser_blocking,
            browser_blocking_available=browser_blocking_available,
            dnr_status=dnr_status,
            dnr_session_rule_count=dnr_session_rule_count,
            dnr_last_update_status=dnr_last_update_status,
            dnr_last_error=dnr_last_error,
            youtube_spa_status=youtube_spa_status,
            extension_version=extension_version,
            last_heartbeat_at=last_heartbeat_at,
            last_heartbeat_age_seconds=age_seconds,
            browser_high_safety="not_ready",
            next_action="Reload the extension or check the native host registration.",
            message="Browser extension heartbeat is stale.",
        )

    def is_trusted_browser_control_ready_for_website_high(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return whether website HIGH/pass can safely leave hosts blocking."""
        if self.settings.safe_mode or self.settings.recovery_mode:
            return False
        day = self.day_state.get()
        if day.day_started_at is None or day.day_ended_at is not None:
            return False
        browser_status = self.get_browser_integration_status(now=now)
        return (
            browser_status.connection_status == "connected"
            and browser_status.browser == "Chrome"
            and browser_status.incognito_status == "allowed"
            and browser_status.browser_blocking != "not_implemented"
        )

    def _site_access_targets_for_hosts(self, *, now: datetime) -> _SiteAccessTargets:
        day = self._expire_high_if_needed(now)
        if day.surrender_requested_at is not None:
            day = self._reconcile_high_during_surrender(now)
        else:
            day = self._apply_bad_day_baseline_if_needed(day)
        site_rules = [
            rule
            for rule in self._rule_repository().list(rule_type="site")
            if not _is_browser_url_pattern_target(rule.target)
        ]
        active_pass = self._active_planned_use_pass(now)
        if day.surrender_requested_at is not None:
            return _SiteAccessTargets(
                blocked_sites=[],
                allowed_sites=[rule.target for rule in site_rules],
                held_closed_sites=[],
                trust_required_sites=[],
            )

        access_level = _effective_access_level_for_rules(day)
        fallback_access_level = (
            self._fallback_access_level_today()
            if access_level is AccessLevel.HIGH
            else access_level
        )
        return _site_access_targets(
            access_level,
            site_rules,
            active_pass,
            trusted_browser_ready=(
                self.is_trusted_browser_control_ready_for_website_high(now=now)
            ),
            fallback_access_level=fallback_access_level,
        )

    def _refresh_hosts_enforcement_after_access_change(self) -> None:
        """Synchronize hosts after HIGH/pass changes in hosts-enforcing modes."""
        if self.get_enforcement_status().effective_mode in HOSTS_ENFORCING_MODES:
            self.run_real_hosts_blocking_cycle(force=True)

    def _activate_surrender_with_high_reconciliation(self, now: datetime) -> DayState:
        return self._finish_high_for_surrender(now, activated_at=now.isoformat())

    def _active_planned_use_pass(
        self,
        now: datetime,
    ) -> PlannedUsePassRecord | None:
        now_iso = now.isoformat()
        self.planned_use_passes.expire_due(now_iso)
        return self.planned_use_passes.get_active(now_iso)

    def _reconcile_high_during_surrender(self, now: datetime) -> DayState:
        day = self.day_state.get()
        if day.surrender_requested_at is None:
            return day
        return self._finish_high_for_surrender(now, activated_at=None)

    def _finish_high_for_surrender(
        self,
        now: datetime,
        *,
        activated_at: str | None,
    ) -> DayState:
        day = self.day_state.get()
        session = self.high_sessions.active_for_day(self._today())
        should_restore_access = day.access_level is AccessLevel.HIGH
        if session is None and not should_restore_access and activated_at is None:
            return day

        remaining_seconds = self._session_remaining_seconds(session, now)
        fallback_level = self._fallback_access_level_today()
        now_iso = now.isoformat()
        connection = self.day_state.connection
        with connection:
            session_was_ended = False
            if session is not None:
                cursor = connection.execute(
                    """
                    UPDATE high_sessions
                    SET ended_at = ?, end_reason = ?
                    WHERE id = ? AND ended_at IS NULL
                    """,
                    (
                        now_iso,
                        "ended_early" if remaining_seconds > 0 else "expired",
                        session.id,
                    ),
                )
                session_was_ended = cursor.rowcount == 1
            if remaining_seconds > 0 and session_was_ended:
                connection.execute(
                    """
                    INSERT INTO reward_ledger (
                        task_id,
                        minutes_delta,
                        seconds_delta,
                        reason,
                        created_at
                    )
                    VALUES (NULL, ?, ?, ?, ?)
                    """,
                    (
                        int(remaining_seconds // 60),
                        remaining_seconds,
                        "high_mode_refund",
                        now_iso,
                    ),
                )

            refunded_seconds = remaining_seconds if session_was_ended else 0

            if activated_at is None:
                if refunded_seconds > 0:
                    connection.execute(
                        """
                        UPDATE day_state
                        SET access_level = ?,
                            reward_balance_seconds = reward_balance_seconds + ?,
                            reward_balance_minutes = CAST(
                                (reward_balance_seconds + ?) / 60 AS INTEGER
                            ),
                            updated_at = ?
                        WHERE id = 1
                        """,
                        (
                            fallback_level.value,
                            refunded_seconds,
                            refunded_seconds,
                            now_iso,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE day_state
                        SET access_level = ?,
                            updated_at = ?
                        WHERE id = 1
                        """,
                        (fallback_level.value, now_iso),
                    )
            elif refunded_seconds > 0:
                connection.execute(
                    """
                    UPDATE day_state
                    SET access_level = ?,
                        reward_balance_seconds = reward_balance_seconds + ?,
                        reward_balance_minutes = CAST(
                            (reward_balance_seconds + ?) / 60 AS INTEGER
                        ),
                        surrender_requested_at = COALESCE(surrender_requested_at, ?),
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        fallback_level.value,
                        refunded_seconds,
                        refunded_seconds,
                        activated_at,
                        now_iso,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE day_state
                    SET access_level = ?,
                        surrender_requested_at = COALESCE(surrender_requested_at, ?),
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (fallback_level.value, activated_at, now_iso),
                )

        return self.day_state.get()

    def _expire_high_if_needed(self, now: datetime) -> DayState:
        day = self.day_state.get()
        session = self.high_sessions.active_for_day(self._today())
        if session is None:
            if day.access_level is AccessLevel.HIGH:
                return self.day_state.set_access_level(self._fallback_access_level_today())
            return day

        remaining_seconds = self._session_remaining_seconds(session, now)
        if remaining_seconds > 0:
            if day.access_level is not AccessLevel.HIGH:
                day = self.day_state.set_access_level(AccessLevel.HIGH)
            return day

        self.high_sessions.end(
            session.id,
            ended_at=now.isoformat(),
            reason="expired",
        )
        return self.day_state.set_access_level(self._fallback_access_level_today())

    def _session_remaining_seconds(
        self,
        session: HighSessionRecord | None,
        now: datetime,
    ) -> int:
        if session is None:
            return 0
        ends_at = _parse_datetime(session.ends_at)
        return max(0, int((ends_at - now).total_seconds()))

    def _high_started_seconds_for_day(self, day_date: str) -> int:
        return sum(
            session.allocated_seconds
            for session in self.high_sessions.list()
            if session.day_date == day_date
        )

    def _high_daily_remaining_seconds(self, day_date: str) -> int:
        cap_seconds = self.get_daily_recreation_cap_minutes() * 60
        return max(
            0,
            cap_seconds - self._high_started_seconds_for_day(day_date),
        )

    def _latest_high_cooldown_start_for_day(
        self,
        day_date: str,
    ) -> datetime | None:
        cooldown_starts: list[datetime] = []
        for session in self.high_sessions.list():
            if session.day_date != day_date or session.ended_at is None:
                continue
            if session.end_reason == "expired":
                cooldown_starts.append(_parse_datetime(session.ends_at))
            else:
                cooldown_starts.append(_parse_datetime(session.ended_at))
        if not cooldown_starts:
            return None
        return max(cooldown_starts)

    def _high_cooldown_remaining_seconds(
        self,
        day_date: str,
        now: datetime,
    ) -> int:
        cooldown_start = self._latest_high_cooldown_start_for_day(day_date)
        if cooldown_start is None:
            return 0
        elapsed_seconds = int((now - cooldown_start).total_seconds())
        return max(0, HIGH_COOLDOWN_SECONDS - elapsed_seconds)

    def _high_remaining_seconds(self, day: DayState) -> int:
        session = self.high_sessions.active_for_day(day.day)
        return self._session_remaining_seconds(session, self._now())

    def _clear_high_runtime(self) -> None:
        self._high_started_at = None
        self._high_minutes_total = 0
        self._high_medium_unlocked = False

    def _ensure_current_day(self) -> DayState:
        today = self._today()
        day = self.day_state.get()
        if day.day == today:
            return day

        now = self._now().isoformat()
        for session in self.high_sessions.list(active_only=True):
            if session.day_date != today:
                self.high_sessions.end(
                    session.id,
                    ended_at=now,
                    reason="day_rollover",
                )
        self._clear_high_runtime()
        self.app_settings.set_value(_END_DAY_PENDING_STARTED_AT_KEY, "")
        self.app_settings.set_value(_HIGH_NOTIFICATION_SESSION_KEY, "")
        self.app_settings.set_value(_HIGH_WARNING_SENT_KEY, "false")
        self.app_settings.set_value(_HIGH_END_SENT_KEY, "false")
        return self.day_state.reset_for_day(today)

    def _today(self) -> str:
        return self._now().astimezone().date().isoformat()

    def _now(self) -> datetime:
        if self._now_provider is not None:
            return self._now_provider()
        return datetime.now(timezone.utc)

    def _rule_repository(self) -> RuleRepository:
        if self.rules is None:
            raise RuntimeError("Rule repository is not configured")
        return self.rules

    def _rule_by_key(self, rule_type: str, target: str) -> RuleRecord | None:
        for rule in self._rule_repository().list(
            rule_type=rule_type,
            enabled_only=False,
        ):
            if rule.target == target:
                return rule
        return None

    def _resolve_stored_rule_target(
        self,
        rule_type: str,
        target: str,
    ) -> tuple[str, RuleRecord | None]:
        raw_target = target.strip()
        try:
            clean_target = _normalize_rule_target(rule_type, raw_target)
        except ValueError:
            clean_target = raw_target

        existing = self._rule_by_key(rule_type, clean_target)
        if existing is None and raw_target != clean_target:
            existing = self._rule_by_key(rule_type, raw_target)
        if existing is not None:
            return existing.target, existing
        return clean_target, None

    def _active_day_is_locked(self) -> bool:
        day = self.day_state.get()
        return day.day_started_at is not None and day.day_ended_at is None

    def _reject_active_day_rule_removal(self) -> None:
        if self._active_day_is_locked():
            raise ValueError(
                "Rules are locked during an active day. "
                "You can add stricter rules or edit tomorrow."
            )

    def _reject_active_day_rule_weakening(
        self,
        current_allow_from_level: str,
        next_allow_from_level: str,
    ) -> None:
        if not self._active_day_is_locked():
            return
        if next_allow_from_level != current_allow_from_level:
            raise ValueError(
                "Rules are locked during an active day. "
                "You can add stricter rules or edit tomorrow."
            )

    def _real_process_action_recently_attempted(
        self,
        rule: RuleRecord,
        now: datetime,
    ) -> bool:
        last_attempt = self._real_process_action_attempts.get((rule.id, rule.target))
        if last_attempt is None:
            return False
        elapsed_seconds = (now - last_attempt).total_seconds()
        return 0 <= elapsed_seconds < REAL_PROCESS_BLOCKING_ACTION_COOLDOWN_SECONDS

    def _remember_real_process_action_attempt(
        self,
        rule: RuleRecord,
        now: datetime,
    ) -> None:
        self._real_process_action_attempts[(rule.id, rule.target)] = now

    def _unmanaged_browser_guard_recently_attempted(
        self,
        target: str,
        now: datetime,
    ) -> bool:
        last_attempt = self._unmanaged_browser_guard_attempts.get(target)
        if last_attempt is None:
            return False
        elapsed_seconds = (now - last_attempt).total_seconds()
        return 0 <= elapsed_seconds < UNMANAGED_BROWSER_GUARD_COOLDOWN_SECONDS

    def _remember_unmanaged_browser_guard_attempt(
        self,
        target: str,
        now: datetime,
    ) -> None:
        self._unmanaged_browser_guard_attempts[target] = now

    def _surrender_delay_seconds(self) -> int:
        return _surrender_delay_seconds_for(self.get_surrender_strictness())

    def _apply_bad_day_baseline_if_needed(self, day: DayState) -> DayState:
        if (
            day.bad_day_mode
            and day.day_started_at is not None
            and day.access_level is AccessLevel.LOW
        ):
            return self.day_state.set_access_level(AccessLevel.MEDIUM)
        return day

    def _has_completed_main_today(self) -> bool:
        if self.day_state.get().day_started_at is None:
            return False
        return any(
            task.kind is TaskKind.MAIN
            and task.status is TaskStatus.DONE
            and task.planning_status is TaskPlanningStatus.PLANNED
            for task in self.tasks.list_for_day(self._today())
        )

    def _has_pending_planned_main_today(self) -> bool:
        return any(
            task.kind is TaskKind.MAIN
            and task.status is TaskStatus.PENDING
            and task.planning_status is TaskPlanningStatus.PLANNED
            for task in self.tasks.list_for_day(self._today())
        )

    def _has_pending_completion_claim_today(self) -> bool:
        return any(
            task.completion_claimed_at is not None
            or task.completion_available_at is not None
            for task in self.tasks.list_for_day(self._today())
        )

    def _rest_token_unavailable_reason(
        self,
        day: DayState,
        now: datetime,
    ) -> str:
        if self.get_rest_token_count() <= 0:
            return REST_TOKEN_NONE_AVAILABLE
        if day.day_started_at is not None:
            return REST_TOKEN_PRE_START_ONLY
        if day.day_ended_at is not None:
            return END_DAY_ALREADY_CLOSED
        if self._has_pending_completion_claim_today():
            return REST_TOKEN_PENDING_CLAIM
        if (
            self._session_remaining_seconds(
                self.high_sessions.active_for_day(self._today()),
                now,
            )
            > 0
        ):
            return "End active Recreation before using earned rest."
        if self._active_planned_use_pass(now) is not None:
            return "End active planned-use pass before using earned rest."
        return ""

    def _award_rest_token_if_earned(self) -> None:
        if self.get_rest_token_count() >= REST_TOKEN_MAX_COUNT:
            return
        if self._normal_main_completion_streak_through(self._today()) >= (
            REST_TOKEN_EARN_STREAK_DAYS
        ):
            self.app_settings.set_rest_token_count(REST_TOKEN_MAX_COUNT)

    def _normal_main_completion_streak_through(self, day_date: str) -> int:
        try:
            current_day = date.fromisoformat(day_date)
        except ValueError:
            return 0

        streak = 0
        for offset in range(REST_TOKEN_EARN_STREAK_DAYS):
            outcome = self.day_outcomes.get(
                (current_day - timedelta(days=offset)).isoformat()
            )
            if (
                outcome is None
                or outcome.close_kind is not DayOutcomeCloseKind.NORMAL
                or not outcome.main_completed
            ):
                break
            streak += 1
        return streak

    def _task_completion_context(
        self,
        task_id: int,
    ) -> tuple[Task, DayState, datetime]:
        self._ensure_current_day()
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task.day_date != self._today():
            raise ValueError("Task is not active today")

        current_day = self.day_state.get()
        if current_day.day_started_at is None:
            raise ValueError("Start Day before completing tasks")
        if current_day.day_ended_at is not None:
            raise ValueError(TASK_COMPLETION_UNAVAILABLE_AFTER_END_DAY)
        now = self._now()
        if current_day.surrender_requested_at is not None:
            raise ValueError(TASK_COMPLETION_UNAVAILABLE_AFTER_SURRENDER)
        soft_start = self._soft_start_status(current_day, now)
        if soft_start.active:
            raise ValueError("Soft Start active: tasks unlock after the buffer")
        return task, current_day, now

    def _complete_task_now(
        self,
        task: Task,
        current_day: DayState,
        now: datetime,
    ) -> TaskCompletionResult:
        completed_task = self.tasks.update_status(task.id, TaskStatus.DONE)
        if completed_task.planning_status is not TaskPlanningStatus.PLANNED:
            return TaskCompletionResult(
                task=completed_task,
                day_state=current_day,
                reward_entry=None,
            )

        reward_event = self.reward_service.complete_task(
            completed_task,
            occurred_at=now,
        )
        reward_entry = self.rewards.add(
            minutes_delta=reward_event.minutes_delta,
            seconds_delta=reward_event.seconds_delta,
            reason=reward_event.reason,
            task_id=completed_task.id,
        )
        updated_day = self.day_state.add_reward_seconds(reward_event.seconds_delta)

        transition = self.state_machine.complete_task(
            _runtime_state_from_day(updated_day),
            task_kind=completed_task.kind,
        )
        if transition.state.access_level is not updated_day.access_level:
            updated_day = self.day_state.set_access_level(transition.state.access_level)

        return TaskCompletionResult(
            task=completed_task,
            day_state=updated_day,
            reward_entry=reward_entry,
        )

    def _fallback_access_level_today(self) -> AccessLevel:
        day = self.day_state.get()
        if day.bad_day_mode and day.day_started_at is not None:
            return AccessLevel.MEDIUM
        return AccessLevel.MEDIUM if self._has_completed_main_today() else AccessLevel.LOW

    def _soft_start_status(
        self,
        day: DayState,
        now: datetime,
    ) -> "_SoftStartStatus":
        if day.day_started_at is None:
            enabled = self.get_soft_start_enabled()
            duration_minutes = self.get_soft_start_duration_minutes()
        else:
            enabled, duration_minutes = self._soft_start_settings_for_started_day(day)
        if (
            not enabled
            or duration_minutes <= 0
            or day.day_started_at is None
            or day.surrender_requested_at is not None
        ):
            return _SoftStartStatus(
                enabled=enabled,
                duration_minutes=duration_minutes,
                active=False,
                remaining_seconds=0,
            )

        soft_start_end = _parse_datetime(day.day_started_at) + timedelta(
            minutes=duration_minutes
        )
        remaining_seconds = max(0, int((soft_start_end - now).total_seconds()))
        return _SoftStartStatus(
            enabled=enabled,
            duration_minutes=duration_minutes,
            active=remaining_seconds > 0,
            remaining_seconds=remaining_seconds,
        )

    def _ensure_soft_start_settings_editable(self) -> None:
        self._ensure_current_day()
        if self.day_state.get().day_started_at is not None:
            raise ValueError(SOFT_START_LOCKED_AFTER_START_DAY)

    def _ensure_surrender_strictness_editable(self) -> None:
        self._ensure_current_day()
        day = self.day_state.get()
        if day.day_started_at is not None and day.day_ended_at is None:
            raise ValueError(SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY)

    def _ensure_daily_recreation_cap_editable(self) -> None:
        self._ensure_current_day()
        day = self.day_state.get()
        if day.day_started_at is not None and day.day_ended_at is None:
            raise ValueError(SURRENDER_STRICTNESS_LOCKED_AFTER_START_DAY)

    def _validate_normal_end_day(self, now: datetime) -> DayState:
        day = self._expire_high_if_needed(now)
        if day.day_started_at is None:
            raise ValueError(END_DAY_REQUIRES_STARTED_DAY)
        if day.day_ended_at is not None:
            raise ValueError(END_DAY_ALREADY_CLOSED)
        if not self._has_completed_main_today():
            raise ValueError(END_DAY_REQUIRES_COMPLETED_MAIN)
        return day

    def _end_day_pending_started_at(self) -> datetime | None:
        value = self.app_settings.get_value(_END_DAY_PENDING_STARTED_AT_KEY)
        if not value:
            return None
        try:
            return _parse_datetime(value)
        except ValueError:
            return None

    def _close_active_day(
        self,
        now: datetime,
        *,
        close_kind: DayOutcomeCloseKind,
    ) -> DashboardSnapshot:
        day = self.day_state.get()
        main_completed = self._has_completed_main_today()
        if (
            self._session_remaining_seconds(
                self.high_sessions.active_for_day(self._today()),
                now,
            )
            > 0
        ):
            self.end_high_access()
        if self._active_planned_use_pass(now) is not None:
            self.planned_use_passes.end_active(now.isoformat())

        self.day_state.end_day(now.isoformat())
        self.day_outcomes.upsert(
            day_date=self._today(),
            started_at=day.day_started_at,
            ended_at=now.isoformat(),
            close_kind=close_kind,
            main_completed=main_completed,
            rest_token_used=False,
        )
        if close_kind is DayOutcomeCloseKind.NORMAL:
            self._award_rest_token_if_earned()
        self.app_settings.set_value(_END_DAY_PENDING_STARTED_AT_KEY, "")
        self._refresh_hosts_enforcement_after_access_change()
        return self.dashboard_snapshot()

    def _capture_soft_start_for_started_day(self, started_at: str) -> tuple[bool, int]:
        enabled = self.get_soft_start_enabled()
        duration_minutes = self.get_soft_start_duration_minutes()
        self.app_settings.set_value(_SOFT_START_ACTIVE_DAY_STARTED_AT_KEY, started_at)
        self.app_settings.set_value(
            _SOFT_START_ACTIVE_DAY_ENABLED_KEY,
            "true" if enabled else "false",
        )
        self.app_settings.set_value(
            _SOFT_START_ACTIVE_DAY_DURATION_KEY,
            str(duration_minutes),
        )
        return enabled, duration_minutes

    def _soft_start_settings_for_started_day(
        self,
        day: DayState,
    ) -> tuple[bool, int]:
        if day.day_started_at is None:
            return self.get_soft_start_enabled(), self.get_soft_start_duration_minutes()

        captured_started_at = self.app_settings.get_value(
            _SOFT_START_ACTIVE_DAY_STARTED_AT_KEY
        )
        if captured_started_at != day.day_started_at:
            return self._capture_soft_start_for_started_day(day.day_started_at)

        enabled = _stored_bool(
            self.app_settings.get_value(_SOFT_START_ACTIVE_DAY_ENABLED_KEY),
            default=self.get_soft_start_enabled(),
        )
        duration_minutes = _stored_minutes(
            self.app_settings.get_value(_SOFT_START_ACTIVE_DAY_DURATION_KEY),
            default=self.get_soft_start_duration_minutes(),
        )
        return enabled, duration_minutes


def _runtime_state_from_day(
    day: DayState,
    *,
    medium_unlocked: bool | None = None,
) -> AccessRuntimeState:
    """Map persisted day state to the pure state machine input."""
    return AccessRuntimeState(
        access_level=day.access_level,
        medium_unlocked=(
            day.access_level is not AccessLevel.LOW
            if medium_unlocked is None
            else medium_unlocked
        ),
        bad_day_mode=day.bad_day_mode,
    )


@dataclass(frozen=True)
class _SurrenderStatus:
    active: bool
    available: bool
    remaining_seconds: int


@dataclass(frozen=True)
class _SoftStartStatus:
    enabled: bool
    duration_minutes: int
    active: bool
    remaining_seconds: int


def _surrender_status(
    day: DayState,
    now: datetime,
    delay_seconds: int,
    *,
    start_offset_seconds: int = 0,
) -> _SurrenderStatus:
    if day.surrender_requested_at is not None:
        return _SurrenderStatus(
            active=True,
            available=False,
            remaining_seconds=0,
        )
    if day.day_started_at is None:
        return _SurrenderStatus(
            active=False,
            available=False,
            remaining_seconds=delay_seconds,
        )

    available_at = _parse_datetime(day.day_started_at) + timedelta(
        seconds=max(0, start_offset_seconds) + delay_seconds
    )
    remaining_seconds = max(0, int((available_at - now).total_seconds()))
    return _SurrenderStatus(
        active=False,
        available=remaining_seconds == 0,
        remaining_seconds=remaining_seconds,
    )


def _surrender_delay_seconds_for(strictness: str) -> int:
    return SURRENDER_STRICTNESS_DELAYS.get(
        strictness.strip().lower(),
        SURRENDER_DELAY_SECONDS,
    )


def _stored_bool(value: str | None, *, default: bool) -> bool:
    normalized = value.strip().lower() if value else ""
    if normalized in {"1", "true", "on", "yes"}:
        return True
    if normalized in {"0", "false", "off", "no"}:
        return False
    return default


def _stored_minutes(value: str | None, *, default: int) -> int:
    try:
        minutes = int(value) if value is not None else default
    except ValueError:
        return default
    return minutes if 0 <= minutes <= 60 else default


def _normalize_high_intent(intent: str) -> str:
    return re.sub(r"\s+", " ", intent.strip())


def _effective_restriction_state(
    day: DayState,
    *,
    surrender_active: bool,
    high_active: bool,
) -> str:
    if surrender_active:
        return "surrender"
    if high_active:
        return "high"
    if day.bad_day_mode and day.day_started_at is not None:
        return "bad_day"
    return day.access_level.value


def _effective_access_level_for_rules(day: DayState) -> AccessLevel:
    if day.bad_day_mode and day.access_level is AccessLevel.LOW:
        return AccessLevel.MEDIUM
    return day.access_level


def _select_main_task(tasks: list[Task]) -> Task | None:
    """Select the dashboard's main task from repository-ordered tasks."""
    main_tasks = [
        task
        for task in tasks
        if task.kind is TaskKind.MAIN
        and task.planning_status is TaskPlanningStatus.PLANNED
    ]
    for task in main_tasks:
        if task.status is not TaskStatus.DONE:
            return task
    return main_tasks[-1] if main_tasks else None


def _select_pending_main_task(tasks: list[Task]) -> Task | None:
    for task in tasks:
        if (
            task.kind is TaskKind.MAIN
            and task.planning_status is TaskPlanningStatus.PLANNED
            and task.status is TaskStatus.PENDING
        ):
            return task
    return None


def _task_count_by_status(tasks: list[Task], status: TaskStatus) -> int:
    return sum(1 for task in tasks if task.status is status)


_ACCESS_LEVEL_RANK = {
    AccessLevel.LOW: 0,
    AccessLevel.MEDIUM: 1,
    AccessLevel.HIGH: 2,
}

_ALLOW_FROM_LEVEL_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


def _is_allowed_at(access_level: AccessLevel, rule: RuleRecord) -> bool:
    return _ACCESS_LEVEL_RANK[access_level] >= _ALLOW_FROM_LEVEL_RANK[
        rule.allow_from_level
    ]


def _pass_matches_rule(
    active_pass: PlannedUsePassRecord | None,
    rule: RuleRecord,
) -> bool:
    return active_pass is not None and active_pass.rule_id == rule.id


def _normalize_process_names(process_names: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for process_name in process_names:
        clean_name = process_name.strip().lower() if process_name else ""
        if not clean_name or clean_name in seen:
            continue
        seen.add(clean_name)
        normalized.append(clean_name)
    return normalized


def _normalize_dry_run_decision_filter(decision: str | None) -> str | None:
    if decision is None:
        return None
    clean_decision = decision.strip().lower()
    if not clean_decision or clean_decision == "all":
        return None
    allowed_decisions = {
        AccessAttemptDecision.WOULD_BLOCK.value,
        AccessAttemptDecision.WOULD_ALLOW.value,
    }
    if clean_decision not in allowed_decisions:
        raise ValueError(f"Unsupported dry-run decision filter: {decision}")
    return clean_decision


def _attempt_matches_decision_filter(
    attempt_decision: str,
    clean_decision: str | None,
) -> bool:
    if clean_decision is None:
        return True
    if clean_decision == AccessAttemptDecision.WOULD_ALLOW.value:
        return attempt_decision in {
            AccessAttemptDecision.WOULD_ALLOW.value,
            AccessAttemptDecision.ALLOWED_BY_PLANNED_USE_PASS.value,
        }
    if clean_decision == AccessAttemptDecision.WOULD_BLOCK.value:
        return attempt_decision == AccessAttemptDecision.WOULD_BLOCK.value
    return attempt_decision == clean_decision


def _normalize_access_level_filter(access_level: str | None) -> str | None:
    if access_level is None:
        return None
    clean_access_level = access_level.strip().lower()
    if not clean_access_level or clean_access_level == "all":
        return None
    allowed_levels = {level.value for level in AccessLevel}
    if clean_access_level not in allowed_levels:
        raise ValueError(f"Unsupported access level filter: {access_level}")
    return clean_access_level


def _process_attempt_recently_logged(
    attempts: list[AccessAttemptRecord],
    *,
    now: datetime,
    rule: RuleRecord,
    decision: str,
    source: str,
) -> bool:
    for attempt in attempts:
        if (
            attempt.source != source
            or attempt.rule_id != rule.id
            or attempt.target != rule.target
            or attempt.decision != decision
        ):
            continue
        occurred_at = _parse_datetime(attempt.occurred_at)
        elapsed_seconds = (now - occurred_at).total_seconds()
        if 0 <= elapsed_seconds < ARMED_DRY_RUN_PROCESS_RATE_LIMIT_SECONDS:
            return True
    return False


def _blocked_targets(
    access_level: AccessLevel,
    rules: list[RuleRecord],
    active_pass: PlannedUsePassRecord | None = None,
) -> list[str]:
    return [
        rule.target
        for rule in rules
        if not _is_allowed_at(access_level, rule)
        and not _pass_matches_rule(active_pass, rule)
    ]


def _site_access_targets(
    access_level: AccessLevel,
    rules: list[RuleRecord],
    active_pass: PlannedUsePassRecord | None,
    *,
    trusted_browser_ready: bool,
    fallback_access_level: AccessLevel,
) -> _SiteAccessTargets:
    blocked_sites: list[str] = []
    allowed_sites: list[str] = []
    held_closed_sites: list[str] = []
    trust_required_sites: list[str] = []

    for rule in rules:
        allowed_by_level = _is_allowed_at(access_level, rule)
        allowed_by_pass = _pass_matches_rule(active_pass, rule)
        allowed_without_high = _is_allowed_at(fallback_access_level, rule)
        trust_required = (
            (allowed_by_pass and not allowed_by_level)
            or (
                allowed_by_level
                and access_level is AccessLevel.HIGH
                and not allowed_without_high
            )
        )
        if trust_required:
            trust_required_sites.append(rule.target)
        if allowed_by_level or allowed_by_pass:
            if trust_required and not trusted_browser_ready:
                blocked_sites.append(rule.target)
                held_closed_sites.append(rule.target)
            else:
                allowed_sites.append(rule.target)
            continue
        blocked_sites.append(rule.target)

    return _SiteAccessTargets(
        blocked_sites=blocked_sites,
        allowed_sites=allowed_sites,
        held_closed_sites=held_closed_sites,
        trust_required_sites=trust_required_sites,
    )


def _allowed_targets(
    access_level: AccessLevel,
    rules: list[RuleRecord],
    active_pass: PlannedUsePassRecord | None = None,
) -> list[str]:
    return [
        rule.target
        for rule in rules
        if _is_allowed_at(access_level, rule)
        or _pass_matches_rule(active_pass, rule)
    ]


def _clamp_summary_limit(limit: int) -> int:
    return max(0, min(limit, 100))


def _summarize_access_attempts(
    attempts: list[AccessAttemptRecord],
) -> AccessAttemptSummary:
    by_escape_family: dict[str, int] = {}
    by_purpose: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    distinct_non_none_families: set[str] = set()

    for attempt in attempts:
        _increment_count(by_escape_family, attempt.escape_family)
        _increment_count(by_purpose, attempt.purpose)
        _increment_count(by_decision, attempt.decision)
        if attempt.escape_family and attempt.escape_family != EscapeFamily.NONE.value:
            distinct_non_none_families.add(attempt.escape_family)
    recent_family_sequence = _chronological_family_sequence(attempts)

    return AccessAttemptSummary(
        total_attempts=len(attempts),
        by_escape_family=by_escape_family,
        by_purpose=by_purpose,
        by_decision=by_decision,
        recent_family_sequence=recent_family_sequence,
        possible_switching_detected=len(distinct_non_none_families) >= 2,
        pattern_explanation=_attempt_pattern_explanation(
            len(attempts),
            recent_family_sequence,
            distinct_non_none_families,
        ),
        suggested_next_action=_attempt_suggested_next_action(
            len(attempts),
            recent_family_sequence,
            distinct_non_none_families,
        ),
    )


def _summarize_browser_escape_attempts(
    attempts: list[AccessAttemptRecord],
) -> BrowserEscapeSummary:
    if not attempts:
        return BrowserEscapeSummary(
            total_attempts=0,
            last_attempt=None,
            top_targets=(),
            has_attempts=False,
            message="No browser escapes logged today.",
        )

    target_counts: dict[str, int] = {}
    for attempt in attempts:
        target = _browser_escape_display_target(attempt)
        target_counts[target] = target_counts.get(target, 0) + 1
    top_targets = tuple(
        BrowserEscapeTargetSummary(display_target=target, count=count)
        for target, count in sorted(
            target_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:3]
    )
    count_label = "attempt" if len(attempts) == 1 else "attempts"
    return BrowserEscapeSummary(
        total_attempts=len(attempts),
        last_attempt=attempts[0],
        top_targets=top_targets,
        has_attempts=True,
        message=f"{len(attempts)} browser {count_label} today.",
    )


def _day_close_review_from_snapshot(
    close_type: str,
    snapshot: DashboardSnapshot,
) -> DayCloseReviewSummary:
    active_pass = snapshot.active_planned_use_pass
    family_path = " -> ".join(
        _readable_attempt_label(family)
        for family in snapshot.recent_attempt_summary.recent_family_sequence
    )
    return DayCloseReviewSummary(
        close_type=close_type,
        title=(
            "Day closed"
            if close_type == DAY_CLOSE_REVIEW_NORMAL
            else "Today closed in Recovery"
        ),
        main_completed=(
            snapshot.main_task is not None
            and snapshot.main_task.status is TaskStatus.DONE
        ),
        planned_done_count=snapshot.planned_done_count,
        planned_task_count=snapshot.planned_task_count,
        unplanned_done_count=snapshot.unplanned_done_count,
        unplanned_task_count=snapshot.unplanned_task_count,
        recreation_used_seconds=snapshot.high_daily_used_seconds,
        recent_attempt_count=snapshot.recent_attempt_summary.total_attempts,
        recent_family_path=family_path,
        active_planned_use_pass_target=(
            active_pass.target if active_pass is not None else None
        ),
        active_planned_use_pass_type=(
            active_pass.target_type if active_pass is not None else None
        ),
        next_action=(
            "Plan tomorrow when ready."
            if close_type == DAY_CLOSE_REVIEW_NORMAL
            else "Plan a smaller anchor task next time."
        ),
    )


def _browser_escape_display_target(attempt: AccessAttemptRecord) -> str:
    target = attempt.matched_rule_target or attempt.target or "unknown"
    return _privacy_safe_browser_text(target, fallback="unknown")


def _privacy_safe_browser_text(value: str, *, fallback: str) -> str:
    clean_value = value.strip().lower()
    had_scheme = "://" in clean_value
    if "://" in clean_value:
        clean_value = clean_value.split("://", 1)[1]
    if had_scheme:
        clean_value = clean_value.split("/", 1)[0]
    clean_value = clean_value.split("?", 1)[0].split("#", 1)[0]
    clean_value = clean_value.split()[0] if clean_value.split() else ""
    if "://" in clean_value:
        clean_value = ""
    return clean_value[:120] if clean_value else fallback


def _chronological_family_sequence(
    attempts: list[AccessAttemptRecord],
    *,
    max_transitions: int = 8,
) -> list[str]:
    sequence: list[str] = []
    for attempt in reversed(attempts):
        family = attempt.escape_family
        if not family or family == EscapeFamily.NONE.value:
            continue
        if not sequence or sequence[-1] != family:
            sequence.append(family)
    return sequence[-max_transitions:]


def _increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _attempt_pattern_explanation(
    total_attempts: int,
    recent_family_sequence: list[str],
    distinct_non_none_families: set[str],
) -> str:
    if total_attempts == 0:
        return "No Test Mode attempts logged yet."
    if not recent_family_sequence:
        return (
            "Attempts are being logged, but they are not grouped into escape "
            "families yet."
        )
    if len(distinct_non_none_families) == 1:
        family = _readable_attempt_label(recent_family_sequence[0])
        return f"Recent attempts are concentrated in one escape family: {family}."
    if len(distinct_non_none_families) >= 2:
        path = " -> ".join(
            _readable_attempt_label(family)
            for family in recent_family_sequence
        )
        return (
            "Possible escape switching detected: recent attempts moved across "
            f"{len(distinct_non_none_families)} families: {path}."
        )
    return (
        "Recent attempts are logged. Use the pattern to check whether one escape "
        "route is being replaced by another."
    )


def _attempt_suggested_next_action(
    total_attempts: int,
    recent_family_sequence: list[str],
    distinct_non_none_families: set[str],
) -> str:
    if total_attempts == 0:
        return "Log a test attempt from a selected rule to see a pattern."
    if not recent_family_sequence:
        return "Add escape families to rules to make the summary more useful."
    if len(distinct_non_none_families) >= 2:
        return (
            "Consider returning to the anchor task, using earned Recreation, or "
            "entering Recovery if the day is breaking."
        )
    return "Check whether this family should stay HIGH during Focus/Utility."


def _readable_attempt_label(value: str) -> str:
    return value.replace("_", " ")


def _day_summary_label(
    *,
    planned_done: int,
    planned_total: int,
    unplanned_done: int,
    unplanned_total: int,
    reward_balance_seconds: int,
    recent_attempt_summary: AccessAttemptSummary,
) -> str:
    parts = [
        f"Planned: {planned_done} / {planned_total} done.",
        f"Unplanned: {unplanned_done} / {unplanned_total} done.",
        f"Reward balance: {_format_summary_reward_time(reward_balance_seconds)}.",
    ]
    if recent_attempt_summary.recent_family_sequence:
        path = " -> ".join(
            _readable_attempt_label(family)
            for family in recent_attempt_summary.recent_family_sequence
        )
        parts.append(f"Recent pattern: {path}.")
    return " ".join(parts)


def _format_summary_reward_time(seconds: int) -> str:
    safe_seconds = max(0, seconds)
    minutes, remaining_seconds = divmod(safe_seconds, 60)
    if minutes == 0 and remaining_seconds == 0:
        return "0m"
    if minutes == 0:
        return f"{remaining_seconds}s"
    if remaining_seconds == 0:
        return f"{minutes}m"
    return f"{minutes}m {remaining_seconds}s"


def _format_high_cooldown_message(remaining_seconds: int) -> str:
    remaining_minutes = max(1, (max(0, remaining_seconds) + 59) // 60)
    return f"Recreation cooldown: {remaining_minutes}m remaining."


def high_warning_threshold_seconds(allocated_seconds: int) -> int | None:
    """Return the one-shot warning threshold for a HIGH session."""
    if allocated_seconds >= 15 * 60:
        return 5 * 60
    if allocated_seconds >= 5 * 60:
        return 60
    return None


def _high_notification_session_key(session: HighSessionRecord) -> str:
    return f"{session.id}:{session.started_at}"


def _browser_status_disconnected(message: str) -> BrowserIntegrationStatus:
    return BrowserIntegrationStatus(
        connection_status="disconnected",
        connected=False,
        extension_heartbeat_status="missing",
        native_host_status="not_connected",
        native_host_prepared_status="unknown",
        browser_blocking_ready=False,
        browser="Chrome",
        context="unknown",
        native_messaging_status="not_connected",
        incognito_status="unknown",
        browser_blocking="not_implemented",
        browser_blocking_available="unknown",
        dnr_status="unknown",
        dnr_session_rule_count=None,
        dnr_last_update_status="unknown",
        dnr_last_error="",
        youtube_spa_status="unknown",
        extension_version="",
        last_heartbeat_at=None,
        last_heartbeat_age_seconds=None,
        browser_high_safety="not_ready",
        next_action="Register the native host or reload the browser extension.",
        message=message,
    )


def _browser_partial_status_message(
    *,
    extension_heartbeat_status: str,
    native_host_status: str,
    native_host_prepared_status: str,
) -> str:
    if extension_heartbeat_status == "disconnected":
        return "Browser disconnected."
    if native_host_status != "connected" and extension_heartbeat_status == "seen":
        return "Extension seen, native host missing."
    if (
        native_host_prepared_status == "prepared"
        and extension_heartbeat_status != "seen"
    ):
        return "Native host prepared, extension not connected."
    return "Status unknown/stale."


def _readable_browser_name(value: object) -> str:
    if not isinstance(value, str):
        return "Chrome"
    cleaned = value.strip().lower()
    if cleaned == "chrome":
        return "Chrome"
    if cleaned == "edge":
        return "Edge"
    return cleaned.title() if cleaned else "Chrome"


def _readable_browser_context(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = value.strip().lower()
    if cleaned in {"regular", "incognito"}:
        return cleaned
    return "unknown"


def _readable_incognito_status(value: object) -> str:
    if value is True:
        return "allowed"
    if value is False:
        return "not_allowed"
    return "unknown"


def _readable_browser_blocking(value: object) -> str:
    if value in {"active", "evaluation_only", "not_implemented"}:
        return str(value)
    return "not_implemented"


def _readable_bool_status(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def _readable_dnr_status(raw: dict[str, object]) -> str:
    supported = raw.get("dnr_supported")
    count = _readable_dnr_rule_count(raw.get("dnr_session_rule_count"))
    last_update = _readable_compact_status(raw.get("dnr_last_update_status"))
    if supported is False:
        return "unavailable"
    if last_update == "error":
        return "error"
    if supported is True:
        if count and count > 0:
            return "active"
        return "supported_no_rules"
    return "unknown"


def _readable_dnr_rule_count(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _readable_compact_status(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = value.strip().lower()
    if cleaned in {
        "unknown",
        "unavailable",
        "active",
        "cleared",
        "error",
        "supported_no_rules",
    }:
        return cleaned
    return "unknown"


def _readable_compact_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:160]


def _readable_youtube_spa_status(value: object) -> str:
    if value is True:
        return "seen"
    if value is False:
        return "not_seen"
    return "unknown"


def _browser_diagnostics_next_action(
    *,
    connection_status: str,
    incognito_status: str,
    dnr_status: str,
    dnr_session_rule_count: int | None,
    dnr_last_update_status: str,
    youtube_spa_status: str,
    browser_blocking_available: str,
) -> str:
    if connection_status not in {"connected", "partial"}:
        return "Reload the extension or check the native host registration."
    if incognito_status != "allowed":
        return "Enable Allow in Incognito for the LoopGuard extension."
    if dnr_status in {"unavailable", "error"} or dnr_last_update_status == "error":
        return "Reload the extension and verify the DNR permission is available."
    if youtube_spa_status in {"not_seen", "unknown"}:
        return "Open a YouTube tab once to activate the SPA detector."
    if browser_blocking_available != "yes" or not dnr_session_rule_count:
        return "Start Full Enforcement or Real Hosts Blocking to activate browser rules."
    return "Browser integration looks connected."


def _readable_enforcement_mode(value: EnforcementMode) -> str:
    return ENFORCEMENT_MODE_LABELS.get(value, value.value.replace("_", " ").title())


def _personal_process_blocking_status(status: EnforcementStatus) -> str:
    if status.effective_mode in PROCESS_ENFORCING_MODES:
        return "Active"
    if status.process_readiness.ready:
        return "Ready"
    return "Not ready"


def _personal_hosts_blocking_status(status: EnforcementStatus) -> str:
    if status.effective_mode in HOSTS_ENFORCING_MODES:
        return "Active"
    if status.hosts_readiness.ready:
        return "Ready"
    return "Not ready"


def _personal_title_status(value: str) -> str:
    return value.replace("_", " ").title() if value else "Unknown"


def _personal_incognito_status(value: str) -> str:
    if value == "allowed":
        return "Allowed"
    if value == "not_allowed":
        return "Not allowed"
    return "Unknown"


def _personal_dnr_status(status: BrowserIntegrationStatus) -> str:
    if status.dnr_status == "active":
        if isinstance(status.dnr_session_rule_count, int):
            return f"Active {status.dnr_session_rule_count} rules"
        return "Active"
    if status.dnr_status == "supported_no_rules":
        return "Supported no rules"
    if status.dnr_status == "unavailable":
        return "Unavailable"
    return "Unknown"


def _personal_youtube_spa_status(value: str) -> str:
    if value == "seen":
        return "Seen"
    if value == "not_seen":
        return "Not seen yet"
    return "Unknown"


def _personal_use_readiness_verdict(
    enforcement: EnforcementStatus,
    browser: BrowserIntegrationStatus,
    qa_checklist: PersonalTrialQaChecklist,
) -> str:
    automatic_ready = (
        (enforcement.process_readiness.ready or enforcement.hosts_readiness.ready)
        and enforcement.recovery_readiness.ready
        and browser.connection_status == "connected"
    )
    diagnostics_ready = (
        browser.incognito_status == "allowed"
        and browser.dnr_status in {"active", "supported_no_rules"}
        and browser.youtube_spa_status == "seen"
    )
    if not automatic_ready or qa_checklist.completed_count == 0:
        return "not_ready"
    if qa_checklist.status != "complete":
        return "partial"
    if (
        enforcement.process_readiness.ready
        and enforcement.hosts_readiness.ready
        and diagnostics_ready
    ):
        return "ready_for_personal_trial"
    return "partial"


def _personal_use_readiness_summary(verdict: str) -> str:
    if verdict == "ready_for_personal_trial":
        return (
            "Ready for personal trial. Manual QA is complete; re-run it "
            "after setup/enforcement changes."
        )
    if verdict == "partial":
        return (
            "Partial. Enforcement exists, but browser diagnostics or manual QA "
            "are incomplete."
        )
    return "Not ready. Fix missing readiness before relying on LoopGuard."


def _decode_personal_trial_qa_state(value: str | None) -> dict[str, bool]:
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    valid_keys = {key for key, _label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS}
    return {
        str(key): bool(checked)
        for key, checked in raw.items()
        if isinstance(key, str) and key in valid_keys
    }


def _encode_personal_trial_qa_state(states: dict[str, bool]) -> str:
    valid_keys = {key for key, _label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS}
    clean = {
        key: bool(states.get(key, False))
        for key in sorted(valid_keys)
        if bool(states.get(key, False))
    }
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _rule_export_payload(rule: RuleRecord) -> dict[str, object]:
    return {
        "rule_type": rule.rule_type,
        "target": rule.target,
        "enabled": rule.enabled,
        "allow_from_level": rule.allow_from_level,
        "purpose": rule.purpose,
        "escape_family": rule.escape_family,
    }


def _validated_configuration_import(
    raw_json: str,
) -> tuple[list[dict[str, object]], dict[str, str]]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise ValueError("Configuration import must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Configuration import must be a JSON object.")
    if payload.get("app") != CONFIG_EXPORT_APP:
        raise ValueError("Configuration import is not a SelfBoss export.")
    if payload.get("export_version") != CONFIG_EXPORT_VERSION:
        raise ValueError("Unsupported configuration export version.")

    raw_rules = payload.get("rules", [])
    raw_settings = payload.get("app_settings", {})
    if not isinstance(raw_rules, list):
        raise ValueError("Configuration import rules must be a list.")
    if not isinstance(raw_settings, dict):
        raise ValueError("Configuration import settings must be an object.")

    rules = [_validated_configuration_rule(rule) for rule in raw_rules]
    seen_rules: set[tuple[str, str]] = set()
    for rule in rules:
        key = (str(rule["rule_type"]), str(rule["target"]))
        if key in seen_rules:
            raise ValueError(f"Duplicate rule in import: {key[0]}:{key[1]}")
        seen_rules.add(key)
    settings = {
        key: _validated_configuration_setting(key, value)
        for key, value in raw_settings.items()
    }
    return rules, settings


def _validated_configuration_rule(raw_rule: object) -> dict[str, object]:
    if not isinstance(raw_rule, dict):
        raise ValueError("Configuration import rule must be an object.")
    enabled = raw_rule.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("Rule enabled must be true or false.")
    clean_type = _normalize_rule_type(str(raw_rule.get("rule_type", "")))
    clean_target = _normalize_rule_target(clean_type, str(raw_rule.get("target", "")))
    return {
        "rule_type": clean_type,
        "target": clean_target,
        "enabled": enabled,
        "allow_from_level": _normalize_allow_from_level(
            str(raw_rule.get("allow_from_level", AccessLevel.HIGH.value))
        ),
        "purpose": _normalize_rule_purpose(
            str(raw_rule.get("purpose", RulePurpose.HIGH_RISK_ESCAPE.value))
        ),
        "escape_family": _normalize_escape_family(
            str(raw_rule.get("escape_family", EscapeFamily.NONE.value))
        ),
    }


def _validated_configuration_setting(key: object, value: object) -> str:
    if not isinstance(key, str) or key not in CONFIG_EXPORT_SETTING_KEYS:
        raise ValueError(f"Unsupported configuration setting: {key}")
    if not isinstance(value, str):
        raise ValueError(f"Configuration setting must be text: {key}")
    normalized = value.strip()
    if key == "enforcement_mode":
        return _normalize_enforcement_mode(normalized).value
    if key == "surrender_strictness":
        strictness = normalized.lower()
        if strictness not in SURRENDER_STRICTNESS_DELAYS:
            raise ValueError("Unsupported surrender strictness")
        return strictness
    if key == "soft_start_enabled":
        enabled = normalized.lower()
        if enabled not in {"true", "false"}:
            raise ValueError("Soft Start enabled must be true or false")
        return enabled
    if key == "soft_start_duration_minutes":
        minutes = _parse_int_setting(normalized, key)
        if not 0 <= minutes <= 60:
            raise ValueError("Soft Start duration must be between 0 and 60 minutes")
        return str(minutes)
    if key == "daily_recreation_cap_minutes":
        minutes = _parse_int_setting(normalized, key)
        if not (
            DAILY_RECREATION_CAP_MIN_MINUTES
            <= minutes
            <= DAILY_RECREATION_CAP_MAX_MINUTES
        ):
            raise ValueError(
                "Daily Recreation cap must be between 15 and 300 minutes"
            )
        return str(minutes)
    if key == _PERSONAL_TRIAL_QA_SETTINGS_KEY:
        return _validated_personal_trial_qa_setting(normalized)
    return normalized


def _parse_int_setting(value: str, key: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"Configuration setting must be a number: {key}") from error


def _validated_personal_trial_qa_setting(value: str) -> str:
    if not value:
        return "{}"
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Personal Trial QA setting must be valid JSON.") from error
    if not isinstance(raw, dict):
        raise ValueError("Personal Trial QA setting must be an object.")
    valid_keys = {key for key, _label in PERSONAL_TRIAL_QA_STEP_DEFINITIONS}
    states: dict[str, bool] = {}
    for key, checked in raw.items():
        if not isinstance(key, str) or key not in valid_keys:
            raise ValueError("Unsupported personal trial QA step")
        if not isinstance(checked, bool):
            raise ValueError("Personal Trial QA values must be true or false.")
        states[key] = checked
    return _encode_personal_trial_qa_state(states)


def _normalize_enforcement_mode(mode: str | EnforcementMode) -> EnforcementMode:
    if isinstance(mode, EnforcementMode):
        return mode
    normalized = mode.strip().lower()
    try:
        return EnforcementMode(normalized)
    except ValueError as error:
        raise ValueError(f"Unsupported enforcement mode: {mode}") from error


def _build_enforcement_status(
    *,
    selected_mode: EnforcementMode,
    safe_mode: bool,
    recovery_mode: bool,
) -> EnforcementStatus:
    process_group = EnforcementReadinessGroup(
        key="process",
        label="Real Process Blocking",
        checks=process_blocking_readiness_checks(),
    )
    hosts_group = EnforcementReadinessGroup(
        key="hosts",
        label="Real Hosts Blocking",
        checks=hosts_blocking_readiness_checks(),
    )
    recovery_group = EnforcementReadinessGroup(
        key="recovery",
        label="Recovery",
        checks=(
            *test_mode_readiness_checks(),
            *recovery_readiness_checks(),
        ),
    )
    full_group = EnforcementReadinessGroup(
        key="full",
        label="Full Enforcement",
        checks=(
            EnforcementReadinessCheck(
                key="process_ready",
                label="Process readiness",
                ready=process_group.ready,
                detail=_readiness_detail(process_group),
            ),
            EnforcementReadinessCheck(
                key="hosts_ready",
                label="Hosts readiness",
                ready=hosts_group.ready,
                detail=_readiness_detail(hosts_group),
            ),
        ),
    )
    effective_mode = (
        EnforcementMode.PREVIEW_ONLY
        if safe_mode or recovery_mode
        else selected_mode
    )
    real_blocking_active = effective_mode in {
        EnforcementMode.REAL_PROCESS_BLOCKING,
        EnforcementMode.REAL_HOSTS_BLOCKING,
        EnforcementMode.FULL_ENFORCEMENT,
    }
    next_available_mode = (
        EnforcementMode.PREVIEW_ONLY
        if safe_mode or recovery_mode
        else EnforcementMode.ARMED_DRY_RUN
    )
    next_step = _enforcement_next_step(
        selected_mode=selected_mode,
        effective_mode=effective_mode,
        safe_mode=safe_mode,
        recovery_mode=recovery_mode,
        process_group=process_group,
        full_group=full_group,
    )
    return EnforcementStatus(
        selected_mode=selected_mode,
        effective_mode=effective_mode,
        real_blocking_active=real_blocking_active,
        next_available_mode=next_available_mode,
        next_step=next_step,
        process_readiness=process_group,
        hosts_readiness=hosts_group,
        recovery_readiness=recovery_group,
        full_readiness=full_group,
        mode_options=_enforcement_mode_options(
            process_group=process_group,
            hosts_group=hosts_group,
            recovery_group=recovery_group,
            full_group=full_group,
            safe_mode=safe_mode,
            recovery_mode=recovery_mode,
        ),
    )


def _readiness_detail(group: EnforcementReadinessGroup) -> str:
    if group.ready:
        return f"{group.label} readiness passes."
    return group.missing_items[0] if group.missing_items else (
        f"{group.label} readiness is incomplete."
    )


def _enforcement_next_step(
    *,
    selected_mode: EnforcementMode,
    effective_mode: EnforcementMode,
    safe_mode: bool,
    recovery_mode: bool,
    process_group: EnforcementReadinessGroup,
    full_group: EnforcementReadinessGroup,
) -> str:
    if safe_mode:
        return "Safe Mode is active; enforcement stays Preview Only."
    if recovery_mode:
        return "Recovery Mode is active; enforcement stays Preview Only."
    if selected_mode is EnforcementMode.PREVIEW_ONLY:
        if full_group.ready:
            return (
                "Next available mode: Armed Dry Run. Real Process Blocking, "
                "Real Hosts Blocking, and Full Enforcement are available."
            )
        if process_group.ready:
            return (
                "Next available mode: Armed Dry Run. Real Process Blocking is "
                "available for explicit app rules; Real Hosts Blocking is "
                "available for domain rules."
            )
        return "Next available mode: Armed Dry Run."
    if effective_mode is EnforcementMode.ARMED_DRY_RUN:
        if full_group.ready:
            return (
                "Real Process Blocking, Real Hosts Blocking, and Full "
                "Enforcement are available."
            )
        if process_group.ready:
            return (
                "Real Process Blocking is available for explicit app rules. "
                "Real Hosts Blocking is available for domain rules."
            )
        return (
            "Armed Dry Run selected. Real process blocking is locked: "
            f"{_readiness_detail(process_group)}"
        )
    if effective_mode is EnforcementMode.REAL_PROCESS_BLOCKING:
        return (
            "Real Process Blocking active for explicit app rules. Websites are "
            "not blocked in this mode."
        )
    if effective_mode is EnforcementMode.REAL_HOSTS_BLOCKING:
        return (
            "Real Hosts Blocking active for website domain rules. "
            "Process blocking is a separate mode."
        )
    if effective_mode is EnforcementMode.FULL_ENFORCEMENT:
        return (
            "Full Enforcement active for explicit app rules and website domain "
            "rules. No URL/path, browser extension, firewall, or DNS flush is active."
        )
    return "Real enforcement is locked until readiness checks pass."


def _enforcement_mode_options(
    *,
    process_group: EnforcementReadinessGroup,
    hosts_group: EnforcementReadinessGroup,
    recovery_group: EnforcementReadinessGroup,
    full_group: EnforcementReadinessGroup,
    safe_mode: bool,
    recovery_mode: bool,
) -> tuple[EnforcementModeOption, ...]:
    safety_reason = ""
    if safe_mode:
        safety_reason = "Locked while Safe Mode is active."
    elif recovery_mode:
        safety_reason = "Locked while Recovery Mode is active."

    return (
        EnforcementModeOption(
            mode=EnforcementMode.PREVIEW_ONLY,
            label=ENFORCEMENT_MODE_LABELS[EnforcementMode.PREVIEW_ONLY],
            enabled=True,
            reason="Always available.",
        ),
        EnforcementModeOption(
            mode=EnforcementMode.ARMED_DRY_RUN,
            label=ENFORCEMENT_MODE_LABELS[EnforcementMode.ARMED_DRY_RUN],
            enabled=True,
            reason="Safe dry-run stage; no system blocking.",
        ),
        EnforcementModeOption(
            mode=EnforcementMode.REAL_PROCESS_BLOCKING,
            label=ENFORCEMENT_MODE_LABELS[EnforcementMode.REAL_PROCESS_BLOCKING],
            enabled=not bool(safety_reason) and process_group.ready,
            reason=safety_reason or _readiness_detail(process_group),
        ),
        EnforcementModeOption(
            mode=EnforcementMode.REAL_HOSTS_BLOCKING,
            label=ENFORCEMENT_MODE_LABELS[EnforcementMode.REAL_HOSTS_BLOCKING],
            enabled=not bool(safety_reason) and hosts_group.ready,
            reason=safety_reason or _readiness_detail(hosts_group),
        ),
        EnforcementModeOption(
            mode=EnforcementMode.FULL_ENFORCEMENT,
            label=ENFORCEMENT_MODE_LABELS[EnforcementMode.FULL_ENFORCEMENT],
            enabled=not bool(safety_reason) and full_group.ready,
            reason=safety_reason or _readiness_detail(full_group),
        ),
    )


def _required_readiness_for_mode(
    status: EnforcementStatus,
    mode: EnforcementMode,
) -> EnforcementReadinessGroup | None:
    if mode is EnforcementMode.REAL_PROCESS_BLOCKING:
        return status.process_readiness
    if mode is EnforcementMode.REAL_HOSTS_BLOCKING:
        return status.hosts_readiness
    if mode is EnforcementMode.FULL_ENFORCEMENT:
        return status.full_readiness
    return None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_duration_minutes_seconds(seconds: int) -> str:
    clean_seconds = max(0, int(seconds))
    minutes, remainder = divmod(clean_seconds, 60)
    if minutes and remainder:
        return f"{minutes}m {remainder}s"
    if minutes:
        return f"{minutes}m"
    return f"{remainder}s"


def canonical_task_allowed_url(value: str) -> str:
    """Return the exact-match canonical form for a task allowed URL."""
    raw_value = value.strip()
    if any(character.isspace() for character in raw_value):
        raise ValueError("Allowed URL cannot contain spaces")
    parsed = urlparse(raw_value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_TASK_ALLOWED_URL_SCHEMES:
        raise ValueError("Allowed URL must start with http:// or https://")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("Allowed URL must include a hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Allowed URL port is invalid") from exc

    include_port = port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    netloc = f"{hostname}:{port}" if include_port else hostname
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def attempt_datetime_in_local_time(value: str, *, tzinfo=None) -> datetime:
    parsed = _parse_datetime(value)
    if tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone(tzinfo)


def attempt_local_day(value: str, *, tzinfo=None) -> str:
    return attempt_datetime_in_local_time(value, tzinfo=tzinfo).date().isoformat()


def format_attempt_local_time(
    value: str,
    *,
    include_date: bool = True,
    tzinfo=None,
) -> str:
    local_dt = attempt_datetime_in_local_time(value, tzinfo=tzinfo)
    return local_dt.strftime("%Y-%m-%d %H:%M" if include_date else "%H:%M")


_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_APP_PROCESS_PATTERN = re.compile(r"^[a-z0-9_.-]+\.exe$")


def _normalize_rule_type(rule_type: str) -> str:
    clean_type = rule_type.strip().lower()
    if clean_type not in {"site", "app"}:
        raise ValueError(f"Unsupported rule type: {rule_type}")
    return clean_type


def recommended_allow_from_level_for_purpose(purpose: str) -> str:
    """Return the default access threshold recommended for a rule purpose."""
    clean_purpose = _normalize_rule_purpose(purpose)
    return RULE_PURPOSE_DEFAULT_ALLOW_FROM_LEVEL[clean_purpose]


def _normalize_allow_from_level(allow_from_level: str) -> str:
    clean_level = allow_from_level.strip().lower()
    if clean_level not in _ALLOW_FROM_LEVEL_RANK:
        raise ValueError(f"Unsupported access level: {allow_from_level}")
    return clean_level


def _normalize_rule_purpose(purpose: str) -> str:
    clean_purpose = purpose.strip().lower()
    if clean_purpose not in RULE_PURPOSE_OPTIONS:
        raise ValueError(f"Unsupported rule purpose: {purpose}")
    return clean_purpose


def _normalize_escape_family(escape_family: str) -> str:
    clean_escape_family = escape_family.strip().lower()
    if clean_escape_family not in ESCAPE_FAMILY_OPTIONS:
        raise ValueError(f"Unsupported escape family: {escape_family}")
    return clean_escape_family


def canonical_rule_target_for_display(rule_type: str, target: str) -> str:
    """Return the safest canonical target display for a rule."""
    try:
        clean_type = _normalize_rule_type(rule_type)
        return _normalize_rule_target(clean_type, target)
    except (AttributeError, ValueError):
        return str(target).strip()


def suggest_escape_family_for_rule(rule_type: str, target: str) -> str:
    """Suggest an escape family for obvious escape targets."""
    try:
        clean_type = _normalize_rule_type(rule_type)
        clean_target = _normalize_rule_target(clean_type, target)
    except (AttributeError, ValueError):
        return EscapeFamily.NONE.value

    if clean_type == "site":
        host = clean_target.split("/", 1)[0]
        bare_host = host[2:] if host.startswith("*.") else host
        if bare_host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            return EscapeFamily.VIDEO.value
        if bare_host in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
            return EscapeFamily.RANDOM_BROWSING.value
        if bare_host in {"discord.com", "www.discord.com"}:
            return EscapeFamily.CHAT.value
        if bare_host in {
            "mangadex.org",
            "www.mangadex.org",
            "mangalib.me",
            "www.mangalib.me",
        }:
            return EscapeFamily.READING_BINGE.value

    if clean_type == "app":
        if clean_target in {
            "steam.exe",
            "steamwebhelper.exe",
            "epicgameslauncher.exe",
            "riotclientservices.exe",
            "battlenet.exe",
            "battle.net.exe",
        }:
            return EscapeFamily.LAUNCHER.value
        if clean_target == "discord.exe":
            return EscapeFamily.CHAT.value

    return EscapeFamily.NONE.value


def utility_leakage_warning_for_rule(
    rule_type: str,
    target: str,
    allow_from_level: str,
    escape_family: str,
) -> str:
    """Return a warning for obvious escape targets configured below HIGH."""
    try:
        clean_level = _normalize_allow_from_level(allow_from_level)
        clean_family = _normalize_escape_family(escape_family)
    except (AttributeError, ValueError):
        return ""
    if clean_level == AccessLevel.HIGH.value:
        return ""

    suggested_family = (
        suggest_escape_family_for_rule(rule_type, target)
        if clean_family == EscapeFamily.NONE.value
        else EscapeFamily.NONE.value
    )
    if (
        clean_family in UTILITY_LEAKAGE_ESCAPE_FAMILIES
        or suggested_family in UTILITY_LEAKAGE_ESCAPE_FAMILIES
    ):
        return UTILITY_LEAKAGE_WARNING
    return ""


def rule_duplicate_equivalence_key(rule: object) -> tuple[
    str,
    str,
    str,
    str,
    str,
    bool,
]:
    """Return the conservative duplicate key used for warning-only UI hints."""
    rule_type = str(getattr(rule, "rule_type", "")).strip().lower()
    target = str(getattr(rule, "target", ""))
    return (
        rule_type,
        canonical_rule_target_for_display(rule_type, target),
        str(getattr(rule, "allow_from_level", "")).strip().lower(),
        str(getattr(rule, "purpose", "")).strip().lower(),
        str(getattr(rule, "escape_family", "")).strip().lower(),
        bool(getattr(rule, "enabled", True)),
    )


def _normalize_rule_target(rule_type: str, target: str) -> str:
    if rule_type == "site":
        return _normalize_site_rule_target(target)
    if rule_type == "app":
        return _normalize_app_rule_target(target)
    raise ValueError(f"Unsupported rule type: {rule_type}")


def _normalize_site_rule_target(target: str) -> str:
    clean_target = target.strip().lower()
    if not clean_target:
        raise ValueError("Website rule target is required")
    if (
        "://" in clean_target
        or "\\" in clean_target
        or "?" in clean_target
        or "#" in clean_target
        or any(character.isspace() for character in clean_target)
    ):
        raise ValueError(
            "Website rules must be domain targets or browser path patterns"
        )
    if clean_target.endswith(".exe"):
        raise ValueError("Website rules cannot target Windows process names")
    if "/" in clean_target:
        return _normalize_browser_url_pattern_target(clean_target)
    if clean_target.endswith("."):
        clean_target = clean_target[:-1]
    _validate_site_host(clean_target, allow_leading_wildcard=False)
    return clean_target


def _normalize_browser_url_pattern_target(target: str) -> str:
    if target in {"*", "*/*", "*.*/*"}:
        raise ValueError("Website path patterns cannot be global catch-all targets")
    host, path = target.split("/", 1)
    if not host or not path:
        raise ValueError("Website path patterns require a host and path")
    if host.endswith(".exe"):
        raise ValueError("Website rules cannot target Windows process names")
    if "*" in host and not host.startswith("*."):
        raise ValueError("Website host wildcards are only allowed as *.example.com")
    if host.endswith("."):
        host = host[:-1]
    _validate_site_host(host, allow_leading_wildcard=True)
    if "*" in path and not path.endswith("/*"):
        raise ValueError("Website path wildcards must be trailing prefix wildcards")
    path_prefix = path.removesuffix("/*").rstrip("/")
    if not path_prefix or path_prefix == "*":
        raise ValueError("Website path patterns require a specific path prefix")
    if "*" in path_prefix:
        raise ValueError("Website path wildcards must be trailing prefix wildcards")
    return f"{host}/{path_prefix}/*"


def _validate_site_host(host: str, *, allow_leading_wildcard: bool) -> None:
    if allow_leading_wildcard and host.startswith("*."):
        host = host[2:]
    elif "*" in host:
        raise ValueError("Website host wildcards are only allowed as *.example.com")
    labels = host.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        raise ValueError("Website rules must include a valid domain suffix")
    for label in labels:
        if _DOMAIN_LABEL_PATTERN.fullmatch(label) is None:
            raise ValueError("Website rules must be valid domain or hostname targets")

    tld = labels[-1]
    if len(tld) < 2 or not tld.isalpha():
        raise ValueError("Website rules must end with a valid domain suffix")


def _is_browser_url_pattern_target(target: str) -> bool:
    return "/" in target


def _normalize_app_rule_target(target: str) -> str:
    clean_target = target.strip().lower()
    if not clean_target:
        raise ValueError("App rule target is required")
    if (
        "/" in clean_target
        or "\\" in clean_target
        or "://" in clean_target
        or any(character.isspace() for character in clean_target)
    ):
        raise ValueError("App rules must be Windows .exe process names only")
    if "." in clean_target.removesuffix(".exe"):
        raise ValueError("App rules cannot target website or domain names")
    if not clean_target.endswith(".exe"):
        raise ValueError("App rules must end with .exe")
    if clean_target.endswith(".exe.exe"):
        raise ValueError("App rules cannot end with .exe.exe")
    if _APP_PROCESS_PATTERN.fullmatch(clean_target) is None:
        raise ValueError("App rules must use letters, numbers, _, -, . and end with .exe")
    if not clean_target.removesuffix(".exe"):
        raise ValueError("App rules must include a process name before .exe")
    return clean_target
