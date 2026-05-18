"""
pubsub_decoder.py — Decode Pub/Sub push delivery payloads.

Pub/Sub wraps messages in a JSON envelope:
{
  "message": {
    "data": "<base64-encoded payload>",
    "attributes": {...},
    "messageId": "...",
    "publishTime": "..."
  },
  "subscription": "projects/.../subscriptions/..."
}

The data field is base64-encoded JSON — usually a Cloud Logging
LogEntry when routing via a logging sink.
"""
import base64
import json
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def decode_pubsub_message(
    envelope: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Decode a Pub/Sub push envelope.

    Returns:
        (payload_dict, attributes) on success
        (None, None) on failure
    """
    try:
        message = envelope.get("message", {})
        if not message:
            logger.warning("Pub/Sub envelope missing 'message' field")
            return None, None

        raw_data = message.get("data", "")
        attributes = message.get("attributes", {})

        if not raw_data:
            logger.warning("Pub/Sub message missing 'data' field")
            return None, None

        # Decode base64 → bytes → JSON string → dict
        decoded_bytes = base64.b64decode(raw_data)
        payload = json.loads(decoded_bytes.decode("utf-8"))

        logger.debug(
            f"Decoded Pub/Sub message: "
            f"messageId={message.get('messageId', 'unknown')} "
            f"payload_keys={list(payload.keys())}"
        )

        return payload, attributes

    except base64.binascii.Error as e:
        logger.error(f"Base64 decode failed: {e}")
        return None, None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode failed: {e}")
        return None, None
    except Exception as e:
        logger.error(f"Pub/Sub decode unexpected error: {e}")
        return None, None


def is_cloud_logging_entry(payload: Dict[str, Any]) -> bool:
    """Detect if a payload is a GCP Cloud Logging LogEntry."""
    return (
        "logName" in payload
        or "textPayload" in payload
        or "jsonPayload" in payload
        or "protoPayload" in payload
        or "resource" in payload
    )


def extract_message_id(envelope: Dict[str, Any]) -> str:
    """Extract Pub/Sub message ID for deduplication/logging."""
    return envelope.get("message", {}).get("messageId", "unknown")
