from __future__ import annotations

import fnmatch
import json
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, TextIO
from urllib.parse import urlparse, urlunparse

from selfboss.config import load_settings
from selfboss.data.db import initialize_database
from selfboss.data.repositories import AccessAttemptRepository

from .protocol import (
    BROWSER_BLOCKING_STATES,
    HeartbeatWriter,
    NativeMessageError,
    OpenSelfBossHandler,
    PROTOCOL_VERSION,
    SnapshotProvider,
    StatusProvider,
    UrlEvaluator,
    build_error_response,
    dispatch_message,
    encode_native_message,
    read_native_message,
)


VALID_ENFORCEMENT_MODES = {
    "preview_only",
    "armed_dry_run",
    "real_process_blocking",
    "real_hosts_blocking",
    "full_enforcement",
}
VALID_ACCESS_LEVELS = {"low", "medium", "high"}
ACCESS_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}
SUPPORTED_URL_SCHEMES = {"http", "https"}
BROWSER_BLOCKING_MODES = {"real_hosts_blocking", "full_enforcement"}
HEARTBEAT_FILE_NAME = "browser_heartbeat.json"
HEARTBEAT_ALLOWED_INCOGNITO_VALUES = {True, False, "unknown"}
BROWSER_HEARTBEAT_STALE_SECONDS = 120
BROWSER_ATTEMPT_RATE_LIMIT_SECONDS = 60
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com"}


def run_host(
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    error_stream: TextIO | None = None,
    status_provider: StatusProvider | None = None,
    snapshot_provider: SnapshotProvider | None = None,
    url_evaluator: UrlEvaluator | None = None,
    heartbeat_writer: HeartbeatWriter | None = None,
    open_selfboss_handler: OpenSelfBossHandler | None = None,
) -> int:
    """Run the Native Messaging stdio loop until EOF."""
    input_stream = input_stream or sys.stdin.buffer
    output_stream = output_stream or sys.stdout.buffer
    error_stream = error_stream or sys.stderr
    status_provider = status_provider or read_selfboss_status
    snapshot_provider = snapshot_provider or read_blocked_domains_snapshot
    url_evaluator = url_evaluator or evaluate_url_read_only
    heartbeat_writer = heartbeat_writer or write_browser_heartbeat
    open_selfboss_handler = open_selfboss_handler or open_selfboss_window

    while True:
        try:
            message = read_native_message(input_stream)
        except NativeMessageError as exc:
            error_stream.write(f"LoopGuard native host protocol error: {exc}\n")
            output_stream.write(encode_native_message(build_error_response(str(exc))))
            output_stream.flush()
            continue

        if message is None:
            return 0

        response = dispatch_message(
            message,
            status_provider=status_provider,
            snapshot_provider=snapshot_provider,
            url_evaluator=url_evaluator,
            heartbeat_writer=heartbeat_writer,
            open_selfboss_handler=open_selfboss_handler,
        )
        output_stream.write(encode_native_message(response))
        output_stream.flush()


