"""
conversation_store.py — Per-incident multi-turn chat history.

Stores conversation messages alongside the incident record.
Each message: role (user|assistant), content, timestamp.
Max 50 messages per incident to avoid context explosion.
"""
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

MAX_MESSAGES = 50


def make_message(role: str, content: str) -> Dict[str, Any]:
    return {
        "role":      role,
        "content":   content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def append_message(
    history: List[Dict[str, Any]],
    role: str,
    content: str,
) -> List[Dict[str, Any]]:
    """Add a message and trim to MAX_MESSAGES (keep most recent)."""
    history = list(history)
    history.append(make_message(role, content))
    if len(history) > MAX_MESSAGES:
        history = history[-MAX_MESSAGES:]
    return history


def to_gemini_messages(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert stored history to Gemini API message format."""
    return [
        {
            "role":    "user" if m["role"] == "user" else "model",
            "parts":   [{"text": m["content"]}],
        }
        for m in history
    ]


def summarise_for_context(
    history: List[Dict[str, Any]],
    max_chars: int = 2000,
) -> str:
    """Compact history into a string for injection into a new prompt."""
    lines = []
    for m in history[-10:]:  # last 10 messages only
        prefix = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{prefix}: {m['content'][:200]}")
    summary = "\n".join(lines)
    return summary[:max_chars]
