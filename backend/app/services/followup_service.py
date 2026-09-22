"""Follow-up domain logic.

Turns an AnalysisResult into persisted state (call_outcomes + the lead's
denormalized follow-up block), derives the time-sensitive bucket at read time,
and applies BD actions (complete / cancel / reschedule).
"""

from datetime import datetime, timedelta, timezone

from pymongo.database import Database

from app.database import CALL_OUTCOMES, CALLS, LEADS
from app.models.call import Outcome
from app.models.lead import FollowUpAction, FollowUpBucket, FollowUpStatus, LeadStatus
from app.models.schemas import FollowUpActionRequest
from app.services.call_analyzer import AnalysisResult

# A follow-up counts as DUE (rather than UPCOMING) once it is this close.
DUE_WINDOW = timedelta(hours=2)

OUTCOME_TO_LEAD_STATUS = {
    Outcome.FOLLOW_UP_REQUIRED: LeadStatus.FOLLOW_UP,
    Outcome.CONVERTED: LeadStatus.CONVERTED,
    Outcome.DROPPED: LeadStatus.DROPPED,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Bucket derivation (never persisted -- always computed against "now")
# --------------------------------------------------------------------------


def compute_bucket(follow_up: dict | None, now: datetime | None = None) -> FollowUpBucket:
    if not follow_up or not follow_up.get("required"):
        return FollowUpBucket.NONE

    status = follow_up.get("status")
    if status == FollowUpStatus.COMPLETED.value:
        return FollowUpBucket.COMPLETED
    if status == FollowUpStatus.CANCELLED.value:
        return FollowUpBucket.CANCELLED

    when = _as_utc(follow_up.get("datetime"))
    if when is None:
        return FollowUpBucket.UPCOMING

    now = now or utcnow()
    if when < now:
        return FollowUpBucket.OVERDUE
    if when.astimezone(timezone.utc).date() == now.date() and when - now <= DUE_WINDOW:
        return FollowUpBucket.DUE
    return FollowUpBucket.UPCOMING


def decorate_lead(lead: dict, now: datetime | None = None) -> dict:
    """Attach the derived bucket and strip Mongo's _id for API responses."""
    lead = dict(lead)
    lead.pop("_id", None)
    follow_up = dict(lead.get("follow_up") or {"required": False})
    follow_up["bucket"] = compute_bucket(follow_up, now).value
    lead["follow_up"] = follow_up
    return lead


def build_follow_up_block(when: datetime) -> dict:
    when = _as_utc(when)
    return {
        "required": True,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H:%M"),
        "datetime": when,
        "status": FollowUpStatus.PENDING.value,
    }


NO_FOLLOW_UP = {
    "required": False,
    "date": None,
    "time": None,
    "datetime": None,
    "status": None,
}


# --------------------------------------------------------------------------
# Analysis -> persistence
# --------------------------------------------------------------------------


def apply_analysis(db: Database, call: dict, analysis: AnalysisResult) -> dict:
    """Persist an analysis result.

    1. Upsert call_outcomes keyed on call_id -- this is what makes the process
       endpoint idempotent: re-processing overwrites instead of duplicating.
    2. Project the outcome onto the lead (status + follow-up block), but only
       when this call is the lead's most recent call, so replaying an older
       call cannot resurrect a stale follow-up.
    """
    now = utcnow()
    lead_id = call["lead_id"]

    if analysis.follow_up_required and analysis.follow_up_datetime:
        follow_up = build_follow_up_block(analysis.follow_up_datetime)
    else:
        follow_up = dict(NO_FOLLOW_UP)

    existing = db[CALL_OUTCOMES].find_one({"call_id": call["call_id"]})

    outcome_doc = {
        "call_id": call["call_id"],
        "lead_id": lead_id,
        "outcome": analysis.outcome.value,
        "reason": analysis.reason,
        "follow_up": follow_up,
        "processed_at": now,
        "analyzer": analysis.analyzer,
        "confidence": analysis.confidence,
    }
    db[CALL_OUTCOMES].update_one(
        {"call_id": call["call_id"]}, {"$set": outcome_doc}, upsert=True
    )

    applied = _is_latest_call(db, lead_id, call)
    if applied:
        lead_update = {
            "lead_status": OUTCOME_TO_LEAD_STATUS[analysis.outcome].value,
            "follow_up": follow_up,
            "last_call_at": _as_utc(call.get("ended_at")),
            "latest_call_id": call["call_id"],
            "latest_outcome": analysis.outcome.value,
            "updated_at": now,
        }
        db[LEADS].update_one({"lead_id": lead_id}, {"$set": lead_update})

    lead = db[LEADS].find_one({"lead_id": lead_id})
    return {
        "outcome_doc": outcome_doc,
        "lead": lead,
        "already_processed": existing is not None,
        "applied_to_lead": applied,
    }


def _is_latest_call(db: Database, lead_id: str, call: dict) -> bool:
    latest = db[CALLS].find_one({"lead_id": lead_id}, sort=[("ended_at", -1)])
    return latest is None or latest["call_id"] == call["call_id"]


# --------------------------------------------------------------------------
# BD actions on a follow-up
# --------------------------------------------------------------------------


def apply_followup_action(db: Database, lead: dict, request: FollowUpActionRequest) -> dict:
    """Apply COMPLETE / CANCEL / RESCHEDULE, recording the reason in history.

    Returns the updated lead. Raises ValueError when the lead has no active
    follow-up to act on (the route turns that into a 409).
    """
    follow_up = dict(lead.get("follow_up") or {})
    if not follow_up.get("required"):
        raise ValueError(
            f"Lead {lead['lead_id']} has no active follow-up, so no action can be applied."
        )

    now = utcnow()
    from_status = follow_up.get("status")
    previous_datetime = _as_utc(follow_up.get("datetime"))
    new_datetime = None

    if request.action is FollowUpAction.COMPLETE:
        follow_up["status"] = FollowUpStatus.COMPLETED.value
        lead_status = LeadStatus.CONTACTED.value
    elif request.action is FollowUpAction.CANCEL:
        follow_up["status"] = FollowUpStatus.CANCELLED.value
        lead_status = LeadStatus.CONTACTED.value
    else:  # RESCHEDULE
        new_datetime = _as_utc(request.new_datetime)
        follow_up = build_follow_up_block(new_datetime)
        lead_status = LeadStatus.FOLLOW_UP.value

    history_entry = {
        "action": request.action.value,
        "reason": request.reason,
        "from_status": from_status,
        "to_status": follow_up.get("status"),
        "previous_datetime": previous_datetime,
        "new_datetime": new_datetime,
        "at": now,
    }

    db[LEADS].update_one(
        {"lead_id": lead["lead_id"]},
        {
            "$set": {"follow_up": follow_up, "lead_status": lead_status, "updated_at": now},
            "$push": {"follow_up_history": history_entry},
        },
    )
    return db[LEADS].find_one({"lead_id": lead["lead_id"]})