def read_selfboss_status(
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Return minimal LoopGuard state without creating or mutating app data."""
    now_provider = now_provider or _utc_now
    settings = load_settings(create_dirs=False)
    base_status: dict[str, Any] = {
        "app": "SelfBoss",
        "native_host": "connected",
        "enforcement_mode": "unknown",
        "access_level": "unknown",
        "day_active": "unknown",
        "high_active": "unknown",
        "high_remaining_seconds": "unknown",
        "safe_mode_active": settings.safe_mode,
        "recovery_mode_active": settings.recovery_mode,
        "browser_blocking": "not_implemented",
    }

    db_path = settings.db_path.expanduser()
    if not db_path.exists():
        return {
            **base_status,
            "status_error": "LoopGuard database not found.",
        }

    try:
        with _connect_read_only(db_path) as connection:
            day = connection.execute(
                """
                SELECT day, day_started_at, day_ended_at, access_level
                FROM day_state
                WHERE id = 1
                """
            ).fetchone()
            if day is None:
                return {
                    **base_status,
                    "status_error": "LoopGuard day state is unavailable.",
                }

            selected_mode = _read_enforcement_mode(connection)
            enforcement_mode = (
                "preview_only"
                if settings.safe_mode or settings.recovery_mode
                else selected_mode
            )
            access_level = _normalize_access_level(day["access_level"])
            high_status = _read_high_status(
                connection,
                day["day"],
                now_provider(),
            )
            return {
                **base_status,
                "enforcement_mode": enforcement_mode,
                "access_level": access_level,
                "day_active": (
                    day["day_started_at"] is not None
                    and day["day_ended_at"] is None
                ),
                "high_active": high_status["active"],
                "high_remaining_seconds": high_status["remaining_seconds"],
            }
    except Exception as exc:  # noqa: BLE001 - status must degrade safely
        return {
            **base_status,
            "status_error": _compact_error(exc),
        }


def evaluate_url_read_only(
    message: dict[str, Any],
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Evaluate one URL against minimal LoopGuard rule state without mutation."""
    now_provider = now_provider or _utc_now
    classification = classify_browser_url(message.get("url"))
    metadata = _classification_metadata(classification)
    parsed_url = _parse_evaluation_url(message.get("url"))
    if not parsed_url["valid"]:
        return {
            "decision": "unknown",
            "reason": parsed_url["reason"],
            "access_level": "unknown",
            "enforcement_mode": "unknown",
            "browser_blocking": "evaluation_only",
            "reason_code": "invalid_url",
            **metadata,
        }
    if parsed_url["unsupported"]:
        return {
            "decision": "allow",
            "reason": parsed_url["reason"],
            "access_level": "unknown",
            "enforcement_mode": "unknown",
            "browser_blocking": "evaluation_only",
            "reason_code": "unsupported_scheme",
            **metadata,
        }

    hostname = parsed_url["hostname"]
    settings = load_settings(create_dirs=False)
    base_response = {
        "decision": "unknown",
        "reason": "LoopGuard state is unavailable.",
        "access_level": "unknown",
        "enforcement_mode": "preview_only"
        if settings.safe_mode or settings.recovery_mode
        else "unknown",
        "browser_blocking": "not_implemented",
        "reason_code": "state_unavailable",
        **metadata,
    }

    db_path = settings.db_path.expanduser()
    if not db_path.exists():
        return {**base_response, "reason": "LoopGuard database not found."}

    try:
        now = now_provider()
        with _connect_read_only(db_path) as connection:
            day = _read_day_state(connection)
            if day is None:
                return {**base_response, "reason": "LoopGuard day state is unavailable."}

            selected_mode = _read_enforcement_mode(connection)
            effective_mode = (
                "preview_only"
                if settings.safe_mode or settings.recovery_mode
                else selected_mode
            )
            day_active = _is_day_active(day)
            access_level = _effective_access_level(day)
            high_status = _read_high_status(connection, day["day"], now)
            if high_status["active"]:
                access_level = "high"

            response_base = {
                **base_response,
                "access_level": access_level,
                "enforcement_mode": effective_mode,
                "browser_blocking": "evaluation_only",
            }
            if settings.safe_mode:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "Safe Mode is active.",
                    "reason_code": "safe_mode",
                }
            if settings.recovery_mode:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "Recovery Mode is active.",
                    "reason_code": "recovery_mode",
                }
            if not day_active:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "LoopGuard day is not active.",
                    "reason_code": "inactive_day",
                }
            if day["surrender_requested_at"] is not None:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "Surrender is active.",
                    "reason_code": "surrender",
                }
            allowed_task = _find_matching_task_allowed_url(
                connection,
                day["day"],
                str(parsed_url["canonical_url"]),
            )
            if allowed_task is not None:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "Allowed by exact planned task URL.",
                    "matched_scope": "task_allowed_url",
                    "reason_code": "task_allowed_url",
                }

            matching_rule = _find_strictest_matching_site_rule(
                connection,
                hostname,
                parsed_url["path"],
            )
            if matching_rule is None:
                return {
                    **response_base,
                    "decision": "allow",
                    "reason": "No matching enabled site rule.",
                    "reason_code": "no_matching_rule",
                }
            if _active_pass_matches_rule(
                connection,
                int(matching_rule["id"]),
                now,
            ):
                result = {
                    **response_base,
                    "decision": "allow",
                    "reason": "Allowed by active planned-use pass.",
                    "matched_scope": matching_rule["matched_scope"],
                    "reason_code": "active_planned_use_pass",
                }
                return _with_browser_attempt_logging(
                    result,
                    db_path=db_path,
                    hostname=hostname,
                    matching_rule=matching_rule,
                    now=now,
                )
            if _is_allowed_at(access_level, str(matching_rule["allow_from_level"])):
                result = {
                    **response_base,
                    "decision": "allow",
                    "reason": "Current access level allows this site rule.",
                    "matched_scope": matching_rule["matched_scope"],
                    "reason_code": "access_level_allowed",
                }
                return _with_browser_attempt_logging(
                    result,
                    db_path=db_path,
                    hostname=hostname,
                    matching_rule=matching_rule,
                    now=now,
                )
            matched_scope = str(matching_rule["matched_scope"])
            result = {
                **response_base,
                "decision": "block",
                "reason": (
                    "Matching browser path rule is blocked at current access level."
                    if matched_scope == "path"
                    else "Matching site rule is blocked at current access level."
                ),
                "matched_scope": matched_scope,
                "reason_code": (
                    "path_rule_blocked"
                    if matched_scope == "path"
                    else "domain_rule_blocked"
                ),
                "browser_blocking": _browser_blocking_for_block_decision(
                    effective_mode,
                    day_active,
                ),
            }
            return _with_browser_attempt_logging(
                result,
                db_path=db_path,
                hostname=hostname,
                matching_rule=matching_rule,
                now=now,
            )
    except Exception as exc:  # noqa: BLE001 - evaluation must degrade safely
        return {**base_response, "reason": _compact_error(exc)}


