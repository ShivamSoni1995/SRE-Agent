from pydantic import BaseModel
from typing import Optional, Dict, List, Any


class AnalyzeRequest(BaseModel):
    logs: str
    metrics: Dict[str, Any]
    events: List[str] = []


class RCAOutput(BaseModel):
    issue: str
    root_cause: str
    solution: str
    confidence: float


class EvaluationResult(BaseModel):
    score: float
    matched_keywords: List[str]
    semantic_score: Optional[float] = None
    semantic_available: bool = False
    scoring_method: str = "keyword+completeness"


class AnalyzeResponse(BaseModel):
    incident_id: str
    issue: str
    root_cause: str
    solution: str
    confidence: float
    evaluation_score: float
    matched_keywords: List[str]
    semantic_score: Optional[float] = None
    semantic_available: bool = False
    scoring_method: str = "keyword+completeness"
    timestamp: str


class IncidentRecord(BaseModel):
    incident_id: str
    timestamp: str
    logs_summary: str
    metrics: Dict[str, Any]
    events: List[str]
    root_cause: str
    confidence: float
    evaluation_score: float
