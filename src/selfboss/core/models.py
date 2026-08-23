"""Core domain models for LoopGuard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class TaskStatus(str, Enum):
    """Lifecycle states for a user task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ARCHIVED = "archived"


class TaskKind(str, Enum):
    """Reward categories for tasks."""

    TINY = "tiny"
    NORMAL = "normal"
    IMPORTANT = "important"
    MAIN = "main"


class TaskPlanningStatus(str, Enum):
    """Whether a task belongs to the locked daily plan."""

    PLANNED = "planned"
    UNPLANNED = "unplanned"


class AccessLevel(str, Enum):
    """Access modes used by LoopGuard."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EnforcementMode(str, Enum):
    """Staged enforcement modes for the safe path beyond preview."""

    PREVIEW_ONLY = "preview_only"
    ARMED_DRY_RUN = "armed_dry_run"
    REAL_PROCESS_BLOCKING = "real_process_blocking"
    REAL_HOSTS_BLOCKING = "real_hosts_blocking"
    FULL_ENFORCEMENT = "full_enforcement"


class RulePurpose(str, Enum):
    """Psychological purpose metadata for a dry-run rule."""

    WORK_TOOL = "work_tool"
    STUDY_REFERENCE = "study_reference"
    ESSENTIAL_COMMUNICATION = "essential_communication"
    INTENTIONAL_REST = "intentional_rest"
    CONTROLLED_RECREATION = "controlled_recreation"
    HIGH_RISK_ESCAPE = "high_risk_escape"
    COMPULSIVE_STIMULATION = "compulsive_stimulation"
    GATEWAY_APP = "gateway_app"
    RECOVERY_SAFETY = "recovery_safety"


class EscapeFamily(str, Enum):
    """Escape-switching family metadata for a dry-run rule."""

    NONE = "none"
    VIDEO = "video"
    GAMES = "games"
    SOCIAL = "social"
    CHAT = "chat"
    READING_BINGE = "reading_binge"
    RANDOM_BROWSING = "random_browsing"
    LAUNCHER = "launcher"
    FAKE_PRODUCTIVITY = "fake_productivity"
    RECOVERY = "recovery"


class AccessAttemptDecision(str, Enum):
    """Decision recorded for a Test Mode access attempt."""

    ALLOWED_NOW = "allowed_now"
    ALLOWED_BY_PLANNED_USE_PASS = "allowed_by_planned_use_pass"
    WOULD_ALLOW = "would_allow"
    WOULD_BLOCK = "would_block"
    WOULD_BLOCK_IN_CURRENT_MODE = "would_block_in_current_mode"
    UNKNOWN = "unknown"


class AccessAttemptSource(str, Enum):
    """Where an access attempt record came from."""

    ARMED_DRY_RUN_PROCESS = "armed_dry_run_process"
    REAL_PROCESS_BLOCKING_PROCESS = "real_process_blocking_process"
    MANUAL_TEST = "manual_test"
    BROWSER = "browser"


class PlannedUsePassStatus(str, Enum):
    """Lifecycle state for a Test Mode planned-use pass."""

    ACTIVE = "active"
    ENDED = "ended"
    EXPIRED = "expired"


class RewardEventType(str, Enum):
    """Types of reward ledger events."""

    TASK_COMPLETED = "task_completed"
    HIGH_TIME_SPENT = "high_time_spent"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class DayOutcomeCloseKind(str, Enum):
    """Persisted close types for completed local days."""

    NORMAL = "normal"
    RECOVERY = "recovery"
    REST = "rest"


@dataclass(frozen=True)
class AppSettings:
    """Resolved local application settings."""

    app_home: Path
    data_dir: Path
    db_path: Path
    log_dir: Path
    test_mode: bool
    recovery_mode: bool
    safe_mode: bool


@dataclass(frozen=True)
class EnforcementReadinessCheck:
    """One conservative readiness check for an enforcement stage."""

    key: str
    label: str
    ready: bool
    detail: str


@dataclass(frozen=True)
class EnforcementReadinessGroup:
    """Grouped readiness checks for one enforcement capability."""

    key: str
    label: str
    checks: tuple[EnforcementReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        """Return whether every check in this group passes."""
        return all(check.ready for check in self.checks)

    @property
    def missing_items(self) -> tuple[str, ...]:
        """Return human-readable missing readiness items."""
        return tuple(check.detail for check in self.checks if not check.ready)


@dataclass(frozen=True)
class EnforcementModeOption:
    """UI-safe option metadata for selecting an enforcement mode."""

    mode: EnforcementMode
    label: str
    enabled: bool
    reason: str


@dataclass(frozen=True)
class EnforcementStatus:
    """Current staged enforcement status and readiness."""

    selected_mode: EnforcementMode
    effective_mode: EnforcementMode
    real_blocking_active: bool
    next_available_mode: EnforcementMode
    next_step: str
    process_readiness: EnforcementReadinessGroup
    hosts_readiness: EnforcementReadinessGroup
    recovery_readiness: EnforcementReadinessGroup
    full_readiness: EnforcementReadinessGroup
    mode_options: tuple[EnforcementModeOption, ...]


@dataclass(frozen=True)
class Task:
    """A user-defined task that can earn reward minutes."""

    id: int
    title: str
    description: str
    status: TaskStatus
    reward_minutes: int
    allowed_url: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    completion_claimed_at: str | None = None
    completion_available_at: str | None = None
    day_date: str = ""
    kind: TaskKind = TaskKind.NORMAL
    planning_status: TaskPlanningStatus = TaskPlanningStatus.PLANNED


@dataclass(frozen=True)
class DayState:
    """Singleton state for the current local day."""

    day: str
    day_started_at: str | None
    day_ended_at: str | None
    access_level: AccessLevel
    reward_balance_minutes: int
    reward_balance_seconds: int
    surrender_requested_at: str | None
    bad_day_mode: bool
    updated_at: str


@dataclass(frozen=True)
class RewardLedgerEntry:
    """A reward balance change."""

    id: int
    task_id: int | None
    minutes_delta: int
    seconds_delta: int
    reason: str
    created_at: str


@dataclass(frozen=True)
class AuditEvent:
    """A local audit event for important state changes."""

    id: int
    event_type: str
    details: str
    created_at: str


@dataclass(frozen=True)
class DayOutcome:
    """Persistent per-day outcome used for planned rest rewards."""

    day_date: str
    started_at: str | None
    ended_at: str
    close_kind: DayOutcomeCloseKind
    main_completed: bool
    rest_token_used: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AccessAttemptRecord:
    """A Test Mode attempt to access a configured rule target."""

    id: int
    occurred_at: str
    target_type: str
    target: str
    rule_id: int | None
    access_level_at_attempt: str
    decision: str
    allow_from_level: str | None
    purpose: str
    escape_family: str
    source: str
    enforcement_mode: str
    action_taken: str
    matched_scope: str = "none"
    matched_rule_target: str | None = None
    url_family: str = "unknown"
    path_kind: str = "unknown"
    reason_code: str = "unknown"


@dataclass(frozen=True)
class AccessAttemptSummary:
    """Read-only summary of recent Test Mode access attempts."""

    total_attempts: int
    by_escape_family: dict[str, int]
    by_purpose: dict[str, int]
    by_decision: dict[str, int]
    recent_family_sequence: list[str]
    possible_switching_detected: bool
    pattern_explanation: str
    suggested_next_action: str


@dataclass(frozen=True)
class DryRunProcessAttemptSummary:
    """Compact review summary for Armed Dry Run process attempts."""

    total_recent_attempts: int
    today_would_block_count: int
    last_would_block_target: str | None
    latest_attempts: list[AccessAttemptRecord]
    real_blocking_note: str


@dataclass(frozen=True)
class PlannedUsePassRecord:
    """A temporary declared pass for using one configured rule target."""

    id: int
    rule_id: int
    target_type: str
    target: str
    purpose: str
    escape_family: str
    reason: str
    duration_seconds: int
    started_at: str
    expires_at: str
    ended_at: str | None
    status: str


@dataclass(frozen=True)
class RewardPolicy:
    """Default reward minutes for task kinds."""

    tiny_minutes: int = 5
    normal_minutes: int = 15
    important_minutes: int = 30
    main_minutes: int = 30


@dataclass(frozen=True)
class RewardLedgerEvent:
    """Pure reward ledger event used by domain logic."""

    event_type: RewardEventType
    minutes_delta: int
    occurred_at: datetime
    seconds_delta: int = 0
    task_id: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class AccessRuntimeState:
    """Pure access state for LOW, MEDIUM, and HIGH decisions."""

    access_level: AccessLevel = AccessLevel.LOW
    medium_unlocked: bool = False
    bad_day_mode: bool = False
    high_started_at: datetime | None = None
    high_minutes_total: int = 0


@dataclass(frozen=True)
class SurrenderState:
    """Delayed surrender request state."""

    requested_at: datetime | None = None
    surrendered_at: datetime | None = None


@dataclass(frozen=True)
class StateTransition:
    """Result of a pure state-machine transition."""

    state: AccessRuntimeState
    reward_events: tuple[RewardLedgerEvent, ...] = ()