def read_blocked_domains_snapshot(
    message: dict[str, Any] | None = None,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Return currently blocked site domains for browser DNR without mutation."""
    _ = message
    now_provider = now_provider or _utc_now
    settings = load_settings(create_dirs=False)
    base_response: dict[str, Any] = {
        "enforcement_mode": "preview_only"
        if settings.safe_mode or settings.recovery_mode
        else "unknown",
        "browser_blocking": "evaluation_only",
        "domains": [],
        "allowed_urls": [],
        "reason": "LoopGuard state is unavailable.",
    }

    db_path = settings.db_path.expanduser()
    if not db_path.exists():
        return {
            **base_response,
            "browser_blocking": "not_implemented",
            "reason": "LoopGuard database not found.",
            "status_error": "LoopGuard database not found.",
        }

    try:
        now = now_provider()
        with _connect_read_only(db_path) as connection:
            day = _read_day_state(connection)
            if day is None:
                return {
                    **base_response,
                    "browser_blocking": "not_implemented",
                    "reason": "LoopGuard day state is unavailable.",
                    "status_error": "LoopGuard day state is unavailable.",
                }

            selected_mode = _read_enforcement_mode(connection)
            effective_mode = (
                "preview_only"
                if settings.safe_mode or settings.recovery_mode
                else selected_mode
            )
            response_base = {
                **base_response,
                "enforcement_mode": effective_mode,
            }
            if settings.safe_mode:
                return {**response_base, "reason": "Safe Mode is active."}
            if settings.recovery_mode:
                return {**response_base, "reason": "Recovery Mode is active."}
            if effective_mode not in BROWSER_BLOCKING_MODES:
                return {
                    **response_base,
                    "reason": "Browser blocking mode is not active.",
                }

            day_active = _is_day_active(day)
            if not day_active:
                return {**response_base, "reason": "LoopGuard day is not active."}
            if day["surrender_requested_at"] is not None:
                return {**response_base, "reason": "Surrender is active."}

            base_access_level = _effective_access_level(day)
            access_level = base_access_level
            high_status = _read_high_status(connection, day["day"], now)
            if high_status["active"]:
                access_level = "high"
            trusted_browser_ready = _trusted_browser_control_ready(settings.data_dir, now)
            domains = _blocked_site_domains_for_snapshot(
                connection,
                access_level=access_level,
                base_access_level=base_access_level,
                high_active=bool(high_status["active"]),
                trusted_browser_ready=trusted_browser_ready,
                now=now,
            )
            allowed_urls = _eligible_task_allowed_urls_for_snapshot(
                connection,
                day["day"],
            )
            return {
                **response_base,
                "browser_blocking": "active",
                "domains": domains,
                "allowed_urls": allowed_urls,
                "reason": (
                    "Blocked-domain snapshot is active."
                    if domains or allowed_urls
                    else "No blocked site domains for current state."
                ),
            }
    except Exception as exc:  # noqa: BLE001 - snapshot must degrade safely
        return {
            **base_response,
            "browser_blocking": "not_implemented",
            "reason": "Blocked-domain snapshot is unavailable.",
            "status_error": _compact_error(exc),
        }


def write_browser_heartbeat(message: dict[str, Any]) -> dict[str, Any]:
    """Persist a minimal browser integration heartbeat for desktop status UI."""
    try:
        settings = load_settings(create_dirs=True)
        heartbeat_path = settings.data_dir / HEARTBEAT_FILE_NAME
        heartbeat_time = _utc_now().isoformat()
        heartbeat = {
            "app": "SelfBoss",
            "protocol_version": PROTOCOL_VERSION,
            "browser": _normalize_browser_name(message.get("browser")),
            "context": _normalize_extension_context(message.get("context")),
            "extension_connected": True,
            "browser_blocking": _normalize_browser_blocking(
                message.get("browser_blocking")
            ),
            "browser_blocking_available": _normalize_optional_bool(
                message.get("browser_blocking_available")
            ),
            "incognito_allowed": _normalize_incognito_allowed(
                message.get("incognito_allowed")
            ),
            "dnr_supported": _normalize_optional_bool(message.get("dnr_supported")),
            "dnr_session_rule_count": _normalize_dnr_rule_count(
                message.get("dnr_session_rule_count")
            ),
            "dnr_last_update_status": _normalize_compact_status(
                message.get("dnr_last_update_status"),
                allowed={
                    "unknown",
                    "unavailable",
                    "active",
                    "cleared",
                    "error",
                    "supported_no_rules",
                },
                default="unknown",
            ),
            "dnr_last_error": _normalize_compact_optional_text(
                message.get("dnr_last_error")
            ),
            "youtube_spa_content_script_seen": _normalize_optional_bool(
                message.get("youtube_spa_content_script_seen")
            ),
            "extension_version": _normalize_compact_optional_text(
                message.get("extension_version")
            ),
            "last_heartbeat_at": heartbeat_time,
            "last_seen": heartbeat_time,
            "source": "native_host",
        }
        heartbeat_path.write_text(
            json.dumps(heartbeat, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 - heartbeat failures must be protocol-safe
        return {
            "heartbeat_saved": False,
            "error": _compact_error(exc),
        }
    return {"heartbeat_saved": True}


def open_selfboss_window() -> dict[str, Any]:
    """Best-effort focus/show for an existing LoopGuard desktop window."""
    if sys.platform != "win32":
        return {
            "ok": False,
            "reason": "Open LoopGuard is only available on Windows.",
        }

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "LoopGuard")
        if not hwnd:
            return {
                "ok": False,
                "reason": "LoopGuard desktop window was not found.",
            }
        sw_restore = 9
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
    except Exception as exc:  # noqa: BLE001 - protocol response must stay compact
        return {
            "ok": False,
            "reason": _compact_error(exc),
        }

    return {
        "ok": True,
        "reason": "LoopGuard desktop window focus was requested.",
    }


def classify_browser_url(value: object) -> dict[str, str]:
    """Classify a browser URL without performing network or database work."""
    base = {
        "scheme": "",
        "hostname": "",
        "normalized_hostname": "",
        "path": "",
        "url_family": "unsupported",
        "path_kind": "unsupported",
    }
    if not isinstance(value, str) or not value.strip():
        return base
    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname or ""
    normalized_hostname = hostname.strip().lower().rstrip(".")
    path = parsed.path or ""
    result = {
        **base,
        "scheme": scheme,
        "hostname": hostname,
        "normalized_hostname": normalized_hostname,
        "path": path,
    }
    if scheme not in SUPPORTED_URL_SCHEMES or not normalized_hostname:
        return result
    if normalized_hostname in YOUTUBE_HOSTS:
        if path == "" or path == "/":
            return {**result, "url_family": "youtube", "path_kind": "youtube_home"}
        if path.startswith("/shorts"):
            return {**result, "url_family": "youtube", "path_kind": "youtube_shorts"}
        if path.startswith("/watch"):
            return {**result, "url_family": "youtube", "path_kind": "youtube_watch"}
        return {**result, "url_family": "youtube", "path_kind": "youtube_other"}
    if normalized_hostname in REDDIT_HOSTS:
        return {**result, "url_family": "reddit", "path_kind": "reddit"}
    return {**result, "url_family": "generic_site", "path_kind": "generic_site"}


def _classification_metadata(classification: dict[str, str]) -> dict[str, str]:
    return {
        "url_family": classification.get("url_family", "unknown"),
        "path_kind": classification.get("path_kind", "unknown"),
        "matched_scope": "none",
    }


def _parse_evaluation_url(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {
            "valid": False,
            "unsupported": False,
            "reason": "URL is required for evaluation.",
            "hostname": "",
        }
    parsed = urlparse(value.strip())
    scheme = parsed.scheme.lower()
    if not scheme:
        return {
            "valid": False,
            "unsupported": False,
            "reason": "URL must include a scheme.",
            "hostname": "",
        }
    if scheme not in SUPPORTED_URL_SCHEMES:
        return {
            "valid": True,
            "unsupported": True,
            "reason": f"Unsupported browser URL scheme: {scheme}.",
            "hostname": "",
        }
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return {
            "valid": False,
            "unsupported": False,
            "reason": "URL hostname is unavailable.",
            "hostname": "",
        }
    try:
        canonical_url = _canonical_task_allowed_url(value.strip())
    except ValueError:
        return {
            "valid": False,
            "unsupported": False,
            "reason": "URL is invalid.",
            "hostname": "",
        }
    return {
        "valid": True,
        "unsupported": False,
        "reason": "",
        "hostname": hostname,
        "path": (parsed.path or "/").lower(),
        "canonical_url": canonical_url,
    }


def _canonical_task_allowed_url(value: str) -> str:
    raw_value = value.strip()
    if any(character.isspace() for character in raw_value):
        raise ValueError("spaces are not allowed")
    parsed = urlparse(raw_value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_URL_SCHEMES:
        raise ValueError("unsupported scheme")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("missing hostname")
    port = parsed.port
    include_port = port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    netloc = f"{hostname}:{port}" if include_port else hostname
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _normalize_browser_name(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = value.strip().lower()
    if not cleaned:
        return "unknown"
    return cleaned[:40]


def _normalize_extension_context(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in {"regular", "incognito"}:
        return value.strip().lower()
    return "unknown"


def _normalize_browser_blocking(value: object) -> str:
    if isinstance(value, str) and value in BROWSER_BLOCKING_STATES:
        return value
    return "not_implemented"


def _normalize_optional_bool(value: object) -> bool | str:
    if value is True:
        return True
    if value is False:
        return False
    return "unknown"


def _normalize_incognito_allowed(value: object) -> bool | str:
    if value in HEARTBEAT_ALLOWED_INCOGNITO_VALUES:
        return value
    return "unknown"


def _normalize_dnr_rule_count(value: object) -> int | str:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, min(value, 100000))
    return "unknown"


def _normalize_compact_status(
    value: object,
    *,
    allowed: set[str],
    default: str,
) -> str:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in allowed:
            return cleaned
    return default


def _normalize_compact_optional_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    if not cleaned:
        return ""
    return cleaned[:160]


def _trusted_browser_control_ready(data_dir: Path, now: datetime) -> bool:
    heartbeat_path = data_dir / HEARTBEAT_FILE_NAME
    if not heartbeat_path.exists():
        return False
    try:
        raw = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return False
        last_heartbeat_at = raw.get("last_heartbeat_at")
        if not isinstance(last_heartbeat_at, str) or not last_heartbeat_at:
            return False
        heartbeat_time = _parse_datetime(last_heartbeat_at)
        age_seconds = int((_as_aware_utc(now) - heartbeat_time).total_seconds())
    except Exception:
        return False
    return (
        0 <= age_seconds <= BROWSER_HEARTBEAT_STALE_SECONDS
        and raw.get("extension_connected") is True
        and _normalize_browser_name(raw.get("browser")) == "chrome"
        and _normalize_incognito_allowed(raw.get("incognito_allowed")) is True
        and _normalize_browser_blocking(raw.get("browser_blocking"))
        != "not_implemented"
    )


def _connect_read_only(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_day_state(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            day,
            day_started_at,
            day_ended_at,
            access_level,
            surrender_requested_at,
            bad_day_mode
        FROM day_state
        WHERE id = 1
        """
    ).fetchone()


