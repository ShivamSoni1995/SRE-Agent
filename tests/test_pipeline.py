import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parser.log_parser import parse_logs
from app.parser.metrics_parser import parse_metrics
from app.services.context_builder import build_context
from app.evaluator.scorer import evaluate_rca


# --- Log Parser Tests ---

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


# --- Metrics Parser Tests ---

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


# --- Context Builder Tests ---

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


# --- Evaluation Engine Tests ---

def test_evaluator_high_score_with_matching_keywords():
    rca = {
        "issue": "Database connection timeout causing latency spike",
        "root_cause": "Database unreachable — connection pool exhausted",
        "solution": "1. Restart DB connection pool. 2. Check DB health.",
        "confidence": 0.85,
    }
    result = evaluate_rca(rca, expected_root_cause="database unreachable")
    assert result["score"] > 0.5
    assert "database" in result["matched_keywords"] or "unreachable" in result["matched_keywords"]


def test_evaluator_penalises_low_confidence():
    rca = {
        "issue": "Something is wrong",
        "root_cause": "Unknown cause",
        "solution": "Investigate further",
        "confidence": 0.2,
    }
    result = evaluate_rca(rca, expected_root_cause="database unreachable")
    assert result["confidence_factor"] < 1.0


def test_evaluator_completeness_requires_all_fields():
    rca = {
        "issue": "High latency issue detected in production",
        "root_cause": "Database connection pool exhausted",
        "solution": "1. Restart pool. 2. Scale DB.",
        "confidence": 0.9,
    }
    result = evaluate_rca(rca)
    assert result["completeness_score"] == 1.0
