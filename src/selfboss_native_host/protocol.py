from __future__ import annotations

import json
import struct
from collections.abc import Callable, Mapping
from typing import Any, BinaryIO
from urllib.parse import urlparse, urlunparse


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
STATUS_FIELDS = {
    "app",
    "native_host",
    "enforcement_mode",
    "access_level",
    "day_active",
    "high_active",
    "high_remaining_seconds",
    "safe_mode_active",
    "recovery_mode_active",
    "browser_blocking",
    "status_error",
}
StatusProvider = Callable[[], Mapping[str, Any]]
SNAPSHOT_FIELDS = {
    "enforcement_mode",
    "browser_blocking",
    "domains",
    "allowed_urls",
    "reason",
    "status_error",
}
SnapshotProvider = Callable[[Mapping[str, Any]], Mapping[str, Any]]
URL_EVALUATION_FIELDS = {
    "decision",
    "reason",
    "access_level",
    "enforcement_mode",
    "browser_blocking",
    "url_family",
    "path_kind",
    "matched_scope",
    "reason_code",
}
BROWSER_BLOCKING_STATES = {"active", "evaluation_only", "not_implemented"}
UrlEvaluator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
HEARTBEAT_FIELDS = {
    "browser",
    "context",
    "incognito_allowed",
    "browser_blocking",
    "browser_blocking_available",
    "dnr_supported",
    "dnr_session_rule_count",
    "dnr_last_update_status",
    "dnr_last_error",
    "youtube_spa_content_script_seen",
    "extension_version",
}
HeartbeatWriter = Callable[[Mapping[str, Any]], Mapping[str, Any]]
OpenSelfBossHandler = Callable[[], Mapping[str, Any]]


class NativeMessageError(ValueError):
    """Raised when a Native Messaging frame cannot be decoded safely."""


