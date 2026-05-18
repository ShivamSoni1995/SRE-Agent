"""
incident_processor.py — Async AI trigger pipeline.

Triggered ONLY when the correlation engine detects an incident candidate.
Reuses all existing components without modification:
  - context_builder
  - gemini_agent
  - evaluator
  - storage
  - slack_notifier

Flow:
    IncidentCandidate
        ↓
    build_context()         (existing context builder)
        ↓
    run_rca()               (existing Gemini agent)
        ↓
    evaluate_rca()          (existing hybrid evaluator)
        ↓
    storage.save_incident() (existing Firestore/SQLite)
        ↓
    notify_incident()       (existing Slack notifier)
        ↓
    Prometheus metrics

This runs as an asyncio.create_task() — non-blocking relative to ingestion.
The /ingest endpoint returns 202 before this completes.
"""
import uuid
import time
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.correlation.rules import IncidentCandidate
from app.parser.log_parser import parse_logs
from app.parser.metrics_parser import parse_metrics
from app.services.context_builder import build_context
from app.agent.gemini_agent import run_rca
from app.evaluator.scorer import evaluate_rca
from app.services import storage
from app.services.slack_notifier import notify_incident
from app.services.incident_lifecycle import initial_status
from app import metrics as m

logger = logging.getLogger(__name__)

SERVICE_URL = os.getenv("SERVICE_URL", "")
_SEVERITY_RANK = {"normal": 0, "warning": 1, "critical": 2}


async def process_incident_candidate(candidate: IncidentCandidate) -> Optional[str]:
    """
    Full async pipeline from incident candidate to stored RCA.

    Returns incident_id on success, None on failure.
    This is fire-and-forget from the ingestion endpoint's perspective.
    """
    incident_id    = f"INC-{uuid.uuid4().hex[:6].upper()}"
    timestamp      = datetime.now(timezone.utc).isoformat()
    pipeline_start = time.perf_counter()

    logger.info(
        f"Processing candidate: "
        f"incident_id={incident_id} "
        f"type={candidate.incident_type} "
        f"service={candidate.service}"
    )

    try:
        # ── Step 1: Convert candidate to analyze payload ──────────────────────
        payload = candidate.to_analyze_payload()

        # ── Step 2: Parse (reuse existing parsers) ────────────────────────────
        parsed_logs    = parse_logs(payload["logs"])
        parsed_metrics = parse_metrics(payload["metrics"])

        # ── Step 3: Build context (reuse existing context builder) ────────────
        context = build_context(
            parsed_logs,
            parsed_metrics,
            payload["events"],
        )

        # Enrich context with correlation metadata
        context["correlation_incident_type"] = candidate.incident_type
        context["correlation_rule"]          = candidate.rule_name
        context["correlation_window_start"]  = candidate.window_start
        context["correlation_window_end"]    = candidate.window_end
        context["correlation_error_count"]   = candidate.error_count

        # ── Step 4: Gemini RCA (reuse existing agent) ─────────────────────────
        gemini_start = time.perf_counter()
        try:
            rca = await run_rca(context)
            m.gemini_api_calls_total.labels(outcome="success").inc()
        except Exception as e:
            m.gemini_api_calls_total.labels(outcome="error").inc()
            logger.error(f"Gemini RCA failed for {incident_id}: {e}")
            raise
        finally:
            m.gemini_api_duration_seconds.observe(
                time.perf_counter() - gemini_start
            )

        # ── Step 5: Evaluate (reuse existing evaluator) ───────────────────────
        evaluation = await evaluate_rca(rca)
        confidence = float(rca.get("confidence", 0))
        severity   = parsed_metrics["severity"]

        m.rca_confidence_score.observe(confidence)
        m.rca_evaluation_score.observe(evaluation["score"])
        if confidence < 0.5:
            m.low_confidence_rca_total.inc()

        # ── Step 6: Store (reuse existing storage abstraction) ────────────────
        backend = "firestore" if os.getenv("USE_FIRESTORE") == "true" else "sqlite"
        try:
            storage.save_incident(
                incident_id=incident_id,
                timestamp=timestamp,
                logs_summary=", ".join(parsed_logs["errors"][:3]),
                metrics=payload["metrics"],
                events=payload["events"],
                root_cause=rca.get("root_cause", ""),
                confidence=confidence,
                evaluation_score=evaluation["score"],
                full_response={
                    **rca,
                    "source":        "correlation_engine",
                    "incident_type": candidate.incident_type,
                    "rule":          candidate.rule_name,
                    "window_start":  candidate.window_start,
                    "window_end":    candidate.window_end,
                    "error_count":   candidate.error_count,
                    "lifecycle":     initial_status(),
                    "conversation":  [],
                    "evaluation": {
                        "score":              evaluation["score"],
                        "semantic_score":     evaluation.get("semantic_score"),
                        "semantic_available": evaluation.get("semantic_available"),
                        "scoring_method":     evaluation.get("scoring_method"),
                        "matched_keywords":   evaluation.get("matched_keywords", []),
                    },
                },
            )
            m.storage_operations_total.labels(
                operation="save", backend=backend, outcome="success"
            ).inc()
        except Exception as e:
            m.storage_operations_total.labels(
                operation="save", backend=backend, outcome="error"
            ).inc()
            logger.error(f"Storage failed for {incident_id}: {e}")

        # ── Step 7: Slack notification ────────────────────────────────────────
        threshold = os.getenv("SLACK_SEVERITY_THRESHOLD", "warning").lower()
        should_notify = (
            _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(threshold, 1)
        )

        if should_notify:
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

        # ── Step 8: Final metrics ─────────────────────────────────────────────
        duration = time.perf_counter() - pipeline_start
        m.analysis_duration_seconds.observe(duration)
        m.incidents_analyzed_total.labels(
            severity=severity, status="success"
        ).inc()
        m.incidents_by_severity.labels(severity=severity).inc()

        logger.info(
            f"Pipeline complete: "
            f"incident_id={incident_id} "
            f"confidence={confidence:.2f} "
            f"eval_score={evaluation['score']:.2f} "
            f"duration={duration:.2f}s"
        )

        return incident_id

    except Exception as e:
        m.incidents_analyzed_total.labels(
            severity="unknown", status="error"
        ).inc()
        m.analysis_duration_seconds.observe(time.perf_counter() - pipeline_start)
        logger.error(
            f"Pipeline failed for {incident_id}: {e}",
            exc_info=True,
        )
        return None
