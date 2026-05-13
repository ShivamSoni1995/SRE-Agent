import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DEPLOY_KEYWORDS = ["deployment_started", "deploy", "release", "rollout", "restart"]
RATE_LIMIT_KEYWORDS = ["rate_limit", "throttle", "429"]


def build_context(
    parsed_logs: Dict[str, Any],
    parsed_metrics: Dict[str, Any],
    events: List[str],
) -> Dict[str, Any]:
    """
    Correlate parsed observability data into a compact, high-signal context
    object that can be sent directly to the AI agent.

    This is the most important preprocessing step — the AI should reason
    over structured signals, not raw text.
    """
    # Detect recent deployment
    recent_deploy = _detect_deploy(events)

    # Detect rate limiting
    rate_limiting = _detect_rate_limiting(events, parsed_logs["errors"])

    # Identify primary service from log signals
    service = _infer_service(parsed_logs)

    # Correlate: latency + DB errors = likely DB issue
    # CPU spike + deploy = likely broken deployment
    # Memory growth + OOM = memory leak
    correlation_hints = _correlate(parsed_logs, parsed_metrics, recent_deploy)

    context = {
        "service": service,
        "top_errors": parsed_logs["errors"][:5],
        "error_frequency": parsed_logs["error_frequency"],
        "warnings": parsed_logs.get("warnings", [])[:3],
        "cpu": parsed_metrics["cpu"],
        "memory": parsed_metrics["memory"],
        "latency": parsed_metrics["latency"],
        "error_rate": parsed_metrics["error_rate"],
        "metric_severity": parsed_metrics["severity"],
        "anomalies": parsed_metrics["anomalies"],
        "recent_deploy": recent_deploy,
        "rate_limiting": rate_limiting,
        "correlation_hints": correlation_hints,
    }

    logger.info(f"Built incident context: service={service}, hints={correlation_hints}")
    return context


def _detect_deploy(events: List[str]) -> bool:
    for e in events:
        if any(kw in e.lower() for kw in DEPLOY_KEYWORDS):
            return True
    return False


def _detect_rate_limiting(events: List[str], errors: List[str]) -> bool:
    for item in events + errors:
        if any(kw in item.lower() for kw in RATE_LIMIT_KEYWORDS):
            return True
    return False


def _infer_service(parsed_logs: Dict[str, Any]) -> str:
    services = parsed_logs.get("detected_services", [])
    if "database" in services or "db" in services:
        return "database"
    if "cache" in services or "redis" in services:
        return "cache"
    if "queue" in services:
        return "queue"
    if services:
        return services[0]
    return "application"


def _correlate(
    parsed_logs: Dict[str, Any],
    parsed_metrics: Dict[str, Any],
    recent_deploy: bool,
) -> List[str]:
    hints = []
    errors = " ".join(parsed_logs["errors"]).lower()
    cpu = parsed_metrics["cpu"]
    memory = parsed_metrics["memory"]
    latency = parsed_metrics["latency"]

    # DB timeout pattern
    if ("timeout" in errors or "connection" in errors) and latency > 1000:
        hints.append("high latency correlates with connection errors — likely DB unreachable")

    # CPU exhaustion pattern
    if cpu > 90:
        hints.append("CPU critical — possible resource exhaustion or runaway process")

    # Memory leak pattern
    if memory > 90 and ("oom" in errors or "killed" in errors or "memory" in errors):
        hints.append("high memory + OOM errors — likely memory leak")

    # Broken deployment pattern
    if recent_deploy and ("crash" in errors or "killed" in errors or "failed" in errors):
        hints.append("errors correlate with recent deployment — possible broken release")

    # Rate limiting pattern
    if "429" in errors or "rate" in errors:
        hints.append("rate limit signals detected — traffic surge or misconfigured limits")

    return hints