def encode_native_message(message: Mapping[str, Any]) -> bytes:
    """Return a Chrome Native Messaging frame for a JSON object."""
    payload = json.dumps(
        dict(message),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def decode_native_message_frame(frame: bytes) -> dict[str, Any]:
    """Decode a complete Native Messaging frame."""
    if len(frame) < 4:
        raise NativeMessageError("message frame is missing length prefix")
    message_length = struct.unpack("<I", frame[:4])[0]
    payload = frame[4:]
    if len(payload) != message_length:
        raise NativeMessageError("message frame length does not match payload")
    return decode_native_message_payload(payload)


def decode_native_message_payload(payload: bytes) -> dict[str, Any]:
    """Decode one JSON payload from a Native Messaging frame."""
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeMessageError("invalid JSON payload") from exc
    if not isinstance(decoded, dict):
        raise NativeMessageError("message payload must be a JSON object")
    return decoded


def read_native_message(input_stream: BinaryIO) -> dict[str, Any] | None:
    """Read one framed JSON message, returning None on clean EOF."""
    header = input_stream.read(4)
    if header == b"":
        return None
    if len(header) != 4:
        raise NativeMessageError("incomplete message length prefix")

    message_length = struct.unpack("<I", header)[0]
    if message_length > MAX_MESSAGE_BYTES:
        raise NativeMessageError("message exceeds maximum size")

    payload = input_stream.read(message_length)
    if len(payload) != message_length:
        raise NativeMessageError("incomplete message payload")
    return decode_native_message_payload(payload)


def build_error_response(error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "protocol_version": PROTOCOL_VERSION,
        "error": error,
    }


def build_status_response(
    status_provider: StatusProvider | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "app": "SelfBoss",
        "native_host": "connected",
        "enforcement_mode": "unknown",
        "access_level": "unknown",
        "day_active": "unknown",
        "high_active": "unknown",
        "high_remaining_seconds": "unknown",
        "safe_mode_active": "unknown",
        "recovery_mode_active": "unknown",
        "browser_blocking": "not_implemented",
    }
    if status_provider is None:
        return response
    try:
        provider_status = status_provider()
    except Exception as exc:  # noqa: BLE001 - status failures must not break protocol
        response["status_error"] = _compact_error(exc)
        return response

    for key, value in provider_status.items():
        if key in STATUS_FIELDS:
            response[key] = value
    response["browser_blocking"] = "not_implemented"
    return response


def build_url_evaluation_response(
    message: Mapping[str, Any],
    url_evaluator: UrlEvaluator | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "decision": "unknown",
        "reason": "URL evaluation is not integrated.",
        "access_level": "unknown",
        "enforcement_mode": "unknown",
        "browser_blocking": "evaluation_only",
        "url_family": "unknown",
        "path_kind": "unknown",
        "matched_scope": "none",
        "reason_code": "unknown",
    }
    url = message.get("url")
    if not isinstance(url, str) or not url.strip():
        response["reason"] = "URL is required for evaluation."
        return response
    if url_evaluator is None:
        return response
    try:
        evaluation = url_evaluator(message)
    except Exception as exc:  # noqa: BLE001 - evaluation must not break protocol
        response["reason"] = _compact_error(exc)
        return response

    for key, value in evaluation.items():
        if key in URL_EVALUATION_FIELDS:
            response[key] = value
    if response["browser_blocking"] not in BROWSER_BLOCKING_STATES:
        response["browser_blocking"] = "evaluation_only"
    return response


def build_blocked_domains_snapshot_response(
    message: Mapping[str, Any],
    snapshot_provider: SnapshotProvider | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "enforcement_mode": "unknown",
        "browser_blocking": "evaluation_only",
        "domains": [],
        "allowed_urls": [],
        "reason": "Blocked-domain snapshot is not integrated.",
    }
    if snapshot_provider is None:
        return response
    try:
        snapshot = snapshot_provider(message)
    except Exception as exc:  # noqa: BLE001 - snapshot failures must not break protocol
        response["reason"] = "Blocked-domain snapshot is unavailable."
        response["status_error"] = _compact_error(exc)
        return response

    for key, value in snapshot.items():
        if key in SNAPSHOT_FIELDS:
            response[key] = value
    if response["browser_blocking"] not in BROWSER_BLOCKING_STATES:
        response["browser_blocking"] = "evaluation_only"
    if not isinstance(response["domains"], list):
        response["domains"] = []
    response["domains"] = [
        domain
        for domain in response["domains"]
        if isinstance(domain, str) and domain.strip()
    ]
    if not isinstance(response["allowed_urls"], list):
        response["allowed_urls"] = []
    response["allowed_urls"] = [
        canonical
        for value in response["allowed_urls"]
        if isinstance(value, str)
        for canonical in [_canonical_allowed_url(value)]
        if canonical
    ]
    return response


def build_browser_heartbeat_response(
    message: Mapping[str, Any],
    heartbeat_writer: HeartbeatWriter | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": True,
        "protocol_version": PROTOCOL_VERSION,
        "heartbeat_saved": False,
    }
    if heartbeat_writer is None:
        response["ok"] = False
        response["error"] = "Heartbeat writer is unavailable."
        return response

    heartbeat = {
        key: value
        for key, value in message.items()
        if key in HEARTBEAT_FIELDS
    }
    try:
        result = heartbeat_writer(heartbeat)
    except Exception as exc:  # noqa: BLE001 - heartbeat must not break protocol
        response["ok"] = False
        response["error"] = _compact_error(exc)
        return response

    response["heartbeat_saved"] = bool(result.get("heartbeat_saved"))
    if not response["heartbeat_saved"]:
        response["ok"] = False
    error = result.get("error")
    if error:
        response["error"] = str(error)[:160]
    return response


def build_open_selfboss_response(
    open_selfboss_handler: OpenSelfBossHandler | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "ok": False,
        "protocol_version": PROTOCOL_VERSION,
        "action": "open_selfboss",
        "reason": "Open LoopGuard handler is unavailable.",
    }
    if open_selfboss_handler is None:
        return response

    try:
        result = open_selfboss_handler()
    except Exception as exc:  # noqa: BLE001 - request must not break protocol
        response["reason"] = _compact_error(exc)
        return response

    if not isinstance(result, Mapping):
        response["reason"] = "Open LoopGuard handler returned an invalid result."
        return response

    response["ok"] = bool(result.get("ok"))
    reason = result.get("reason")
    response["reason"] = (
        str(reason).strip()[:160]
        if reason
        else (
            "LoopGuard window focus was requested."
            if response["ok"]
            else "LoopGuard window could not be opened."
        )
    )
    return response


def _canonical_allowed_url(value: str) -> str:
    raw_value = value.strip()
    if any(character.isspace() for character in raw_value):
        return ""
    parsed = urlparse(raw_value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return ""
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    include_port = port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    netloc = f"{hostname}:{port}" if include_port else hostname
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def dispatch_message(
    message: Mapping[str, Any],
    *,
    status_provider: StatusProvider | None = None,
    snapshot_provider: SnapshotProvider | None = None,
    url_evaluator: UrlEvaluator | None = None,
    heartbeat_writer: HeartbeatWriter | None = None,
    open_selfboss_handler: OpenSelfBossHandler | None = None,
) -> dict[str, Any]:
    """Handle a browser/native-host request without mutating LoopGuard state."""
    message_type = message.get("type")
    if message_type == "hello":
        return {
            "ok": True,
            "app": "SelfBoss",
            "protocol_version": PROTOCOL_VERSION,
            "native_host": "connected",
        }
    if message_type == "get_status":
        return build_status_response(status_provider)
    if message_type == "get_blocked_domains_snapshot":
        return build_blocked_domains_snapshot_response(message, snapshot_provider)
    if message_type == "evaluate_url":
        return build_url_evaluation_response(message, url_evaluator)
    if message_type == "browser_heartbeat":
        return build_browser_heartbeat_response(message, heartbeat_writer)
    if message_type == "request_open_selfboss":
        return build_open_selfboss_response(open_selfboss_handler)
    if message_type is None:
        return build_error_response("missing message type")
    return build_error_response(f"unknown message type: {message_type}")


def _compact_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:160]
