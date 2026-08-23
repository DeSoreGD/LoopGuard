from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from selfboss.data.db import initialize_database
from selfboss_native_host.host import (
    HEARTBEAT_FILE_NAME,
    read_selfboss_status,
    write_browser_heartbeat,
)
from selfboss_native_host.protocol import build_status_response, dispatch_message


PRIVATE_FIELDS = {
    "tasks",
    "rules",
    "reward_ledger",
    "reward_history",
    "notes",
    "browsing_history",
    "urls",
    "tabs",
    "tab_list",
    "page_contents",
    "cookies",
    "form_inputs",
    "titles",
}


def test_status_reads_minimal_selfboss_state_from_existing_db(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"
    db_path = app_home / "data" / "selfboss.db"
    with initialize_database(db_path) as connection:
        day = connection.execute("SELECT day FROM day_state WHERE id = 1").fetchone()
        assert day is not None
        with connection:
            connection.execute(
                """
                UPDATE day_state
                SET day_started_at = ?, access_level = ?
                WHERE id = 1
                """,
                ("2026-05-19T08:00:00+00:00", "medium"),
            )
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    "enforcement_mode",
                    "full_enforcement",
                    "2026-05-19T08:00:00+00:00",
                ),
            )
            connection.execute(
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
                    day["day"],
                    "2026-05-19T08:00:00+00:00",
                    "2026-05-19T08:15:00+00:00",
                    15,
                    900,
                    "declared use",
                ),
            )
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))

    status = read_selfboss_status(
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc)
    )

    assert status["enforcement_mode"] == "full_enforcement"
    assert status["access_level"] == "medium"
    assert status["day_active"] is True
    assert status["high_active"] is True
    assert status["high_remaining_seconds"] == 600
    assert status["safe_mode_active"] is False
    assert status["recovery_mode_active"] is False
    assert status["browser_blocking"] == "not_implemented"
    assert not (PRIVATE_FIELDS & set(status))


def test_safe_and_recovery_modes_report_preview_effective_mode(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"
    db_path = app_home / "data" / "selfboss.db"
    with initialize_database(db_path) as connection:
        with connection:
            connection.execute(
                "UPDATE day_state SET day_started_at = ?, access_level = ? WHERE id = 1",
                ("2026-05-19T08:00:00+00:00", "high"),
            )
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    "enforcement_mode",
                    "full_enforcement",
                    "2026-05-19T08:00:00+00:00",
                ),
            )
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))
    monkeypatch.setenv("SELF_BOSS_SAFE_MODE", "1")
    monkeypatch.setenv("SELF_BOSS_RECOVERY_MODE", "1")

    status = read_selfboss_status()

    assert status["enforcement_mode"] == "preview_only"
    assert status["access_level"] == "high"
    assert status["safe_mode_active"] is True
    assert status["recovery_mode_active"] is True


def test_expired_or_ended_high_session_is_reported_inactive_without_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"
    db_path = app_home / "data" / "selfboss.db"
    with initialize_database(db_path) as connection:
        day = connection.execute("SELECT day FROM day_state WHERE id = 1").fetchone()
        assert day is not None
        with connection:
            connection.execute(
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
                    day["day"],
                    "2026-05-19T08:00:00+00:00",
                    "2026-05-19T08:15:00+00:00",
                    15,
                    900,
                    "declared use",
                ),
            )
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))

    status = read_selfboss_status(
        now_provider=lambda: datetime(2026, 5, 19, 8, 20, tzinfo=timezone.utc)
    )

    assert status["high_active"] is False
    assert status["high_remaining_seconds"] == 0
    with sqlite3.connect(db_path) as connection:
        ended_at = connection.execute(
            "SELECT ended_at FROM high_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    assert ended_at is None


def test_missing_database_returns_safe_unknown_without_creating_files(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "missing-app-home"
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))

    status = read_selfboss_status()

    assert status["enforcement_mode"] == "unknown"
    assert status["access_level"] == "unknown"
    assert status["day_active"] == "unknown"
    assert status["high_active"] == "unknown"
    assert status["browser_blocking"] == "not_implemented"
    assert "status_error" in status
    assert not app_home.exists()


def test_status_response_filters_private_provider_fields() -> None:
    response = build_status_response(
        lambda: {
            "enforcement_mode": "full_enforcement",
            "tasks": ["private"],
            "rules": ["private"],
            "reward_ledger": ["private"],
            "browsing_history": ["private"],
            "tabs": ["private"],
            "tab_list": ["private"],
            "page_contents": ["private"],
            "browser_blocking": "attempted_override",
        }
    )

    assert response["enforcement_mode"] == "full_enforcement"
    assert response["browser_blocking"] == "not_implemented"
    assert not (PRIVATE_FIELDS & set(response))


def test_browser_heartbeat_saves_privacy_minimal_json(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))

    response = dispatch_message(
        {
            "type": "browser_heartbeat",
            "browser": "chrome",
            "incognito_allowed": False,
            "browser_blocking": "active",
            "url": "https://private.example/",
            "tabs": ["private"],
            "tasks": ["private"],
            "rules": ["private"],
            "cookies": ["private"],
        },
        heartbeat_writer=write_browser_heartbeat,
    )

    assert response["ok"] is True
    assert response["heartbeat_saved"] is True
    heartbeat_path = app_home / "data" / HEARTBEAT_FILE_NAME
    saved = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    assert saved["app"] == "SelfBoss"
    assert saved["protocol_version"] == 1
    assert saved["browser"] == "chrome"
    assert saved["extension_connected"] is True
    assert saved["browser_blocking"] == "active"
    assert saved["incognito_allowed"] is False
    assert saved["source"] == "native_host"
    assert "last_heartbeat_at" in saved
    assert not (PRIVATE_FIELDS & set(saved))
    assert "url" not in saved


def test_browser_heartbeat_save_failure_returns_protocol_error() -> None:
    response = dispatch_message(
        {
            "type": "browser_heartbeat",
            "browser": "chrome",
            "incognito_allowed": True,
            "browser_blocking": "active",
        },
        heartbeat_writer=lambda _message: {
            "heartbeat_saved": False,
            "error": "disk unavailable",
        },
    )

    assert response == {
        "ok": False,
        "protocol_version": 1,
        "heartbeat_saved": False,
        "error": "disk unavailable",
    }
