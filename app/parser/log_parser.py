import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

ERROR_PATTERNS = [
    r"(?i)\berror\b",
    r"(?i)\bfailed\b",
    r"(?i)\btimeout\b",
    r"(?i)\bexception\b",
    r"(?i)\bcrash\b",
    r"(?i)\bkilled\b",
    r"(?i)\boom\b",
    r"(?i)\b429\b",
    r"(?i)\bfatal\b",
]

KNOWN_SERVICES = ["database", "db", "cache", "redis", "api", "worker", "queue"]


def parse_logs(raw_logs: str) -> Dict[str, Any]:
    """
    Parse raw log text and extract structured error information.
    Filters INFO lines, identifies errors, extracts services and frequencies.
    """
    lines = raw_logs.strip().splitlines()
    errors: List[str] = []
    warnings: List[str] = []
    detected_services: List[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        lower = line.lower()

        # Skip pure INFO lines (no embedded error signals)
        if lower.startswith("info") and not any(
            re.search(p, lower) for p in ERROR_PATTERNS
        ):
            continue

        # Capture warnings
        if lower.startswith("warn"):
            warnings.append(_clean_line(line))
            continue

        # Capture error lines
        if any(re.search(p, lower) for p in ERROR_PATTERNS):
            cleaned = _clean_line(line)
            errors.append(cleaned)

            # Detect mentioned services
            for svc in KNOWN_SERVICES:
                if svc in lower and svc not in detected_services:
                    detected_services.append(svc)

    error_counts: Dict[str, int] = {}
    for err in errors:
        key = err.lower()
        error_counts[key] = error_counts.get(key, 0) + 1

    top_errors = sorted(error_counts.keys(), key=lambda k: error_counts[k], reverse=True)[:5]

    logger.info(f"Parsed logs: {len(errors)} errors, {len(warnings)} warnings")

    return {
        "errors": top_errors,
        "error_frequency": len(errors),
        "warnings": warnings[:5],
        "detected_services": detected_services,
        "raw_error_count": len(errors),
    }


def _clean_line(line: str) -> str:
    """Strip log level prefix and trim whitespace."""
    for prefix in ["ERROR ", "WARN ", "INFO ", "DEBUG ", "FATAL "]:
        if line.upper().startswith(prefix):
            return line[len(prefix):].strip()
    return line.strip()
