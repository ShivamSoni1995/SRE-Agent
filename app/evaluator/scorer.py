import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Keywords grouped by incident type for richer matching
KEYWORD_GROUPS = {
    "database": ["database", "db", "connection", "timeout", "pool", "query", "sql", "postgres", "mysql"],
    "memory": ["memory", "leak", "oom", "heap", "garbage", "allocation", "ram"],
    "cpu": ["cpu", "resource", "exhaustion", "compute", "process", "thread", "load"],
    "deployment": ["deploy", "release", "rollback", "version", "startup", "crash", "regression"],
    "rate_limit": ["rate", "limit", "throttle", "429", "traffic", "surge", "quota"],
    "network": ["network", "dns", "latency", "packet", "timeout", "connectivity"],
}


def evaluate_rca(
    rca_output: Dict[str, Any],
    expected_root_cause: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score an RCA output using keyword overlap and confidence thresholding.

    Returns a score (0-1) and the matched keywords that drove the score.
    """
    root_cause = rca_output.get("root_cause", "").lower()
    solution = rca_output.get("solution", "").lower()
    confidence = float(rca_output.get("confidence", 0))
    combined_text = f"{root_cause} {solution}"

    matched_keywords: List[str] = []

    # --- Step 1: Keyword overlap score ---
    if expected_root_cause:
        expected_lower = expected_root_cause.lower()
        expected_words = set(expected_lower.split())

        # Direct word overlap
        root_words = set(root_cause.split())
        overlap = expected_words & root_words
        matched_keywords.extend(list(overlap))

        # Group-based matching: if expected mentions "database", check db group
        for group_name, keywords in KEYWORD_GROUPS.items():
            if any(kw in expected_lower for kw in keywords):
                group_hits = [kw for kw in keywords if kw in combined_text]
                matched_keywords.extend(group_hits)

        matched_keywords = list(set(matched_keywords))  # deduplicate
        keyword_score = min(len(matched_keywords) / max(len(expected_words), 1), 1.0)
    else:
        # No expected ground truth — score based on richness of the response
        all_keywords = [kw for group in KEYWORD_GROUPS.values() for kw in group]
        hits = [kw for kw in all_keywords if kw in combined_text]
        matched_keywords = list(set(hits))
        keyword_score = min(len(matched_keywords) / 5, 1.0)

    # --- Step 2: Confidence penalty ---
    # Penalise very low confidence (possible hallucination or uncertainty)
    confidence_factor = 1.0
    if confidence < 0.4:
        confidence_factor = 0.6  # heavy penalty
        logger.warning(f"Low confidence RCA ({confidence}) — applying penalty")
    elif confidence < 0.6:
        confidence_factor = 0.85

    # --- Step 3: Response completeness check ---
    completeness = _check_completeness(rca_output)

    # --- Final score ---
    raw_score = (keyword_score * 0.6) + (completeness * 0.4)
    final_score = round(raw_score * confidence_factor, 3)
    final_score = min(final_score, 1.0)

    logger.info(f"Evaluation: score={final_score}, matched={matched_keywords}")

    return {
        "score": final_score,
        "matched_keywords": matched_keywords,
        "keyword_score": round(keyword_score, 3),
        "completeness_score": round(completeness, 3),
        "confidence_factor": confidence_factor,
    }


def _check_completeness(rca_output: Dict[str, Any]) -> float:
    """Score how complete the RCA response is."""
    score = 0.0
    checks = [
        ("issue", lambda v: len(v) > 10),
        ("root_cause", lambda v: len(v) > 15),
        ("solution", lambda v: len(v) > 20),
        ("confidence", lambda v: 0.0 < float(v) <= 1.0),
    ]
    for field, check in checks:
        val = rca_output.get(field)
        if val and check(str(val)):
            score += 0.25
    return score
