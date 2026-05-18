"""
engine.py — Correlation engine orchestrator.

Wires together:
  window_store  →  receives every normalized event
  rules         →  evaluated after each event
  candidate_store → deduplication

The engine is the decision point:
  - most events pass through silently (window updated, no incident)
  - threshold-breaching windows produce an IncidentCandidate
  - duplicates are suppressed
  - new candidates are returned to the pipeline for AI processing

Usage:
    from app.correlation.engine import correlation_engine
    candidate = correlation_engine.add_event(event, metrics)
    if candidate:
        await pipeline.process(candidate)
"""
import logging
from typing import Optional, Dict, Any

from app.ingestion.schemas import NormalizedEvent
from app.correlation.windows import window_store
from app.correlation.rules import RULES, IncidentCandidate
from app.correlation.incident_store import candidate_store
from app import metrics as m

logger = logging.getLogger(__name__)


class CorrelationEngine:

    def add_event(
        self,
        event: NormalizedEvent,
        current_metrics: Optional[Dict[str, Any]] = None,
    ) -> Optional[IncidentCandidate]:
        """
        Process one normalized event.

        1. Add to rolling window
        2. Evaluate all rules against updated window
        3. Deduplicate
        4. Return candidate if incident detected, else None

        This is synchronous and fast (~1ms).
        Gemini is NOT called here — that happens in the pipeline.
        """
        # Update window
        window = window_store.add_event(event)

        # Update Prometheus window size metric
        m.correlation_window_size.labels(
            service=event.service,
            severity=event.severity,
        ).set(window.event_count)

        # Only run rules on error/warning events — skip INFO
        if not (event.is_error or event.is_warning):
            return None

        metrics = current_metrics or {}

        # Evaluate rules in order — first match wins
        for rule_fn in RULES:
            candidate = rule_fn(window, event.service, metrics)
            if candidate is None:
                continue

            # Check deduplication
            sig = candidate.signature()
            is_dup, last_seen = candidate_store.is_duplicate(sig)

            if is_dup:
                logger.info(
                    f"Incident suppressed (duplicate): "
                    f"type={candidate.incident_type} service={event.service} "
                    f"last_seen={last_seen}"
                )
                m.incident_deduplicated_total.inc()
                return None

            # New incident — record and return
            candidate_store.record(sig)
            m.correlated_incidents_total.labels(
                incident_type=candidate.incident_type,
                service=event.service,
            ).inc()
            m.ai_triggers_total.inc()

            logger.info(
                f"Incident candidate detected: "
                f"type={candidate.incident_type} "
                f"service={event.service} "
                f"rule={candidate.rule_name} "
                f"errors={candidate.error_count}"
            )
            return candidate

        return None

    def stats(self) -> Dict:
        return {
            "windows":    window_store.stats(),
            "candidates": candidate_store.stats(),
            "rules":      len(RULES),
        }


# Singleton
correlation_engine = CorrelationEngine()
