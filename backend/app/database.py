"""MongoDB connection handling.

One shared MongoClient for the process. `ping()` is called at startup so an
unreachable database produces a clear error instead of failing per-request.
"""

import logging

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

from app.config import settings

logger = logging.getLogger(__name__)

# Collection names, referenced everywhere instead of raw strings.
LEADS = "leads"
CALLERS = "callers"
CALLS = "calls"
CALL_TRANSCRIPTS = "call_transcripts"
CALL_ANALYSES = "call_analyses"

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000, tz_aware=True)
    return _client


def get_db() -> Database:
    return get_client()[settings.database_name]


def ping() -> None:
    """Raise a readable error if MongoDB is unavailable."""
    try:
        get_client().admin.command("ping")
    except PyMongoError as exc:
        raise RuntimeError(
            f"Cannot reach MongoDB at the configured MONGODB_URL. Is mongod running? ({exc})"
        ) from exc


def ensure_indexes() -> None:
    db = get_db()
    db[LEADS].create_index([("lead_id", ASCENDING)], unique=True)
    db[LEADS].create_index([("follow_up.datetime", ASCENDING)])
    db[LEADS].create_index([("assigned_bd.name", ASCENDING)])

    db[CALLERS].create_index([("caller_id", ASCENDING)], unique=True)

    db[CALLS].create_index([("call_id", ASCENDING)], unique=True)
    db[CALLS].create_index([("lead_id", ASCENDING), ("ended_at", DESCENDING)])
    db[CALLS].create_index([("caller_id", ASCENDING)])

    db[CALL_TRANSCRIPTS].create_index([("transcript_id", ASCENDING)], unique=True)
    db[CALL_TRANSCRIPTS].create_index([("call_id", ASCENDING)])
    db[CALL_TRANSCRIPTS].create_index([("lead_id", ASCENDING), ("created_at", DESCENDING)])

    db[CALL_ANALYSES].create_index([("analysis_id", ASCENDING)], unique=True)
    # Not unique on call_id: a failed Gemini attempt and its keyword fallback
    # are two documents for the same call, kept apart by `status`.
    db[CALL_ANALYSES].create_index([("call_id", ASCENDING), ("created_at", DESCENDING)])
    db[CALL_ANALYSES].create_index([("lead_id", ASCENDING), ("created_at", DESCENDING)])
    logger.info("MongoDB indexes ensured")


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
