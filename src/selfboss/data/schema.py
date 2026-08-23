"""SQLite schema bootstrap for LoopGuard local storage."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

from selfboss.core.models import AccessLevel


SCHEMA_VERSION = 18


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp for persisted records."""
    return datetime.now(timezone.utc).isoformat()


def bootstrap_schema(connection: sqlite3.Connection) -> None:
    """Create all MVP tables if they do not exist."""
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported database schema version: {current_version}"
        )

    now = utc_now_iso()
    today = date.today().isoformat()

    with connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'normal',
                reward_minutes INTEGER NOT NULL DEFAULT 0,
                allowed_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                completion_claimed_at TEXT,
                completion_available_at TEXT,
                day_date TEXT NOT NULL,
                planning_status TEXT NOT NULL DEFAULT 'planned'
            );

            CREATE TABLE IF NOT EXISTS day_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                day TEXT NOT NULL,
                day_started_at TEXT,
                day_ended_at TEXT,
                access_level TEXT NOT NULL,
                reward_balance_minutes INTEGER NOT NULL DEFAULT 0,
                reward_balance_seconds INTEGER NOT NULL DEFAULT 0,
                surrender_requested_at TEXT,
                bad_day_mode INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reward_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                minutes_delta INTEGER NOT NULL,
                seconds_delta INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS high_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                allocated_minutes INTEGER NOT NULL,
                allocated_seconds INTEGER NOT NULL DEFAULT 0,
                intent TEXT NOT NULL DEFAULT '',
                ended_at TEXT,
                end_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_type TEXT NOT NULL,
                target TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                allow_from_level TEXT NOT NULL DEFAULT 'high',
                purpose TEXT NOT NULL DEFAULT 'high_risk_escape',
                escape_family TEXT NOT NULL DEFAULT 'none',
                created_at TEXT NOT NULL,
                UNIQUE(rule_type, target)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS day_outcomes (
                day_date TEXT PRIMARY KEY,
                started_at TEXT,
                ended_at TEXT NOT NULL,
                close_kind TEXT NOT NULL,
                main_completed INTEGER NOT NULL DEFAULT 0,
                rest_token_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target TEXT NOT NULL,
                rule_id INTEGER REFERENCES rules(id) ON DELETE SET NULL,
                access_level_at_attempt TEXT NOT NULL,
                decision TEXT NOT NULL,
                allow_from_level TEXT,
                purpose TEXT NOT NULL DEFAULT 'high_risk_escape',
                escape_family TEXT NOT NULL DEFAULT 'none',
                source TEXT NOT NULL DEFAULT 'manual_test',
                enforcement_mode TEXT NOT NULL DEFAULT 'preview_only',
                action_taken TEXT NOT NULL DEFAULT 'none',
                matched_scope TEXT NOT NULL DEFAULT 'none',
                matched_rule_target TEXT,
                url_family TEXT NOT NULL DEFAULT 'unknown',
                path_kind TEXT NOT NULL DEFAULT 'unknown',
                reason_code TEXT NOT NULL DEFAULT 'unknown'
            );

            CREATE TABLE IF NOT EXISTS planned_use_passes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
                target_type TEXT NOT NULL,
                target TEXT NOT NULL,
                purpose TEXT NOT NULL,
                escape_family TEXT NOT NULL,
                reason TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL
            );
            """
        )
        _ensure_task_kind_column(connection)
        _ensure_task_day_date_column(connection, today)
        _ensure_task_planning_status_column(connection)
        _ensure_task_completion_claim_columns(connection)
        _ensure_day_started_at_column(connection)
        _ensure_day_ended_at_column(connection)
        _ensure_rule_allow_from_level_column(connection)
        _ensure_rule_metadata_columns(connection)
        _ensure_access_attempt_enforcement_columns(connection)
        _ensure_access_attempt_browser_metadata_columns(connection)
        _ensure_reward_seconds_columns(connection)
        _ensure_high_session_intent_column(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute(
            """
            INSERT OR IGNORE INTO day_state (
                id,
                day,
                day_started_at,
                day_ended_at,
                access_level,
                reward_balance_minutes,
                reward_balance_seconds,
                bad_day_mode,
                updated_at
            )
            VALUES (1, ?, NULL, NULL, ?, 0, 0, 0, ?)
            """,
            (today, AccessLevel.LOW.value, now),
        )


def _ensure_task_kind_column(connection: sqlite3.Connection) -> None:
    """Upgrade v1 task rows so task kind can round-trip through storage."""
    task_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "kind" not in task_columns:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'normal'"
        )


def _ensure_task_day_date_column(
    connection: sqlite3.Connection,
    fallback_day: str,
) -> None:
    """Upgrade old task rows so tasks can be scoped to a local day."""
    task_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "day_date" not in task_columns:
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN day_date TEXT NOT NULL DEFAULT ''"
        )

    connection.execute(
        """
        UPDATE tasks
        SET day_date = CASE
            WHEN length(created_at) >= 10
             AND substr(created_at, 5, 1) = '-'
             AND substr(created_at, 8, 1) = '-'
            THEN substr(created_at, 1, 10)
            ELSE ?
        END
        WHERE day_date = ''
        """,
        (fallback_day,),
    )


def _ensure_task_planning_status_column(connection: sqlite3.Connection) -> None:
    """Upgrade old task rows so planned/unplanned status can round-trip."""
    task_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "planning_status" not in task_columns:
        connection.execute(
            "ALTER TABLE tasks "
            "ADD COLUMN planning_status TEXT NOT NULL DEFAULT 'planned'"
        )


def _ensure_task_completion_claim_columns(connection: sqlite3.Connection) -> None:
    """Upgrade task rows so completion claims can survive app restarts."""
    task_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "completion_claimed_at" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN completion_claimed_at TEXT")
    if "completion_available_at" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN completion_available_at TEXT")


def _ensure_day_started_at_column(connection: sqlite3.Connection) -> None:
    """Upgrade day_state so Start Day can be persisted."""
    day_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(day_state)").fetchall()
    }
    if "day_started_at" not in day_columns:
        connection.execute(
            "ALTER TABLE day_state ADD COLUMN day_started_at TEXT"
        )


def _ensure_day_ended_at_column(connection: sqlite3.Connection) -> None:
    """Upgrade day_state so End Day can be persisted."""
    day_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(day_state)").fetchall()
    }
    if "day_ended_at" not in day_columns:
        connection.execute(
            "ALTER TABLE day_state ADD COLUMN day_ended_at TEXT"
        )


def _ensure_rule_allow_from_level_column(connection: sqlite3.Connection) -> None:
    """Upgrade v3 rules so thresholds can round-trip through storage."""
    rule_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(rules)").fetchall()
    }
    if "allow_from_level" not in rule_columns:
        connection.execute(
            "ALTER TABLE rules "
            "ADD COLUMN allow_from_level TEXT NOT NULL DEFAULT 'high'"
        )


def _ensure_rule_metadata_columns(connection: sqlite3.Connection) -> None:
    """Upgrade rules so purpose and escape family can round-trip."""
    rule_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(rules)").fetchall()
    }
    if "purpose" not in rule_columns:
        connection.execute(
            "ALTER TABLE rules "
            "ADD COLUMN purpose TEXT NOT NULL DEFAULT 'high_risk_escape'"
        )
    if "escape_family" not in rule_columns:
        connection.execute(
            "ALTER TABLE rules "
            "ADD COLUMN escape_family TEXT NOT NULL DEFAULT 'none'"
        )


def _ensure_access_attempt_enforcement_columns(connection: sqlite3.Connection) -> None:
    """Upgrade access attempts with enforcement-stage metadata."""
    attempt_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(access_attempts)").fetchall()
    }
    if "enforcement_mode" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN enforcement_mode TEXT NOT NULL DEFAULT 'preview_only'"
        )
    if "action_taken" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN action_taken TEXT NOT NULL DEFAULT 'none'"
        )


def _ensure_access_attempt_browser_metadata_columns(
    connection: sqlite3.Connection,
) -> None:
    """Upgrade access attempts with browser evaluation metadata."""
    attempt_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(access_attempts)").fetchall()
    }
    if "matched_scope" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN matched_scope TEXT NOT NULL DEFAULT 'none'"
        )
    if "matched_rule_target" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts ADD COLUMN matched_rule_target TEXT"
        )
    if "url_family" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN url_family TEXT NOT NULL DEFAULT 'unknown'"
        )
    if "path_kind" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN path_kind TEXT NOT NULL DEFAULT 'unknown'"
        )
    if "reason_code" not in attempt_columns:
        connection.execute(
            "ALTER TABLE access_attempts "
            "ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'unknown'"
        )


def _ensure_reward_seconds_columns(connection: sqlite3.Connection) -> None:
    """Upgrade minute-based reward storage to canonical seconds columns."""
    day_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(day_state)").fetchall()
    }
    if "reward_balance_seconds" not in day_columns:
        connection.execute(
            "ALTER TABLE day_state "
            "ADD COLUMN reward_balance_seconds INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            """
            UPDATE day_state
            SET reward_balance_seconds = reward_balance_minutes * 60
            """
        )

    ledger_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(reward_ledger)").fetchall()
    }
    if "seconds_delta" not in ledger_columns:
        connection.execute(
            "ALTER TABLE reward_ledger "
            "ADD COLUMN seconds_delta INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            """
            UPDATE reward_ledger
            SET seconds_delta = minutes_delta * 60
            """
        )

    session_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(high_sessions)").fetchall()
    }
    if "allocated_seconds" not in session_columns:
        connection.execute(
            "ALTER TABLE high_sessions "
            "ADD COLUMN allocated_seconds INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            """
            UPDATE high_sessions
            SET allocated_seconds = allocated_minutes * 60
            """
        )


def _ensure_high_session_intent_column(connection: sqlite3.Connection) -> None:
    """Upgrade HIGH sessions so declared intent can round-trip."""
    session_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(high_sessions)").fetchall()
    }
    if "intent" not in session_columns:
        connection.execute(
            "ALTER TABLE high_sessions "
            "ADD COLUMN intent TEXT NOT NULL DEFAULT ''"
        )
