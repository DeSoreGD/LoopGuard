from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from selfboss.data.db import initialize_database
from selfboss_native_host.host import (
    HEARTBEAT_FILE_NAME,
    classify_browser_url,
    evaluate_url_read_only,
    read_blocked_domains_snapshot,
)
from selfboss_native_host.protocol import (
    build_blocked_domains_snapshot_response,
    build_url_evaluation_response,
    dispatch_message,
)


PRIVATE_FIELDS = {
    "tasks",
    "rules",
    "reward_ledger",
    "reward_history",
    "notes",
    "browsing_history",
    "page_contents",
    "cookies",
    "form_inputs",
    "tabs",
    "tab_list",
    "title",
    "url",
}


def _seed_state(
    db_path,
    *,
    access_level: str = "low",
    enforcement_mode: str = "full_enforcement",
    rule_target: str = "reddit.com",
    allow_from_level: str = "high",
    extra_rules: tuple[tuple[str, str], ...] = (),
    high_active: bool = False,
    planned_use_pass: bool = False,
    day_active: bool = True,
    task_allowed_url: str | None = None,
    task_status: str = "pending",
) -> None:
    now = datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc)
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
                (now.isoformat() if day_active else None, access_level),
            )
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                ("enforcement_mode", enforcement_mode, now.isoformat()),
            )
            cursor = connection.execute(
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
                VALUES ('site', ?, 1, ?, 'high_risk_escape', 'none', ?)
                """,
                (rule_target, allow_from_level, now.isoformat()),
            )
            for extra_target, extra_allow_from_level in extra_rules:
                connection.execute(
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
                    VALUES ('site', ?, 1, ?, 'high_risk_escape', 'none', ?)
                    """,
                    (extra_target, extra_allow_from_level, now.isoformat()),
                )
            if high_active:
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
                    VALUES (?, ?, ?, 15, 900, 'declared use')
                    """,
                    (
                        day["day"],
                        now.isoformat(),
                        (now + timedelta(minutes=15)).isoformat(),
                    ),
                )
            if planned_use_pass:
                connection.execute(
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
                        status
                    )
                    VALUES (?, 'site', ?, 'high_risk_escape', 'none', ?, 900, ?, ?, 'active')
                    """,
                    (
                        cursor.lastrowid,
                        rule_target,
                        "declared use",
                        now.isoformat(),
                        (now + timedelta(minutes=15)).isoformat(),
                    ),
                )
            if task_allowed_url is not None:
                connection.execute(
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
                    VALUES (
                        'Task URL',
                        '',
                        ?,
                        'normal',
                        0,
                        ?,
                        ?,
                        ?,
                        ?,
                        'planned'
                    )
                    """,
                    (
                        task_status,
                        task_allowed_url,
                        now.isoformat(),
                        now.isoformat(),
                        day["day"],
                    ),
                )


def _evaluate(tmp_path, monkeypatch, url: str, **seed_kwargs):
    app_home = tmp_path / "app-home"
    return _evaluate_in_home(app_home, monkeypatch, url, **seed_kwargs)


def _evaluate_in_home(app_home, monkeypatch, url: str, **seed_kwargs):
    db_path = app_home / "data" / "selfboss.db"
    _seed_state(db_path, **seed_kwargs)
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))
    return evaluate_url_read_only(
        {"type": "evaluate_url", "url": url},
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc),
    )


def _evaluate_existing_home(app_home, monkeypatch, url: str):
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))
    return evaluate_url_read_only(
        {"type": "evaluate_url", "url": url},
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc),
    )


def _recent_attempts(app_home):
    with initialize_database(app_home / "data" / "selfboss.db") as connection:
        return connection.execute(
            """
            SELECT *
            FROM access_attempts
            ORDER BY id DESC
            """
        ).fetchall()


def _snapshot(tmp_path, monkeypatch, **seed_kwargs):
    app_home = tmp_path / "app-home"
    db_path = app_home / "data" / "selfboss.db"
    _seed_state(db_path, **seed_kwargs)
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))
    return read_blocked_domains_snapshot(
        {"type": "get_blocked_domains_snapshot"},
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc),
    )


def _write_trusted_heartbeat(app_home, *, age_seconds: int = 30) -> None:
    heartbeat_path = app_home / "data" / HEARTBEAT_FILE_NAME
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_at = datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc) - timedelta(
        seconds=age_seconds
    )
    heartbeat_path.write_text(
        (
            "{"
            '"app":"SelfBoss",'
            '"protocol_version":1,'
            '"browser":"chrome",'
            '"extension_connected":true,'
            '"browser_blocking":"active",'
            '"incognito_allowed":true,'
            f'"last_heartbeat_at":"{heartbeat_at.isoformat()}",'
            '"source":"native_host"'
            "}"
        ),
        encoding="utf-8",
    )


def test_classify_browser_url_identifies_initial_url_families() -> None:
    assert (
        classify_browser_url("https://youtube.com/shorts/abc")["path_kind"]
        == "youtube_shorts"
    )
    assert (
        classify_browser_url("https://www.youtube.com/shorts/abc")["path_kind"]
        == "youtube_shorts"
    )
    mobile_shorts = classify_browser_url("https://m.youtube.com/shorts/abc")
    assert mobile_shorts["url_family"] == "youtube"
    assert mobile_shorts["path_kind"] == "youtube_shorts"
    assert (
        classify_browser_url("https://youtube.com/watch?v=abc")["path_kind"]
        == "youtube_watch"
    )
    assert classify_browser_url("https://old.reddit.com/r/test")["url_family"] == "reddit"
    assert (
        classify_browser_url("https://example.com/path")["url_family"]
        == "generic_site"
    )
    assert classify_browser_url("chrome://extensions")["url_family"] == "unsupported"
    assert classify_browser_url("not a url")["path_kind"] == "unsupported"


def test_evaluate_url_blocks_matching_site_rule_in_low(tmp_path, monkeypatch) -> None:
    response = _evaluate(tmp_path, monkeypatch, "https://www.reddit.com/")

    assert response["decision"] == "block"
    assert response["reason"] == "Matching site rule is blocked at current access level."
    assert response["access_level"] == "low"
    assert response["enforcement_mode"] == "full_enforcement"
    assert response["browser_blocking"] == "active"


def test_blocked_browser_domain_evaluation_logs_minimal_attempt(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"

    response = _evaluate_in_home(
        app_home,
        monkeypatch,
        "https://www.reddit.com/r/all/?private=query",
    )
    attempts = _recent_attempts(app_home)

    assert response["decision"] == "block"
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["source"] == "browser"
    assert attempt["target_type"] == "site"
    assert attempt["target"] == "www.reddit.com"
    assert attempt["target"] != "https://www.reddit.com/r/all/?private=query"
    assert attempt["matched_scope"] == "domain"
    assert attempt["matched_rule_target"] == "reddit.com"
    assert attempt["url_family"] == "reddit"
    assert attempt["path_kind"] == "reddit"
    assert attempt["reason_code"] == "domain_rule_blocked"
    assert attempt["decision"] == "would_block"
    assert attempt["action_taken"] == "browser_redirect"
    assert "private=query" not in " ".join(str(value) for value in attempt)


def test_evaluate_url_allows_in_high_or_with_matching_pass(tmp_path, monkeypatch) -> None:
    high_response = _evaluate(
        tmp_path / "high",
        monkeypatch,
        "https://reddit.com/",
        high_active=True,
    )
    pass_response = _evaluate(
        tmp_path / "pass",
        monkeypatch,
        "https://reddit.com/",
        planned_use_pass=True,
    )

    assert high_response["decision"] == "allow"
    assert high_response["reason"] == "Current access level allows this site rule."
    assert pass_response["decision"] == "allow"
    assert pass_response["reason"] == "Allowed by active planned-use pass."
    assert high_response["browser_blocking"] == "evaluation_only"
    assert pass_response["browser_blocking"] == "evaluation_only"


def test_evaluate_url_allows_exact_task_allowed_url_only(
    tmp_path,
    monkeypatch,
) -> None:
    allowed_response = _evaluate(
        tmp_path / "allowed",
        monkeypatch,
        "https://www.youtube.com/watch?v=abc123#section",
        rule_target="youtube.com",
        task_allowed_url="https://www.youtube.com/watch?v=abc123",
    )
    different_query_response = _evaluate(
        tmp_path / "query",
        monkeypatch,
        "https://www.youtube.com/watch?v=other",
        rule_target="youtube.com",
        task_allowed_url="https://www.youtube.com/watch?v=abc123",
    )
    different_path_response = _evaluate(
        tmp_path / "path",
        monkeypatch,
        "https://www.youtube.com/",
        rule_target="youtube.com",
        task_allowed_url="https://www.youtube.com/watch?v=abc123",
    )

    assert allowed_response["decision"] == "allow"
    assert allowed_response["reason_code"] == "task_allowed_url"
    assert allowed_response["matched_scope"] == "task_allowed_url"
    assert different_query_response["decision"] == "block"
    assert different_query_response["reason_code"] == "domain_rule_blocked"
    assert different_path_response["decision"] == "block"
    assert different_path_response["reason_code"] == "domain_rule_blocked"


def test_task_allowed_url_requires_active_pending_planned_task(
    tmp_path,
    monkeypatch,
) -> None:
    before_start_response = _evaluate(
        tmp_path / "inactive",
        monkeypatch,
        "https://www.youtube.com/watch?v=abc123",
        rule_target="youtube.com",
        task_allowed_url="https://www.youtube.com/watch?v=abc123",
        day_active=False,
    )
    completed_response = _evaluate(
        tmp_path / "completed",
        monkeypatch,
        "https://www.youtube.com/watch?v=abc123",
        rule_target="youtube.com",
        task_allowed_url="https://www.youtube.com/watch?v=abc123",
        task_status="done",
    )

    assert before_start_response["decision"] == "allow"
    assert before_start_response["reason_code"] == "inactive_day"
    assert completed_response["decision"] == "block"
    assert completed_response["reason_code"] == "domain_rule_blocked"


def test_browser_blocking_is_active_only_in_hosts_enforcing_modes(
    tmp_path,
    monkeypatch,
) -> None:
    hosts_response = _evaluate(
        tmp_path / "hosts",
        monkeypatch,
        "https://reddit.com/",
        enforcement_mode="real_hosts_blocking",
    )
    preview_response = _evaluate(
        tmp_path / "preview",
        monkeypatch,
        "https://reddit.com/",
        enforcement_mode="preview_only",
    )
    dry_run_response = _evaluate(
        tmp_path / "dry-run",
        monkeypatch,
        "https://reddit.com/",
        enforcement_mode="armed_dry_run",
    )
    process_response = _evaluate(
        tmp_path / "process",
        monkeypatch,
        "https://reddit.com/",
        enforcement_mode="real_process_blocking",
    )
    inactive_day_response = _evaluate(
        tmp_path / "inactive-day",
        monkeypatch,
        "https://reddit.com/",
        day_active=False,
    )

    assert hosts_response["decision"] == "block"
    assert hosts_response["browser_blocking"] == "active"
    assert preview_response["decision"] == "block"
    assert preview_response["browser_blocking"] == "evaluation_only"
    assert dry_run_response["decision"] == "block"
    assert dry_run_response["browser_blocking"] == "evaluation_only"
    assert process_response["decision"] == "block"
    assert process_response["browser_blocking"] == "evaluation_only"
    assert inactive_day_response["decision"] == "allow"
    assert inactive_day_response["reason"] == "LoopGuard day is not active."
    assert inactive_day_response["browser_blocking"] == "evaluation_only"


def test_unsupported_and_invalid_urls_are_safe() -> None:
    unsupported = evaluate_url_read_only({"type": "evaluate_url", "url": "chrome://settings"})
    invalid = evaluate_url_read_only({"type": "evaluate_url", "url": "not a url"})

    assert unsupported["decision"] == "allow"
    assert "Unsupported browser URL scheme" in unsupported["reason"]
    assert invalid["decision"] == "unknown"
    assert "scheme" in invalid["reason"]
    assert unsupported["url_family"] == "unsupported"
    assert invalid["path_kind"] == "unsupported"


def test_youtube_shorts_without_matching_rule_is_not_hardcoded(
    tmp_path,
    monkeypatch,
) -> None:
    response = _evaluate(tmp_path, monkeypatch, "https://www.youtube.com/shorts/abc")

    assert response["decision"] == "allow"
    assert response["browser_blocking"] == "evaluation_only"
    assert response["url_family"] == "youtube"
    assert response["path_kind"] == "youtube_shorts"
    assert response["matched_scope"] == "none"
    assert response["reason_code"] == "no_matching_rule"


def test_youtube_shorts_pattern_rule_blocks_low_and_allows_high(
    tmp_path,
    monkeypatch,
) -> None:
    exact_response = _evaluate(
        tmp_path / "exact",
        monkeypatch,
        "https://youtube.com/shorts",
        rule_target="youtube.com/shorts/*",
    )
    low_response = _evaluate(
        tmp_path / "low",
        monkeypatch,
        "https://youtube.com/shorts/abc",
        rule_target="youtube.com/shorts/*",
    )
    www_response = _evaluate(
        tmp_path / "www",
        monkeypatch,
        "https://www.youtube.com/shorts/abc",
        rule_target="youtube.com/shorts/*",
    )
    high_response = _evaluate(
        tmp_path / "high",
        monkeypatch,
        "https://youtube.com/shorts/abc",
        rule_target="youtube.com/shorts/*",
        high_active=True,
    )

    assert exact_response["decision"] == "block"
    assert exact_response["matched_scope"] == "path"
    assert low_response["decision"] == "block"
    assert low_response["browser_blocking"] == "active"
    assert low_response["matched_scope"] == "path"
    assert low_response["reason_code"] == "path_rule_blocked"
    assert www_response["decision"] == "block"
    assert www_response["matched_scope"] == "path"
    assert high_response["decision"] == "allow"
    assert high_response["matched_scope"] == "path"
    assert high_response["reason_code"] == "access_level_allowed"


def test_raw_prefix_path_rule_from_existing_data_still_matches_www_alias(
    tmp_path,
    monkeypatch,
) -> None:
    response = _evaluate(
        tmp_path,
        monkeypatch,
        "https://www.youtube.com/shorts/abc",
        rule_target="youtube.com/shorts",
    )

    assert response["decision"] == "block"
    assert response["matched_scope"] == "path"
    assert response["reason_code"] == "path_rule_blocked"


def test_blocked_browser_path_rule_logs_path_metadata_without_full_url(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"

    response = _evaluate_in_home(
        app_home,
        monkeypatch,
        "https://youtube.com/shorts/abc?watch=secret",
        rule_target="youtube.com/shorts/*",
    )
    attempts = _recent_attempts(app_home)

    assert response["decision"] == "block"
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["target"] == "youtube.com"
    assert attempt["matched_scope"] == "path"
    assert attempt["matched_rule_target"] == "youtube.com/shorts/*"
    assert attempt["url_family"] == "youtube"
    assert attempt["path_kind"] == "youtube_shorts"
    assert attempt["reason_code"] == "path_rule_blocked"
    assert "watch=secret" not in " ".join(str(value) for value in attempt)


def test_youtube_shorts_armed_dry_run_is_evaluation_only(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"

    response = _evaluate_in_home(
        app_home,
        monkeypatch,
        "https://m.youtube.com/shorts/abc",
        enforcement_mode="armed_dry_run",
        rule_target="m.youtube.com/shorts/*",
    )
    attempts = _recent_attempts(app_home)

    assert response["decision"] == "block"
    assert response["browser_blocking"] == "evaluation_only"
    assert response["path_kind"] == "youtube_shorts"
    assert response["matched_scope"] == "path"
    assert response["reason_code"] == "path_rule_blocked"
    assert len(attempts) == 1
    assert attempts[0]["action_taken"] == "evaluation_only"


def test_generic_browser_path_rule_matches_reddit_path(
    tmp_path,
    monkeypatch,
) -> None:
    exact_response = _evaluate(
        tmp_path / "exact",
        monkeypatch,
        "https://reddit.com/r/all",
        rule_target="reddit.com/r/all/*",
    )
    www_response = _evaluate(
        tmp_path / "www",
        monkeypatch,
        "https://www.reddit.com/r/all/",
        rule_target="reddit.com/r/all/*",
    )

    assert exact_response["decision"] == "block"
    assert exact_response["matched_scope"] == "path"
    assert www_response["decision"] == "block"
    assert www_response["matched_scope"] == "path"
    assert www_response["reason_code"] == "path_rule_blocked"


def test_wildcard_host_browser_path_rule_matches_subdomains_only(
    tmp_path,
    monkeypatch,
) -> None:
    subdomain_response = _evaluate(
        tmp_path / "subdomain",
        monkeypatch,
        "https://sub.example.com/feed/x",
        rule_target="*.example.com/feed/*",
    )
    root_response = _evaluate(
        tmp_path / "root",
        monkeypatch,
        "https://example.com/feed/x",
        rule_target="*.example.com/feed/*",
    )
    sibling_response = _evaluate(
        tmp_path / "sibling",
        monkeypatch,
        "https://badexample.com/feed/x",
        rule_target="*.example.com/feed/*",
    )

    assert subdomain_response["decision"] == "block"
    assert subdomain_response["matched_scope"] == "path"
    assert root_response["decision"] == "allow"
    assert root_response["reason_code"] == "no_matching_rule"
    assert sibling_response["decision"] == "allow"
    assert sibling_response["reason_code"] == "no_matching_rule"


def test_bare_path_rule_does_not_match_mobile_youtube_without_explicit_rule(
    tmp_path,
    monkeypatch,
) -> None:
    response = _evaluate(
        tmp_path,
        monkeypatch,
        "https://m.youtube.com/shorts/abc",
        rule_target="youtube.com/shorts/*",
    )

    assert response["decision"] == "allow"
    assert response["reason_code"] == "no_matching_rule"


def test_duplicate_browser_route_change_evaluations_are_rate_limited(
    tmp_path,
    monkeypatch,
) -> None:
    app_home = tmp_path / "app-home"
    _seed_state(
        app_home / "data" / "selfboss.db",
        rule_target="youtube.com/shorts/*",
    )

    first = _evaluate_existing_home(
        app_home,
        monkeypatch,
        "https://youtube.com/shorts/abc",
    )
    second = _evaluate_existing_home(
        app_home,
        monkeypatch,
        "https://youtube.com/shorts/abc",
    )
    attempts = _recent_attempts(app_home)

    assert first["decision"] == "block"
    assert second["decision"] == "block"
    assert len(attempts) == 1
    assert attempts[0]["matched_rule_target"] == "youtube.com/shorts/*"


def test_allowed_high_and_pass_browser_attempts_are_rate_limited(
    tmp_path,
    monkeypatch,
) -> None:
    high_home = tmp_path / "high" / "app-home"
    _seed_state(
        high_home / "data" / "selfboss.db",
        rule_target="youtube.com/shorts/*",
        high_active=True,
    )
    _evaluate_existing_home(high_home, monkeypatch, "https://youtube.com/shorts/abc")
    _evaluate_existing_home(high_home, monkeypatch, "https://youtube.com/shorts/abc")
    high_attempts = _recent_attempts(high_home)

    pass_home = tmp_path / "pass" / "app-home"
    _seed_state(
        pass_home / "data" / "selfboss.db",
        rule_target="reddit.com/r/all/*",
        planned_use_pass=True,
    )
    _evaluate_existing_home(pass_home, monkeypatch, "https://reddit.com/r/all/")
    _evaluate_existing_home(pass_home, monkeypatch, "https://reddit.com/r/all/")
    pass_attempts = _recent_attempts(pass_home)

    assert len(high_attempts) == 1
    assert high_attempts[0]["decision"] == "would_allow"
    assert high_attempts[0]["action_taken"] == "allowed"
    assert len(pass_attempts) == 1
    assert pass_attempts[0]["decision"] == "allowed_by_planned_use_pass"
    assert pass_attempts[0]["action_taken"] == "allowed"


def test_strictest_matching_site_rule_wins(
    tmp_path,
    monkeypatch,
) -> None:
    path_stricter_response = _evaluate(
        tmp_path / "path-stricter",
        monkeypatch,
        "https://youtube.com/shorts/abc",
        rule_target="youtube.com",
        allow_from_level="low",
        extra_rules=(("youtube.com/shorts/*", "high"),),
    )
    domain_stricter_response = _evaluate(
        tmp_path / "domain-stricter",
        monkeypatch,
        "https://youtube.com/shorts/abc",
        rule_target="youtube.com",
        allow_from_level="high",
        extra_rules=(("youtube.com/shorts/*", "low"),),
    )

    assert path_stricter_response["decision"] == "block"
    assert path_stricter_response["matched_scope"] == "path"
    assert path_stricter_response["reason_code"] == "path_rule_blocked"
    assert domain_stricter_response["decision"] == "block"
    assert domain_stricter_response["matched_scope"] == "domain"
    assert domain_stricter_response["reason_code"] == "domain_rule_blocked"


def test_missing_database_returns_unknown_not_implemented(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SELF_BOSS_HOME", str(tmp_path / "missing-home"))

    response = evaluate_url_read_only({"type": "evaluate_url", "url": "https://reddit.com/"})

    assert response["decision"] == "unknown"
    assert response["reason"] == "LoopGuard database not found."
    assert response["browser_blocking"] == "not_implemented"


def test_exact_subdomain_matching_stays_narrow(tmp_path, monkeypatch) -> None:
    www_response = _evaluate(
        tmp_path / "www",
        monkeypatch,
        "https://www.reddit.com/",
        rule_target="reddit.com",
    )
    old_response = _evaluate(
        tmp_path / "old",
        monkeypatch,
        "https://www.old.reddit.com/",
        rule_target="old.reddit.com",
    )
    exact_old_response = _evaluate(
        tmp_path / "exact-old",
        monkeypatch,
        "https://old.reddit.com/",
        rule_target="old.reddit.com",
    )

    assert www_response["decision"] == "block"
    assert old_response["decision"] == "allow"
    assert old_response["reason"] == "No matching enabled site rule."
    assert exact_old_response["decision"] == "block"


def test_safe_mode_allows_without_mutation(tmp_path, monkeypatch) -> None:
    app_home = tmp_path / "app-home"
    db_path = app_home / "data" / "selfboss.db"
    _seed_state(db_path)
    monkeypatch.setenv("SELF_BOSS_HOME", str(app_home))
    monkeypatch.setenv("SELF_BOSS_SAFE_MODE", "1")

    response = evaluate_url_read_only({"type": "evaluate_url", "url": "https://reddit.com/"})

    assert response["decision"] == "allow"
    assert response["reason"] == "Safe Mode is active."
    assert response["enforcement_mode"] == "preview_only"
    assert response["browser_blocking"] == "evaluation_only"


def test_evaluation_response_filters_private_fields() -> None:
    response = build_url_evaluation_response(
        {"type": "evaluate_url", "url": "https://reddit.com/"},
        lambda message: {
            "decision": "block",
            "reason": "test",
            "access_level": "low",
            "enforcement_mode": "full_enforcement",
            "tasks": ["private"],
            "rules": ["private"],
            "reward_ledger": ["private"],
            "cookies": ["private"],
            "tabs": ["private"],
            "tab_list": ["private"],
            "page_contents": ["private"],
            "browser_blocking": "active",
            "url_family": "youtube",
            "path_kind": "youtube_shorts",
            "matched_scope": "path",
            "reason_code": "path_rule_blocked",
        },
    )

    assert response["decision"] == "block"
    assert response["browser_blocking"] == "active"
    assert response["url_family"] == "youtube"
    assert response["path_kind"] == "youtube_shorts"
    assert response["matched_scope"] == "path"
    assert response["reason_code"] == "path_rule_blocked"
    assert not (PRIVATE_FIELDS & set(response))


def test_evaluation_response_rejects_unknown_browser_blocking_value() -> None:
    response = build_url_evaluation_response(
        {"type": "evaluate_url", "url": "https://reddit.com/"},
        lambda message: {
            "decision": "block",
            "reason": "test",
            "access_level": "low",
            "enforcement_mode": "full_enforcement",
            "browser_blocking": "attempted_override",
        },
    )

    assert response["browser_blocking"] == "evaluation_only"


def test_snapshot_dispatch_returns_privacy_minimal_shape() -> None:
    response = dispatch_message(
        {"type": "get_blocked_domains_snapshot", "browser": "chrome"},
        snapshot_provider=lambda message: {
            "enforcement_mode": "full_enforcement",
            "browser_blocking": "active",
            "domains": ["reddit.com"],
            "allowed_urls": [
                "HTTPS://WWW.YOUTUBE.COM:443/watch?v=abc123#private-fragment",
                "chrome://extensions",
            ],
            "reason": "test",
            "tasks": ["private"],
            "rules": ["private"],
            "reward_history": ["private"],
            "url": "https://private.example/",
            "cookies": ["private"],
            "title": "private",
        },
    )

    assert response["ok"] is True
    assert response["domains"] == ["reddit.com"]
    assert response["allowed_urls"] == ["https://www.youtube.com/watch?v=abc123"]
    assert response["browser_blocking"] == "active"
    assert not (PRIVATE_FIELDS & set(response))


def test_snapshot_response_rejects_private_or_invalid_provider_fields() -> None:
    response = build_blocked_domains_snapshot_response(
        {"type": "get_blocked_domains_snapshot"},
        lambda message: {
            "domains": "reddit.com",
            "allowed_urls": "https://youtube.com/watch?v=abc123",
            "browser_blocking": "attempted_override",
            "tasks": ["private"],
            "rules": ["private"],
            "browsing_history": ["private"],
        },
    )

    assert response["domains"] == []
    assert response["allowed_urls"] == []
    assert response["browser_blocking"] == "evaluation_only"
    assert not (PRIVATE_FIELDS & set(response))


def test_snapshot_returns_domains_for_hosts_enforcing_active_day(
    tmp_path,
    monkeypatch,
) -> None:
    full_response = _snapshot(tmp_path / "full", monkeypatch)
    hosts_response = _snapshot(
        tmp_path / "hosts",
        monkeypatch,
        enforcement_mode="real_hosts_blocking",
    )

    assert full_response["browser_blocking"] == "active"
    assert full_response["domains"] == ["reddit.com", "www.reddit.com"]
    assert hosts_response["browser_blocking"] == "active"
    assert hosts_response["domains"] == ["reddit.com", "www.reddit.com"]


def test_snapshot_includes_eligible_task_allowed_urls(
    tmp_path,
    monkeypatch,
) -> None:
    response = _snapshot(
        tmp_path,
        monkeypatch,
        rule_target="youtube.com",
        task_allowed_url="HTTPS://WWW.YOUTUBE.COM:443/watch?v=abc123#frag",
    )

    assert response["browser_blocking"] == "active"
    assert response["domains"] == ["youtube.com", "www.youtube.com"]
    assert response["allowed_urls"] == ["https://www.youtube.com/watch?v=abc123"]


def test_browser_extension_dnr_builds_exact_allow_rules_before_domain_blocks() -> None:
    background = Path("browser_extension/chrome_mv3/background.js").read_text(
        encoding="utf-8"
    )

    assert 'action: { type: "allow" }' in background
    assert "priority: 2" in background
    assert "regexFilter: `^${escapeRegex(url)}$`" in background
    assert 'action: { type: "block" }' in background
    assert "priority: 1" in background


def test_snapshot_excludes_browser_path_pattern_targets(
    tmp_path,
    monkeypatch,
) -> None:
    response = _snapshot(
        tmp_path,
        monkeypatch,
        rule_target="youtube.com/shorts/*",
    )

    assert response["browser_blocking"] == "active"
    assert response["domains"] == []


def test_snapshot_empty_in_non_browser_blocking_modes(tmp_path, monkeypatch) -> None:
    preview_response = _snapshot(
        tmp_path / "preview",
        monkeypatch,
        enforcement_mode="preview_only",
    )
    dry_run_response = _snapshot(
        tmp_path / "dry-run",
        monkeypatch,
        enforcement_mode="armed_dry_run",
    )
    process_response = _snapshot(
        tmp_path / "process",
        monkeypatch,
        enforcement_mode="real_process_blocking",
    )
    inactive_response = _snapshot(
        tmp_path / "inactive",
        monkeypatch,
        day_active=False,
    )

    assert preview_response["domains"] == []
    assert preview_response["browser_blocking"] == "evaluation_only"
    assert dry_run_response["domains"] == []
    assert process_response["domains"] == []
    assert inactive_response["domains"] == []


def test_snapshot_empty_in_safe_or_recovery_mode(tmp_path, monkeypatch) -> None:
    safe_home = tmp_path / "safe" / "app-home"
    _seed_state(safe_home / "data" / "selfboss.db")
    monkeypatch.setenv("SELF_BOSS_HOME", str(safe_home))
    monkeypatch.setenv("SELF_BOSS_SAFE_MODE", "1")

    safe_response = read_blocked_domains_snapshot(
        {"type": "get_blocked_domains_snapshot"}
    )
    monkeypatch.delenv("SELF_BOSS_SAFE_MODE")
    monkeypatch.setenv("SELF_BOSS_RECOVERY_MODE", "1")
    recovery_response = read_blocked_domains_snapshot(
        {"type": "get_blocked_domains_snapshot"}
    )

    assert safe_response["domains"] == []
    assert safe_response["browser_blocking"] == "evaluation_only"
    assert recovery_response["domains"] == []
    assert recovery_response["browser_blocking"] == "evaluation_only"


def test_snapshot_missing_database_returns_empty_not_implemented(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SELF_BOSS_HOME", str(tmp_path / "missing-home"))

    response = read_blocked_domains_snapshot({"type": "get_blocked_domains_snapshot"})

    assert response["domains"] == []
    assert response["browser_blocking"] == "not_implemented"
    assert "status_error" in response


def test_snapshot_high_release_requires_trusted_heartbeat(
    tmp_path,
    monkeypatch,
) -> None:
    untrusted_response = _snapshot(
        tmp_path / "untrusted",
        monkeypatch,
        high_active=True,
    )
    trusted_home = tmp_path / "trusted" / "app-home"
    _seed_state(trusted_home / "data" / "selfboss.db", high_active=True)
    _write_trusted_heartbeat(trusted_home)
    monkeypatch.setenv("SELF_BOSS_HOME", str(trusted_home))

    trusted_response = read_blocked_domains_snapshot(
        {"type": "get_blocked_domains_snapshot"},
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc),
    )

    assert untrusted_response["domains"] == ["reddit.com", "www.reddit.com"]
    assert trusted_response["domains"] == []
    assert trusted_response["browser_blocking"] == "active"


def test_snapshot_planned_use_pass_release_requires_trusted_heartbeat(
    tmp_path,
    monkeypatch,
) -> None:
    untrusted_response = _snapshot(
        tmp_path / "untrusted-pass",
        monkeypatch,
        planned_use_pass=True,
    )
    trusted_home = tmp_path / "trusted-pass" / "app-home"
    _seed_state(trusted_home / "data" / "selfboss.db", planned_use_pass=True)
    _write_trusted_heartbeat(trusted_home)
    monkeypatch.setenv("SELF_BOSS_HOME", str(trusted_home))

    trusted_response = read_blocked_domains_snapshot(
        {"type": "get_blocked_domains_snapshot"},
        now_provider=lambda: datetime(2026, 5, 19, 8, 5, tzinfo=timezone.utc),
    )

    assert untrusted_response["domains"] == ["reddit.com", "www.reddit.com"]
    assert trusted_response["domains"] == []
