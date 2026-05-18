import logging
import os
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.routes.analyze import router as analyze_router
from app.routes.webhooks import router as webhook_router
from app.routes.ingest import router as ingest_router
from app.services import storage
from app.middleware import PrometheusMiddleware
from app.auth import APIKeyMiddleware
from app.metrics import get_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OpenSRE Mini",
    description=(
        "AI-powered SRE incident analysis — "
        "event-driven telemetry ingestion, rolling correlation, "
        "reactive alerting, multi-turn chat, semantic RCA evaluation"
    ),
    version="0.4.0",
)

app.add_middleware(APIKeyMiddleware)
app.add_middleware(PrometheusMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    storage.init_db()
    logger.info("OpenSRE Mini v0.4.0 started — event-driven ingestion active")


@app.get("/health")
async def health():
    return {
        "status":  "healthy",
        "version": "0.4.0",
        "auth":    "enabled" if os.getenv("API_KEYS") else "disabled (dev mode)",
        "mode":    "event-driven",
    }


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    data, content_type = get_metrics()
    return Response(content=data, media_type=content_type)


@app.get("/")
async def root():
    return {
        "service": "OpenSRE Mini",
        "version": "0.4.0",
        "docs":    "/docs",
        "endpoints": {
            "manual_analysis":      "POST /analyze",
            "ingest_gcp":           "POST /ingest/gcp",
            "ingest_test":          "POST /ingest/test",
            "ingest_status":        "GET  /ingest/status",
            "incidents":            "GET  /incidents",
            "incident_status":      "POST /incidents/{id}/status",
            "incident_chat":        "POST /incidents/{id}/chat",
            "grafana_webhook":      "POST /webhook/grafana",
            "pagerduty_webhook":    "POST /webhook/pagerduty",
            "metrics":              "GET  /metrics",
            "health":               "GET  /health",
        },
    }


app.include_router(analyze_router)
app.include_router(webhook_router)
app.include_router(ingest_router)
