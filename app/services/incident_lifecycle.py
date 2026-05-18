"""
incident_lifecycle.py — Open / acknowledged / resolved state machine.

Tracks incident status transitions and stores them alongside the
RCA record. Enables the UI to show live incident state and lets
Slack buttons update status without a full re-analysis.

States: open → acknowledged → resolved
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

VALID_TRANSITIONS = {
    "open":         ["acknowledged", "resolved"],
    "acknowledged": ["resolved"],
    "resolved":     [],
}


def initial_status() -> Dict[str, Any]:
    return {
        "status":       "open",
        "opened_at":    datetime.now(timezone.utc).isoformat(),
        "acknowledged_at": None,
        "resolved_at":  None,
        "acknowledged_by": None,
        "resolved_by":  None,
        "resolution_note": None,
    }


def transition(
    current: Dict[str, Any],
    new_status: str,
    actor: str = "system",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Apply a status transition. Returns updated lifecycle dict.
    Raises ValueError on invalid transition.
    """
    current_status = current.get("status", "open")
    allowed = VALID_TRANSITIONS.get(current_status, [])

    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition from '{current_status}' to '{new_status}'. "
            f"Allowed: {allowed}"
        )

    updated = dict(current)
    updated["status"] = new_status
    now = datetime.now(timezone.utc).isoformat()

    if new_status == "acknowledged":
        updated["acknowledged_at"] = now
        updated["acknowledged_by"] = actor
    elif new_status == "resolved":
        updated["resolved_at"] = now
        updated["resolved_by"] = actor
        updated["resolution_note"] = note

    logger.info(f"Incident status: {current_status} → {new_status} by {actor}")
    return updated


def is_terminal(lifecycle: Dict[str, Any]) -> bool:
    return lifecycle.get("status") == "resolved"


def format_duration(lifecycle: Dict[str, Any]) -> Optional[str]:
    """Return human-readable time from open to resolution."""
    opened = lifecycle.get("opened_at")
    resolved = lifecycle.get("resolved_at")
    if not opened or not resolved:
        return None
    try:
        delta = (
            datetime.fromisoformat(resolved) - datetime.fromisoformat(opened)
        )
        total = int(delta.total_seconds())
        if total < 60:
            return f"{total}s"
        if total < 3600:
            return f"{total // 60}m {total % 60}s"
        return f"{total // 3600}h {(total % 3600) // 60}m"
    except Exception:
        return None