def _read_enforcement_mode(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        ("enforcement_mode",),
    ).fetchone()
    value = "" if row is None else str(row["value"]).strip().lower()
    return value if value in VALID_ENFORCEMENT_MODES else "preview_only"


def _is_day_active(day: sqlite3.Row) -> bool:
    return day["day_started_at"] is not None and day["day_ended_at"] is None


def _browser_blocking_for_block_decision(
    effective_mode: str,
    day_active: bool,
) -> str:
    if day_active and effective_mode in BROWSER_BLOCKING_MODES:
        return "active"
    return "evaluation_only"


def _normalize_access_level(value: object) -> str:
    normalized = str(value).strip().lower()
    return normalized if normalized in VALID_ACCESS_LEVELS else "unknown"


def _effective_access_level(day: sqlite3.Row) -> str:
    access_level = _normalize_access_level(day["access_level"])
    if access_level == "low" and bool(day["bad_day_mode"]):
        return "medium"
    return access_level


def _read_high_status(
    connection: sqlite3.Connection,
    day: str,
    now: datetime,
) -> dict[str, int | bool]:
    row = connection.execute(
        """
        SELECT ends_at
        FROM high_sessions
        WHERE day_date = ? AND ended_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (day,),
    ).fetchone()
    if row is None:
        return {"active": False, "remaining_seconds": 0}

    try:
        ends_at = _parse_datetime(str(row["ends_at"]))
    except ValueError:
        return {"active": False, "remaining_seconds": 0}

    remaining_seconds = max(
        0,
        int((ends_at - _as_aware_utc(now)).total_seconds()),
    )
    return {
        "active": remaining_seconds > 0,
        "remaining_seconds": remaining_seconds,
    }


def _find_strictest_matching_site_rule(
    connection: sqlite3.Connection,
    hostname: str,
    path: str,
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT id, target, allow_from_level
        FROM rules
        WHERE rule_type = 'site' AND enabled = 1
        ORDER BY id
        """
    ).fetchall()
    matches: list[dict[str, Any]] = []
    for row in rows:
        target = str(row["target"]).strip().lower().rstrip(".")
        matched_scope = _site_rule_match_scope(target, hostname, path)
        if matched_scope is None:
            continue
        matches.append(
            {
                "id": row["id"],
                "target": row["target"],
                "allow_from_level": row["allow_from_level"],
                "matched_scope": matched_scope,
            }
        )
    if not matches:
        return None
    return max(
        matches,
        key=lambda match: (
            ACCESS_LEVEL_RANK.get(str(match["allow_from_level"]).lower(), -1),
            1 if match["matched_scope"] == "path" else 0,
        ),
    )


