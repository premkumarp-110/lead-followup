"""Lead read endpoints and follow-up actions.

Literal sub-paths (/follow-ups, /closed, ...) are declared BEFORE /{lead_id};
anything added below /{lead_id} would match as a lead id instead.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

from app.database import CALL_ANALYSES, CALL_TRANSCRIPTS, CALLERS, CALLS, LEADS, get_db
from app.models.analysis import CallAnalysis
from app.models.call import Call, CallTranscript
from app.models.lead import FollowUpStatus, LeadStatus
from app.models.schemas import FollowUpActionRequest, LeadDetail, LeadListItem
from app.routes.filters import LeadFilters, apply_bucket_filter, lead_filters
from app.services import followup_service
from app.services.followup_service import decorate_lead, sort_worklist, utcnow

router = APIRouter(prefix="/api/leads", tags=["leads"])

# A lead is on the active follow-up worklist only while its follow-up is
# required AND still pending. Converted/dropped leads can never match.
ACTIVE_FOLLOW_UP_QUERY = {
    "follow_up.required": True,
    "follow_up.status": FollowUpStatus.PENDING.value,
}

CLOSED_QUERY = {
    "$or": [
        {"lead_status": {"$in": [LeadStatus.CONVERTED.value, LeadStatus.DROPPED.value]}},
        {"follow_up.status": {"$in": [FollowUpStatus.COMPLETED.value, FollowUpStatus.CANCELLED.value]}},
    ]
}


def _strip_id(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _get_lead_or_404(db: Database, lead_id: str) -> dict:
    lead = db[LEADS].find_one({"lead_id": lead_id})
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id}' not found")
    return lead


def _latest_call(db: Database, lead_id: str) -> dict | None:
    return db[CALLS].find_one({"lead_id": lead_id}, sort=[("ended_at", -1), ("created_at", -1)])


def _latest_analysis(db: Database, lead: dict) -> dict | None:
    if lead.get("latest_analysis_id"):
        found = db[CALL_ANALYSES].find_one({"analysis_id": lead["latest_analysis_id"]})
        if found:
            return found
    return db[CALL_ANALYSES].find_one(
        {"lead_id": lead["lead_id"], "status": "COMPLETED"}, sort=[("created_at", -1)]
    )


def _latest_transcript(db: Database, lead_id: str, call: dict | None) -> dict | None:
    if call and call.get("transcript_id"):
        found = db[CALL_TRANSCRIPTS].find_one({"transcript_id": call["transcript_id"]})
        if found:
            return found
    return db[CALL_TRANSCRIPTS].find_one({"lead_id": lead_id}, sort=[("created_at", -1)])


# --------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------


@router.get("", response_model=list[LeadListItem])
@router.get("/", response_model=list[LeadListItem], include_in_schema=False)
def list_leads(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Every lead, filtered. The dashboard uses the two focused lists below."""
    docs = list(db[LEADS].find(filters.mongo_query()).sort("updated_at", -1))
    now = utcnow()
    return [decorate_lead(d, now) for d in apply_bucket_filter(docs, filters, now)]


@router.get("/follow-ups", response_model=list[LeadListItem])
def list_follow_ups(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Leads that currently require action: overdue, due, upcoming, then unscheduled."""
    query = filters.mongo_query(ACTIVE_FOLLOW_UP_QUERY)
    docs = list(db[LEADS].find(query))
    now = utcnow()
    decorated = [decorate_lead(d, now) for d in apply_bucket_filter(docs, filters, now)]
    return sort_worklist(decorated)


@router.get("/closed", response_model=list[LeadListItem])
@router.get("/non-follow-ups", response_model=list[LeadListItem], include_in_schema=False)
def list_closed(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Converted, dropped, and leads whose follow-up was completed or cancelled."""
    query = filters.mongo_query(CLOSED_QUERY)
    docs = list(db[LEADS].find(query).sort("updated_at", -1))
    now = utcnow()
    return [decorate_lead(d, now) for d in apply_bucket_filter(docs, filters, now)]


# --------------------------------------------------------------------------
# Single lead
# --------------------------------------------------------------------------


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: str, db: Database = Depends(get_db)) -> dict:
    """Everything the details modal needs, in one round trip."""
    lead = _get_lead_or_404(db, lead_id)
    detail = decorate_lead(lead)

    call = _latest_call(db, lead_id)
    caller = (
        db[CALLERS].find_one({"caller_id": call["caller_id"]}) if call and call.get("caller_id") else None
    )
    detail["latest_call"] = _strip_id(call)
    detail["latest_caller"] = _strip_id(caller)
    detail["latest_transcript"] = _strip_id(_latest_transcript(db, lead_id, call))
    detail["latest_analysis"] = _strip_id(_latest_analysis(db, lead))
    return detail


@router.get("/{lead_id}/calls", response_model=list[Call])
def get_lead_calls(lead_id: str, db: Database = Depends(get_db)) -> list[dict]:
    _get_lead_or_404(db, lead_id)
    return [_strip_id(c) for c in db[CALLS].find({"lead_id": lead_id}).sort("created_at", -1)]


@router.get("/{lead_id}/latest-transcript", response_model=CallTranscript)
@router.get("/{lead_id}/transcript/latest", response_model=CallTranscript, include_in_schema=False)
def get_latest_transcript(lead_id: str, db: Database = Depends(get_db)) -> dict:
    _get_lead_or_404(db, lead_id)
    transcript = _latest_transcript(db, lead_id, _latest_call(db, lead_id))
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"No transcript found for lead '{lead_id}'")
    return _strip_id(transcript)


@router.get("/{lead_id}/latest-analysis", response_model=CallAnalysis)
@router.get("/{lead_id}/outcome", response_model=CallAnalysis, include_in_schema=False)
def get_latest_analysis(lead_id: str, db: Database = Depends(get_db)) -> dict:
    lead = _get_lead_or_404(db, lead_id)
    analysis = _latest_analysis(db, lead)
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail=f"No completed analysis for lead '{lead_id}'. Process a call first.",
        )
    return _strip_id(analysis)


@router.patch("/{lead_id}/follow-up", response_model=LeadDetail)
def update_follow_up(
    lead_id: str,
    request: FollowUpActionRequest,
    db: Database = Depends(get_db),
) -> dict:
    """Mark done / cancel / reschedule a follow-up. A reason is always required."""
    lead = _get_lead_or_404(db, lead_id)
    try:
        followup_service.apply_followup_action(db, lead, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return get_lead(lead_id, db)
