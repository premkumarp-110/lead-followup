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
FOLLOWUP_ALERTS = "followup_alerts"

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongodb_url,
            serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
            tz_aware=True,
        )
    return _client


def get_db() -> Database:
    return get_client()[settings.database_name]


def ping() -> None:
    """Raise a readable error if MongoDB is unavailable."""
    try:
        get_client().admin.command("ping")
    except PyMongoError as exc:
        # "Is mongod running?" is useless advice for a hosted cluster, where
        # the usual causes are an IP access list or a timeout that is too
        # short for the TLS handshake.
        remote = settings.mongodb_url.startswith("mongodb+srv://")
        hint = (
            "Check that this machine's IP is on the cluster's access list, that the cluster is "
            f"not paused, and that MONGODB_TIMEOUT_MS ({settings.mongodb_timeout_ms} ms) is long "
            "enough for the connection to be established."
            if remote
            else "Is mongod running?"
        )
        raise RuntimeError(
            f"Cannot reach MongoDB at the configured MONGODB_URL. {hint} ({exc})"
        ) from exc


def ensure_indexes() -> None:
    db = get_db()
    db[LEADS].create_index([("lead_id", ASCENDING)], unique=True)
    db[LEADS].create_index([("follow_up.datetime", ASCENDING)])
    # Ownership: the BD filter matches on email (names collide across 100+
    # owners) but id lookups happen too, so both are indexed.
    db[LEADS].create_index([("owner_email", ASCENDING)])
    db[LEADS].create_index([("owner_id", ASCENDING)])
    # The CRM join key. ~4% of real leads have externalId: null, and `sparse`
    # is NOT enough here -- sparse only skips documents where the field is
    # ABSENT, so two explicit nulls still collide. A partial index restricted to
    # actual strings is what makes uniqueness apply only where a value exists.
    db[LEADS].create_index(
        [("external_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"external_id": {"$type": "string"}},
    )
    # Filter targets.
    db[LEADS].create_index([("stage", ASCENDING)])
    db[LEADS].create_index([("product", ASCENDING)])
    db[LEADS].create_index([("lead_status", ASCENDING)])
    db[LEADS].create_index([("latest_outcome", ASCENDING)])
    # Filtered on in the leads list and read by sort_worklist on every request.
    db[LEADS].create_index([("latest_sentiment.label", ASCENDING)])
    # Every list endpoint sorts on this.
    db[LEADS].create_index([("updated_at", DESCENDING)])
    # Backs ACTIVE_FOLLOW_UP_QUERY plus its date bounds in one index.
    db[LEADS].create_index(
        [
            ("follow_up.required", ASCENDING),
            ("follow_up.status", ASCENDING),
            ("follow_up.datetime", ASCENDING),
        ]
    )

    db[CALLERS].create_index([("caller_id", ASCENDING)], unique=True)

    db[CALLS].create_index([("call_id", ASCENDING)], unique=True)
    db[CALLS].create_index([("lead_id", ASCENDING), ("end_time", DESCENDING)])
    db[CALLS].create_index([("status", ASCENDING)])
    db[CALLS].create_index([("caller_id", ASCENDING)])

    db[CALL_TRANSCRIPTS].create_index([("transcript_id", ASCENDING)], unique=True)
    db[CALL_TRANSCRIPTS].create_index([("call_id", ASCENDING)])
    db[CALL_TRANSCRIPTS].create_index([("lead_id", ASCENDING), ("created_at", DESCENDING)])

    db[CALL_ANALYSES].create_index([("analysis_id", ASCENDING)], unique=True)
    # Not unique on call_id: a failed Gemini attempt and its keyword fallback
    # are two documents for the same call, kept apart by `status`.
    db[CALL_ANALYSES].create_index([("call_id", ASCENDING), ("created_at", DESCENDING)])
    db[CALL_ANALYSES].create_index([("lead_id", ASCENDING), ("created_at", DESCENDING)])

    db[FOLLOWUP_ALERTS].create_index([("alert_id", ASCENDING)], unique=True)
    # Not unique: one BD can have a SKIPPED row and a later SENT row on the
    # same day, and manual re-sends are deliberately allowed to repeat. The
    # once-a-day rule for SCHEDULED sends is enforced in the service.
    db[FOLLOWUP_ALERTS].create_index([("bd_id", ASCENDING), ("sent_for_date", DESCENDING)])
    db[FOLLOWUP_ALERTS].create_index([("sent_at", DESCENDING)])
    logger.info("MongoDB indexes ensured")


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
