"""
webhooks.py — Reactive alert ingestion.

Accepts webhooks from Grafana and PagerDuty.
Converts alert payloads to AnalyzeRequest format,
runs deduplication, then triggers the full analysis pipeline.

Grafana:    POST /webhook/grafana
PagerDuty:  POST /webhook/pagerduty
Status:     POST /incidents/{id}/status
Chat:       POST /incidents/{id}/chat
            GET  /incidents/{id}/chat
"""
import uuid
import time
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.parser.log_parser import parse_logs
from app.parser.metrics_parser import parse_metrics
from app.services.context_builder import build_context
from app.agent.gemini_agent import run_rca
from app.evaluator.scorer import evaluate_rca
from app.services import storage
from app.services.slack_notifier import notify_incident
from app.services.deduplication import compute_signature, is_duplicate, record_seen
from app.services.incident_lifecycle import initial_status, transition
from app.services.conversation_store import append_message, to_gemini_messages
from app import metrics as m
import httpx
import json

router = APIRouter()
logger = logging.getLogger(__name__)

SERVICE_URL = os.getenv("SERVICE_URL", "")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


# ── Shared pipeline ───────────────────────────────────────────────────────────

async def _run_pipeline(
    logs: str,
    metrics: Dict[str, Any],
    events: List[str],
    source: str = "api",
) -> Dict[str, Any]:
    """
    Core analysis pipeline reused by all ingestion paths.
    Returns the full result dict.
    """
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    timestamp = datetime.now(timezone.utc).isoformat()
    pipeline_start = time.perf_counter()

    parsed_logs = parse_logs(logs)
    parsed_metrics = parse_metrics(metrics)
    context = build_context(parsed_logs, parsed_metrics, events)

    gemini_start = time.perf_counter()
    try:
        rca = await run_rca(context)
        m.gemini_api_calls_total.labels(outcome="success").inc()
    except Exception:
        m.gemini_api_calls_total.labels(outcome="error").inc()
        raise
    finally:
        m.gemini_api_duration_seconds.observe(time.perf_counter() - gemini_start)

    evaluation = await evaluate_rca(rca)
    severity = parsed_metrics["severity"]
    confidence = float(rca.get("confidence", 0))

    m.rca_confidence_score.observe(confidence)
    m.rca_evaluation_score.observe(evaluation["score"])
    m.incidents_by_severity.labels(severity=severity).inc()
    m.ingested_cpu_gauge.set(metrics.get("cpu", 0))
    m.ingested_memory_gauge.set(metrics.get("memory", 0))
    m.ingested_latency_gauge.set(metrics.get("latency", 0))
    m.ingested_error_rate_gauge.set(metrics.get("error_rate", 0))

    lifecycle = initial_status()

    storage.save_incident(
        incident_id=incident_id,
        timestamp=timestamp,
        logs_summary=", ".join(parsed_logs["errors"][:3]),
        metrics=metrics,
        events=events,
        root_cause=rca.get("root_cause", ""),
        confidence=confidence,
        evaluation_score=evaluation["score"],
        full_response={
            **rca,
            "source": source,
            "lifecycle": lifecycle,
            "conversation": [],
        },
    )
    m.storage_operations_total.labels(
        operation="save",
        backend="firestore" if os.getenv("USE_FIRESTORE") == "true" else "sqlite",
        outcome="success",
    ).inc()

    slack_sent = False
    if _should_notify(severity):
        slack_sent = await notify_incident(
            incident_id=incident_id,
            rca=rca,
            evaluation_score=evaluation["score"],
            severity=severity,
            service_url=SERVICE_URL,
        )
        m.slack_notifications_total.labels(
            outcome="success" if slack_sent else "error"
        ).inc()
    else:
        m.slack_notifications_total.labels(outcome="skipped").inc()

    m.analysis_duration_seconds.observe(time.perf_counter() - pipeline_start)
    m.incidents_analyzed_total.labels(severity=severity, status="success").inc()

    return {
        "incident_id":        incident_id,
        "issue":              rca.get("issue", ""),
        "root_cause":         rca.get("root_cause", ""),
        "solution":           rca.get("solution", ""),
        "confidence":         confidence,
        "evaluation_score":   evaluation["score"],
        "matched_keywords":   evaluation["matched_keywords"],
        "semantic_score":     evaluation.get("semantic_score"),
        "semantic_available": evaluation.get("semantic_available", False),
        "scoring_method":     evaluation.get("scoring_method", "keyword+completeness"),
        "severity":           severity,
        "source":             source,
        "timestamp":          timestamp,
    }


