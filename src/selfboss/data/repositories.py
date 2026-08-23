"""SQLite repositories for LoopGuard domain data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from selfboss.core.models import (
    AccessLevel,
    AccessAttemptDecision,
    AccessAttemptRecord,
    AccessAttemptSource,
    AuditEvent,
    DayOutcome,
    DayOutcomeCloseKind,
    DayState,
    EscapeFamily,
    EnforcementMode,
    PlannedUsePassRecord,
    PlannedUsePassStatus,
    RewardLedgerEntry,
    RulePurpose,
    Task,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)


def _utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp for repository writes."""
    return datetime.now(timezone.utc).isoformat()


def _task_from_row(row: sqlite3.Row) -> Task:
    """Map a SQLite row to a task model."""
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        kind=TaskKind(row["kind"]),
        reward_minutes=row["reward_minutes"],
        allowed_url=row["allowed_url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
        completion_claimed_at=row["completion_claimed_at"],
        completion_available_at=row["completion_available_at"],
        day_date=row["day_date"],
        planning_status=TaskPlanningStatus(row["planning_status"]),
    )


def _day_outcome_from_row(row: sqlite3.Row) -> DayOutcome:
    """Map a SQLite row to a day outcome model."""
    return DayOutcome(
        day_date=row["day_date"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        close_kind=DayOutcomeCloseKind(row["close_kind"]),
        main_completed=bool(row["main_completed"]),
        rest_token_used=bool(row["rest_token_used"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@dataclass(frozen=True)
class RuleRecord:
    """A stored dry-run blocking rule."""

    id: int
    rule_type: str
    target: str
    enabled: bool
    allow_from_level: str
    purpose: str
    escape_family: str
    created_at: str


@dataclass(frozen=True)
class HighSessionRecord:
    """A persisted HIGH access session."""

    id: int
    day_date: str
    started_at: str
    ends_at: str
    allocated_minutes: int
    allocated_seconds: int
    intent: str
    ended_at: str | None
    end_reason: str | None


class PlannedUsePassRepository:
    """Persist Test Mode planned-use passes."""

    VALID_TARGET_TYPES = {"site", "app"}
    VALID_PURPOSES = {purpose.value for purpose in RulePurpose}
    VALID_ESCAPE_FAMILIES = {family.value for family in EscapeFamily}
    VALID_STATUSES = {status.value for status in PlannedUsePassStatus}

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        *,
        rule_id: int,
        target_type: str,
        target: str,
        purpose: str,
        escape_family: str,
        reason: str,
        duration_seconds: int,
        started_at: str,
        expires_at: str,
        status: str = PlannedUsePassStatus.ACTIVE.value,
    ) -> PlannedUsePassRecord:
        """Create and return a planned-use pass record."""
        clean_target_type = self._normalize_target_type(target_type)
        clean_target = target.strip()
        clean_reason = reason.strip()
        if not clean_target:
            raise ValueError("Planned-use pass target is required")
        if not clean_reason:
            raise ValueError("Planned-use pass reason is required")
        if duration_seconds <= 0:
            raise ValueError("Planned-use pass duration must be positive")
        clean_purpose = self._normalize_purpose(purpose)
        clean_escape_family = self._normalize_escape_family(escape_family)
        clean_status = self._normalize_status(status)

        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO planned_use_passes (
                    rule_id,
                    target_type,
                    target,
                    purpose,
                    escape_family,
                    reason,
                    duration_seconds,
                    started_at,
                    expires_at,
                    ended_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    rule_id,
                    clean_target_type,
                    clean_target,
                    clean_purpose,
                    clean_escape_family,
                    clean_reason,
                    duration_seconds,
                    started_at,
                    expires_at,
                    clean_status,
                ),
            )

        row = self._connection.execute(
            "SELECT * FROM planned_use_passes WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Stored planned-use pass could not be loaded")
        return _planned_use_pass_from_row(row)

    def get_active(self, now_iso: str) -> PlannedUsePassRecord | None:
        """Return the current active, non-expired pass if one exists."""
        row = self._connection.execute(
            """
            SELECT * FROM planned_use_passes
            WHERE status = ? AND expires_at > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (PlannedUsePassStatus.ACTIVE.value, now_iso),
        ).fetchone()
        return _planned_use_pass_from_row(row) if row else None

    def expire_due(self, now_iso: str) -> int:
        """Mark active passes expired once their wall-clock expiry has passed."""
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE planned_use_passes
                SET status = ?
                WHERE status = ? AND expires_at <= ?
                """,
                (
                    PlannedUsePassStatus.EXPIRED.value,
                    PlannedUsePassStatus.ACTIVE.value,
                    now_iso,
                ),
            )
        return cursor.rowcount

    def end_active(self, now_iso: str) -> PlannedUsePassRecord | None:
        """End the current active pass and return the ended record."""
        active = self.get_active(now_iso)
        if active is None:
            return None
        with self._connection:
            self._connection.execute(
                """
                UPDATE planned_use_passes
                SET status = ?, ended_at = ?
                WHERE id = ?
                """,
                (PlannedUsePassStatus.ENDED.value, now_iso, active.id),
            )
        row = self._connection.execute(
            "SELECT * FROM planned_use_passes WHERE id = ?",
            (active.id,),
        ).fetchone()
        return _planned_use_pass_from_row(row) if row else None

    def list_recent(self, *, limit: int = 10) -> list[PlannedUsePassRecord]:
        """Return recent planned-use passes, newest first."""
        if limit <= 0:
            return []
        rows = self._connection.execute(
            """
            SELECT * FROM planned_use_passes
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_planned_use_pass_from_row(row) for row in rows]

    def _normalize_target_type(self, target_type: str) -> str:
        clean_target_type = target_type.strip().lower()
        if clean_target_type not in self.VALID_TARGET_TYPES:
            raise ValueError(f"Unsupported planned-use pass target type: {target_type}")
        return clean_target_type

    def _normalize_purpose(self, purpose: str) -> str:
        clean_purpose = purpose.strip().lower()
        if clean_purpose not in self.VALID_PURPOSES:
            raise ValueError(f"Unsupported rule purpose: {purpose}")
        return clean_purpose

    def _normalize_escape_family(self, escape_family: str) -> str:
        clean_escape_family = escape_family.strip().lower()
        if clean_escape_family not in self.VALID_ESCAPE_FAMILIES:
            raise ValueError(f"Unsupported escape family: {escape_family}")
        return clean_escape_family

    def _normalize_status(self, status: str) -> str:
        clean_status = status.strip().lower()
        if clean_status not in self.VALID_STATUSES:
            raise ValueError(f"Unsupported planned-use pass status: {status}")
        return clean_status


class TaskRepository:
    """Persist and query tasks."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        title: str,
        description: str = "",
        reward_minutes: int = 0,
        allowed_url: str | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        kind: TaskKind = TaskKind.NORMAL,
        day_date: str | None = None,
        planning_status: TaskPlanningStatus = TaskPlanningStatus.PLANNED,
    ) -> Task:
        """Create a task and return the saved model."""
        now = _utc_now_iso()
        clean_day = day_date or now[:10]
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO tasks (
                    title,
                    description,
                    status,
                    kind,
                    reward_minutes,
                    allowed_url,
                    created_at,
                    updated_at,
                    day_date,
                    planning_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    description,
                    status.value,
                    kind.value,
                    reward_minutes,
                    allowed_url,
                    now,
                    now,
                    clean_day,
                    planning_status.value,
                ),
            )

        task = self.get(cursor.lastrowid)
        if task is None:
            raise RuntimeError("Created task could not be loaded")
        return task

    def get(self, task_id: int) -> Task | None:
        """Return one task by id, if present."""
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return _task_from_row(row) if row else None

    def list(self, *, status: TaskStatus | None = None) -> list[Task]:
        """Return tasks, optionally filtered by status."""
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM tasks ORDER BY id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY id",
                (status.value,),
            ).fetchall()

        return [_task_from_row(row) for row in rows]

    def list_for_day(
        self,
        day_date: str,
        *,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        """Return tasks for a specific local day."""
        if status is None:
            rows = self._connection.execute(
                "SELECT * FROM tasks WHERE day_date = ? ORDER BY id",
                (day_date,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT * FROM tasks
                WHERE day_date = ? AND status = ?
                ORDER BY id
                """,
                (day_date, status.value),
            ).fetchall()

        return [_task_from_row(row) for row in rows]

    def update_status(self, task_id: int, status: TaskStatus) -> Task:
        """Update task status and return the saved model."""
        now = _utc_now_iso()
        completed_at = now if status is TaskStatus.DONE else None

        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    updated_at = ?,
                    completed_at = ?,
                    completion_claimed_at = NULL,
                    completion_available_at = NULL
                WHERE id = ?
                """,
                (status.value, now, completed_at, task_id),
            )

        if cursor.rowcount != 1:
            raise KeyError(f"Task not found: {task_id}")

        task = self.get(task_id)
        if task is None:
            raise RuntimeError("Updated task could not be loaded")
        return task

    def claim_completion(
        self,
        task_id: int,
        *,
        claimed_at: str,
        available_at: str,
    ) -> Task:
        """Persist a pending task completion claim."""
        now = _utc_now_iso()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET completion_claimed_at = ?,
                    completion_available_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (claimed_at, available_at, now, task_id),
            )

        if cursor.rowcount != 1:
            raise KeyError(f"Task not found: {task_id}")

        task = self.get(task_id)
        if task is None:
            raise RuntimeError("Claimed task could not be loaded")
        return task

    def clear_completion_claim(self, task_id: int) -> Task:
        """Clear a pending completion claim without completing the task."""
        now = _utc_now_iso()
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE tasks
                SET completion_claimed_at = NULL,
                    completion_available_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )

        if cursor.rowcount != 1:
            raise KeyError(f"Task not found: {task_id}")

        task = self.get(task_id)
        if task is None:
            raise RuntimeError("Updated task could not be loaded")
        return task

    def delete(self, task_id: int) -> None:
        """Delete one task row without touching reward/history tables."""
        with self._connection:
            cursor = self._connection.execute(
                "DELETE FROM tasks WHERE id = ?",
                (task_id,),
            )

        if cursor.rowcount != 1:
            raise KeyError(f"Task not found: {task_id}")


