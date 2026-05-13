import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

THRESHOLDS = {
    "cpu": {"warning": 70, "critical": 90},
    "memory": {"warning": 75, "critical": 90},
    "latency": {"warning": 500, "critical": 1000},  # ms
    "error_rate": {"warning": 5, "critical": 10},   # percent
}


def parse_metrics(raw_metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and enrich infrastructure metrics with severity signals.
    Returns normalised metrics plus derived anomaly flags.
    """
    cpu = float(raw_metrics.get("cpu", 0))
    memory = float(raw_metrics.get("memory", 0))
    latency = float(raw_metrics.get("latency", 0))
    error_rate = float(raw_metrics.get("error_rate", 0))

    anomalies = []

    if cpu >= THRESHOLDS["cpu"]["critical"]:
        anomalies.append(f"cpu critical ({cpu}%)")
    elif cpu >= THRESHOLDS["cpu"]["warning"]:
        anomalies.append(f"cpu elevated ({cpu}%)")

    if memory >= THRESHOLDS["memory"]["critical"]:
        anomalies.append(f"memory critical ({memory}%)")
    elif memory >= THRESHOLDS["memory"]["warning"]:
        anomalies.append(f"memory elevated ({memory}%)")

    if latency >= THRESHOLDS["latency"]["critical"]:
        anomalies.append(f"latency critical ({latency}ms)")
    elif latency >= THRESHOLDS["latency"]["warning"]:
        anomalies.append(f"latency elevated ({latency}ms)")

    if error_rate >= THRESHOLDS["error_rate"]["critical"]:
        anomalies.append(f"error_rate critical ({error_rate}%)")
    elif error_rate >= THRESHOLDS["error_rate"]["warning"]:
        anomalies.append(f"error_rate elevated ({error_rate}%)")

    severity = "normal"
    if len(anomalies) >= 3:
        severity = "critical"
    elif len(anomalies) >= 1:
        severity = "warning"

    logger.info(f"Metrics parsed: severity={severity}, anomalies={anomalies}")

    return {
        "cpu": cpu,
        "memory": memory,
        "latency": latency,
        "error_rate": error_rate,
        "anomalies": anomalies,
        "severity": severity,
    }