_SEVERITY_RANK = {"normal": 0, "warning": 1, "critical": 2}

def _should_notify(severity: str) -> bool:
    threshold = os.getenv("SLACK_SEVERITY_THRESHOLD", "warning").lower()
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(threshold, 1)


# ── Grafana webhook ───────────────────────────────────────────────────────────

@router.post("/webhook/grafana", status_code=200)
async def grafana_webhook(request: Request):
    """
    Accepts Grafana alerting webhooks.
    Auto-converts alert state to logs + metrics and runs analysis.
    Skips if alert is resolved or deduplicated.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    status = body.get("status", "")
    if status == "resolved":
        logger.info("Grafana resolved alert — skipping analysis")
        return {"status": "skipped", "reason": "alert resolved"}

    alerts = body.get("alerts", [body])
    results = []

    for alert in alerts:
        labels   = alert.get("labels", {})
        values   = alert.get("values", {})
        annotations = alert.get("annotations", {})

        alert_name = labels.get("alertname", "unknown_alert")
        severity   = labels.get("severity", "warning")
        summary    = annotations.get("summary", alert_name)
        description = annotations.get("description", "")

        # Build synthetic logs from alert metadata
        logs = f"ERROR {alert_name}: {summary}\n"
        if description:
            logs += f"ERROR {description}\n"

        # Extract metric values Grafana may pass
        metrics = {
            "cpu":        float(values.get("cpu", values.get("CPU", 0))),
            "memory":     float(values.get("memory", values.get("mem", 0))),
            "latency":    float(values.get("latency", values.get("response_time", 0))),
            "error_rate": float(values.get("error_rate", values.get("errors", 0))),
        }

        events = [f"grafana_alert:{alert_name}"]
        if labels.get("service"):
            events.append(f"service:{labels['service']}")

        # Deduplication
        parsed = parse_logs(logs)
        parsed_m = parse_metrics(metrics)
        sig = compute_signature(
            parsed["errors"],
            parsed_m["severity"],
            labels.get("service", "unknown"),
        )
        is_dup, last_seen = is_duplicate(sig)
        if is_dup:
            logger.info(f"Duplicate alert suppressed (last seen: {last_seen})")
            results.append({"status": "deduplicated", "last_seen": last_seen})
            continue

        record_seen(sig)
        result = await _run_pipeline(logs, metrics, events, source="grafana")
        results.append(result)
        logger.info(f"Grafana alert → {result['incident_id']}")

    return {"processed": len(results), "results": results}


# ── PagerDuty webhook ─────────────────────────────────────────────────────────

@router.post("/webhook/pagerduty", status_code=200)
async def pagerduty_webhook(request: Request):
    """
    Accepts PagerDuty v3 webhook events.
    Triggers analysis on incident.triggered and incident.escalated.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    events = body.get("messages", body.get("events", []))
    if not events:
        events = [body]

    results = []
    for event in events:
        event_type = event.get("event", {}).get("event_type", "")
        if event_type not in ("incident.triggered", "incident.escalated", ""):
            logger.info(f"PagerDuty event '{event_type}' skipped")
            continue

        incident = event.get("event", {}).get("data", event)
        title   = incident.get("title", incident.get("summary", "PagerDuty incident"))
        service = incident.get("service", {}).get("name", "unknown")
        urgency = incident.get("urgency", "high")

        logs = f"ERROR PagerDuty incident triggered: {title}\nERROR service: {service}\n"
        metrics = {"cpu": 0, "memory": 0, "latency": 0, "error_rate": 0}
        pd_events = [f"pagerduty:{event_type}", f"service:{service}", f"urgency:{urgency}"]

        result = await _run_pipeline(logs, metrics, pd_events, source="pagerduty")
        results.append(result)
        logger.info(f"PagerDuty event → {result['incident_id']}")

    return {"processed": len(results), "results": results}


# ── Incident status update ────────────────────────────────────────────────────

class StatusUpdate(BaseModel):
    status: str
    actor: str = "api"
    note: Optional[str] = None


@router.post("/incidents/{incident_id}/status")
async def update_incident_status(incident_id: str, update: StatusUpdate):
    """Update incident lifecycle status: open → acknowledged → resolved."""
    incident = storage.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    full = incident.get("full_response", {})
    lifecycle = full.get("lifecycle", {"status": "open"})

    try:
        new_lifecycle = transition(
            lifecycle,
            update.status,
            actor=update.actor,
            note=update.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    full["lifecycle"] = new_lifecycle
    storage.save_incident(
        incident_id=incident_id,
        timestamp=incident.get("timestamp", ""),
        logs_summary=incident.get("logs_summary", ""),
        metrics=incident.get("metrics", {}),
        events=incident.get("events", []),
        root_cause=incident.get("root_cause", ""),
        confidence=incident.get("confidence", 0),
        evaluation_score=incident.get("evaluation_score", 0),
        full_response=full,
    )

    return {"incident_id": incident_id, "lifecycle": new_lifecycle}


# ── Multi-turn chat ───────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    message: str
    actor: str = "user"


@router.post("/incidents/{incident_id}/chat")
async def chat_with_incident(incident_id: str, msg: ChatMessage):
    """
    Multi-turn conversational interface for any stored incident.
    Maintains full conversation history. Gemini has full RCA context.
    """
    incident = storage.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    full = incident.get("full_response", {})
    rca = {k: full.get(k, "") for k in ["issue", "root_cause", "solution", "confidence"]}
    history = full.get("conversation", [])

    # Build context-aware system prompt
    system = f"""You are an SRE expert helping investigate a specific incident.

Incident ID: {incident_id}
Issue: {rca.get('issue')}
Root cause: {rca.get('root_cause')}
Solution: {rca.get('solution')}
Confidence: {rca.get('confidence')}

Answer the user's follow-up questions about this incident.
Be specific, technical, and concise. Reference the incident details above.
If asked about something outside this incident's scope, say so clearly."""

    # Append user message
    history = append_message(history, "user", msg.message)

    # Build Gemini payload with full conversation history
    gemini_messages = to_gemini_messages(history[:-1])  # exclude latest user msg
    gemini_messages.append({"role": "user", "parts": [{"text": msg.message}]})

    api_key = os.getenv("GEMINI_API_KEY", "")
    response_text = "I'm unable to respond right now — Gemini API key not configured."

    if api_key:
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": gemini_messages,
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 512},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{GEMINI_API_URL}?key={api_key}", json=payload
                )
                resp.raise_for_status()
                response_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error(f"Chat Gemini call failed: {e}")
            response_text = f"Analysis error: {e}"

    # Append assistant response and persist
    history = append_message(history, "assistant", response_text)
    full["conversation"] = history
    storage.save_incident(
        incident_id=incident_id,
        timestamp=incident.get("timestamp", ""),
        logs_summary=incident.get("logs_summary", ""),
        metrics=incident.get("metrics", {}),
        events=incident.get("events", []),
        root_cause=incident.get("root_cause", ""),
        confidence=incident.get("confidence", 0),
        evaluation_score=incident.get("evaluation_score", 0),
        full_response=full,
    )

    return {
        "incident_id":  incident_id,
        "response":     response_text,
        "message_count": len(history),
    }


@router.get("/incidents/{incident_id}/chat")
async def get_chat_history(incident_id: str):
    """Return full conversation history for an incident."""
    incident = storage.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    history = incident.get("full_response", {}).get("conversation", [])
    return {"incident_id": incident_id, "messages": history, "count": len(history)}


# ── Dedup stats ───────────────────────────────────────────────────────────────

@router.get("/system/dedup")
async def dedup_stats():
    from app.services.deduplication import get_store_stats
    return get_store_stats()
