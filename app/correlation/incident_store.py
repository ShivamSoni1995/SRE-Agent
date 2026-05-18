"""
incident_store.py — In-memory deduplication store for incident candidates.

Prevents the same incident type for the same service from triggering
repeated AI analysis within a cooldown window.

Cooldown: 5 minutes (configurable via CORRELATION_COOLDOWN_MINUTES env var)

Signature: hash(service + incident_type)

Production note: replace with Redis for multi-instance deployments.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = int(os.getenv("CORRELATION_COOLDOWN_MINUTES", "5"))


class IncidentCandidateStore:
    """
    Tracks recently triggered incident signatures to prevent duplicates.
    """

    def __init__(self) -> None:
        # signature -> last_triggered UTC datetime
        self._store: Dict[str, datetime] = {}
        self._total_triggered  = 0
        self._total_suppressed = 0

    def is_duplicate(self, signature: str) -> Tuple[bool, Optional[str]]:
        """
        Returns (is_duplicate, last_triggered_iso) if within cooldown.
        """
        last = self._store.get(signature)
        if not last:
            return False, None

        cooldown = timedelta(minutes=COOLDOWN_MINUTES)
        if datetime.now(timezone.utc) - last < cooldown:
            self._total_suppressed += 1
            return True, last.isoformat()

        return False, None

    def record(self, signature: str) -> None:
        """Mark this signature as triggered now."""
        self._store[signature] = datetime.now(timezone.utc)
        self._total_triggered += 1
        self._evict_expired()

    def _evict_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MINUTES * 3)
        expired = [s for s, t in self._store.items() if t < cutoff]
        for s in expired:
            del self._store[s]

    def stats(self) -> Dict:
        return {
            "active_signatures":  len(self._store),
            "total_triggered":    self._total_triggered,
            "total_suppressed":   self._total_suppressed,
            "cooldown_minutes":   COOLDOWN_MINUTES,
        }


# Singleton
candidate_store = IncidentCandidateStore()