class DayStateRepository:
    """Persist the current local day state."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the shared SQLite connection for adjacent repositories."""
        return self._connection

    def get(self) -> DayState:
        """Return the singleton day state."""
        row = self._connection.execute(
            "SELECT * FROM day_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("day_state bootstrap row is missing")

        return DayState(
            day=row["day"],
            day_started_at=row["day_started_at"],
            day_ended_at=row["day_ended_at"],
            access_level=AccessLevel(row["access_level"]),
            reward_balance_minutes=row["reward_balance_minutes"],
            reward_balance_seconds=row["reward_balance_seconds"],
            surrender_requested_at=row["surrender_requested_at"],
            bad_day_mode=bool(row["bad_day_mode"]),
            updated_at=row["updated_at"],
        )

    def set_access_level(self, access_level: AccessLevel) -> DayState:
        """Update the active access level."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET access_level = ?, updated_at = ?
                WHERE id = 1
                """,
                (access_level.value, _utc_now_iso()),
            )
        return self.get()

    def start_day(self, started_at: str) -> DayState:
        """Mark the current day as started idempotently."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET day_started_at = COALESCE(day_started_at, ?),
                    updated_at = ?
                WHERE id = 1
                """,
                (started_at, _utc_now_iso()),
            )
        return self.get()

    def end_day(self, ended_at: str) -> DayState:
        """Mark the current day as ended idempotently."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET day_ended_at = COALESCE(day_ended_at, ?),
                    updated_at = ?
                WHERE id = 1
                """,
                (ended_at, _utc_now_iso()),
            )
        return self.get()

    def activate_surrender(self, activated_at: str) -> DayState:
        """Mark surrender active for the current day idempotently."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET surrender_requested_at = COALESCE(surrender_requested_at, ?),
                    updated_at = ?
                WHERE id = 1
                """,
                (activated_at, _utc_now_iso()),
            )
        return self.get()

    def activate_bad_day_mode(self) -> DayState:
        """Mark Bad Day Mode active and keep HIGH sessions dominant."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET bad_day_mode = 1,
                    access_level = CASE
                        WHEN access_level = ? THEN access_level
                        ELSE ?
                    END,
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    AccessLevel.HIGH.value,
                    AccessLevel.MEDIUM.value,
                    _utc_now_iso(),
                ),
            )
        return self.get()

    def add_reward_minutes(self, minutes_delta: int) -> DayState:
        """Add or subtract reward minutes from the current balance."""
        return self.add_reward_seconds(minutes_delta * 60)

    def add_reward_seconds(self, seconds_delta: int) -> DayState:
        """Add or subtract reward seconds from the current balance."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET reward_balance_seconds = reward_balance_seconds + ?,
                    reward_balance_minutes = CAST(
                        (reward_balance_seconds + ?) / 60 AS INTEGER
                    ),
                    updated_at = ?
                WHERE id = 1
                """,
                (seconds_delta, seconds_delta, _utc_now_iso()),
            )
        return self.get()

    def reset_for_day(self, day: str) -> DayState:
        """Reset the singleton state for a fresh local day."""
        with self._connection:
            self._connection.execute(
                """
                UPDATE day_state
                SET day = ?,
                    day_started_at = NULL,
                    day_ended_at = NULL,
                    access_level = ?,
                    reward_balance_minutes = 0,
                    reward_balance_seconds = 0,
                    surrender_requested_at = NULL,
                    bad_day_mode = 0,
                    updated_at = ?
                WHERE id = 1
                """,
                (day, AccessLevel.LOW.value, _utc_now_iso()),
            )
        return self.get()


class AppSettingsRepository:
    """Persist safe app-level preferences."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_value(self, key: str) -> str | None:
        """Return a stored setting value, if present."""
        row = self._connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else row["value"]

    def set_value(self, key: str, value: str) -> None:
        """Store an app setting value."""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, _utc_now_iso()),
            )

    def get_surrender_strictness(self) -> str:
        """Return the stored surrender strictness with a safe default."""
        value = self.get_value("surrender_strictness")
        normalized = value.strip().lower() if value else ""
        return normalized if normalized in {"low", "medium", "high"} else "medium"

    def set_surrender_strictness(self, value: str) -> str:
        """Persist a normalized surrender strictness value."""
        normalized = value.strip().lower()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError("Unsupported surrender strictness")
        self.set_value("surrender_strictness", normalized)
        return normalized

    def get_enforcement_mode(
        self,
        default: str = EnforcementMode.PREVIEW_ONLY.value,
    ) -> str:
        """Return the stored staged enforcement mode with a safe default."""
        value = self.get_value("enforcement_mode")
        normalized = value.strip().lower() if value else ""
        valid_modes = {mode.value for mode in EnforcementMode}
        return normalized if normalized in valid_modes else default

    def set_enforcement_mode(self, value: str) -> str:
        """Persist a normalized staged enforcement mode."""
        normalized = value.strip().lower()
        valid_modes = {mode.value for mode in EnforcementMode}
        if normalized not in valid_modes:
            raise ValueError("Unsupported enforcement mode")
        self.set_value("enforcement_mode", normalized)
        return normalized

    def ensure_enforcement_mode_if_missing(self, value: str) -> str:
        """Seed the staged enforcement mode only for a fresh profile."""
        existing = self.get_value("enforcement_mode")
        if existing is not None:
            return self.get_enforcement_mode()
        return self.set_enforcement_mode(value)

    def get_soft_start_enabled(self) -> bool:
        """Return whether Soft Start is enabled with a safe default."""
        value = self.get_value("soft_start_enabled")
        normalized = value.strip().lower() if value else ""
        if normalized in {"0", "false", "off", "no"}:
            return False
        if normalized in {"1", "true", "on", "yes"}:
            return True
        return True

    def set_soft_start_enabled(self, enabled: bool) -> bool:
        """Persist whether Soft Start is enabled."""
        self.set_value("soft_start_enabled", "true" if enabled else "false")
        return enabled

    def get_soft_start_duration_minutes(self) -> int:
        """Return Soft Start duration minutes with a safe default."""
        value = self.get_value("soft_start_duration_minutes")
        try:
            minutes = int(value) if value is not None else 15
        except ValueError:
            return 15
        return minutes if 0 <= minutes <= 60 else 15

    def set_soft_start_duration_minutes(self, minutes: int) -> int:
        """Persist Soft Start duration minutes."""
        if not 0 <= minutes <= 60:
            raise ValueError("Soft Start duration must be between 0 and 60 minutes")
        self.set_value("soft_start_duration_minutes", str(minutes))
        return minutes

    def get_daily_recreation_cap_minutes(self) -> int:
        """Return daily Recreation cap minutes with a safe default."""
        value = self.get_value("daily_recreation_cap_minutes")
        try:
            minutes = int(value) if value is not None else 90
        except ValueError:
            return 90
        return minutes if 15 <= minutes <= 300 else 90

    def set_daily_recreation_cap_minutes(self, minutes: int) -> int:
        """Persist daily Recreation cap minutes."""
        if not 15 <= minutes <= 300:
            raise ValueError(
                "Daily Recreation cap must be between 15 and 300 minutes"
            )
        self.set_value("daily_recreation_cap_minutes", str(minutes))
        return minutes

    def get_rest_token_count(self) -> int:
        """Return stored Rest Token count, clamped to the supported 0/1 range."""
        value = self.get_value("rest_token_count")
        try:
            count = int(value) if value is not None else 0
        except ValueError:
            return 0
        return 1 if count > 0 else 0

    def set_rest_token_count(self, count: int) -> int:
        """Persist Rest Token count, clamped to the supported 0/1 range."""
        normalized = 1 if count > 0 else 0
        self.set_value("rest_token_count", str(normalized))
        return normalized


