"""
test_ingestion.py — Tests for Pub/Sub decoder and GCP log normalizer.
"""
import base64
import json
import pytest

from app.ingestion.pubsub_decoder import (
    decode_pubsub_message,
    is_cloud_logging_entry,
    extract_message_id,
)
from app.ingestion.normalizer import (
    normalize_gcp_log_entry,
    normalize_test_event,
)


# ── Pub/Sub decoder ───────────────────────────────────────────────────────────

def _make_envelope(payload: dict, message_id: str = "msg-123") -> dict:
    """Build a valid Pub/Sub push envelope."""
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {
        "message": {
            "data":        data,
            "messageId":   message_id,
            "publishTime": "2026-05-18T10:00:00Z",
            "attributes":  {"source": "test"},
        },
        "subscription": "projects/open-sre/subscriptions/opensre-logs-sub",
    }


def test_decode_valid_pubsub_message():
    payload = {"logName": "projects/test/logs/app", "textPayload": "ERROR timeout"}
    envelope = _make_envelope(payload)
    decoded, attrs = decode_pubsub_message(envelope)
    assert decoded is not None
    assert decoded["textPayload"] == "ERROR timeout"
    assert attrs == {"source": "test"}


def test_decode_missing_message_field():
    decoded, attrs = decode_pubsub_message({})
    assert decoded is None
    assert attrs is None


def test_decode_missing_data_field():
    envelope = {"message": {"messageId": "x"}}
    decoded, attrs = decode_pubsub_message(envelope)
    assert decoded is None


def test_decode_invalid_base64():
    envelope = {
        "message": {
            "data": "NOT_VALID_BASE64!!!",
            "messageId": "x",
        }
    }
    decoded, attrs = decode_pubsub_message(envelope)
    assert decoded is None


def test_decode_invalid_json_after_base64():
    # Valid base64 but not valid JSON
    data = base64.b64encode(b"not json at all").decode()
    envelope = {"message": {"data": data, "messageId": "x"}}
    decoded, attrs = decode_pubsub_message(envelope)
    assert decoded is None


def test_extract_message_id():
    envelope = _make_envelope({"text": "hello"}, message_id="abc-456")
    assert extract_message_id(envelope) == "abc-456"


def test_extract_message_id_missing():
    assert extract_message_id({}) == "unknown"


def test_is_cloud_logging_entry_textpayload():
    assert is_cloud_logging_entry({"textPayload": "hello"}) is True


def test_is_cloud_logging_entry_jsonpayload():
    assert is_cloud_logging_entry({"jsonPayload": {"msg": "x"}}) is True


def test_is_cloud_logging_entry_resource():
    assert is_cloud_logging_entry({"resource": {"type": "cloud_run_revision"}}) is True


def test_is_cloud_logging_entry_false():
    assert is_cloud_logging_entry({"someOtherField": "value"}) is False


# ── GCP log normalizer ────────────────────────────────────────────────────────

def _cloud_run_entry(
    message: str,
    severity: str = "ERROR",
    service_name: str = "opensre-mini",
) -> dict:
    return {
        "logName":     f"projects/open-sre/logs/{service_name}",
        "severity":    severity,
        "textPayload": message,
        "timestamp":   "2026-05-18T10:00:00.000000Z",
        "resource": {
            "type": "cloud_run_revision",
            "labels": {
                "service_name": service_name,
                "revision_name": f"{service_name}-00001",
                "location": "us-central1",
            },
        },
    }


def test_normalize_cloud_run_error():
    entry = _cloud_run_entry("ERROR database timeout")
    event = normalize_gcp_log_entry(entry)
    assert event is not None
    assert event.severity == "ERROR"
    assert event.service == "opensre-mini"
    assert "timeout" in event.message
    assert event.source == "gcp"


def test_normalize_extracts_service_from_resource():
    entry = _cloud_run_entry("ERROR crash", service_name="payments-api")
    event = normalize_gcp_log_entry(entry)
    assert event.service == "payments-api"


def test_normalize_health_check_filtered():
    entry = _cloud_run_entry("GET /health 200 OK", severity="INFO")
    event = normalize_gcp_log_entry(entry)
    assert event is None   # health checks should be filtered


def test_normalize_info_with_error_signal_upgraded():
    entry = _cloud_run_entry("database timeout occurred", severity="INFO")
    event = normalize_gcp_log_entry(entry)
    assert event is not None
    assert event.severity == "ERROR"   # upgraded due to error signal in message


def test_normalize_json_payload():
    entry = {
        "logName":    "projects/open-sre/logs/app",
        "severity":   "ERROR",
        "jsonPayload": {"message": "connection refused", "code": 500},
        "timestamp":  "2026-05-18T10:00:00Z",
        "resource":   {"type": "cloud_run_revision", "labels": {"service_name": "api"}},
    }
    event = normalize_gcp_log_entry(entry)
    assert event is not None
    assert "connection refused" in event.message


def test_normalize_malformed_entry_returns_none():
    event = normalize_gcp_log_entry({})
    assert event is None   # no message extractable


def test_normalize_gcp_severity_mapping():
    cases = [
        ("DEFAULT",   "INFO"),
        ("WARNING",   "WARNING"),
        ("CRITICAL",  "CRITICAL"),
        ("ALERT",     "CRITICAL"),
        ("EMERGENCY", "CRITICAL"),
    ]
    for gcp_sev, expected in cases:
        entry = _cloud_run_entry("some message", severity=gcp_sev)
        event = normalize_gcp_log_entry(entry)
        if event:
            assert event.severity == expected, f"Failed for {gcp_sev}"


def test_normalize_test_event():
    event = normalize_test_event(
        service="test-service",
        severity="ERROR",
        message="test error message",
        labels={"env": "test"},
    )
    assert event.service == "test-service"
    assert event.severity == "ERROR"
    assert event.source == "test"
    assert event.labels["env"] == "test"
    assert event.is_error is True


def test_normalized_event_correlation_key():
    event = normalize_test_event("payments", "ERROR", "timeout")
    assert event.to_correlation_key() == "payments:ERROR"


def test_normalized_event_is_error_flags():
    error_event   = normalize_test_event("svc", "ERROR",    "msg")
    warning_event = normalize_test_event("svc", "WARNING",  "msg")
    info_event    = normalize_test_event("svc", "INFO",     "msg")
    critical_event = normalize_test_event("svc", "CRITICAL", "msg")

    assert error_event.is_error    is True
    assert warning_event.is_error  is False
    assert info_event.is_error     is False
    assert critical_event.is_error is True
    assert warning_event.is_warning is True
