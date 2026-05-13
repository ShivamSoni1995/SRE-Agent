import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.analyze import router as analyze_router
from app.services import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="OpenSRE Mini",
    description="AI-powered SRE incident analysis — root cause analysis via structured observability reasoning",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    storage.init_db()
    logger.info("OpenSRE Mini started")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/")
async def root():
    return {
        "service": "OpenSRE Mini",
        "docs": "/docs",
        "health": "/health",
        "analyze": "POST /analyze",
        "incidents": "GET /incidents",
    }


app.include_router(analyze_router)
