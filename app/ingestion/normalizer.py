"""
normalizer.py — Normalize GCP Cloud Logging entries into NormalizedEvent.

GCP Cloud Logging LogEntry has several payload types:
  - textPayload:  plain string log message
  - jsonPayload:  structured JSON object
  - protoPayload: proto-serialized (HTTP requests, audit logs)

Severity mapping from GCP levels to our internal schema:
  DEFAULT → INFO
  DEBUG   → DEBUG
  INFO    → INFO
  NOTICE  → INFO
  WARNING → WARNING
  ERROR   → ERROR
  CRITICAL → CRITICAL
  ALERT   → CRITICAL
  EMERGENCY → CRITICAL

Resource types we care about:
  cloud_run_revision    → Cloud Run services
  gce_instance          → Compute Engine VMs
  k8s_container         → GKE containers
  generic_task          → generic

Self-monitoring note:
When routing opensre-mini's own Cloud Run logs through Pub/Sub,
the resource.type = "cloud_run_revision" and
resource.labels.service_name = "opensre-mini".
"""
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.ingestion.schemas import NormalizedEvent

logger = logging.getLogger(__name__)

# GCP severity → internal severity
GCP_SEVERITY_MAP = {
    "DEFAULT":   "INFO",
    "DEBUG":     "DEBUG",
    "INFO":      "INFO",
    "NOTICE":    "INFO",
    "WARNING":   "WARNING",
    "WARN":      "WARNING",
    "ERROR":     "ERROR",
    "CRITICAL":  "CRITICAL",
    "ALERT":     "CRITICAL",
    "EMERGENCY": "CRITICAL",
}

# Patterns that identify error signals in text
ERROR_SIGNAL_PATTERNS = [
    r"(?i)\berror\b",
    r"(?i)\bfailed\b",
    r"(?i)\btimeout\b",
    r"(?i)\bexception\b",
    r"(?i)\boom\b",
    r"(?i)\bkilled\b",
    r"(?i)\bcrash\b",
    r"(?i)\b429\b",
    r"(?i)\bunreachable\b",
    r"(?i)\bconnection refused\b",
]


def normalize_gcp_log_entry(
    payload: Dict[str, Any],
    source: str = "gcp",
) -> Optional[NormalizedEvent]:
    """
    Convert a GCP Cloud Logging LogEntry dict into a NormalizedEvent.
    Returns None if the entry should be skipped (e.g. pure health checks).
    """
    try:
        # ── Severity ──────────────────────────────────────────────────────────
        raw_severity = payload.get("severity", "DEFAULT").upper()
        severity = GCP_SEVERITY_MAP.get(raw_severity, "INFO")

        # ── Message ───────────────────────────────────────────────────────────
        message = _extract_message(payload)
        if not message:
            return None

        # Skip noisy health check logs
        if _is_health_check(message):
            return None

        # ── Service name ──────────────────────────────────────────────────────
        service = _extract_service(payload)

        # ── Timestamp ─────────────────────────────────────────────────────────
        timestamp = _extract_timestamp(payload)

        # ── Labels ────────────────────────────────────────────────────────────
        labels = _extract_labels(payload)

        # Upgrade severity if message contains error signals
        # (some GCP entries have DEFAULT severity but ERROR content)
        if severity == "INFO" and _has_error_signal(message):
            severity = "ERROR"
            logger.debug(f"Severity upgraded to ERROR based on message content")

        event = NormalizedEvent(
            tenant_id="default",
            source=source,
            service=service,
            severity=severity,
            message=message[:500],   # cap message length
            timestamp=timestamp,
            labels=labels,
            raw=payload,
        )

        logger.debug(
            f"Normalized: service={service} severity={severity} "
            f"message={message[:60]}"
        )
        return event

    except Exception as e:
        logger.error(f"Normalization failed: {e} payload_keys={list(payload.keys())}")
        return None


def normalize_test_event(
    service: str,
    severity: str,
    message: str,
    labels: Dict[str, Any] = {},
) -> NormalizedEvent:
    """Create a NormalizedEvent directly from test parameters."""
    return NormalizedEvent(
        tenant_id="default",
        source="test",
        service=service,
        severity=severity.upper(),
        message=message,
        timestamp=datetime.now(timezone.utc).isoformat(),
        labels=labels,
        raw=None,
    )


# ── Private helpers ───────────────────────────────────────────────────────────

def _extract_message(payload: Dict[str, Any]) -> str:
    """Extract human-readable message from any payload type."""
    # textPayload is the most common for application logs
    if "textPayload" in payload:
        return str(payload["textPayload"]).strip()

    # jsonPayload — look for common message field names
    if "jsonPayload" in payload:
        jp = payload["jsonPayload"]
        for field in ("message", "msg", "text", "log", "error", "details"):
            if field in jp:
                return str(jp[field]).strip()
        # Fall back to serializing the whole jsonPayload
        return str(jp)[:300]

    # protoPayload — usually HTTP request logs, less useful for RCA
    if "protoPayload" in payload:
        pp = payload["protoPayload"]
        status = pp.get("status", "")
        method = pp.get("requestMethod", "")
        url    = pp.get("resourceName", pp.get("requestUrl", ""))
        return f"{method} {url} status={status}".strip()

    return ""


def _extract_service(payload: Dict[str, Any]) -> str:
    """Extract service name from resource labels."""
    resource = payload.get("resource", {})
    labels   = resource.get("labels", {})
    res_type = resource.get("type", "")

    # Cloud Run
    if res_type == "cloud_run_revision":
        return labels.get("service_name", "cloud-run-service")

    # GKE
    if res_type == "k8s_container":
        container = labels.get("container_name", "")
        namespace = labels.get("namespace_name", "")
        return f"{namespace}/{container}" if namespace else container

    # Compute Engine
    if res_type == "gce_instance":
        return labels.get("instance_id", "gce-instance")

    # Log name fallback — e.g. projects/xxx/logs/opensre-mini
    log_name = payload.get("logName", "")
    if "/logs/" in log_name:
        return log_name.split("/logs/")[-1].replace("%2F", "/")

    return labels.get("service_name", "unknown-service")


def _extract_timestamp(payload: Dict[str, Any]) -> str:
    """Extract ISO timestamp, defaulting to now."""
    for field in ("timestamp", "receiveTimestamp"):
        ts = payload.get(field, "")
        if ts:
            # Normalize GCP format: 2026-05-18T10:00:00.000000Z
            return ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
    return datetime.now(timezone.utc).isoformat()


def _extract_labels(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Merge resource labels and log-level labels."""
    labels = {}
    resource = payload.get("resource", {})
    labels.update(resource.get("labels", {}))
    labels.update(payload.get("labels", {}))
    labels["resource_type"] = resource.get("type", "unknown")
    labels["log_name"] = payload.get("logName", "")
    return labels


def _is_health_check(message: str) -> bool:
    """Skip noisy health check and readiness probe logs."""
    lower = message.lower()
    skip_patterns = [
        "get /health",
        "get /metrics",
        "get /favicon",
        "readiness probe",
        "liveness probe",
        "200 ok",
    ]
    return any(p in lower for p in skip_patterns)


def _has_error_signal(message: str) -> bool:
    """Check if a message contains error-level signals."""
    return any(re.search(p, message) for p in ERROR_SIGNAL_PATTERNS)
