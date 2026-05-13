import os
import logging
import httpx
from typing import Dict, Any

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "warning": ":large_yellow_circle:",
    "normal": ":large_green_circle:",
}

CONFIDENCE_BAR = {
    (0.9, 1.0): "████████████ 90%+",
    (0.7, 0.9): "█████████░░░ 70–90%",
    (0.5, 0.7): "██████░░░░░░ 50–70%",
    (0.0, 0.5): "███░░░░░░░░░ <50%",
}


def _confidence_bar(score: float) -> str:
    for (lo, hi), label in CONFIDENCE_BAR.items():
        if lo <= score <= hi:
            return label
    return str(round(score * 100)) + "%"


async def notify_incident(
    incident_id: str,
    rca: Dict[str, Any],
    evaluation_score: float,
    severity: str,
    service_url: str = "",
) -> bool:
    """
    Post an RCA summary to Slack via incoming webhook.
    Returns True if sent successfully, False otherwise.
    Silently skips if SLACK_WEBHOOK_URL is not set.
    """
    if not SLACK_WEBHOOK_URL:
        logger.debug("SLACK_WEBHOOK_URL not set — skipping Slack notification")
        return False

    emoji = SEVERITY_EMOJI.get(severity, ":large_yellow_circle:")
    confidence = float(rca.get("confidence", 0))
    solution_lines = rca.get("solution", "").split(". ")
    solution_formatted = "\n".join(
        f"  {i+1}. {s.strip()}" for i, s in enumerate(solution_lines) if s.strip()
    )

    incident_link = f"{service_url}/incidents/{incident_id}" if service_url else incident_id

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji}  Incident detected — {incident_id}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Issue*\n{rca.get('issue', 'Unknown')}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity*\n{severity.upper()}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Root cause*\n{rca.get('root_cause', 'Unknown')}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Remediation steps*\n{solution_formatted}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Confidence: `{_confidence_bar(confidence)}`  |  "
                            f"Eval score: `{round(evaluation_score * 100)}%`  |  "
                            f"ID: `{incident_link}`"
                        ),
                    }
                ],
            },
            {"type": "divider"},
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(SLACK_WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        logger.info(f"Slack notification sent for {incident_id}")
        return True
    except Exception as e:
        logger.error(f"Slack notification failed for {incident_id}: {e}")
        return False
