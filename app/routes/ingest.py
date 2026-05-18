"""
ingest.py — Telemetry ingestion endpoints.

POST /ingest/gcp    — Pub/Sub push subscription receiver
POST /ingest/test   — Direct test ingestion (no Pub/Sub needed)
GET  /ingest/status — Pipeline health and window stats
"""
import asyncio
import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks

from app.ingestion.schemas import PubSubMessage, TestIngestRequest
from app.ingestion.pubsub_decoder import (
    decode_pubsub_message,
    is_cloud_logging_entry,
    extract_message_id,
)
from app.ingestion.normalizer import normalize_gcp_log_entry, normalize_test_event
from app.correlation.engine import correlation_engine
from app.pipeline.incident_processor import process_incident_candidate
from app import metrics as m

router = APIRouter(prefix="/ingest", tags=["ingestion"])
logger = logging.getLogger(__name__)


# ── GCP / Pub/Sub endpoint ────────────────────────────────────────────────────

@router.post("/gcp", status_code=202)
async def ingest_gcp(request: Request):
    """
    Receive Pub/Sub push delivery from Cloud Logging sink.

    Returns 202 immediately — pipeline runs as background asyncio task.
    Pub/Sub considers 202 a successful acknowledgement.

    Setup:
        1. Create Pub/Sub topic
        2. Create Cloud Logging sink → topic
        3. Create push subscription → this endpoint
    """
    try:
        envelope = await request.json()
    except Exception:
        # Return 204 (not 400) — bad messages shouldn't cause Pub/Sub retries
        m.ingestion_errors_total.labels(
            source="gcp", error_type="invalid_json"
        ).inc()
        logger.warning("GCP ingest: invalid JSON body — returning 204")
        return {"status": "skipped", "reason": "invalid_json"}

    message_id = extract_message_id(envelope)
    logger.debug(f"GCP ingest received: messageId={message_id}")

    # Decode Pub/Sub wrapper
    payload, attributes = decode_pubsub_message(envelope)
    if payload is None:
        m.ingestion_errors_total.labels(
            source="gcp", error_type="decode_failed"
        ).inc()
        # 204 to avoid Pub/Sub retry storms on permanently bad messages
        return {"status": "skipped", "reason": "decode_failed"}

    # Normalize to unified schema
    if is_cloud_logging_entry(payload):
        event = normalize_gcp_log_entry(payload, source="gcp")
    else:
        # Generic Pub/Sub message — treat as text
        text = str(payload.get("message", payload.get("data", str(payload))))
        from app.ingestion.normalizer import normalize_test_event
        event = normalize_test_event(
            service=attributes.get("service", "unknown"),
            severity=attributes.get("severity", "INFO"),
            message=text,
            labels=attributes,
        )

    if event is None:
        # Normalized to None = should be skipped (health check, etc.)
        return {"status": "skipped", "reason": "filtered"}

    m.ingested_events_total.labels(
        source="gcp", severity=event.severity
    ).inc()

    # Run correlation + pipeline in background — return 202 immediately
    asyncio.create_task(_correlate_and_process(event))

    return {
        "status":     "accepted",
        "message_id": message_id,
        "service":    event.service,
        "severity":   event.severity,
    }


# ── Test ingestion endpoint ───────────────────────────────────────────────────

@router.post("/test", status_code=202)
async def ingest_test(req: TestIngestRequest):
    """
    Direct test ingestion — bypasses Pub/Sub entirely.
    Use this to simulate events and trigger correlation rules.

    Set count > 1 to simulate a burst of events (load simulation).
    Returns immediately — pipeline runs in background.

    Example — trigger database_failure rule:
        POST /ingest/test
        {
            "service": "payments-api",
            "severity": "ERROR",
            "message": "database timeout: connection pool exhausted",
            "count": 25
        }
    """
    events_queued = 0

    for i in range(req.count):
        event = normalize_test_event(
            service=req.service,
            severity=req.severity,
            message=req.message,
            labels={**req.labels, "test_batch_index": i},
        )
        m.ingested_events_total.labels(
            source="test", severity=event.severity
        ).inc()
        asyncio.create_task(_correlate_and_process(event))
        events_queued += 1

    logger.info(
        f"Test ingest: service={req.service} "
        f"severity={req.severity} "
        f"count={events_queued} "
        f"message={req.message[:60]}"
    )

    return {
        "status":       "accepted",
        "events_queued": events_queued,
        "service":      req.service,
        "severity":     req.severity,
        "note": (
            "Pipeline runs async. "
            "Poll GET /incidents to see RCA when incident is detected."
        ),
    }


# ── Status endpoint ───────────────────────────────────────────────────────────

@router.get("/status")
async def ingestion_status():
    """
    Current state of the correlation engine and rolling windows.
    Useful for monitoring and debugging.
    """
    return correlation_engine.stats()


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _correlate_and_process(event) -> None:
    """
    Background task: run correlation engine, trigger pipeline if incident detected.
    Errors are caught and logged — must not propagate to break the event loop.
    """
    try:
        candidate = correlation_engine.add_event(event)
        if candidate:
            incident_id = await process_incident_candidate(candidate)
            if incident_id:
                logger.info(
                    f"Auto-incident created: {incident_id} "
                    f"from correlation rule '{candidate.rule_name}'"
                )
    except Exception as e:
        m.ingestion_errors_total.labels(
            source=event.source, error_type="pipeline_error"
        ).inc()
        logger.error(f"Background pipeline error: {e}", exc_info=True)
