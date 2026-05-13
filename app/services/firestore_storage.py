import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_db = None


def _get_db():
    global _db
    if _db is None:
        from google.cloud import firestore
        _db = firestore.Client()
    return _db


COLLECTION = "incidents"


def save_incident(
    incident_id: str,
    timestamp: str,
    logs_summary: str,
    metrics: Dict[str, Any],
    events: List[str],
    root_cause: str,
    confidence: float,
    evaluation_score: float,
    full_response: Dict[str, Any],
) -> None:
    db = _get_db()
    doc = {
        "id": incident_id,
        "timestamp": timestamp,
        "logs_summary": logs_summary,
        "metrics": metrics,
        "events": events,
        "root_cause": root_cause,
        "confidence": confidence,
        "evaluation_score": evaluation_score,
        "full_response": full_response,
    }
    db.collection(COLLECTION).document(incident_id).set(doc)
    logger.info(f"Saved incident {incident_id} to Firestore")


def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    db = _get_db()
    doc = db.collection(COLLECTION).document(incident_id).get()
    if doc.exists:
        return doc.to_dict()
    return None


def list_incidents(limit: int = 20) -> List[Dict[str, Any]]:
    db = _get_db()
    docs = (
        db.collection(COLLECTION)
        .order_by("timestamp", direction="DESCENDING")
        .limit(limit)
        .stream()
    )
    return [d.to_dict() for d in docs]
