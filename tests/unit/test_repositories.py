from __future__ import annotations

import sqlite3
from pathlib import Path

from selfboss.core.models import (
    AccessLevel,
    AccessAttemptDecision,
    AccessAttemptSource,
    DayOutcomeCloseKind,
    EscapeFamily,
    PlannedUsePassStatus,
    RulePurpose,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.data.db import initialize_database
from selfboss.data.repositories import (
    AccessAttemptRepository,
    AppSettingsRepository,
    AuditEventRepository,
    DayOutcomeRepository,
    DayStateRepository,
    HighSessionRepository,
    PlannedUsePassRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)


def test_database_initializes_expected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "selfboss.db"

    with initialize_database(db_path) as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        rule_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(rules)").fetchall()
        }
        day_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(day_state)").fetchall()
        }
        ledger_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(reward_ledger)").fetchall()
        }
        app_settings_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(app_settings)").fetchall()
        }
        day_outcome_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(day_outcomes)").fetchall()
        }
        access_attempt_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(access_attempts)"
            ).fetchall()
        }
        planned_use_pass_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(planned_use_passes)"
            ).fetchall()
        }
        session_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(high_sessions)").fetchall()
        }

    assert db_path.is_file()
    assert {
        "tasks",
        "day_state",
        "reward_ledger",
        "audit_events",
        "rules",
        "high_sessions",
        "app_settings",
        "day_outcomes",
        "access_attempts",
        "planned_use_passes",
    }.issubset(table_names)
    assert "kind" in task_columns
    assert "day_date" in task_columns
    assert "planning_status" in task_columns
    assert "completion_claimed_at" in task_columns
    assert "completion_available_at" in task_columns
    assert "day_started_at" in day_columns
    assert "day_ended_at" in day_columns
    assert "allow_from_level" in rule_columns
    assert "purpose" in rule_columns
    assert "escape_family" in rule_columns
    assert "reward_balance_seconds" in day_columns
    assert "seconds_delta" in ledger_columns
    assert "allocated_seconds" in session_columns
    assert "intent" in session_columns
    assert {"key", "value", "updated_at"}.issubset(app_settings_columns)
    assert {
        "day_date",
        "started_at",
        "ended_at",
        "close_kind",
        "main_completed",
        "rest_token_used",
        "created_at",
        "updated_at",
    }.issubset(day_outcome_columns)
    assert {
        "occurred_at",
        "target_type",
        "target",
        "rule_id",
        "access_level_at_attempt",
        "decision",
        "allow_from_level",
        "purpose",
        "escape_family",
        "source",
        "enforcement_mode",
        "action_taken",
        "matched_scope",
        "matched_rule_target",
        "url_family",
        "path_kind",
        "reason_code",
    }.issubset(access_attempt_columns)
    assert {
        "rule_id",
        "target_type",
        "target",
        "purpose",
        "escape_family",
        "reason",
        "duration_seconds",
        "started_at",
        "expires_at",
        "ended_at",
        "status",
    }.issubset(planned_use_pass_columns)
    assert user_version == 18


