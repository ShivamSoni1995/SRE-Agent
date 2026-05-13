import uuid
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
    logger.info(f"Starting analysis for {incident_id}")

    try:
        parsed_logs = parse_logs(req.logs)
        parsed_metrics = parse_metrics(req.metrics)
        context = build_context(parsed_logs, parsed_metrics, req.events)
        rca = await run_rca(context)
        evaluation = evaluate_rca(rca)
        severity = parsed_metrics["severity"]

        storage.save_incident(
            incident_id=incident_id,
            timestamp=timestamp,
            logs_summary=", ".join(parsed_logs["errors"][:3]),
            metrics=req.metrics,
            events=req.events,
            root_cause=rca.get("root_cause", ""),
            confidence=rca.get("confidence", 0),
            evaluation_score=evaluation["score"],
            full_response=rca,
        )

        if _should_notify(severity):
            await notify_incident(
                incident_id=incident_id,
                rca=rca,
                evaluation_score=evaluation["score"],
                severity=severity,
                service_url=SERVICE_URL,
            )

        return AnalyzeResponse(
            incident_id=incident_id,
            issue=rca.get("issue", ""),
            root_cause=rca.get("root_cause", ""),
            solution=rca.get("solution", ""),
            confidence=rca.get("confidence", 0),
            evaluation_score=evaluation["score"],
            matched_keywords=evaluation["matched_keywords"],
            timestamp=timestamp,
        )

    except Exception as e:
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
