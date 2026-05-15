import uuid
import time
import logging
import os
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.parser.log_parser import parse_logs
from app.parser.metrics_parser import parse_metrics
from app.services.context_builder import build_context
from app.agent.gemini_agent import run_rca
from app.evaluator.scorer import evaluate_rca
from app.services import storage
from app.services.slack_notifier import notify_incident
from app import metrics as m

router = APIRouter()
logger = logging.getLogger(__name__)

SERVICE_URL = os.getenv("SERVICE_URL", "")
SLACK_SEVERITY_THRESHOLD = os.getenv("SLACK_SEVERITY_THRESHOLD", "warning")
_SEVERITY_RANK = {"normal": 0, "warning": 1, "critical": 2}


def _should_notify(severity: str) -> bool:
    threshold = SLACK_SEVERITY_THRESHOLD.lower()
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(threshold, 1)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_incident(req: AnalyzeRequest):
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    timestamp = datetime.now(timezone.utc).isoformat()
    pipeline_start = time.perf_counter()

    logger.info(f"Starting analysis for {incident_id}")

    try:
        # Parse
        parsed_logs = parse_logs(req.logs)
        parsed_metrics = parse_metrics(req.metrics)
        context = build_context(parsed_logs, parsed_metrics, req.events)

        # AI RCA — timed separately
        gemini_start = time.perf_counter()
        try:
            rca = await run_rca(context)
            m.gemini_api_calls_total.labels(outcome="success").inc()
        except Exception as e:
            m.gemini_api_calls_total.labels(outcome="error").inc()
            raise
        finally:
            m.gemini_api_duration_seconds.observe(time.perf_counter() - gemini_start)

        evaluation = evaluate_rca(rca)
        severity = parsed_metrics["severity"]
        confidence = float(rca.get("confidence", 0))

        # Record RCA quality metrics
        m.rca_confidence_score.observe(confidence)
        m.rca_evaluation_score.observe(evaluation["score"])
        if confidence < 0.5:
            m.low_confidence_rca_total.inc()

        # Record ingested infrastructure signals
        m.ingested_cpu_gauge.set(req.metrics.get("cpu", 0))
        m.ingested_memory_gauge.set(req.metrics.get("memory", 0))
        m.ingested_latency_gauge.set(req.metrics.get("latency", 0))
        m.ingested_error_rate_gauge.set(req.metrics.get("error_rate", 0))

        # Severity counter
        m.incidents_by_severity.labels(severity=severity).inc()

        # Storage
        try:
            storage.save_incident(
                incident_id=incident_id,
                timestamp=timestamp,
                logs_summary=", ".join(parsed_logs["errors"][:3]),
                metrics=req.metrics,
                events=req.events,
                root_cause=rca.get("root_cause", ""),
                confidence=confidence,
                evaluation_score=evaluation["score"],
                full_response=rca,
            )
            m.storage_operations_total.labels(
                operation="save",
                backend="firestore" if os.getenv("USE_FIRESTORE") == "true" else "sqlite",
                outcome="success",
            ).inc()
        except Exception as e:
            m.storage_operations_total.labels(
                operation="save",
                backend="firestore" if os.getenv("USE_FIRESTORE") == "true" else "sqlite",
                outcome="error",
            ).inc()
            logger.error(f"Storage failed: {e}")

        # Slack
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

        # Pipeline total duration
        m.analysis_duration_seconds.observe(time.perf_counter() - pipeline_start)
        m.incidents_analyzed_total.labels(severity=severity, status="success").inc()

        return AnalyzeResponse(
            incident_id=incident_id,
            issue=rca.get("issue", ""),
            root_cause=rca.get("root_cause", ""),
            solution=rca.get("solution", ""),
            confidence=confidence,
            evaluation_score=evaluation["score"],
            matched_keywords=evaluation["matched_keywords"],
            timestamp=timestamp,
        )

    except Exception as e:
        m.incidents_analyzed_total.labels(severity="unknown", status="error").inc()
        m.analysis_duration_seconds.observe(time.perf_counter() - pipeline_start)
        logger.error(f"Analysis failed for {incident_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/incidents")
async def list_incidents(limit: int = 20):
    return storage.list_incidents(limit=limit)


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    incident = storage.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident
