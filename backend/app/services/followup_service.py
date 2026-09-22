"""Follow-up domain logic.

Turns a validated analysis into persisted state (the lead's denormalized
follow-up block), derives the time-sensitive bucket at read time, and applies
BD actions (complete / cancel / reschedule).

This module does not know where an analysis came from -- Gemini, the keyword
fallback and the seed all arrive here as a `CallAnalysisResult`.
"""

from datetime import datetime, timedelta, timezone

from pymongo.database import Database

from app.database import CALLS, LEADS
from app.models.analysis import CallAnalysisResult
from app.models.call import CallStatus, Outcome
from app.models.lead import FollowUpAction, FollowUpBucket, FollowUpStatus, LeadStatus
from app.models.schemas import FollowUpActionRequest

# A follow-up counts as DUE (rather than UPCOMING) once it is this close.
DUE_WINDOW = timedelta(hours=2)

OUTCOME_TO_LEAD_STATUS = {
    Outcome.FOLLOW_UP_REQUIRED: LeadStatus.FOLLOW_UP,
    Outcome.CONVERTED: LeadStatus.CONVERTED,
    Outcome.DROPPED: LeadStatus.DROPPED,
}

# Order used when sorting the active worklist: most urgent first, and leads
# with no date at the end (they need a call to *set* a date, not a timed one).
BUCKET_SORT_ORDER = {
    FollowUpBucket.OVERDUE.value: 0,
    FollowUpBucket.DUE.value: 1,
    FollowUpBucket.UPCOMING.value: 2,
    FollowUpBucket.UNSCHEDULED.value: 3,
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
        # Required, pending, but the lead never named a time (spec S12).
        return FollowUpBucket.UNSCHEDULED

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


def sort_worklist(leads: list[dict]) -> list[dict]:
    """Overdue -> due -> upcoming (each soonest first) -> unscheduled.

    Mongo sorts null datetimes FIRST in ascending order, which would put
    unscheduled leads at the top; this puts them where they belong.
    """
    far_future = datetime.max.replace(tzinfo=timezone.utc)

    def key(lead: dict):
        follow_up = lead.get("follow_up") or {}
        bucket = follow_up.get("bucket") or compute_bucket(follow_up).value
        when = _as_utc(follow_up.get("datetime")) or far_future
        return (BUCKET_SORT_ORDER.get(bucket, 9), when)

    return sorted(leads, key=key)


def build_follow_up_block(
    when: datetime | None,
    reason: str | None = None,
    date_str: str | None = None,
    time_str: str | None = None,
) -> dict:
    """The lead's denormalized follow-up block.

    `date`/`time` come from the analysis when present (they are in the lead's
    local timezone); otherwise they are rendered from the UTC instant.
    """
    when = _as_utc(when)
    return {
        "required": True,
        "date": date_str or (when.strftime("%Y-%m-%d") if when else None),
        "time": time_str or (when.strftime("%H:%M") if when else None),
        "datetime": when,
        "status": FollowUpStatus.PENDING.value,
        "reason": reason,
    }


NO_FOLLOW_UP = {
    "required": False,
    "date": None,
    "time": None,
    "datetime": None,
    "status": None,
    "reason": None,
}


# --------------------------------------------------------------------------
# Analysis -> lead projection
# --------------------------------------------------------------------------


def follow_up_block_from_analysis(analysis: CallAnalysisResult) -> dict:
    if analysis.follow_up_required:
        fu = analysis.follow_up
        return build_follow_up_block(
            fu.datetime if fu else None,
            reason=fu.reason if fu else None,
            date_str=fu.date if fu else None,
            time_str=fu.time if fu else None,
        )
    return dict(NO_FOLLOW_UP)


def is_latest_call(db: Database, lead_id: str, call: dict) -> bool:
    """True unless a *processed* call for this lead ended after this one.

    Only COMPLETED calls count: the guard exists to stop an older recording
    overwriting a newer call's outcome, and an unprocessed or failed call has
    no outcome to protect.
    """
    this_ended = _as_utc(call.get("ended_at") or call.get("created_at"))
    newer = db[CALLS].find_one(
        {
            "lead_id": lead_id,
            "call_id": {"$ne": call["call_id"]},
            "status": CallStatus.COMPLETED.value,
            "ended_at": {"$gt": this_ended},
        }
    )
    return newer is None


def apply_analysis_to_lead(
    db: Database, call: dict, analysis: CallAnalysisResult, analysis_id: str
) -> bool:
    """Project a validated analysis onto the lead (spec S15).

    Only applied when this call is the lead's most recent call, so replaying an
    older recording cannot resurrect a stale follow-up. Returns whether the
    lead was updated.
    """
    lead_id = call["lead_id"]
    if not is_latest_call(db, lead_id, call):
        return False

    now = utcnow()
    db[LEADS].update_one(
        {"lead_id": lead_id},
        {
            "$set": {
                "lead_status": OUTCOME_TO_LEAD_STATUS[analysis.outcome].value,
                "follow_up": follow_up_block_from_analysis(analysis),
                "last_call_at": _as_utc(call.get("ended_at")) or _as_utc(call.get("created_at")),
                "latest_call_id": call["call_id"],
                "latest_outcome": analysis.outcome.value,
                "latest_analysis_id": analysis_id,
                "updated_at": now,
            }
        },
    )
    return True


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
        follow_up = build_follow_up_block(new_datetime, reason=follow_up.get("reason"))
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