class DayOutcomeRepository:
    """Persist per-day close outcomes for earned planned rest."""

    VALID_CLOSE_KINDS = {kind.value for kind in DayOutcomeCloseKind}

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, day_date: str) -> DayOutcome | None:
        """Return the persisted outcome for one local day, if any."""
        row = self._connection.execute(
            "SELECT * FROM day_outcomes WHERE day_date = ?",
            (day_date,),
        ).fetchone()
        return None if row is None else _day_outcome_from_row(row)

    def upsert(
        self,
        *,
        day_date: str,
        started_at: str | None,
        ended_at: str,
        close_kind: DayOutcomeCloseKind | str,
        main_completed: bool,
        rest_token_used: bool = False,
    ) -> DayOutcome:
        """Create or replace the local day's persisted close outcome."""
        normalized_kind = (
            close_kind.value
            if isinstance(close_kind, DayOutcomeCloseKind)
            else close_kind.strip().lower()
        )
        if normalized_kind not in self.VALID_CLOSE_KINDS:
            raise ValueError("Unsupported day outcome close kind")

        now = _utc_now_iso()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO day_outcomes (
                    day_date,
                    started_at,
                    ended_at,
                    close_kind,
                    main_completed,
                    rest_token_used,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(day_date) DO UPDATE SET
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    close_kind = excluded.close_kind,
                    main_completed = excluded.main_completed,
                    rest_token_used = excluded.rest_token_used,
                    updated_at = excluded.updated_at
                """,
                (
                    day_date,
                    started_at,
                    ended_at,
                    normalized_kind,
                    1 if main_completed else 0,
                    1 if rest_token_used else 0,
                    now,
                    now,
                ),
            )

        outcome = self.get(day_date)
        if outcome is None:
            raise RuntimeError("Day outcome could not be loaded")
        return outcome


class RewardLedgerRepository:
    """Record reward balance changes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        *,
        minutes_delta: int | None = None,
        seconds_delta: int | None = None,
        reason: str,
        task_id: int | None = None,
    ) -> RewardLedgerEntry:
        """Create a reward ledger entry."""
        if seconds_delta is None:
            if minutes_delta is None:
                raise ValueError("Reward ledger delta is required")
            seconds_delta = minutes_delta * 60
        if minutes_delta is None:
            minutes_delta = int(seconds_delta // 60)

        created_at = _utc_now_iso()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO reward_ledger (
                    task_id,
                    minutes_delta,
                    seconds_delta,
                    reason,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, minutes_delta, seconds_delta, reason, created_at),
            )

        return RewardLedgerEntry(
            id=cursor.lastrowid,
            task_id=task_id,
            minutes_delta=minutes_delta,
            seconds_delta=seconds_delta,
            reason=reason,
            created_at=created_at,
        )

    def list(self) -> list[RewardLedgerEntry]:
        """Return reward ledger entries in insertion order."""
        rows = self._connection.execute(
            "SELECT * FROM reward_ledger ORDER BY id"
        ).fetchall()
        return [
            RewardLedgerEntry(
                id=row["id"],
                task_id=row["task_id"],
                minutes_delta=row["minutes_delta"],
                seconds_delta=row["seconds_delta"],
                reason=row["reason"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


class AuditEventRepository:
    """Record local audit events for important state changes."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(self, *, event_type: str, details: str = "") -> AuditEvent:
        """Create an audit event."""
        created_at = _utc_now_iso()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO audit_events (event_type, details, created_at)
                VALUES (?, ?, ?)
                """,
                (event_type, details, created_at),
            )

        return AuditEvent(
            id=cursor.lastrowid,
            event_type=event_type,
            details=details,
            created_at=created_at,
        )

    def list(self) -> list[AuditEvent]:
        """Return audit events in insertion order."""
        rows = self._connection.execute(
            "SELECT * FROM audit_events ORDER BY id"
        ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                event_type=row["event_type"],
                details=row["details"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


class HighSessionRepository:
    """Persist HIGH access sessions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def start(
        self,
        *,
        day_date: str,
        started_at: str,
        ends_at: str,
        allocated_minutes: int,
        allocated_seconds: int | None = None,
        intent: str = "",
    ) -> HighSessionRecord:
        """Create a HIGH session and return the stored record."""
        clean_allocated_seconds = (
            allocated_minutes * 60
            if allocated_seconds is None
            else allocated_seconds
        )
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO high_sessions (
                    day_date,
                    started_at,
                    ends_at,
                    allocated_minutes,
                    allocated_seconds,
                    intent
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    day_date,
                    started_at,
                    ends_at,
                    allocated_minutes,
                    clean_allocated_seconds,
                    intent,
                ),
            )

        session = self.get(cursor.lastrowid)
        if session is None:
            raise RuntimeError("Created HIGH session could not be loaded")
        return session

    def get(self, session_id: int) -> HighSessionRecord | None:
        """Return one HIGH session by id, if present."""
        row = self._connection.execute(
            "SELECT * FROM high_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return _high_session_from_row(row) if row else None

    def active_for_day(self, day_date: str) -> HighSessionRecord | None:
        """Return the latest unended HIGH session for a local day."""
        row = self._connection.execute(
            """
            SELECT * FROM high_sessions
            WHERE day_date = ? AND ended_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (day_date,),
        ).fetchone()
        return _high_session_from_row(row) if row else None

    def list(self, *, active_only: bool = False) -> list[HighSessionRecord]:
        """Return HIGH sessions in insertion order."""
        where_clause = " WHERE ended_at IS NULL" if active_only else ""
        rows = self._connection.execute(
            f"SELECT * FROM high_sessions{where_clause} ORDER BY id"
        ).fetchall()
        return [_high_session_from_row(row) for row in rows]

    def end(
        self,
        session_id: int,
        *,
        ended_at: str,
        reason: str,
    ) -> HighSessionRecord:
        """Mark a HIGH session ended and return the saved row."""
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE high_sessions
                SET ended_at = ?, end_reason = ?
                WHERE id = ?
                """,
                (ended_at, reason, session_id),
            )

        if cursor.rowcount != 1:
            raise KeyError(f"HIGH session not found: {session_id}")

        session = self.get(session_id)
        if session is None:
            raise RuntimeError("Ended HIGH session could not be loaded")
        return session


class RuleRepository:
    """Persist dry-run site and app rules."""

    VALID_TYPES = {"site", "app"}
    VALID_ALLOW_FROM_LEVELS = {"low", "medium", "high"}
    VALID_PURPOSES = {purpose.value for purpose in RulePurpose}
    VALID_ESCAPE_FAMILIES = {family.value for family in EscapeFamily}

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        *,
        rule_type: str,
        target: str,
        allow_from_level: str = "high",
        purpose: str = RulePurpose.HIGH_RISK_ESCAPE.value,
        escape_family: str = EscapeFamily.NONE.value,
    ) -> RuleRecord:
        """Add a rule if missing and return the stored record."""
        clean_type = self._normalize_rule_type(rule_type)
        clean_target = target.strip()
        if not clean_target:
            raise ValueError("Rule target is required")
        clean_level = self._normalize_allow_from_level(allow_from_level)
        clean_purpose = self._normalize_purpose(purpose)
        clean_escape_family = self._normalize_escape_family(escape_family)

        created_at = _utc_now_iso()
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO rules (
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
                (
                    clean_type,
                    clean_target,
                    clean_level,
                    clean_purpose,
                    clean_escape_family,
                    created_at,
                ),
            )

        row = self._connection.execute(
            """
            SELECT * FROM rules
            WHERE rule_type = ? AND target = ?
            """,
            (clean_type, clean_target),
        ).fetchone()
        if row is None:
            raise RuntimeError("Stored rule could not be loaded")
        return _rule_from_row(row)

    def get_by_id(
        self,
        rule_id: int,
        *,
        enabled_only: bool = False,
    ) -> RuleRecord | None:
        """Return one rule by id, optionally filtering disabled rows."""
        if enabled_only:
            row = self._connection.execute(
                "SELECT * FROM rules WHERE id = ? AND enabled = 1",
                (rule_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM rules WHERE id = ?",
                (rule_id,),
            ).fetchone()
        return _rule_from_row(row) if row else None

    def update_allow_from_level(
        self,
        *,
        rule_type: str,
        target: str,
        allow_from_level: str,
        purpose: str | None = None,
        escape_family: str | None = None,
    ) -> RuleRecord:
        """Update rule metadata and return the saved record."""
        clean_type = self._normalize_rule_type(rule_type)
        clean_target = target.strip()
        if not clean_target:
            raise ValueError("Rule target is required")
        clean_level = self._normalize_allow_from_level(allow_from_level)
        clean_purpose = (
            None if purpose is None else self._normalize_purpose(purpose)
        )
        clean_escape_family = (
            None
            if escape_family is None
            else self._normalize_escape_family(escape_family)
        )

        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE rules
                SET allow_from_level = ?,
                    purpose = COALESCE(?, purpose),
                    escape_family = COALESCE(?, escape_family)
                WHERE rule_type = ? AND target = ?
                """,
                (
                    clean_level,
                    clean_purpose,
                    clean_escape_family,
                    clean_type,
                    clean_target,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"Rule not found: {clean_type}:{clean_target}")

        row = self._connection.execute(
            """
            SELECT * FROM rules
            WHERE rule_type = ? AND target = ?
            """,
            (clean_type, clean_target),
        ).fetchone()
        if row is None:
            raise RuntimeError("Updated rule could not be loaded")
        return _rule_from_row(row)

    def remove(self, *, rule_type: str, target: str) -> None:
        """Remove a rule if it exists."""
        clean_type = self._normalize_rule_type(rule_type)
        clean_target = target.strip()
        if not clean_target:
            return

        with self._connection:
            self._connection.execute(
                """
                DELETE FROM rules
                WHERE rule_type = ? AND target = ?
                """,
                (clean_type, clean_target),
            )

    def replace_all(self, rules: list[dict[str, object]]) -> list[RuleRecord]:
        """Replace all rules with validated imported rule records."""
        cleaned: list[tuple[str, str, int, str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for rule in rules:
            clean_type = self._normalize_rule_type(str(rule.get("rule_type", "")))
            clean_target = str(rule.get("target", "")).strip()
            if not clean_target:
                raise ValueError("Rule target is required")
            key = (clean_type, clean_target)
            if key in seen:
                raise ValueError(f"Duplicate rule in import: {clean_type}:{clean_target}")
            seen.add(key)
            enabled = rule.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("Rule enabled must be true or false")
            cleaned.append(
                (
                    clean_type,
                    clean_target,
                    1 if enabled else 0,
                    self._normalize_allow_from_level(
                        str(rule.get("allow_from_level", "high"))
                    ),
                    self._normalize_purpose(
                        str(rule.get("purpose", RulePurpose.HIGH_RISK_ESCAPE.value))
                    ),
                    self._normalize_escape_family(
                        str(rule.get("escape_family", EscapeFamily.NONE.value))
                    ),
                )
            )

        created_at = _utc_now_iso()
        with self._connection:
            self._connection.execute("DELETE FROM rules")
            self._connection.executemany(
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
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        rule_type,
                        target,
                        enabled,
                        allow_from_level,
                        purpose,
                        escape_family,
                        created_at,
                    )
                    for (
                        rule_type,
                        target,
                        enabled,
                        allow_from_level,
                        purpose,
                        escape_family,
                    ) in cleaned
                ],
            )
        return self.list(enabled_only=False)

    def list(
        self,
        *,
        rule_type: str | None = None,
        enabled_only: bool = True,
    ) -> list[RuleRecord]:
        """Return rules in insertion order."""
        where_parts = []
        params: list[object] = []
        if rule_type is not None:
            where_parts.append("rule_type = ?")
            params.append(self._normalize_rule_type(rule_type))
        if enabled_only:
            where_parts.append("enabled = 1")

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = self._connection.execute(
            f"SELECT * FROM rules{where_clause} ORDER BY id",
            params,
        ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def _normalize_rule_type(self, rule_type: str) -> str:
        clean_type = rule_type.strip().lower()
        if clean_type not in self.VALID_TYPES:
            raise ValueError(f"Unsupported rule type: {rule_type}")
        return clean_type

    def _normalize_allow_from_level(self, allow_from_level: str) -> str:
        clean_level = allow_from_level.strip().lower()
        if clean_level not in self.VALID_ALLOW_FROM_LEVELS:
            raise ValueError(f"Unsupported allow_from_level: {allow_from_level}")
        return clean_level

    def _normalize_purpose(self, purpose: str) -> str:
        clean_purpose = purpose.strip().lower()
        if clean_purpose not in self.VALID_PURPOSES:
            raise ValueError(f"Unsupported rule purpose: {purpose}")
        return clean_purpose

    def _normalize_escape_family(self, escape_family: str) -> str:
        clean_escape_family = escape_family.strip().lower()
        if clean_escape_family not in self.VALID_ESCAPE_FAMILIES:
            raise ValueError(f"Unsupported escape family: {escape_family}")
        return clean_escape_family


class AccessAttemptRepository:
    """Persist Test Mode access-attempt records."""

    VALID_TARGET_TYPES = RuleRepository.VALID_TYPES
    VALID_ALLOW_FROM_LEVELS = RuleRepository.VALID_ALLOW_FROM_LEVELS
    VALID_ACCESS_LEVELS = {level.value for level in AccessLevel}
    VALID_DECISIONS = {decision.value for decision in AccessAttemptDecision}
    VALID_SOURCES = {source.value for source in AccessAttemptSource}
    VALID_ENFORCEMENT_MODES = {mode.value for mode in EnforcementMode}
    VALID_PURPOSES = RuleRepository.VALID_PURPOSES
    VALID_ESCAPE_FAMILIES = RuleRepository.VALID_ESCAPE_FAMILIES

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(
        self,
        *,
        occurred_at: str,
        target_type: str,
        target: str,
        rule_id: int | None,
        access_level_at_attempt: str,
        decision: str,
        allow_from_level: str | None,
        purpose: str = RulePurpose.HIGH_RISK_ESCAPE.value,
        escape_family: str = EscapeFamily.NONE.value,
        source: str = AccessAttemptSource.MANUAL_TEST.value,
        enforcement_mode: str = EnforcementMode.PREVIEW_ONLY.value,
        action_taken: str = "none",
        matched_scope: str = "none",
        matched_rule_target: str | None = None,
        url_family: str = "unknown",
        path_kind: str = "unknown",
        reason_code: str = "unknown",
    ) -> AccessAttemptRecord:
        """Create and return an access-attempt record."""
        clean_target_type = self._normalize_target_type(target_type)
        clean_target = target.strip()
        if not clean_target:
            raise ValueError("Access attempt target is required")
        clean_access_level = self._normalize_access_level(access_level_at_attempt)
        clean_decision = self._normalize_decision(decision)
        clean_allow_from_level = (
            None
            if allow_from_level is None
            else self._normalize_allow_from_level(allow_from_level)
        )
        clean_purpose = self._normalize_purpose(purpose)
        clean_escape_family = self._normalize_escape_family(escape_family)
        clean_source = self._normalize_source(source)
        clean_enforcement_mode = self._normalize_enforcement_mode(enforcement_mode)
        clean_action_taken = self._normalize_action_taken(action_taken)
        clean_matched_scope = self._normalize_matched_scope(matched_scope)
        clean_matched_rule_target = (
            None
            if matched_rule_target is None
            else self._normalize_optional_metadata(matched_rule_target)
        )
        clean_url_family = self._normalize_metadata_value(url_family, "url_family")
        clean_path_kind = self._normalize_metadata_value(path_kind, "path_kind")
        clean_reason_code = self._normalize_metadata_value(reason_code, "reason_code")

        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO access_attempts (
                    occurred_at,
                    target_type,
                    target,
                    rule_id,
                    access_level_at_attempt,
                    decision,
                    allow_from_level,
                    purpose,
                    escape_family,
                    source,
                    enforcement_mode,
                    action_taken,
                    matched_scope,
                    matched_rule_target,
                    url_family,
                    path_kind,
                    reason_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at,
                    clean_target_type,
                    clean_target,
                    rule_id,
                    clean_access_level,
                    clean_decision,
                    clean_allow_from_level,
                    clean_purpose,
                    clean_escape_family,
                    clean_source,
                    clean_enforcement_mode,
                    clean_action_taken,
                    clean_matched_scope,
                    clean_matched_rule_target,
                    clean_url_family,
                    clean_path_kind,
                    clean_reason_code,
                ),
            )

        row = self._connection.execute(
            "SELECT * FROM access_attempts WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Stored access attempt could not be loaded")
        return _access_attempt_from_row(row)

    def list_recent(
        self,
        *,
        limit: int = 10,
        source: str | None = None,
        occurred_on: str | None = None,
    ) -> list[AccessAttemptRecord]:
        """Return recent attempts, newest first."""
        if limit <= 0:
            return []
        conditions: list[str] = []
        params: list[object] = []
        if source is not None:
            clean_source = self._normalize_source(source)
            conditions.append("source = ?")
            params.append(clean_source)
        if occurred_on is not None:
            conditions.append("occurred_at LIKE ?")
            params.append(f"{self._normalize_occurred_on(occurred_on)}%")
        where_clause = (
            "WHERE " + " AND ".join(conditions) if conditions else ""
        )
        params.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT * FROM access_attempts
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [_access_attempt_from_row(row) for row in rows]

    def _normalize_target_type(self, target_type: str) -> str:
        clean_target_type = target_type.strip().lower()
        if clean_target_type not in self.VALID_TARGET_TYPES:
            raise ValueError(f"Unsupported attempt target type: {target_type}")
        return clean_target_type

    def _normalize_access_level(self, access_level: str) -> str:
        clean_access_level = access_level.strip().lower()
        if clean_access_level not in self.VALID_ACCESS_LEVELS:
            raise ValueError(f"Unsupported access level: {access_level}")
        return clean_access_level

    def _normalize_decision(self, decision: str) -> str:
        clean_decision = decision.strip().lower()
        if clean_decision not in self.VALID_DECISIONS:
            raise ValueError(f"Unsupported access attempt decision: {decision}")
        return clean_decision

    def _normalize_allow_from_level(self, allow_from_level: str) -> str:
        clean_level = allow_from_level.strip().lower()
        if clean_level not in self.VALID_ALLOW_FROM_LEVELS:
            raise ValueError(f"Unsupported allow_from_level: {allow_from_level}")
        return clean_level

    def _normalize_purpose(self, purpose: str) -> str:
        clean_purpose = purpose.strip().lower()
        if clean_purpose not in self.VALID_PURPOSES:
            raise ValueError(f"Unsupported rule purpose: {purpose}")
        return clean_purpose

    def _normalize_escape_family(self, escape_family: str) -> str:
        clean_escape_family = escape_family.strip().lower()
        if clean_escape_family not in self.VALID_ESCAPE_FAMILIES:
            raise ValueError(f"Unsupported escape family: {escape_family}")
        return clean_escape_family

    def _normalize_source(self, source: str) -> str:
        clean_source = source.strip().lower()
        if clean_source not in self.VALID_SOURCES:
            raise ValueError(f"Unsupported access attempt source: {source}")
        return clean_source

    def _normalize_enforcement_mode(self, enforcement_mode: str) -> str:
        clean_mode = enforcement_mode.strip().lower()
        if clean_mode not in self.VALID_ENFORCEMENT_MODES:
            raise ValueError(f"Unsupported enforcement mode: {enforcement_mode}")
        return clean_mode

    def _normalize_action_taken(self, action_taken: str) -> str:
        clean_action = action_taken.strip().lower()
        if not clean_action:
            raise ValueError("Access attempt action_taken is required")
        return clean_action

    def _normalize_matched_scope(self, matched_scope: str) -> str:
        clean_scope = matched_scope.strip().lower()
        if clean_scope not in {"domain", "path", "none"}:
            raise ValueError(f"Unsupported matched_scope: {matched_scope}")
        return clean_scope

    def _normalize_optional_metadata(self, value: str) -> str | None:
        cleaned = value.strip().lower()
        return cleaned[:255] if cleaned else None

    def _normalize_metadata_value(self, value: str, field_name: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError(f"Access attempt {field_name} is required")
        return cleaned[:80]

    def _normalize_occurred_on(self, occurred_on: str) -> str:
        clean_day = occurred_on.strip()
        parts = clean_day.split("-")
        if (
            len(parts) != 3
            or len(parts[0]) != 4
            or len(parts[1]) != 2
            or len(parts[2]) != 2
            or not all(part.isdigit() for part in parts)
        ):
            raise ValueError(f"Unsupported access attempt day: {occurred_on}")
        return clean_day


def _rule_from_row(row: sqlite3.Row) -> RuleRecord:
    return RuleRecord(
        id=row["id"],
        rule_type=row["rule_type"],
        target=row["target"],
        enabled=bool(row["enabled"]),
        allow_from_level=row["allow_from_level"],
        purpose=row["purpose"],
        escape_family=row["escape_family"],
        created_at=row["created_at"],
    )


def _access_attempt_from_row(row: sqlite3.Row) -> AccessAttemptRecord:
    return AccessAttemptRecord(
        id=row["id"],
        occurred_at=row["occurred_at"],
        target_type=row["target_type"],
        target=row["target"],
        rule_id=row["rule_id"],
        access_level_at_attempt=row["access_level_at_attempt"],
        decision=row["decision"],
        allow_from_level=row["allow_from_level"],
        purpose=row["purpose"],
        escape_family=row["escape_family"],
        source=row["source"],
        enforcement_mode=row["enforcement_mode"],
        action_taken=row["action_taken"],
        matched_scope=row["matched_scope"],
        matched_rule_target=row["matched_rule_target"],
        url_family=row["url_family"],
        path_kind=row["path_kind"],
        reason_code=row["reason_code"],
    )


def _planned_use_pass_from_row(row: sqlite3.Row) -> PlannedUsePassRecord:
    return PlannedUsePassRecord(
        id=row["id"],
        rule_id=row["rule_id"],
        target_type=row["target_type"],
        target=row["target"],
        purpose=row["purpose"],
        escape_family=row["escape_family"],
        reason=row["reason"],
        duration_seconds=row["duration_seconds"],
        started_at=row["started_at"],
        expires_at=row["expires_at"],
        ended_at=row["ended_at"],
        status=row["status"],
    )


def _high_session_from_row(row: sqlite3.Row) -> HighSessionRecord:
    return HighSessionRecord(
        id=row["id"],
        day_date=row["day_date"],
        started_at=row["started_at"],
        ends_at=row["ends_at"],
        allocated_minutes=row["allocated_minutes"],
        allocated_seconds=row["allocated_seconds"],
        intent=row["intent"],
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
    )
