"""Lead read endpoints and follow-up actions."""

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.database import Database

from app.database import CALL_OUTCOMES, CALL_TRANSCRIPTS, CALLS, LEADS, get_db
from app.models.call import Call, CallOutcome, CallTranscript
from app.models.lead import FollowUpStatus
from app.models.schemas import FollowUpActionRequest, LeadDetail, LeadListItem
from app.routes.filters import LeadFilters, apply_bucket_filter, lead_filters
from app.services import followup_service
from app.services.followup_service import decorate_lead, utcnow

router = APIRouter(prefix="/api/leads", tags=["leads"])

# A lead is on the active follow-up worklist only while its follow-up is
# required AND still pending. Converted/dropped leads can never match.
ACTIVE_FOLLOW_UP_QUERY = {
    "follow_up.required": True,
    "follow_up.status": FollowUpStatus.PENDING.value,
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
    return db[CALLS].find_one({"lead_id": lead_id}, sort=[("ended_at", -1)])


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
    """Leads that currently require action, most overdue first."""
    query = filters.mongo_query(ACTIVE_FOLLOW_UP_QUERY)
    docs = list(db[LEADS].find(query).sort("follow_up.datetime", 1))
    now = utcnow()
    return [decorate_lead(d, now) for d in apply_bucket_filter(docs, filters, now)]


@router.get("/non-follow-ups", response_model=list[LeadListItem])
def list_non_follow_ups(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Converted, dropped and otherwise closed leads."""
    base = {
        "$or": [
            {"follow_up.required": {"$ne": True}},
            {
                "follow_up.status": {
                    "$in": [FollowUpStatus.COMPLETED.value, FollowUpStatus.CANCELLED.value]
                }
            },
        ]
    }
    docs = list(db[LEADS].find(filters.mongo_query(base)).sort("updated_at", -1))
    now = utcnow()
    return [decorate_lead(d, now) for d in apply_bucket_filter(docs, filters, now)]


@router.get("/{lead_id}", response_model=LeadDetail)
def get_lead(lead_id: str, db: Database = Depends(get_db)) -> dict:
    """Everything the details modal needs, in one round trip."""
    lead = _get_lead_or_404(db, lead_id)
    detail = decorate_lead(lead)

    call = _latest_call(db, lead_id)
    transcript = None
    outcome = None
    if call:
        transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call["call_id"]})
        outcome = db[CALL_OUTCOMES].find_one({"call_id": call["call_id"]})
    if transcript is None:
        transcript = db[CALL_TRANSCRIPTS].find_one({"lead_id": lead_id}, sort=[("created_at", -1)])
    if outcome is None:
        outcome = db[CALL_OUTCOMES].find_one({"lead_id": lead_id}, sort=[("processed_at", -1)])

    detail["latest_call"] = _strip_id(call)
    detail["latest_transcript"] = _strip_id(transcript)
    detail["latest_call_outcome"] = _strip_id(outcome)
    return detail


@router.get("/{lead_id}/calls", response_model=list[Call])
def get_lead_calls(lead_id: str, db: Database = Depends(get_db)) -> list[dict]:
    _get_lead_or_404(db, lead_id)
    return [_strip_id(c) for c in db[CALLS].find({"lead_id": lead_id}).sort("ended_at", -1)]


@router.get("/{lead_id}/transcript/latest", response_model=CallTranscript)
def get_latest_transcript(lead_id: str, db: Database = Depends(get_db)) -> dict:
    _get_lead_or_404(db, lead_id)
    transcript = db[CALL_TRANSCRIPTS].find_one({"lead_id": lead_id}, sort=[("created_at", -1)])
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"No transcript found for lead '{lead_id}'")
    return _strip_id(transcript)


@router.get("/{lead_id}/outcome", response_model=CallOutcome)
def get_latest_outcome(lead_id: str, db: Database = Depends(get_db)) -> dict:
    _get_lead_or_404(db, lead_id)
    outcome = db[CALL_OUTCOMES].find_one({"lead_id": lead_id}, sort=[("processed_at", -1)])
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail=f"No processed call outcome for lead '{lead_id}'. "
            "Run POST /api/calls/{call_id}/process first.",
        )
    return _strip_id(outcome)


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
