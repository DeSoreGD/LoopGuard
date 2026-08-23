from __future__ import annotations

import json
import struct
from io import BytesIO

import pytest

from selfboss_native_host.host import run_host
from selfboss_native_host.protocol import (
    NativeMessageError,
    decode_native_message_frame,
    decode_native_message_payload,
    dispatch_message,
    encode_native_message,
)


def test_native_message_frame_round_trip_uses_little_endian_length() -> None:
    frame = encode_native_message({"type": "hello"})
    payload_length = struct.unpack("<I", frame[:4])[0]

    assert payload_length == len(frame[4:])
    assert decode_native_message_frame(frame) == {"type": "hello"}


def test_hello_request_returns_connected_response() -> None:
    response = dispatch_message({"type": "hello"})

    assert response == {
        "ok": True,
        "app": "SelfBoss",
        "protocol_version": 1,
        "native_host": "connected",
    }


def test_get_status_without_provider_returns_safe_unknown_status() -> None:
    response = dispatch_message({"type": "get_status"})

    assert response == {
        "ok": True,
        "protocol_version": 1,
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


def test_get_status_provider_exception_returns_safe_unknown_status() -> None:
    def broken_provider() -> dict[str, object]:
        raise RuntimeError("database locked with private path")

    response = dispatch_message(
        {"type": "get_status"},
        status_provider=broken_provider,
    )

    assert response["ok"] is True
    assert response["enforcement_mode"] == "unknown"
    assert response["access_level"] == "unknown"
    assert response["browser_blocking"] == "not_implemented"
    assert response["status_error"] == "database locked with private path"


def test_unknown_message_returns_error_response() -> None:
    response = dispatch_message({"type": "block_url"})

    assert response["ok"] is False
    assert response["protocol_version"] == 1
    assert "unknown message type" in response["error"]


def test_request_open_selfboss_success_is_privacy_minimal() -> None:
    response = dispatch_message(
        {"type": "request_open_selfboss", "tasks": ["private"]},
        open_selfboss_handler=lambda: {
            "ok": True,
            "reason": "LoopGuard desktop window focus was requested.",
            "rules": ["private"],
        },
    )

    assert response == {
        "ok": True,
        "protocol_version": 1,
        "action": "open_selfboss",
        "reason": "LoopGuard desktop window focus was requested.",
    }
    assert "tasks" not in response
    assert "rules" not in response
    assert "reward_history" not in response


def test_request_open_selfboss_failure_is_compact() -> None:
    response = dispatch_message(
        {"type": "request_open_selfboss"},
        open_selfboss_handler=lambda: {
            "ok": False,
            "reason": "LoopGuard desktop window was not found.",
        },
    )

    assert response == {
        "ok": False,
        "protocol_version": 1,
        "action": "open_selfboss",
        "reason": "LoopGuard desktop window was not found.",
    }


def test_browser_heartbeat_allows_diagnostics_and_filters_private_fields() -> None:
    saved: dict[str, object] = {}

    def heartbeat_writer(message: dict[str, object]) -> dict[str, object]:
        saved.update(message)
        return {"heartbeat_saved": True}

    response = dispatch_message(
        {
            "type": "browser_heartbeat",
            "browser": "chrome",
            "context": "regular",
            "incognito_allowed": True,
            "browser_blocking": "active",
            "browser_blocking_available": True,
            "dnr_supported": True,
            "dnr_session_rule_count": 7,
            "dnr_last_update_status": "active",
            "dnr_last_error": "",
            "youtube_spa_content_script_seen": True,
            "extension_version": "0.0.1",
            "url": "https://private.example/path",
            "domains": ["reddit.com"],
            "rules": ["private"],
            "tasks": ["private"],
            "reward_history": ["private"],
            "browsing_history": ["private"],
        },
        heartbeat_writer=heartbeat_writer,
    )

    assert response == {
        "ok": True,
        "protocol_version": 1,
        "heartbeat_saved": True,
    }
    assert saved == {
        "browser": "chrome",
        "context": "regular",
        "incognito_allowed": True,
        "browser_blocking": "active",
        "browser_blocking_available": True,
        "dnr_supported": True,
        "dnr_session_rule_count": 7,
        "dnr_last_update_status": "active",
        "dnr_last_error": "",
        "youtube_spa_content_script_seen": True,
        "extension_version": "0.0.1",
    }


def test_browser_heartbeat_old_shape_still_saves() -> None:
    response = dispatch_message(
        {
            "type": "browser_heartbeat",
            "browser": "chrome",
            "incognito_allowed": "unknown",
            "browser_blocking": "evaluation_only",
        },
        heartbeat_writer=lambda message: {"heartbeat_saved": True},
    )

    assert response == {
        "ok": True,
        "protocol_version": 1,
        "heartbeat_saved": True,
    }


def test_evaluate_url_without_evaluator_returns_safe_unknown_decision() -> None:
    response = dispatch_message(
        {
            "type": "evaluate_url",
            "url": "https://www.reddit.com/",
            "tab_id": 123,
            "reason": "active_tab",
            "browser": "chrome",
        }
    )

    assert response == {
        "ok": True,
        "protocol_version": 1,
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


def test_invalid_json_payload_is_rejected_safely() -> None:
    with pytest.raises(NativeMessageError, match="invalid JSON payload"):
        decode_native_message_payload(b"{not json")


def test_host_stdout_contains_only_framed_json_response() -> None:
    input_stream = BytesIO(encode_native_message({"type": "hello"}))
    output_stream = BytesIO()
    error_stream = BytesIOText()

    exit_code = run_host(input_stream, output_stream, error_stream)
    output = output_stream.getvalue()
    payload_length = struct.unpack("<I", output[:4])[0]
    payload = output[4:]

    assert exit_code == 0
    assert payload_length == len(payload)
    assert json.loads(payload.decode("utf-8"))["native_host"] == "connected"
    assert error_stream.getvalue() == ""


def test_host_open_selfboss_response_is_framed_json_only() -> None:
    input_stream = BytesIO(encode_native_message({"type": "request_open_selfboss"}))
    output_stream = BytesIO()
    error_stream = BytesIOText()

    exit_code = run_host(
        input_stream,
        output_stream,
        error_stream,
        open_selfboss_handler=lambda: {
            "ok": True,
            "reason": "LoopGuard desktop window focus was requested.",
        },
    )
    output = output_stream.getvalue()
    payload_length = struct.unpack("<I", output[:4])[0]
    payload = output[4:]
    response = json.loads(payload.decode("utf-8"))

    assert exit_code == 0
    assert payload_length == len(payload)
    assert response == {
        "ok": True,
        "protocol_version": 1,
        "action": "open_selfboss",
        "reason": "LoopGuard desktop window focus was requested.",
    }
    assert error_stream.getvalue() == ""


class BytesIOText:
    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, value: str) -> int:
        self._parts.append(value)
        return len(value)

    def getvalue(self) -> str:
        return "".join(self._parts)
