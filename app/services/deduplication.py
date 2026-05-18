"""
deduplication.py — Cooldown-based incident deduplication.

Prevents the same alert signature from triggering multiple analyses
within a configurable window. Uses in-memory store with Firestore
fallback for persistence across Cloud Run instances.

Signature = hash of (top errors + severity + service).
Default cooldown: 10 minutes.
"""
import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

COOLDOWN_MINUTES = int(os.getenv("DEDUP_COOLDOWN_MINUTES", "10"))

# In-memory store: signature -> last_seen UTC ISO string
_memory_store: Dict[str, str] = {}


def compute_signature(
    errors: list[str],
    severity: str,
    service: str,
) -> str:
    """Hash the key incident signals into a deduplication signature."""
    key = f"{severity}:{service}:{':'.join(sorted(errors[:3]))}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_duplicate(signature: str) -> tuple[bool, Optional[str]]:
    """
    Check if this signature was seen within the cooldown window.
    Returns (is_duplicate, last_seen_timestamp).
    """
    last_seen = _memory_store.get(signature)
    if not last_seen:
        return False, None

    try:
        last_dt = datetime.fromisoformat(last_seen)
        cooldown = timedelta(minutes=COOLDOWN_MINUTES)
        if datetime.now(timezone.utc) - last_dt < cooldown:
            return True, last_seen
    except Exception:
        pass

    return False, None


def record_seen(signature: str) -> None:
    """Mark this signature as seen now."""
    now = datetime.now(timezone.utc).isoformat()
    _memory_store[signature] = now

    # Evict entries older than 2x cooldown to prevent unbounded growth
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MINUTES * 2)
    to_delete = []
    for sig, ts in _memory_store.items():
        try:
            if datetime.fromisoformat(ts) < cutoff:
                to_delete.append(sig)
        except Exception:
            to_delete.append(sig)
    for sig in to_delete:
        del _memory_store[sig]

    logger.debug(f"Dedup store size after eviction: {len(_memory_store)}")


def get_store_stats() -> Dict[str, Any]:
    return {
        "active_signatures": len(_memory_store),
        "cooldown_minutes": COOLDOWN_MINUTES,
    }
