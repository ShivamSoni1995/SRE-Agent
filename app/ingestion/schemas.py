"""
schemas.py — Unified internal event schema.

All telemetry sources (GCP Cloud Logging, direct API, test)
normalize into NormalizedEvent before entering the correlation engine.
This is the contract between ingestion and correlation.
"""
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime, timezone


class NormalizedEvent(BaseModel):
    """
    Single normalized telemetry event.
    All ingestion paths produce this schema.
    """
    tenant_id:  str = "default"
    source:     str = "unknown"          # gcp | test | api
    service:    str = "unknown"
    severity:   str = "INFO"             # DEBUG | INFO | WARNING | ERROR | CRITICAL
    message:    str = ""
    timestamp:  str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    labels:     Dict[str, Any] = {}
    raw:        Optional[Dict[str, Any]] = None   # original payload for debugging

    @property
    def is_error(self) -> bool:
        return self.severity.upper() in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY")

    @property
    def is_warning(self) -> bool:
        return self.severity.upper() in ("WARNING", "WARN")

    def to_correlation_key(self) -> str:
        """Key used to bucket events in the rolling window."""
        return f"{self.service}:{self.severity.upper()}"


class PubSubMessage(BaseModel):
    """Pub/Sub push delivery wrapper."""
    message: Dict[str, Any]
    subscription: str = ""


class TestIngestRequest(BaseModel):
    """Direct test ingestion — bypasses Pub/Sub."""
    service:   str = "opensre-mini"
    severity:  str = "ERROR"
    message:   str
    labels:    Dict[str, Any] = {}
    count:     int = Field(default=1, ge=1, le=100)   # repeat N times for load simulation