def _find_matching_task_allowed_url(
    connection: sqlite3.Connection,
    day_date: str,
    canonical_url: str,
) -> sqlite3.Row | None:
    rows = connection.execute(
        """
        SELECT id, title, allowed_url
        FROM tasks
        WHERE day_date = ?
          AND planning_status = 'planned'
          AND status != 'done'
          AND allowed_url IS NOT NULL
          AND trim(allowed_url) != ''
        ORDER BY id
        """,
        (day_date,),
    ).fetchall()
    for row in rows:
        try:
            if _canonical_task_allowed_url(str(row["allowed_url"])) == canonical_url:
                return row
        except ValueError:
            continue
    return None


def _site_rule_match_scope(
    target: str,
    hostname: str,
    path: str,
) -> str | None:
    if "/" not in target:
        if hostname == target:
            return "domain"
        if _is_bare_domain(target) and hostname == f"www.{target}":
            return "domain"
        return None

    host_pattern, path_pattern = target.split("/", 1)
    if not host_pattern or not path_pattern:
        return None
    if not _host_pattern_matches(host_pattern, hostname):
        return None
    if _path_pattern_matches(path_pattern, path):
        return "path"
    return None


def _host_pattern_matches(host_pattern: str, hostname: str) -> bool:
    if host_pattern.startswith("*."):
        suffix = host_pattern[2:]
        return hostname.endswith(f".{suffix}") and hostname != suffix
    if hostname == host_pattern:
        return True
    return _is_bare_domain(host_pattern) and hostname == f"www.{host_pattern}"


