import os
import json
import logging
import httpx
from typing import Dict, Any

logger = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

SYSTEM_PROMPT = """You are an SRE incident analysis assistant.

You receive structured observability context containing:
- error logs and their frequency
- infrastructure metrics (CPU, memory, latency, error_rate)
- correlation hints from automated analysis
- recent deployment signals

Your task:
1. Identify the most likely incident type
2. Determine the probable root cause
3. Recommend concrete remediation steps
4. Estimate your confidence (0.0 to 1.0)

Rules:
- Base your analysis on the structured signals, not assumptions
- Be specific — name the likely failing component
- Keep remediation steps actionable and ordered by priority
- Lower confidence if signals are ambiguous or conflicting

Return ONLY valid JSON with exactly these keys:
{
  "issue": "brief incident summary (1 sentence)",
  "root_cause": "specific technical root cause",
  "solution": "ordered remediation steps as a single string",
  "confidence": 0.0
}"""


async def run_rca(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send structured incident context to Gemini and return parsed RCA.
    Falls back to a rule-based response if the API key is not set.
    """
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        logger.warning("GEMINI_API_KEY not set — using rule-based fallback")
        return _rule_based_rca(context)

    prompt = f"{SYSTEM_PROMPT}\n\nIncident context:\n{json.dumps(context, indent=2)}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GEMINI_API_URL}?key={api_key}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        # Strip markdown code fences if present
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        result = json.loads(raw_text.strip())
        logger.info(f"Gemini RCA complete: confidence={result.get('confidence')}")
        return result

    except Exception as e:
        logger.error(f"Gemini API error: {e} — falling back to rule-based RCA")
        return _rule_based_rca(context)


def _rule_based_rca(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback RCA using correlation hints and metric thresholds.
    Used when Gemini API is unavailable or key is not configured.
    """
    hints = context.get("correlation_hints", [])
    service = context.get("service", "application")
    cpu = context.get("cpu", 0)
    memory = context.get("memory", 0)
    errors = context.get("top_errors", [])
    recent_deploy = context.get("recent_deploy", False)

    error_text = " ".join(errors).lower()

    # DB timeout
    if "database" in service or "timeout" in error_text:
        return {
            "issue": f"High latency and connection errors in {service} service",
            "root_cause": "Database connection timeout — DB unreachable or connection pool exhausted",
            "solution": "1. Check DB health and connectivity. 2. Restart connection pool. 3. Review recent schema changes. 4. Consider rolling back recent deployment if applicable.",
            "confidence": 0.78,
        }

    # Memory leak
    if memory > 90 or "oom" in error_text or "killed" in error_text:
        return {
            "issue": "Memory exhaustion causing process termination",
            "root_cause": "Application memory leak — memory growing unbounded until OOM kill",
            "solution": "1. Identify memory-leaking component via heap profiling. 2. Restart affected pods immediately. 3. Set memory limits if not configured. 4. Deploy fix and monitor growth rate.",
            "confidence": 0.82,
        }

    # CPU exhaustion
    if cpu > 90:
        return {
            "issue": "CPU saturation causing request slowdowns",
            "root_cause": "Resource exhaustion — CPU maxed out likely due to runaway process or traffic spike",
            "solution": "1. Identify top CPU consumer (top/htop). 2. Kill or throttle runaway process. 3. Scale horizontally if traffic spike. 4. Review recent code changes for expensive loops.",
            "confidence": 0.75,
        }

    # Broken deployment
    if recent_deploy and ("failed" in error_text or "crash" in error_text):
        return {
            "issue": "Service instability following deployment",
            "root_cause": "Broken deployment — new release introduced crashing behaviour",
            "solution": "1. Rollback to previous deployment immediately. 2. Review deployment diff. 3. Check startup logs for exceptions. 4. Re-deploy with fixes after root cause confirmed.",
            "confidence": 0.80,
        }

    # Generic fallback
    return {
        "issue": "Application errors detected — root cause unclear",
        "root_cause": "Multiple signals present — requires deeper investigation",
        "solution": "1. Review full logs for error patterns. 2. Check recent deployments. 3. Verify downstream dependencies. 4. Escalate if errors persist.",
        "confidence": 0.45,
    }