def test_app_settings_seeds_enforcement_mode_only_when_missing(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        settings = AppSettingsRepository(connection)

        assert settings.ensure_enforcement_mode_if_missing("full_enforcement") == (
            "full_enforcement"
        )
        assert settings.get_enforcement_mode() == "full_enforcement"
        assert settings.ensure_enforcement_mode_if_missing("preview_only") == (
            "full_enforcement"
        )


def test_day_outcome_round_trips_and_rest_token_count_clamps(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        outcomes = DayOutcomeRepository(connection)
        saved = outcomes.upsert(
            day_date="2026-05-08",
            started_at=None,
            ended_at="2026-05-08T18:00:00+00:00",
            close_kind=DayOutcomeCloseKind.NORMAL,
            main_completed=True,
        )

        assert outcomes.get("2026-05-08") == saved
        assert saved.main_completed is True
        assert AppSettingsRepository(connection).set_rest_token_count(3) == 1


def test_task_repository_round_trips_task_kind(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        tasks = TaskRepository(connection)

        created = tasks.create(
            title="Write architecture notes",
            description="Keep it local-first",
            reward_minutes=15,
            allowed_url="https://doc.qt.io/",
            kind=TaskKind.IMPORTANT,
            day_date="2026-05-08",
            planning_status=TaskPlanningStatus.UNPLANNED,
        )
        updated = tasks.update_status(created.id, TaskStatus.DONE)

        assert created.id > 0
        assert created.status is TaskStatus.PENDING
        assert created.kind is TaskKind.IMPORTANT
        assert created.day_date == "2026-05-08"
        assert created.planning_status is TaskPlanningStatus.UNPLANNED
        assert updated.status is TaskStatus.DONE
        assert updated.kind is TaskKind.IMPORTANT
        assert updated.day_date == "2026-05-08"
        assert updated.planning_status is TaskPlanningStatus.UNPLANNED
        assert updated.completed_at is not None
        assert tasks.get(created.id) == updated
        assert tasks.list(status=TaskStatus.DONE) == [updated]


def test_task_repository_persists_and_clears_completion_claim(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        tasks = TaskRepository(connection)
        task = tasks.create(title="Write the MAIN task")

        claimed = tasks.claim_completion(
            task.id,
            claimed_at="2026-05-08T10:00:00+00:00",
            available_at="2026-05-08T10:03:00+00:00",
        )
        reloaded = tasks.get(task.id)

        assert claimed.completion_claimed_at == "2026-05-08T10:00:00+00:00"
        assert claimed.completion_available_at == "2026-05-08T10:03:00+00:00"
        assert reloaded == claimed

        cleared = tasks.clear_completion_claim(task.id)
        assert cleared.completion_claimed_at is None
        assert cleared.completion_available_at is None

        claimed_again = tasks.claim_completion(
            task.id,
            claimed_at="2026-05-08T11:00:00+00:00",
            available_at="2026-05-08T11:03:00+00:00",
        )
        completed = tasks.update_status(claimed_again.id, TaskStatus.DONE)
        assert completed.status is TaskStatus.DONE
        assert completed.completion_claimed_at is None
        assert completed.completion_available_at is None


def test_v1_task_table_is_upgraded_with_default_kind(tmp_path: Path) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reward_minutes INTEGER NOT NULL DEFAULT 0,
                allowed_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            PRAGMA user_version = 1;
            """
        )

    with initialize_database(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        task = TaskRepository(connection).create(title="Existing database task")

        assert "kind" in columns
        assert "day_date" in columns
        assert "planning_status" in columns
        assert user_version == 18
        assert task.kind is TaskKind.NORMAL
        assert task.planning_status is TaskPlanningStatus.PLANNED


def test_v4_task_table_is_upgraded_with_day_date(tmp_path: Path) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'normal',
                reward_minutes INTEGER NOT NULL DEFAULT 0,
                allowed_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            INSERT INTO tasks (
                title,
                description,
                status,
                kind,
                reward_minutes,
                created_at,
                updated_at
            )
            VALUES (
                'Old task',
                '',
                'pending',
                'main',
                0,
                '2026-05-07T09:00:00+00:00',
                '2026-05-07T09:00:00+00:00'
            );
            PRAGMA user_version = 4;
            """
        )

    with initialize_database(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        task = TaskRepository(connection).list()[0]

        assert "day_date" in columns
        assert "planning_status" in columns
        assert user_version == 18
        assert task.day_date == "2026-05-07"
        assert task.planning_status is TaskPlanningStatus.PLANNED


def test_task_repository_lists_tasks_for_day(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        tasks = TaskRepository(connection)
        yesterday = tasks.create(title="Yesterday", day_date="2026-05-07")
        today = tasks.create(title="Today", day_date="2026-05-08")

        assert tasks.list_for_day("2026-05-07") == [yesterday]
        assert tasks.list_for_day("2026-05-08") == [today]


def test_task_repository_deletes_task_without_touching_ledger(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        tasks = TaskRepository(connection)
        rewards = RewardLedgerRepository(connection)
        task = tasks.create(title="No history yet")
        rewards.add(seconds_delta=300, reason="task_completed", task_id=task.id)

        tasks.delete(task.id)
        ledger = rewards.list()

        assert tasks.get(task.id) is None
        assert len(ledger) == 1
        assert ledger[0].seconds_delta == 300
        assert ledger[0].reason == "task_completed"


def test_rule_repository_adds_lists_and_removes_rules(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)

        site = rules.add(
            rule_type="site",
            target="  example.com  ",
            allow_from_level="medium",
            purpose=RulePurpose.STUDY_REFERENCE.value,
            escape_family=EscapeFamily.NONE.value,
        )
        app = rules.add(
            rule_type="app",
            target="game.exe",
            purpose=RulePurpose.GATEWAY_APP.value,
            escape_family=EscapeFamily.LAUNCHER.value,
        )

        assert site.id > 0
        assert site.rule_type == "site"
        assert site.target == "example.com"
        assert site.enabled is True
        assert site.allow_from_level == "medium"
        assert site.purpose == "study_reference"
        assert site.escape_family == "none"
        assert app.allow_from_level == "high"
        assert app.purpose == "gateway_app"
        assert app.escape_family == "launcher"
        assert rules.list(rule_type="site") == [site]
        assert rules.list(rule_type="app") == [app]

        rules.remove(rule_type="site", target="example.com")
        assert rules.list(rule_type="site") == []
        assert rules.list(rule_type="app") == [app]


def test_rule_repository_does_not_duplicate_rules(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)

        first = rules.add(rule_type="site", target="example.com")
        second = rules.add(rule_type="site", target="example.com")

        assert first == second
        assert rules.list(rule_type="site") == [first]
        assert rules.get_by_id(first.id) == first


def test_rule_repository_get_by_id_can_filter_disabled_rules(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)
        rule = rules.add(rule_type="site", target="example.com")
        connection.execute("UPDATE rules SET enabled = 0 WHERE id = ?", (rule.id,))

        disabled = rules.get_by_id(rule.id)

        assert disabled is not None
        assert disabled.enabled is False
        assert rules.get_by_id(rule.id, enabled_only=True) is None


def test_rules_persistence_round_trip(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)

        site = rules.add(
            rule_type="site",
            target="example.com",
            allow_from_level="MEDIUM",
        )
        updated = rules.update_allow_from_level(
            rule_type="site",
            target="example.com",
            allow_from_level="low",
            purpose="work_tool",
            escape_family="fake_productivity",
        )

        assert site.allow_from_level == "medium"
        assert updated.allow_from_level == "low"
        assert updated.purpose == "work_tool"
        assert updated.escape_family == "fake_productivity"
        assert rules.list(rule_type="site") == [updated]


def test_v3_rules_table_is_upgraded_with_high_default(tmp_path: Path) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_type TEXT NOT NULL,
                target TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(rule_type, target)
            );
            INSERT INTO rules (rule_type, target, enabled, created_at)
            VALUES ('site', 'example.com', 1, '2026-05-08T09:00:00+00:00');
            PRAGMA user_version = 3;
            """
        )

    with initialize_database(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(rules)").fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        rules = RuleRepository(connection).list(rule_type="site")

        assert "allow_from_level" in columns
        assert "purpose" in columns
        assert "escape_family" in columns
        assert user_version == 18
        assert rules[0].allow_from_level == "high"
        assert rules[0].purpose == "high_risk_escape"
        assert rules[0].escape_family == "none"


def test_v9_rules_table_is_upgraded_with_metadata_defaults(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_type TEXT NOT NULL,
                target TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                allow_from_level TEXT NOT NULL DEFAULT 'high',
                created_at TEXT NOT NULL,
                UNIQUE(rule_type, target)
            );
            INSERT INTO rules (
                rule_type,
                target,
                enabled,
                allow_from_level,
                created_at
            )
            VALUES (
                'site',
                'medium.example',
                1,
                'medium',
                '2026-05-08T09:00:00+00:00'
            );
            PRAGMA user_version = 9;
            """
        )

    with initialize_database(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(rules)").fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        rules = RuleRepository(connection).list(rule_type="site")

        assert "purpose" in columns
        assert "escape_family" in columns
        assert user_version == 18
        assert rules[0].target == "medium.example"
        assert rules[0].allow_from_level == "medium"
        assert rules[0].purpose == "high_risk_escape"
        assert rules[0].escape_family == "none"


def test_access_attempt_repository_adds_and_lists_recent_attempts(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)
        attempts = AccessAttemptRepository(connection)
        rule = rules.add(
            rule_type="site",
            target="youtube.com",
            allow_from_level="high",
            purpose=RulePurpose.COMPULSIVE_STIMULATION.value,
            escape_family=EscapeFamily.VIDEO.value,
        )

        first = attempts.add(
            occurred_at="2026-05-08T09:00:00+00:00",
            target_type=rule.rule_type,
            target=rule.target,
            rule_id=rule.id,
            access_level_at_attempt=AccessLevel.LOW.value,
            decision=AccessAttemptDecision.WOULD_BLOCK_IN_CURRENT_MODE.value,
            allow_from_level=rule.allow_from_level,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
        )
        second = attempts.add(
            occurred_at="2026-05-08T09:05:00+00:00",
            target_type="app",
            target="steam.exe",
            rule_id=None,
            access_level_at_attempt=AccessLevel.HIGH.value,
            decision=AccessAttemptDecision.ALLOWED_NOW.value,
            allow_from_level="high",
            purpose=RulePurpose.GATEWAY_APP.value,
            escape_family=EscapeFamily.LAUNCHER.value,
            source=AccessAttemptSource.MANUAL_TEST.value,
        )

        recent = attempts.list_recent(limit=10)

        assert first.id > 0
        assert first.target_type == "site"
        assert first.target == "youtube.com"
        assert first.rule_id == rule.id
        assert first.access_level_at_attempt == "low"
        assert first.decision == "would_block_in_current_mode"
        assert first.allow_from_level == "high"
        assert first.purpose == "compulsive_stimulation"
        assert first.escape_family == "video"
        assert first.source == "manual_test"
        assert first.enforcement_mode == "preview_only"
        assert first.action_taken == "none"
        assert first.matched_scope == "none"
        assert first.matched_rule_target is None
        assert first.url_family == "unknown"
        assert first.path_kind == "unknown"
        assert first.reason_code == "unknown"
        assert second.enforcement_mode == "preview_only"
        assert second.action_taken == "none"
        assert recent == [second, first]
        assert attempts.list_recent(limit=1) == [second]
        assert attempts.list_recent(limit=0) == []

        dry_run = attempts.add(
            occurred_at="2026-05-08T09:06:00+00:00",
            target_type="app",
            target="steam.exe",
            rule_id=None,
            access_level_at_attempt=AccessLevel.LOW.value,
            decision=AccessAttemptDecision.WOULD_BLOCK.value,
            allow_from_level="high",
            purpose=RulePurpose.GATEWAY_APP.value,
            escape_family=EscapeFamily.LAUNCHER.value,
            source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
            enforcement_mode="armed_dry_run",
            action_taken="none",
        )

        assert dry_run.decision == "would_block"
        assert dry_run.source == "armed_dry_run_process"
        assert dry_run.enforcement_mode == "armed_dry_run"
        assert dry_run.action_taken == "none"
        assert attempts.list_recent(
            limit=10,
            source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
        ) == [dry_run]

        real_process = attempts.add(
            occurred_at="2026-05-08T09:07:00+00:00",
            target_type="app",
            target="steam.exe",
            rule_id=None,
            access_level_at_attempt=AccessLevel.LOW.value,
            decision=AccessAttemptDecision.WOULD_BLOCK.value,
            allow_from_level="high",
            purpose=RulePurpose.GATEWAY_APP.value,
            escape_family=EscapeFamily.LAUNCHER.value,
            source=AccessAttemptSource.REAL_PROCESS_BLOCKING_PROCESS.value,
            enforcement_mode="real_process_blocking",
            action_taken="terminate_requested",
        )

        assert real_process.source == "real_process_blocking_process"
        assert real_process.enforcement_mode == "real_process_blocking"
        assert real_process.action_taken == "terminate_requested"
        assert attempts.list_recent(
            limit=10,
            source=AccessAttemptSource.REAL_PROCESS_BLOCKING_PROCESS.value,
        ) == [real_process]

        browser = attempts.add(
            occurred_at="2026-05-08T09:08:00+00:00",
            target_type="site",
            target="www.youtube.com",
            rule_id=rule.id,
            access_level_at_attempt=AccessLevel.LOW.value,
            decision=AccessAttemptDecision.WOULD_BLOCK.value,
            allow_from_level=rule.allow_from_level,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
            source=AccessAttemptSource.BROWSER.value,
            enforcement_mode="full_enforcement",
            action_taken="browser_redirect",
            matched_scope="path",
            matched_rule_target="youtube.com/shorts/*",
            url_family="youtube",
            path_kind="youtube_shorts",
            reason_code="path_rule_blocked",
        )

        assert browser.source == "browser"
        assert browser.target == "www.youtube.com"
        assert browser.action_taken == "browser_redirect"
        assert browser.matched_scope == "path"
        assert browser.matched_rule_target == "youtube.com/shorts/*"
        assert browser.url_family == "youtube"
        assert browser.path_kind == "youtube_shorts"
        assert browser.reason_code == "path_rule_blocked"
        assert attempts.list_recent(
            limit=10,
            source=AccessAttemptSource.BROWSER.value,
        ) == [browser]
        assert attempts.list_recent(limit=10, occurred_on="2026-05-08") == [
            browser,
            real_process,
            dry_run,
            second,
            first,
        ]
        assert attempts.list_recent(
            limit=10,
            source=AccessAttemptSource.ARMED_DRY_RUN_PROCESS.value,
            occurred_on="2026-05-08",
        ) == [dry_run]
        assert attempts.list_recent(limit=10, occurred_on="2026-05-09") == []


def test_planned_use_pass_repository_persists_active_end_and_expiry(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)
        passes = PlannedUsePassRepository(connection)
        rule = rules.add(
            rule_type="site",
            target="youtube.com",
            allow_from_level="high",
            purpose=RulePurpose.COMPULSIVE_STIMULATION.value,
            escape_family=EscapeFamily.VIDEO.value,
        )

        active = passes.add(
            rule_id=rule.id,
            target_type=rule.rule_type,
            target=rule.target,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
            reason="Watch one PySide6 tutorial",
            duration_seconds=900,
            started_at="2026-05-08T09:00:00+00:00",
            expires_at="2026-05-08T09:15:00+00:00",
        )

        assert active.id > 0
        assert active.rule_id == rule.id
        assert active.target_type == "site"
        assert active.target == "youtube.com"
        assert active.purpose == "compulsive_stimulation"
        assert active.escape_family == "video"
        assert active.reason == "Watch one PySide6 tutorial"
        assert active.duration_seconds == 900
        assert active.ended_at is None
        assert active.status == PlannedUsePassStatus.ACTIVE.value
        assert passes.get_active("2026-05-08T09:14:00+00:00") == active

        ended = passes.end_active("2026-05-08T09:05:00+00:00")

        assert ended is not None
        assert ended.id == active.id
        assert ended.status == PlannedUsePassStatus.ENDED.value
        assert ended.ended_at == "2026-05-08T09:05:00+00:00"
        assert passes.get_active("2026-05-08T09:06:00+00:00") is None
        assert passes.list_recent(limit=1) == [ended]

        expiring = passes.add(
            rule_id=rule.id,
            target_type=rule.rule_type,
            target=rule.target,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
            reason="Watch one PySide6 tutorial",
            duration_seconds=300,
            started_at="2026-05-08T10:00:00+00:00",
            expires_at="2026-05-08T10:05:00+00:00",
        )

        assert passes.expire_due("2026-05-08T10:05:00+00:00") == 1
        recent = passes.list_recent(limit=1)
        assert recent[0].id == expiring.id
        assert recent[0].status == PlannedUsePassStatus.EXPIRED.value
        assert passes.list_recent(limit=0) == []


def test_planned_use_pass_repository_preserves_snapshots_and_active_order(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)
        passes = PlannedUsePassRepository(connection)
        rule = rules.add(
            rule_type="site",
            target="youtube.com",
            allow_from_level="high",
            purpose=RulePurpose.COMPULSIVE_STIMULATION.value,
            escape_family=EscapeFamily.VIDEO.value,
        )

        first = passes.add(
            rule_id=rule.id,
            target_type=rule.rule_type,
            target=rule.target,
            purpose=rule.purpose,
            escape_family=rule.escape_family,
            reason="Watch one PySide6 tutorial",
            duration_seconds=900,
            started_at="2026-05-08T09:00:00+00:00",
            expires_at="2026-05-08T09:15:00+00:00",
        )
        rules.update_allow_from_level(
            rule_type="site",
            target="youtube.com",
            allow_from_level="low",
            purpose=RulePurpose.WORK_TOOL.value,
            escape_family=EscapeFamily.NONE.value,
        )

        loaded_first = passes.list_recent(limit=1)[0]

        assert loaded_first.id == first.id
        assert loaded_first.purpose == RulePurpose.COMPULSIVE_STIMULATION.value
        assert loaded_first.escape_family == EscapeFamily.VIDEO.value

        second = passes.add(
            rule_id=rule.id,
            target_type=rule.rule_type,
            target=rule.target,
            purpose=RulePurpose.WORK_TOOL.value,
            escape_family=EscapeFamily.NONE.value,
            reason="Use the tutorial as direct task reference",
            duration_seconds=600,
            started_at="2026-05-08T09:01:00+00:00",
            expires_at="2026-05-08T09:11:00+00:00",
        )

        assert passes.get_active("2026-05-08T09:05:00+00:00") == second
        assert passes.list_recent(limit=2) == [second, first]


def test_v10_database_is_upgraded_with_access_attempts_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE rules (
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
            INSERT INTO rules (
                rule_type,
                target,
                enabled,
                allow_from_level,
                purpose,
                escape_family,
                created_at
            )
            VALUES (
                'site',
                'youtube.com',
                1,
                'high',
                'compulsive_stimulation',
                'video',
                '2026-05-08T09:00:00+00:00'
            );
            PRAGMA user_version = 10;
            """
        )

    with initialize_database(db_path) as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        rules = RuleRepository(connection).list(rule_type="site")

        assert "access_attempts" in table_names
        assert "planned_use_passes" in table_names
        assert user_version == 18
        assert rules[0].target == "youtube.com"
        assert rules[0].allow_from_level == "high"
        assert rules[0].purpose == "compulsive_stimulation"
        assert rules[0].escape_family == "video"


def test_v11_database_is_upgraded_with_planned_use_passes_table(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE rules (
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
            CREATE TABLE access_attempts (
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
                source TEXT NOT NULL DEFAULT 'manual_test'
            );
            INSERT INTO rules (
                id,
                rule_type,
                target,
                enabled,
                allow_from_level,
                purpose,
                escape_family,
                created_at
            )
            VALUES (
                1,
                'site',
                'youtube.com',
                1,
                'high',
                'compulsive_stimulation',
                'video',
                '2026-05-08T09:00:00+00:00'
            );
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
                source
            )
            VALUES (
                '2026-05-08T09:05:00+00:00',
                'site',
                'youtube.com',
                1,
                'low',
                'would_block_in_current_mode',
                'high',
                'compulsive_stimulation',
                'video',
                'manual_test'
            );
            PRAGMA user_version = 11;
            """
        )

    with initialize_database(db_path) as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        rules = RuleRepository(connection).list(rule_type="site")
        attempts = AccessAttemptRepository(connection).list_recent()

        assert "planned_use_passes" in table_names
        assert user_version == 18
        assert rules[0].target == "youtube.com"
        assert attempts[0].target == "youtube.com"
        assert attempts[0].purpose == "compulsive_stimulation"
        assert attempts[0].escape_family == "video"
        assert attempts[0].enforcement_mode == "preview_only"
        assert attempts[0].action_taken == "none"
        assert attempts[0].matched_scope == "none"
        assert attempts[0].matched_rule_target is None
        assert attempts[0].url_family == "unknown"
        assert attempts[0].path_kind == "unknown"
        assert attempts[0].reason_code == "unknown"


def test_v14_database_is_upgraded_with_access_attempt_enforcement_columns(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE rules (
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
            CREATE TABLE access_attempts (
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
                source TEXT NOT NULL DEFAULT 'manual_test'
            );
            INSERT INTO rules (
                id,
                rule_type,
                target,
                enabled,
                allow_from_level,
                purpose,
                escape_family,
                created_at
            )
            VALUES (
                1,
                'app',
                'steam.exe',
                1,
                'high',
                'gateway_app',
                'launcher',
                '2026-05-08T09:00:00+00:00'
            );
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
                source
            )
            VALUES (
                '2026-05-08T09:05:00+00:00',
                'app',
                'steam.exe',
                1,
                'low',
                'would_block_in_current_mode',
                'high',
                'gateway_app',
                'launcher',
                'manual_test'
            );
            PRAGMA user_version = 14;
            """
        )

    with initialize_database(db_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(access_attempts)"
            ).fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        attempts = AccessAttemptRepository(connection).list_recent()

        assert user_version == 18
        assert {
            "enforcement_mode",
            "action_taken",
            "matched_scope",
            "matched_rule_target",
            "url_family",
            "path_kind",
            "reason_code",
        }.issubset(columns)
        assert attempts[0].target == "steam.exe"
        assert attempts[0].enforcement_mode == "preview_only"
        assert attempts[0].action_taken == "none"
        assert attempts[0].matched_scope == "none"
        assert attempts[0].matched_rule_target is None
        assert attempts[0].url_family == "unknown"
        assert attempts[0].path_kind == "unknown"
        assert attempts[0].reason_code == "unknown"


def test_v6_minute_values_are_upgraded_to_seconds(tmp_path: Path) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE day_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                day TEXT NOT NULL,
                access_level TEXT NOT NULL,
                reward_balance_minutes INTEGER NOT NULL DEFAULT 0,
                surrender_requested_at TEXT,
                bad_day_mode INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            INSERT INTO day_state (
                id,
                day,
                access_level,
                reward_balance_minutes,
                bad_day_mode,
                updated_at
            )
            VALUES (1, '2026-05-08', 'medium', 25, 0, '2026-05-08T09:00:00+00:00');

            CREATE TABLE reward_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                minutes_delta INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO reward_ledger (task_id, minutes_delta, reason, created_at)
            VALUES (NULL, 25, 'task_completed', '2026-05-08T09:00:00+00:00');

            CREATE TABLE high_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                allocated_minutes INTEGER NOT NULL,
                ended_at TEXT,
                end_reason TEXT
            );
            INSERT INTO high_sessions (
                day_date,
                started_at,
                ends_at,
                allocated_minutes
            )
            VALUES (
                '2026-05-08',
                '2026-05-08T09:00:00+00:00',
                '2026-05-08T09:15:00+00:00',
                15
            );
            PRAGMA user_version = 6;
            """
        )

    with initialize_database(db_path) as connection:
        day = DayStateRepository(connection).get()
        ledger = RewardLedgerRepository(connection).list()
        session = HighSessionRepository(connection).list()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]

        assert user_version == 18
        assert day.reward_balance_seconds == 25 * 60
        assert ledger[0].seconds_delta == 25 * 60
        assert session.allocated_seconds == 15 * 60
        assert session.intent == ""


def test_v7_database_is_upgraded_with_start_day_and_planning_status(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selfboss.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
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
                day_date TEXT NOT NULL
            );
            INSERT INTO tasks (
                title,
                description,
                status,
                kind,
                reward_minutes,
                created_at,
                updated_at,
                day_date
            )
            VALUES (
                'Existing planned task',
                '',
                'pending',
                'normal',
                0,
                '2026-05-08T09:00:00+00:00',
                '2026-05-08T09:00:00+00:00',
                '2026-05-08'
            );

            CREATE TABLE day_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                day TEXT NOT NULL,
                access_level TEXT NOT NULL,
                reward_balance_minutes INTEGER NOT NULL DEFAULT 0,
                reward_balance_seconds INTEGER NOT NULL DEFAULT 0,
                surrender_requested_at TEXT,
                bad_day_mode INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            INSERT INTO day_state (
                id,
                day,
                access_level,
                reward_balance_minutes,
                reward_balance_seconds,
                bad_day_mode,
                updated_at
            )
            VALUES (1, '2026-05-08', 'low', 0, 0, 0, '2026-05-08T09:00:00+00:00');
            PRAGMA user_version = 7;
            """
        )

    with initialize_database(db_path) as connection:
        task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        day_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(day_state)").fetchall()
        }
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        task = TaskRepository(connection).list()[0]
        day = DayStateRepository(connection).get()

        assert user_version == 18
        assert "planning_status" in task_columns
        assert "day_started_at" in day_columns
        assert "day_ended_at" in day_columns
        assert task.planning_status is TaskPlanningStatus.PLANNED
        assert day.day_started_at is None
        assert day.day_ended_at is None


def test_rule_repository_rejects_invalid_allow_from_level(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)

        try:
            rules.add(
                rule_type="site",
                target="example.com",
                allow_from_level="later",
            )
        except ValueError as error:
            assert "Unsupported allow_from_level" in str(error)
        else:
            raise AssertionError("invalid allow_from_level should fail")


def test_rule_repository_rejects_invalid_metadata(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)

        for kwargs, expected in (
            ({"purpose": "doomscrolling"}, "Unsupported rule purpose"),
            ({"escape_family": "unknown"}, "Unsupported escape family"),
        ):
            try:
                rules.add(rule_type="site", target="example.com", **kwargs)
            except ValueError as error:
                assert expected in str(error)
            else:
                raise AssertionError("invalid rule metadata should fail")


def test_rule_repository_replaces_all_rules_preserving_enabled_state(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        rules = RuleRepository(connection)
        rules.add(rule_type="site", target="old.example.com")

        replaced = rules.replace_all(
            [
                {
                    "rule_type": "site",
                    "target": "youtube.com",
                    "enabled": True,
                    "allow_from_level": "high",
                    "purpose": "compulsive_stimulation",
                    "escape_family": "video",
                },
                {
                    "rule_type": "app",
                    "target": "steam.exe",
                    "enabled": False,
                    "allow_from_level": "high",
                    "purpose": "gateway_app",
                    "escape_family": "launcher",
                },
            ]
        )

        assert [rule.target for rule in replaced] == ["youtube.com", "steam.exe"]
        assert replaced[0].enabled is True
        assert replaced[1].enabled is False
        assert rules.list() == [replaced[0]]
        assert rules.list(enabled_only=False) == replaced

        try:
            rules.replace_all(
                [
                    {
                        "rule_type": "site",
                        "target": "youtube.com",
                        "enabled": True,
                    },
                    {
                        "rule_type": "site",
                        "target": "youtube.com",
                        "enabled": True,
                    },
                ]
            )
        except ValueError as error:
            assert "Duplicate rule" in str(error)
        else:
            raise AssertionError("duplicate imported rules should fail")


def test_app_settings_repository_persists_surrender_strictness(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        settings = AppSettingsRepository(connection)

        assert settings.get_surrender_strictness() == "medium"

        stored = settings.set_surrender_strictness("LOW")

        assert stored == "low"
        assert settings.get_surrender_strictness() == "low"

        settings.set_value("surrender_strictness", "later")

        assert settings.get_surrender_strictness() == "medium"


def test_app_settings_repository_persists_soft_start_preferences(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        settings = AppSettingsRepository(connection)

        assert settings.get_soft_start_enabled() is True
        assert settings.get_soft_start_duration_minutes() == 15

        assert settings.set_soft_start_enabled(False) is False
        assert settings.get_soft_start_enabled() is False
        assert settings.set_soft_start_enabled(True) is True
        assert settings.get_soft_start_enabled() is True

        for minutes in (0, 15, 60):
            assert settings.set_soft_start_duration_minutes(minutes) == minutes
            assert settings.get_soft_start_duration_minutes() == minutes

        for minutes in (-1, 61):
            try:
                settings.set_soft_start_duration_minutes(minutes)
            except ValueError as error:
                assert "between 0 and 60" in str(error)
            else:
                raise AssertionError("invalid Soft Start duration should fail")

        settings.set_value("soft_start_duration_minutes", "later")
        assert settings.get_soft_start_duration_minutes() == 15


def test_day_state_and_reward_ledger_use_temp_database(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        day_state = DayStateRepository(connection)
        rewards = RewardLedgerRepository(connection)

        initial = day_state.get()
        changed = day_state.set_access_level(AccessLevel.MEDIUM)
        credited = day_state.add_reward_minutes(15)
        entry = rewards.add(minutes_delta=15, reason="task_completed", task_id=None)

        assert initial.access_level is AccessLevel.LOW
        assert changed.access_level is AccessLevel.MEDIUM
        assert credited.reward_balance_minutes == initial.reward_balance_minutes + 15
        assert credited.reward_balance_seconds == initial.reward_balance_seconds + 900
        assert rewards.list() == [entry]
        assert entry.seconds_delta == 900


def test_reward_seconds_round_trip(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        day_state = DayStateRepository(connection)
        rewards = RewardLedgerRepository(connection)

        credited = day_state.add_reward_seconds(1550)
        entry = rewards.add(
            seconds_delta=1550,
            reason="high_mode_refund",
            task_id=None,
        )

        assert credited.reward_balance_seconds == 1550
        assert credited.reward_balance_minutes == 25
        assert rewards.list() == [entry]
        assert entry.seconds_delta == 1550
        assert entry.minutes_delta == 25


def test_day_state_resets_for_new_day(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        day_state = DayStateRepository(connection)
        day_state.set_access_level(AccessLevel.MEDIUM)
        day_state.add_reward_minutes(30)

        reset = day_state.reset_for_day("2026-05-08")

        assert reset.day == "2026-05-08"
        assert reset.access_level is AccessLevel.LOW
        assert reset.reward_balance_minutes == 0
        assert reset.reward_balance_seconds == 0
        assert reset.surrender_requested_at is None
        assert reset.bad_day_mode is False
        assert reset.day_ended_at is None


def test_day_state_start_day_round_trips_and_resets(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        day_state = DayStateRepository(connection)

        started = day_state.start_day("2026-05-08T09:00:00+00:00")
        repeated = day_state.start_day("2026-05-08T10:00:00+00:00")
        ended = day_state.end_day("2026-05-08T17:00:00+00:00")
        reset = day_state.reset_for_day("2026-05-09")

        assert started.day_started_at == "2026-05-08T09:00:00+00:00"
        assert repeated.day_started_at == "2026-05-08T09:00:00+00:00"
        assert ended.day_ended_at == "2026-05-08T17:00:00+00:00"
        assert reset.day_started_at is None
        assert reset.day_ended_at is None


def test_high_session_repository_round_trips_active_and_ended_session(
    tmp_path: Path,
) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        sessions = HighSessionRepository(connection)

        first = sessions.start(
            day_date="2026-05-08",
            started_at="2026-05-08T09:00:00+00:00",
            ends_at="2026-05-08T09:15:00+00:00",
            allocated_minutes=15,
            allocated_seconds=900,
            intent="watch one tutorial",
        )
        second = sessions.start(
            day_date="2026-05-08",
            started_at="2026-05-08T10:00:00+00:00",
            ends_at="2026-05-08T10:05:00+00:00",
            allocated_minutes=5,
            allocated_seconds=300,
            intent="play one match",
        )

        assert sessions.get(first.id) == first
        assert first.allocated_seconds == 900
        assert first.intent == "watch one tutorial"
        assert sessions.active_for_day("2026-05-08") == second
        assert sessions.list(active_only=True) == [first, second]

        ended = sessions.end(
            second.id,
            ended_at="2026-05-08T10:01:00+00:00",
            reason="ended_early",
        )

        assert ended.ended_at == "2026-05-08T10:01:00+00:00"
        assert ended.end_reason == "ended_early"
        assert sessions.active_for_day("2026-05-08") == first
        assert sessions.list() == [first, ended]


def test_audit_events_are_recorded_locally(tmp_path: Path) -> None:
    with initialize_database(tmp_path / "selfboss.db") as connection:
        audit_events = AuditEventRepository(connection)

        event = audit_events.record(
            event_type="database_initialized",
            details="temp test database",
        )

        assert event.id > 0
        assert audit_events.list() == [event]
