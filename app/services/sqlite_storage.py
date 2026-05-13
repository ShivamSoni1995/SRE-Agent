import sqlite3
import json
import logging
import os
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("SQLITE_PATH", "data/incidents.db")


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                logs_summary TEXT,
                metrics TEXT,
                events TEXT,
                root_cause TEXT,
                confidence REAL,
                evaluation_score REAL,
                full_response TEXT
            )
        """)
        conn.commit()
    logger.info(f"Database initialised at {DB_PATH}")


def save_incident(
    incident_id: str,
    timestamp: str,
    logs_summary: str,
    metrics: Dict[str, Any],
    events: List[str],
    root_cause: str,
    confidence: float,
    evaluation_score: float,
    full_response: Dict[str, Any],
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO incidents
            (id, timestamp, logs_summary, metrics, events, root_cause, confidence, evaluation_score, full_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                timestamp,
                logs_summary,
                json.dumps(metrics),
                json.dumps(events),
                root_cause,
                confidence,
                evaluation_score,
                json.dumps(full_response),
            ),
        )
        conn.commit()
    logger.info(f"Saved incident {incident_id}")


def get_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
    if row:
        return _row_to_dict(row)
    return None


def list_incidents(limit: int = 20) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM incidents ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["metrics"] = json.loads(d["metrics"])
    d["events"] = json.loads(d["events"])
    d["full_response"] = json.loads(d["full_response"])
    return d
