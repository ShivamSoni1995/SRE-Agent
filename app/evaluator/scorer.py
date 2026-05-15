"""
scorer.py — Hybrid RCA evaluation engine.

Three scoring signals blended into a final score:

  1. Keyword score   (fast, always runs, no API needed)
     — direct word overlap + incident-type group matching

  2. Semantic score  (async, uses Gemini embeddings, cached)
     — cosine similarity between RCA text and expected root cause
     — falls back to 0 gracefully when API is unavailable

  3. Completeness    (structural check — are all fields present and non-trivial?)

Final score = weighted blend × confidence penalty

Weights (with semantic available):  keyword 0.25 | semantic 0.50 | completeness 0.25
Weights (semantic unavailable):     keyword 0.60 | completeness 0.40
"""

import logging
from typing import Dict, Any, List, Optional

from app.evaluator.embeddings import embed, cosine_similarity

logger = logging.getLogger(__name__)

# ── Keyword groups (kept for fast baseline + explainability) ──────────────────

KEYWORD_GROUPS = {
    "database": ["database", "db", "connection", "timeout", "pool", "query", "sql", "postgres", "mysql"],
    "memory":   ["memory", "leak", "oom", "heap", "garbage", "allocation", "ram"],
    "cpu":      ["cpu", "resource", "exhaustion", "compute", "process", "thread", "load"],
    "deployment": ["deploy", "release", "rollback", "version", "startup", "crash", "regression"],
    "rate_limit": ["rate", "limit", "throttle", "429", "traffic", "surge", "quota"],
    "network":  ["network", "dns", "latency", "packet", "timeout", "connectivity"],
}

# ── Scoring weights ───────────────────────────────────────────────────────────

WEIGHTS_WITH_SEMANTIC    = {"keyword": 0.25, "semantic": 0.50, "completeness": 0.25}
WEIGHTS_WITHOUT_SEMANTIC = {"keyword": 0.60, "semantic": 0.00, "completeness": 0.40}


