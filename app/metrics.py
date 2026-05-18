"""
metrics.py — Prometheus instrumentation for OpenSRE Mini.

Tracks:
  - API request counts and latency
  - Incident analysis pipeline timing
  - RCA confidence and evaluation scores
  - Gemini API call counts and errors
  - Slack notification outcomes
  - Per-severity incident counts
  - Active incidents gauge
"""
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Summary,
    REGISTRY,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ── HTTP layer ────────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "opensre_http_requests_total",
    "Total HTTP requests received",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "opensre_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ── Incident analysis pipeline ────────────────────────────────────────────────

incidents_analyzed_total = Counter(
    "opensre_incidents_analyzed_total",
    "Total incidents analyzed",
    ["severity", "status"],  # status: success | error
)

analysis_duration_seconds = Histogram(
    "opensre_analysis_duration_seconds",
    "End-to-end incident analysis pipeline duration",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
)

# ── RCA quality signals ───────────────────────────────────────────────────────

rca_confidence_score = Histogram(
    "opensre_rca_confidence_score",
    "Distribution of AI RCA confidence scores",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

rca_evaluation_score = Histogram(
    "opensre_rca_evaluation_score",
    "Distribution of RCA evaluation scores",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

low_confidence_rca_total = Counter(
    "opensre_low_confidence_rca_total",
    "RCA responses with confidence below 0.5 (possible hallucination)",
)

# ── Gemini API ────────────────────────────────────────────────────────────────

gemini_api_calls_total = Counter(
    "opensre_gemini_api_calls_total",
    "Total Gemini API calls",
    ["outcome"],  # outcome: success | error | fallback
)

gemini_api_duration_seconds = Histogram(
    "opensre_gemini_api_duration_seconds",
    "Gemini API call latency",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0],
)

# ── Slack notifications ───────────────────────────────────────────────────────

slack_notifications_total = Counter(
    "opensre_slack_notifications_total",
    "Total Slack notifications sent",
    ["outcome"],  # outcome: success | error | skipped
)

# ── Infrastructure signals (from ingested metrics) ────────────────────────────

ingested_cpu_gauge = Gauge(
    "opensre_last_ingested_cpu_percent",
    "CPU % from the most recently analyzed incident",
)

ingested_memory_gauge = Gauge(
    "opensre_last_ingested_memory_percent",
    "Memory % from the most recently analyzed incident",
)

ingested_latency_gauge = Gauge(
    "opensre_last_ingested_latency_ms",
    "Latency ms from the most recently analyzed incident",
)

ingested_error_rate_gauge = Gauge(
    "opensre_last_ingested_error_rate_percent",
    "Error rate % from the most recently analyzed incident",
)

# ── Severity counters ─────────────────────────────────────────────────────────

incidents_by_severity = Counter(
    "opensre_incidents_by_severity_total",
    "Incidents broken down by detected severity",
    ["severity"],  # normal | warning | critical
)

# ── Storage ───────────────────────────────────────────────────────────────────

storage_operations_total = Counter(
    "opensre_storage_operations_total",
    "Storage read/write operations",
    ["operation", "backend", "outcome"],  # operation: save|get|list, backend: sqlite|firestore
)



# ── Semantic evaluation ───────────────────────────────────────────────────────

semantic_similarity_score = Histogram(
    "opensre_semantic_similarity_score",
    "Distribution of semantic similarity scores from embedding-based evaluation",
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

embedding_api_calls_total = Counter(
    "opensre_embedding_api_calls_total",
    "Total Gemini embedding API calls",
    ["outcome"],  # success | error | cache_hit
)

embedding_cache_size = Gauge(
    "opensre_embedding_cache_size",
    "Current number of cached embeddings",
)
def get_metrics() -> tuple[bytes, str]:
    """Return current metrics in Prometheus text format."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

# ── Streaming ingestion pipeline ──────────────────────────────────────────────

ingested_events_total = Counter(
    "opensre_ingested_events_total",
    "Total telemetry events ingested",
    ["source", "severity"],
)

ingestion_errors_total = Counter(
    "opensre_ingestion_errors_total",
    "Total ingestion errors (decode/normalize failures)",
    ["source", "error_type"],
)

correlated_incidents_total = Counter(
    "opensre_correlated_incidents_total",
    "Total incident candidates detected by correlation engine",
    ["incident_type", "service"],
)

incident_deduplicated_total = Counter(
    "opensre_incident_deduplicated_total",
    "Total incident candidates suppressed by deduplication",
)

correlation_window_size = Gauge(
    "opensre_correlation_window_size",
    "Current event count in rolling correlation windows",
    ["service", "severity"],
)

ai_triggers_total = Counter(
    "opensre_ai_triggers_total",
    "Total times AI RCA was triggered by correlation engine",
)
