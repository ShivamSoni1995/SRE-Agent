"""
windows.py — Rolling time-window event aggregator.

Maintains per-service buckets of recent events.
Windows expire after WINDOW_DURATION_SECONDS.
Thread-safe via asyncio — no locking needed (single-threaded event loop).

Data structure:
    _windows = {
        "service:SEVERITY": Window(
            events=[NormalizedEvent, ...],
            window_start=datetime,
        )
    }

Design notes:
- In-memory only — fast, no I/O per event
- Resets on container cold start (acceptable for MVP)
- Production upgrade: replace with Redis ZSET with TTL
"""
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from app.ingestion.schemas import NormalizedEvent

logger = logging.getLogger(__name__)

WINDOW_DURATION_SECONDS = 120   # 2 minutes
MAX_EVENTS_PER_WINDOW   = 500   # cap memory usage


@dataclass
class Window:
    events:       deque = field(default_factory=deque)
    window_start: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def add(self, event: NormalizedEvent) -> None:
        self.events.append(event)
        if len(self.events) > MAX_EVENTS_PER_WINDOW:
            self.events.popleft()

    def is_expired(self) -> bool:
        age = datetime.now(timezone.utc) - self.window_start
        return age.total_seconds() > WINDOW_DURATION_SECONDS

    def reset(self) -> None:
        self.events.clear()
        self.window_start = datetime.now(timezone.utc)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def error_messages(self) -> List[str]:
        return [e.message for e in self.events if e.is_error]

    @property
    def window_end(self) -> datetime:
        return datetime.now(timezone.utc)

    def messages_matching(self, *keywords: str) -> int:
        """Count events whose message contains any of the given keywords."""
        count = 0
        for event in self.events:
            msg = event.message.lower()
            if any(kw.lower() in msg for kw in keywords):
                count += 1
        return count


class RollingWindowStore:
    """
    In-memory store of rolling windows keyed by service+severity.
    One instance shared across the application lifetime.
    """

    def __init__(self) -> None:
        self._windows: Dict[str, Window] = defaultdict(Window)
        self._total_events_processed = 0

    def add_event(self, event: NormalizedEvent) -> Window:
        """
        Add an event to its window. Resets window if expired.
        Returns the updated window.
        """
        key = event.to_correlation_key()
        window = self._windows[key]

        if window.is_expired():
            logger.debug(f"Window expired for {key} — resetting")
            window.reset()

        window.add(event)
        self._total_events_processed += 1

        return window

    def get_window(self, service: str, severity: str) -> Optional[Window]:
        key = f"{service}:{severity.upper()}"
        return self._windows.get(key)

    def get_all_windows(self) -> Dict[str, Window]:
        return dict(self._windows)

    def get_service_windows(self, service: str) -> Dict[str, Window]:
        """Get all severity windows for a specific service."""
        return {
            k: v for k, v in self._windows.items()
            if k.startswith(f"{service}:")
        }

    def total_active_events(self) -> int:
        return sum(w.event_count for w in self._windows.values())

    def stats(self) -> Dict:
        return {
            "active_windows":        len(self._windows),
            "total_active_events":   self.total_active_events(),
            "total_events_processed": self._total_events_processed,
            "window_duration_seconds": WINDOW_DURATION_SECONDS,
            "windows": {
                k: {
                    "event_count":  w.event_count,
                    "window_start": w.window_start.isoformat(),
                    "expired":      w.is_expired(),
                }
                for k, w in self._windows.items()
            },
        }

    def purge_expired(self) -> int:
        """Remove expired empty windows. Call periodically."""
        expired_keys = [
            k for k, w in self._windows.items()
            if w.is_expired() and w.event_count == 0
        ]
        for k in expired_keys:
            del self._windows[k]
        return len(expired_keys)


# ── Singleton instance shared across the app ──────────────────────────────────
window_store = RollingWindowStore()