async def evaluate_rca(
    rca_output: Dict[str, Any],
    expected_root_cause: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Async hybrid evaluator.
    Returns score (0-1), matched keywords, and a breakdown of all sub-scores.
    """
    root_cause  = rca_output.get("root_cause", "")
    solution    = rca_output.get("solution", "")
    issue       = rca_output.get("issue", "")
    confidence  = float(rca_output.get("confidence", 0))

    # Text we'll embed — combine all meaningful RCA fields
    rca_text = f"{issue}. {root_cause}. {solution}"

    # ── 1. Keyword score ──────────────────────────────────────────────────────
    keyword_score, matched_keywords = _keyword_score(
        root_cause.lower(), solution.lower(), expected_root_cause
    )

    # ── 2. Semantic score (async, may return None) ────────────────────────────
    semantic_score: Optional[float] = None
    semantic_available = False

    if expected_root_cause:
        rca_vec, exp_vec = None, None
        try:
            rca_vec = await embed(rca_text)
            exp_vec = await embed(expected_root_cause)
        except Exception as e:
            logger.warning(f"Embedding call raised unexpectedly: {e}")

        if rca_vec and exp_vec:
            raw_similarity = cosine_similarity(rca_vec, exp_vec)
            # Cosine similarity for similar short texts tends to cluster
            # in [0.6, 1.0]. Rescale to [0, 1] for more useful scores.
            semantic_score = max(0.0, (raw_similarity - 0.5) / 0.5)
            semantic_available = True
            logger.info(
                f"Semantic similarity: raw={raw_similarity:.3f} "
                f"rescaled={semantic_score:.3f}"
            )
        else:
            logger.info("Semantic scoring unavailable — using keyword+completeness only")
    else:
        # No ground truth to compare against — skip semantic
        logger.info("No expected_root_cause provided — semantic scoring skipped")

    # ── 3. Completeness score ─────────────────────────────────────────────────
    completeness = _completeness_score(rca_output)

    # ── 4. Confidence penalty ─────────────────────────────────────────────────
    confidence_factor = _confidence_factor(confidence)

    # ── 5. Blend ──────────────────────────────────────────────────────────────
    if semantic_available and semantic_score is not None:
        w = WEIGHTS_WITH_SEMANTIC
        raw_score = (
            keyword_score  * w["keyword"] +
            semantic_score * w["semantic"] +
            completeness   * w["completeness"]
        )
        method = "keyword+semantic+completeness"
    else:
        w = WEIGHTS_WITHOUT_SEMANTIC
        raw_score = (
            keyword_score * w["keyword"] +
            completeness  * w["completeness"]
        )
        method = "keyword+completeness"

    final_score = round(min(raw_score * confidence_factor, 1.0), 3)

    logger.info(
        f"Evaluation [{method}]: "
        f"keyword={keyword_score:.3f} "
        f"semantic={round(semantic_score, 3) if semantic_score is not None else 'n/a'} "
        f"completeness={completeness:.3f} "
        f"confidence_factor={confidence_factor} "
        f"→ final={final_score}"
    )

    return {
        "score":             final_score,
        "matched_keywords":  matched_keywords,
        "keyword_score":     round(keyword_score, 3),
        "semantic_score":    round(semantic_score, 3) if semantic_score is not None else None,
        "semantic_available": semantic_available,
        "completeness_score": round(completeness, 3),
        "confidence_factor": confidence_factor,
        "scoring_method":    method,
    }


# ── Sync wrapper for backward compat (used in tests / rule-based path) ────────

def evaluate_rca_sync(
    rca_output: Dict[str, Any],
    expected_root_cause: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Synchronous evaluator — keyword + completeness only.
    Used in tests and wherever an async context isn't available.
    """
    root_cause = rca_output.get("root_cause", "").lower()
    solution   = rca_output.get("solution",   "").lower()
    confidence = float(rca_output.get("confidence", 0))

    keyword_score, matched_keywords = _keyword_score(
        root_cause, solution, expected_root_cause
    )
    completeness     = _completeness_score(rca_output)
    confidence_factor = _confidence_factor(confidence)

    w = WEIGHTS_WITHOUT_SEMANTIC
    raw_score   = keyword_score * w["keyword"] + completeness * w["completeness"]
    final_score = round(min(raw_score * confidence_factor, 1.0), 3)

    return {
        "score":              final_score,
        "matched_keywords":   matched_keywords,
        "keyword_score":      round(keyword_score, 3),
        "semantic_score":     None,
        "semantic_available": False,
        "completeness_score": round(completeness, 3),
        "confidence_factor":  confidence_factor,
        "scoring_method":     "keyword+completeness (sync)",
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _keyword_score(
    root_cause: str,
    solution: str,
    expected_root_cause: Optional[str],
) -> tuple[float, List[str]]:
    combined = f"{root_cause} {solution}"
    matched: List[str] = []

    if expected_root_cause:
        expected_lower = expected_root_cause.lower()
        expected_words = set(expected_lower.split())
        root_words     = set(root_cause.split())

        # Direct word overlap
        matched.extend(list(expected_words & root_words))

        # Group-based expansion
        for keywords in KEYWORD_GROUPS.values():
            if any(kw in expected_lower for kw in keywords):
                matched.extend(kw for kw in keywords if kw in combined)

        matched = list(set(matched))
        score = min(len(matched) / max(len(expected_words), 1), 1.0)
    else:
        all_kw = [kw for group in KEYWORD_GROUPS.values() for kw in group]
        matched = list(set(kw for kw in all_kw if kw in combined))
        score   = min(len(matched) / 5, 1.0)

    return score, matched


def _completeness_score(rca_output: Dict[str, Any]) -> float:
    checks = [
        ("issue",       lambda v: len(v) > 10),
        ("root_cause",  lambda v: len(v) > 15),
        ("solution",    lambda v: len(v) > 20),
        ("confidence",  lambda v: 0.0 < float(v) <= 1.0),
    ]
    return sum(
        0.25 for field, check in checks
        if (val := rca_output.get(field)) and check(str(val))
    )


def _confidence_factor(confidence: float) -> float:
    if confidence < 0.4:
        logger.warning(f"Low confidence RCA ({confidence}) — applying penalty")
        return 0.6
    if confidence < 0.6:
        return 0.85
    return 1.0
