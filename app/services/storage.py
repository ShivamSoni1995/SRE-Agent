"""
Storage router.

Set USE_FIRESTORE=true to use Firestore (production / Cloud Run).
Falls back to SQLite automatically for local dev.
"""
import os
import logging

logger = logging.getLogger(__name__)

_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        if os.getenv("USE_FIRESTORE", "").lower() == "true":
            from app.services import firestore_storage
            _backend = firestore_storage
            logger.info("Storage backend: Firestore")
        else:
            from app.services import sqlite_storage
            _backend = sqlite_storage
            logger.info("Storage backend: SQLite")
    return _backend


def init_db():
    backend = _get_backend()
    if hasattr(backend, "init_db"):
        backend.init_db()


def save_incident(*args, **kwargs):
    return _get_backend().save_incident(*args, **kwargs)


def get_incident(*args, **kwargs):
    return _get_backend().get_incident(*args, **kwargs)


def list_incidents(*args, **kwargs):
    return _get_backend().list_incidents(*args, **kwargs)