def _path_pattern_matches(path_pattern: str, path: str) -> bool:
    normalized_path = path.lower() or "/"
    normalized_pattern = f"/{path_pattern.lower()}"
    if normalized_pattern.endswith("/*"):
        prefix = normalized_pattern[:-2].rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
    if "*" not in normalized_pattern:
        prefix = normalized_pattern.rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
    return fnmatch.fnmatchcase(normalized_path, normalized_pattern)


def _with_browser_attempt_logging(
    result: dict[str, Any],
    *,
    db_path: Path,
    hostname: str,
    matching_rule: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    try:
        _log_browser_attempt_if_needed(
            db_path=db_path,
            hostname=hostname,
            matching_rule=matching_rule,
            result=result,
            now=now,
        )
    except Exception:
        pass
    return result


def _log_browser_attempt_if_needed(
    *,
    db_path: Path,
    hostname: str,
    matching_rule: dict[str, Any],
    result: dict[str, Any],
    now: datetime,
) -> None:
    attempt_decision = _browser_attempt_decision(result)
    if attempt_decision is None:
        return

    target = hostname.strip().lower()
    matched_rule_target = str(matching_rule.get("target", "")).strip().lower()
    if not target or not matched_rule_target:
        return

    access_level = str(result.get("access_level", "unknown")).strip().lower()
    with initialize_database(db_path) as connection:
        repository = AccessAttemptRepository(connection)
        rule_metadata = _read_rule_metadata_for_logging(
            connection,
            int(matching_rule["id"]),
        )
        recent = repository.list_recent(
            limit=100,
            source="browser",
        )
        if _browser_attempt_recently_logged(
            recent,
            now=now,
            target=target,
            matched_rule_target=matched_rule_target,
            decision=attempt_decision,
            access_level=access_level,
        ):
            return

        repository.add(
            occurred_at=now.isoformat(),
            target_type="site",
            target=target,
            rule_id=int(matching_rule["id"]),
            access_level_at_attempt=access_level,
            decision=attempt_decision,
            allow_from_level=str(matching_rule["allow_from_level"]),
            purpose=rule_metadata["purpose"],
            escape_family=rule_metadata["escape_family"],
            source="browser",
            enforcement_mode=str(result.get("enforcement_mode", "preview_only")),
            action_taken=_browser_attempt_action_taken(result),
            matched_scope=str(result.get("matched_scope", "none")),
            matched_rule_target=matched_rule_target,
            url_family=str(result.get("url_family", "unknown")),
            path_kind=str(result.get("path_kind", "unknown")),
            reason_code=str(result.get("reason_code", "unknown")),
        )


def _read_rule_metadata_for_logging(
    connection: sqlite3.Connection,
    rule_id: int,
) -> dict[str, str]:
    row = connection.execute(
        """
        SELECT purpose, escape_family
        FROM rules
        WHERE id = ?
        """,
        (rule_id,),
    ).fetchone()
    if row is None:
        return {"purpose": "high_risk_escape", "escape_family": "none"}
    return {
        "purpose": str(row["purpose"]),
        "escape_family": str(row["escape_family"]),
    }


def _browser_attempt_decision(result: dict[str, Any]) -> str | None:
    decision = str(result.get("decision", "")).strip().lower()
    reason_code = str(result.get("reason_code", "")).strip().lower()
    access_level = str(result.get("access_level", "")).strip().lower()
    if decision == "block":
        return "would_block"
    if reason_code == "active_planned_use_pass":
        return "allowed_by_planned_use_pass"
    if reason_code == "access_level_allowed" and access_level == "high":
        return "would_allow"
    return None


def _browser_attempt_action_taken(result: dict[str, Any]) -> str:
    decision = str(result.get("decision", "")).strip().lower()
    browser_blocking = str(result.get("browser_blocking", "")).strip().lower()
    if decision == "block":
        return "browser_redirect" if browser_blocking == "active" else "evaluation_only"
    return "allowed"


def _browser_attempt_recently_logged(
    attempts: list[Any],
    *,
    now: datetime,
    target: str,
    matched_rule_target: str,
    decision: str,
    access_level: str,
) -> bool:
    for attempt in attempts:
        if (
            attempt.target != target
            or attempt.matched_rule_target != matched_rule_target
            or attempt.decision != decision
            or attempt.access_level_at_attempt != access_level
        ):
            continue
        try:
            occurred_at = _parse_datetime(attempt.occurred_at)
        except ValueError:
            continue
        elapsed_seconds = (_as_aware_utc(now) - occurred_at).total_seconds()
        if 0 <= elapsed_seconds < BROWSER_ATTEMPT_RATE_LIMIT_SECONDS:
            return True
    return False


def _read_enabled_site_rules(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT id, target, allow_from_level
        FROM rules
        WHERE rule_type = 'site' AND enabled = 1
        ORDER BY id
        """
    ).fetchall()


def _blocked_site_domains_for_snapshot(
    connection: sqlite3.Connection,
    *,
    access_level: str,
    base_access_level: str,
    high_active: bool,
    trusted_browser_ready: bool,
    now: datetime,
) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for rule in _read_enabled_site_rules(connection):
        target = _normalize_site_target(rule["target"])
        if not target:
            continue
        allow_from_level = str(rule["allow_from_level"])
        allowed_by_pass = _active_pass_matches_rule(connection, int(rule["id"]), now)
        allowed_by_level = _is_allowed_at(access_level, allow_from_level)
        allowed_by_base_level = _is_allowed_at(base_access_level, allow_from_level)
        allowed_by_high_only = (
            high_active and allowed_by_level and not allowed_by_base_level
        )
        trust_required = allowed_by_pass or allowed_by_high_only
        if allowed_by_pass or allowed_by_level:
            if not (trust_required and not trusted_browser_ready):
                continue
        for domain in _expand_site_target_for_snapshot(target):
            if domain not in seen:
                seen.add(domain)
                domains.append(domain)
    return domains


def _eligible_task_allowed_urls_for_snapshot(
    connection: sqlite3.Connection,
    day_date: str,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    rows = connection.execute(
        """
        SELECT allowed_url
        FROM tasks
        WHERE day_date = ?
          AND planning_status = 'planned'
          AND status != 'done'
          AND allowed_url IS NOT NULL
          AND trim(allowed_url) != ''
        ORDER BY id
        """,
        (day_date,),
    ).fetchall()
    for row in rows:
        try:
            canonical_url = _canonical_task_allowed_url(str(row["allowed_url"]))
        except ValueError:
            continue
        if canonical_url not in seen:
            seen.add(canonical_url)
            urls.append(canonical_url)
    return urls


def _normalize_site_target(value: object) -> str:
    if not isinstance(value, str):
        return ""
    target = value.strip().lower().rstrip(".")
    if not target or "/" in target or ":" in target or " " in target:
        return ""
    labels = target.split(".")
    if len(labels) < 2 or not all(labels):
        return ""
    return target


def _expand_site_target_for_snapshot(target: str) -> tuple[str, ...]:
    if _is_bare_domain(target) and not target.startswith("www."):
        return (target, f"www.{target}")
    return (target,)


def _is_bare_domain(hostname: str) -> bool:
    labels = hostname.split(".")
    return len(labels) == 2 and all(labels)


def _active_pass_matches_rule(
    connection: sqlite3.Connection,
    rule_id: int,
    now: datetime,
) -> bool:
    row = connection.execute(
        """
        SELECT id
        FROM planned_use_passes
        WHERE rule_id = ?
          AND target_type = 'site'
          AND status = 'active'
          AND expires_at > ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (rule_id, _as_aware_utc(now).isoformat()),
    ).fetchone()
    return row is not None


def _is_allowed_at(access_level: str, allow_from_level: str) -> bool:
    access_rank = ACCESS_LEVEL_RANK.get(access_level)
    allow_rank = ACCESS_LEVEL_RANK.get(allow_from_level.strip().lower())
    if access_rank is None or allow_rank is None:
        return False
    return access_rank >= allow_rank


def _parse_datetime(value: str) -> datetime:
    return _as_aware_utc(datetime.fromisoformat(value))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _compact_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:160]


def main() -> int:
    return run_host()
