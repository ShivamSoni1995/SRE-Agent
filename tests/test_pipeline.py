import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parser.log_parser import parse_logs
from app.parser.metrics_parser import parse_metrics
from app.services.context_builder import build_context
from app.evaluator.scorer import evaluate_rca_sync, evaluate_rca
from app.evaluator.embeddings import cosine_similarity


# ── Log Parser ────────────────────────────────────────────────────────────────

def test_parse_logs_extracts_errors():
    logs = "INFO started\nERROR database timeout\nERROR retry failed\nINFO done"
    result = parse_logs(logs)
    assert result["error_frequency"] == 2
    assert any("timeout" in e for e in result["errors"])


def test_parse_logs_filters_info():
    logs = "INFO request started\nINFO db query initialized"
    result = parse_logs(logs)
    assert result["error_frequency"] == 0
    assert result["errors"] == []


def test_parse_logs_detects_database_service():
    logs = "ERROR database timeout\nERROR db connection failed"
    result = parse_logs(logs)
    assert "database" in result["detected_services"] or "db" in result["detected_services"]


# ── Metrics Parser ────────────────────────────────────────────────────────────

def test_parse_metrics_critical_cpu():
    result = parse_metrics({"cpu": 95, "memory": 50, "latency": 200, "error_rate": 2})
    assert result["severity"] in ("warning", "critical")
    assert any("cpu" in a for a in result["anomalies"])


def test_parse_metrics_normal():
    result = parse_metrics({"cpu": 20, "memory": 30, "latency": 150, "error_rate": 1})
    assert result["severity"] == "normal"
    assert result["anomalies"] == []


def test_parse_metrics_critical_latency():
    result = parse_metrics({"cpu": 40, "memory": 50, "latency": 1500, "error_rate": 3})
    assert any("latency" in a for a in result["anomalies"])


# ── Context Builder ───────────────────────────────────────────────────────────

def test_context_builder_detects_deploy():
    logs = parse_logs("ERROR crash loop\nERROR service killed")
    metrics = parse_metrics({"cpu": 60, "memory": 50, "latency": 300, "error_rate": 10})
    ctx = build_context(logs, metrics, events=["deployment_started"])
    assert ctx["recent_deploy"] is True


def test_context_builder_db_correlation():
    logs = parse_logs("ERROR database timeout\nERROR connection pool exhausted")
    metrics = parse_metrics({"cpu": 50, "memory": 60, "latency": 1400, "error_rate": 15})
    ctx = build_context(logs, metrics, events=[])
    assert any("latency" in h or "connection" in h for h in ctx["correlation_hints"])


# ── Evaluation — sync (keyword + completeness, no API needed) ─────────────────

def test_evaluator_high_score_with_matching_keywords():
    rca = {
        "issue":       "Database connection timeout causing latency spike",
        "root_cause":  "Database unreachable — connection pool exhausted",
        "solution":    "1. Restart DB connection pool. 2. Check DB health.",
        "confidence":  0.85,
    }
    result = evaluate_rca_sync(rca, expected_root_cause="database unreachable")
    assert result["score"] > 0.5
    assert "database" in result["matched_keywords"] or "unreachable" in result["matched_keywords"]


def test_evaluator_penalises_low_confidence():
    rca = {
        "issue":      "Something is wrong",
        "root_cause": "Unknown cause",
        "solution":   "Investigate further",
        "confidence": 0.2,
    }
    result = evaluate_rca_sync(rca, expected_root_cause="database unreachable")
    assert result["confidence_factor"] < 1.0


def test_evaluator_completeness_requires_all_fields():
    rca = {
        "issue":      "High latency issue detected in production",
        "root_cause": "Database connection pool exhausted",
        "solution":   "1. Restart pool. 2. Scale DB.",
        "confidence": 0.9,
    }
    result = evaluate_rca_sync(rca)
    assert result["completeness_score"] == 1.0


def test_evaluator_no_semantic_when_no_expected():
    rca = {
        "issue":      "CPU exhaustion on worker nodes",
        "root_cause": "Runaway process consuming all CPU resources",
        "solution":   "Kill process, scale horizontally",
        "confidence": 0.8,
    }
    result = evaluate_rca_sync(rca)  # no expected_root_cause
    assert result["semantic_score"] is None
    assert result["semantic_available"] is False


def test_evaluator_scoring_method_label_sync():
    rca = {
        "issue": "x" * 15, "root_cause": "y" * 20,
        "solution": "z" * 25, "confidence": 0.75,
    }
    result = evaluate_rca_sync(rca)
    assert "keyword" in result["scoring_method"]


# ── Cosine similarity unit tests (no API needed) ──────────────────────────────

def test_cosine_similarity_identical_vectors():
    v = [1.0, 0.5, 0.3, 0.8]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_zero_vector():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.5]) == 0.0


def test_cosine_similarity_opposite_vectors():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


# ── Async semantic evaluator (mocked — no real API call) ─────────────────────

@pytest.mark.asyncio
async def test_async_evaluator_without_api_key(monkeypatch):
    """When GEMINI_API_KEY is not set, semantic falls back gracefully."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    rca = {
        "issue":      "Memory leak detected",
        "root_cause": "Application memory leak — heap growing unbounded",
        "solution":   "Restart pods, profile heap, deploy fix",
        "confidence": 0.8,
    }
    result = await evaluate_rca(rca, expected_root_cause="application memory leak")
    assert result["semantic_available"] is False
    assert result["semantic_score"] is None
    assert result["score"] > 0          # keyword fallback still scores it
    assert "keyword" in result["scoring_method"]


@pytest.mark.asyncio
async def test_async_evaluator_with_mocked_embeddings(monkeypatch):
    """Mock the embed function to return fixed vectors, verify semantic path."""
    import app.evaluator.embeddings as emb_module

    # Two similar vectors → high cosine similarity
    vec_a = [1.0, 0.8, 0.6, 0.9, 0.7]
    vec_b = [0.9, 0.85, 0.55, 0.95, 0.65]

    call_count = 0
    async def mock_embed(text):
        nonlocal call_count
        call_count += 1
        return vec_a if call_count == 1 else vec_b

    monkeypatch.setattr(emb_module, "embed", mock_embed)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    # Also patch the embed import inside scorer
    import app.evaluator.scorer as scorer_module
    monkeypatch.setattr(scorer_module, "embed", mock_embed)

    rca = {
        "issue":      "Database connection timeout",
        "root_cause": "Database unreachable — connection pool exhausted",
        "solution":   "Restart DB pool, check connectivity",
        "confidence": 0.85,
    }
    result = await evaluate_rca(rca, expected_root_cause="database unreachable")
    assert result["semantic_available"] is True
    assert result["semantic_score"] is not None
    assert result["semantic_score"] >= 0
    assert "semantic" in result["scoring_method"]
    assert result["score"] > 0.5
