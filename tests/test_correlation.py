"""
test_correlation.py — Tests for rolling windows, rules, dedup, and engine.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

from app.ingestion.normalizer import normalize_test_event
from app.correlation.windows import RollingWindowStore, Window, WINDOW_DURATION_SECONDS
from app.correlation.rules import (
    rule_database_failure,
    rule_cpu_exhaustion,
    rule_memory_leak,
    rule_high_error_rate,
    rule_crash_loop,
    IncidentCandidate,
)
from app.correlation.incident_store import IncidentCandidateStore
from app.correlation.engine import CorrelationEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def error_event(message: str, service: str = "test-service") -> object:
    return normalize_test_event(service, "ERROR", message)


def make_window_with_events(messages: list[str], service: str = "svc") -> Window:
    """Populate a fresh Window with events."""
    w = Window()
    for msg in messages:
        event = error_event(msg, service)
        w.add(event)
    return w


# ── Rolling windows ───────────────────────────────────────────────────────────

def test_window_add_event():
    store = RollingWindowStore()
    event = error_event("database timeout")
    window = store.add_event(event)
    assert window.event_count == 1


def test_window_accumulates_events():
    store = RollingWindowStore()
    for i in range(10):
        store.add_event(error_event(f"error {i}"))
    stats = store.stats()
    assert stats["total_active_events"] >= 10


def test_window_groups_by_service_and_severity():
    store = RollingWindowStore()
    store.add_event(normalize_test_event("service-a", "ERROR", "err"))
    store.add_event(normalize_test_event("service-b", "ERROR", "err"))
    store.add_event(normalize_test_event("service-a", "WARNING", "warn"))
    # 3 distinct keys
    assert store.stats()["active_windows"] == 3


def test_window_expiry():
    w = Window()
    # Manually backdate the start
    w.window_start = datetime.now(timezone.utc) - timedelta(
        seconds=WINDOW_DURATION_SECONDS + 10
    )
    assert w.is_expired() is True


def test_window_resets_on_expiry():
    store = RollingWindowStore()
    event = error_event("timeout")
    window = store.add_event(event)
    # Expire it
    window.window_start = datetime.now(timezone.utc) - timedelta(
        seconds=WINDOW_DURATION_SECONDS + 10
    )
    # Next event should reset
    window2 = store.add_event(error_event("another error"))
    assert window2.event_count == 1


def test_window_messages_matching():
    w = make_window_with_events([
        "database timeout",
        "connection pool exhausted",
        "retry failed",
        "request timeout",
    ])
    count = w.messages_matching("timeout", "connection")
    assert count == 3


def test_window_max_events_cap():
    from app.correlation.windows import MAX_EVENTS_PER_WINDOW
    w = Window()
    for i in range(MAX_EVENTS_PER_WINDOW + 50):
        w.add(error_event(f"msg {i}"))
    assert w.event_count == MAX_EVENTS_PER_WINDOW


# ── Rules ─────────────────────────────────────────────────────────────────────

def test_rule_database_failure_fires():
    w = make_window_with_events(
        ["database timeout: connection failed"] * 25
    )
    result = rule_database_failure(
        w, "payments-api", {"latency": 1500, "cpu": 50, "memory": 60}
    )
    assert result is not None
    assert result.incident_type == "database_failure"
    assert result.service == "payments-api"


def test_rule_database_failure_below_threshold():
    w = make_window_with_events(["database timeout"] * 10)
    result = rule_database_failure(
        w, "svc", {"latency": 1500, "cpu": 50, "memory": 60}
    )
    assert result is None   # count < 20


def test_rule_database_failure_low_latency():
    w = make_window_with_events(["database timeout"] * 25)
    result = rule_database_failure(
        w, "svc", {"latency": 200, "cpu": 50, "memory": 60}
    )
    assert result is None   # latency < 1000ms


def test_rule_cpu_exhaustion_fires():
    w = make_window_with_events(["high cpu usage"] * 5)
    result = rule_cpu_exhaustion(
        w, "worker", {"cpu": 97, "latency": 1200, "memory": 60}
    )
    assert result is not None
    assert result.incident_type == "cpu_exhaustion"


def test_rule_cpu_exhaustion_cpu_below_threshold():
    w = make_window_with_events(["high cpu"] * 5)
    result = rule_cpu_exhaustion(
        w, "svc", {"cpu": 80, "latency": 1200, "memory": 60}
    )
    assert result is None


def test_rule_memory_leak_fires():
    w = make_window_with_events(["oom killer invoked", "process killed"] * 3)
    result = rule_memory_leak(
        w, "api", {"memory": 97, "cpu": 40, "latency": 500}
    )
    assert result is not None
    assert result.incident_type == "memory_leak"


def test_rule_memory_leak_no_oom_signals():
    w = make_window_with_events(["some other error"] * 5)
    result = rule_memory_leak(
        w, "svc", {"memory": 97, "cpu": 40, "latency": 500}
    )
    assert result is None   # memory high but no OOM signals


def test_rule_high_error_rate_fires():
    w = make_window_with_events(["generic error"] * 35)
    result = rule_high_error_rate(
        w, "svc", {"cpu": 50, "memory": 50, "latency": 200}
    )
    assert result is not None
    assert result.incident_type == "high_error_rate"


def test_rule_crash_loop_fires():
    w = make_window_with_events(["crash loop detected: service restarting"] * 6)
    result = rule_crash_loop(
        w, "api", {"cpu": 50, "memory": 50, "latency": 200}
    )
    assert result is not None
    assert result.incident_type == "crash_loop"


def test_incident_candidate_signature_is_consistent():
    w = make_window_with_events(["timeout"] * 25)
    c1 = rule_database_failure(w, "payments", {"latency": 1200})
    c2 = rule_database_failure(w, "payments", {"latency": 1200})
    assert c1 is not None and c2 is not None
    assert c1.signature() == c2.signature()


def test_incident_candidate_to_analyze_payload():
    w = make_window_with_events(["database timeout"] * 25)
    candidate = rule_database_failure(w, "db-svc", {"latency": 1500})
    assert candidate is not None
    payload = candidate.to_analyze_payload()
    assert "logs" in payload
    assert "metrics" in payload
    assert "events" in payload
    assert "timeout" in payload["logs"].lower()


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_dedup_first_occurrence_not_duplicate():
    store = IncidentCandidateStore()
    is_dup, _ = store.is_duplicate("sig-abc")
    assert is_dup is False


def test_dedup_second_occurrence_is_duplicate():
    store = IncidentCandidateStore()
    store.record("sig-abc")
    is_dup, last = store.is_duplicate("sig-abc")
    assert is_dup is True
    assert last is not None


def test_dedup_different_signatures_independent():
    store = IncidentCandidateStore()
    store.record("sig-a")
    is_dup_a, _ = store.is_duplicate("sig-a")
    is_dup_b, _ = store.is_duplicate("sig-b")
    assert is_dup_a is True
    assert is_dup_b is False


def test_dedup_stats():
    store = IncidentCandidateStore()
    store.record("x")
    store.record("y")
    store.is_duplicate("x")
    s = store.stats()
    assert s["total_triggered"] == 2
    assert s["total_suppressed"] == 1


# ── Correlation engine ────────────────────────────────────────────────────────

def test_engine_returns_none_for_info_events():
    engine = CorrelationEngine()
    event  = normalize_test_event("svc", "INFO", "request started")
    result = engine.add_event(event)
    assert result is None


def test_engine_returns_none_below_threshold():
    engine = CorrelationEngine()
    for _ in range(5):
        event = normalize_test_event("svc", "ERROR", "database timeout")
        result = engine.add_event(event, {"latency": 1500})
    # 5 events — below rule_database_failure threshold of 20
    assert result is None


def test_engine_detects_incident_above_threshold():
    """Test rule fires directly — bypasses singleton state."""
    w = make_window_with_events(
        ["database timeout: connection pool exhausted"] * 25,
        service="isolated-svc",
    )
    result = rule_database_failure(
        w, "isolated-svc", {"latency": 1500, "cpu": 50, "memory": 60}
    )
    assert result is not None
    assert result.incident_type == "database_failure"
    assert result.service == "isolated-svc"


def test_engine_suppresses_duplicate():
    engine = CorrelationEngine()
    candidates = []
    # First burst — should trigger
    for _ in range(25):
        r = engine.add_event(
            normalize_test_event("svc", "ERROR", "database timeout connection"),
            {"latency": 1500}
        )
        if r:
            candidates.append(r)
    # Second burst — should be deduplicated
    for _ in range(25):
        r = engine.add_event(
            normalize_test_event("svc", "ERROR", "database timeout connection"),
            {"latency": 1500}
        )
        if r:
            candidates.append(r)
    assert len(candidates) == 1   # only one, second suppressed


def test_engine_stats_structure():
    engine = CorrelationEngine()
    stats = engine.stats()
    assert "windows" in stats
    assert "candidates" in stats
    assert "rules" in stats
    assert stats["rules"] == 5


# ── Async pipeline (mocked) ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_runs_on_candidate(monkeypatch):
    """Verify process_incident_candidate calls Gemini and storage."""
    from app.pipeline.incident_processor import process_incident_candidate
    from app.correlation.rules import IncidentCandidate

    candidate = IncidentCandidate(
        incident_type="database_failure",
        service="payments-api",
        severity="critical",
        error_count=25,
        error_messages=["database timeout"] * 5,
        latency=1500,
        cpu=70,
        memory=60,
    )

    mock_rca = {
        "issue":       "Database connection failure",
        "root_cause":  "Connection pool exhausted under load",
        "solution":    "1. Restart pool. 2. Scale DB.",
        "confidence":  0.87,
    }
    mock_eval = {
        "score":              0.91,
        "matched_keywords":   ["database", "connection"],
        "semantic_score":     None,
        "semantic_available": False,
        "scoring_method":     "keyword+completeness",
    }

    monkeypatch.setattr(
        "app.pipeline.incident_processor.run_rca",
        AsyncMock(return_value=mock_rca),
    )
    monkeypatch.setattr(
        "app.pipeline.incident_processor.evaluate_rca",
        AsyncMock(return_value=mock_eval),
    )
    monkeypatch.setattr(
        "app.pipeline.incident_processor.storage.save_incident",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "app.pipeline.incident_processor.notify_incident",
        AsyncMock(return_value=True),
    )

    incident_id = await process_incident_candidate(candidate)
    assert incident_id is not None
    assert incident_id.startswith("INC-")
