"""
rules.py — Incident detection rules applied to rolling windows.

Each rule inspects a Window and optional metric signals.
Returns an IncidentCandidate if the rule fires, None otherwise.

MVP rules:
  RULE 1 — Database failure     (timeout errors > 20 in 2min + latency > 1000ms)
  RULE 2 — CPU exhaustion       (CPU > 95% + latency > 1000ms)
  RULE 3 — Memory leak          (memory > 95% + OOM logs detected)
  RULE 4 — High error rate      (any error count > 30 in 2min)
  RULE 5 — Crash loop           (crash/killed messages > 5 in 2min)

Adding new rules: implement a function matching the signature:
  def rule_name(window, service, metrics) -> Optional[IncidentCandidate]
and add it to RULES list at the bottom.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from app.correlation.windows import Window

logger = logging.getLogger(__name__)


@dataclass
class IncidentCandidate:
    """
    Detected incident candidate ready for AI RCA.
    Produced by correlation rules, consumed by the pipeline.
    """
    incident_type:  str
    service:        str
    severity:       str
    error_count:    int
    error_messages: List[str]
    latency:        float = 0.0
    cpu:            float = 0.0
    memory:         float = 0.0
    recent_deploy:  bool = False
    rule_name:      str = ""
    window_start:   str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    window_end:     str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata:       Dict[str, Any] = field(default_factory=dict)

    def to_analyze_payload(self) -> Dict[str, Any]:
        """
        Convert to /analyze-compatible payload.
        Reuses the existing context builder and RCA pipeline.
        """
        logs = "\n".join(
            f"ERROR {msg}" for msg in self.error_messages[:20]
        )
        return {
            "logs":    logs or f"ERROR {self.incident_type} detected",
            "metrics": {
                "cpu":        self.cpu,
                "memory":     self.memory,
                "latency":    self.latency,
                "error_rate": min(self.error_count * 2, 100),
            },
            "events": (
                ["deployment_started"] if self.recent_deploy else []
            ),
        }

    def signature(self) -> str:
        """Deduplication signature — hash(service + incident_type)."""
        import hashlib
        key = f"{self.service}:{self.incident_type}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Rule implementations ──────────────────────────────────────────────────────

def rule_database_failure(
    window: Window,
    service: str,
    metrics: Dict[str, Any],
) -> Optional[IncidentCandidate]:
    """
    RULE 1: Database timeout errors > 20 in window AND latency > 1000ms.
    """
    timeout_count = window.messages_matching(
        "timeout", "connection", "db ", "database", "pool exhausted"
    )
    latency = metrics.get("latency", 0)

    if timeout_count >= 20 and latency > 1000:
        logger.info(
            f"RULE 1 fired: service={service} "
            f"timeout_count={timeout_count} latency={latency}ms"
        )
        return IncidentCandidate(
            incident_type="database_failure",
            service=service,
            severity="critical",
            error_count=timeout_count,
            error_messages=window.error_messages[:10],
            latency=latency,
            cpu=metrics.get("cpu", 0),
            memory=metrics.get("memory", 0),
            recent_deploy=metrics.get("recent_deploy", False),
            rule_name="database_failure",
            window_start=window.window_start.isoformat(),
            window_end=window.window_end.isoformat(),
            metadata={"timeout_count": timeout_count},
        )
    return None


def rule_cpu_exhaustion(
    window: Window,
    service: str,
    metrics: Dict[str, Any],
) -> Optional[IncidentCandidate]:
    """
    RULE 2: CPU > 95% AND latency > 1000ms.
    """
    cpu     = metrics.get("cpu", 0)
    latency = metrics.get("latency", 0)

    if cpu > 95 and latency > 1000:
        logger.info(
            f"RULE 2 fired: service={service} cpu={cpu}% latency={latency}ms"
        )
        return IncidentCandidate(
            incident_type="cpu_exhaustion",
            service=service,
            severity="critical",
            error_count=window.event_count,
            error_messages=window.error_messages[:10],
            latency=latency,
            cpu=cpu,
            memory=metrics.get("memory", 0),
            rule_name="cpu_exhaustion",
            window_start=window.window_start.isoformat(),
            window_end=window.window_end.isoformat(),
            metadata={"cpu_percent": cpu},
        )
    return None


def rule_memory_leak(
    window: Window,
    service: str,
    metrics: Dict[str, Any],
) -> Optional[IncidentCandidate]:
    """
    RULE 3: Memory > 95% AND OOM logs detected.
    """
    memory    = metrics.get("memory", 0)
    oom_count = window.messages_matching("oom", "killed", "memory", "heap")

    if memory > 95 and oom_count > 0:
        logger.info(
            f"RULE 3 fired: service={service} "
            f"memory={memory}% oom_signals={oom_count}"
        )
        return IncidentCandidate(
            incident_type="memory_leak",
            service=service,
            severity="critical",
            error_count=oom_count,
            error_messages=window.error_messages[:10],
            latency=metrics.get("latency", 0),
            cpu=metrics.get("cpu", 0),
            memory=memory,
            rule_name="memory_leak",
            window_start=window.window_start.isoformat(),
            window_end=window.window_end.isoformat(),
            metadata={"oom_signals": oom_count, "memory_percent": memory},
        )
    return None


def rule_high_error_rate(
    window: Window,
    service: str,
    metrics: Dict[str, Any],
) -> Optional[IncidentCandidate]:
    """
    RULE 4: Any error count > 30 in the 2-minute window.
    Catches catch-all error spikes not covered by specific rules.
    """
    error_count = len(window.error_messages)

    if error_count >= 30:
        logger.info(
            f"RULE 4 fired: service={service} error_count={error_count}"
        )
        return IncidentCandidate(
            incident_type="high_error_rate",
            service=service,
            severity="warning",
            error_count=error_count,
            error_messages=window.error_messages[:10],
            latency=metrics.get("latency", 0),
            cpu=metrics.get("cpu", 0),
            memory=metrics.get("memory", 0),
            rule_name="high_error_rate",
            window_start=window.window_start.isoformat(),
            window_end=window.window_end.isoformat(),
            metadata={"error_count": error_count},
        )
    return None


def rule_crash_loop(
    window: Window,
    service: str,
    metrics: Dict[str, Any],
) -> Optional[IncidentCandidate]:
    """
    RULE 5: Crash/killed signals > 5 in window.
    """
    crash_count = window.messages_matching(
        "crash", "killed", "signal 9", "sigkill", "restart", "crash loop"
    )

    if crash_count >= 5:
        logger.info(
            f"RULE 5 fired: service={service} crash_signals={crash_count}"
        )
        return IncidentCandidate(
            incident_type="crash_loop",
            service=service,
            severity="critical",
            error_count=crash_count,
            error_messages=window.error_messages[:10],
            latency=metrics.get("latency", 0),
            cpu=metrics.get("cpu", 0),
            memory=metrics.get("memory", 0),
            rule_name="crash_loop",
            window_start=window.window_start.isoformat(),
            window_end=window.window_end.isoformat(),
            metadata={"crash_signals": crash_count},
        )
    return None


# ── Rule registry — add new rules here ───────────────────────────────────────
RULES = [
    rule_database_failure,
    rule_cpu_exhaustion,
    rule_memory_leak,
    rule_high_error_rate,
    rule_crash_loop,
]
