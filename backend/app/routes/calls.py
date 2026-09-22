"""Call endpoints, including the 'call completed' processing flow.

POST /api/calls/{call_id}/process is the seam where, in production, a worker
reacting to a call-completed event would call in. Today it runs the
deterministic analyzer; tomorrow the same endpoint runs the LLM one, selected
by ANALYZER_BACKEND, with no change here.
"""

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from app.database import CALL_OUTCOMES, CALL_TRANSCRIPTS, CALLS, get_db
from app.models.call import Call, CallOutcome, CallTranscript
from app.models.schemas import ProcessCallResponse
from app.services import followup_service
from app.services.call_analyzer import get_analyzer

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _strip_id(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _get_call_or_404(db: Database, call_id: str) -> dict:
    call = db[CALLS].find_one({"call_id": call_id})
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' not found")
    return call


@router.get("", response_model=list[Call])
def list_calls(lead_id: str | None = None, db: Database = Depends(get_db)) -> list[dict]:
    query = {"lead_id": lead_id} if lead_id else {}
    return [_strip_id(c) for c in db[CALLS].find(query).sort("ended_at", -1)]


@router.get("/{call_id}", response_model=Call)
def get_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    return _strip_id(_get_call_or_404(db, call_id))


@router.get("/{call_id}/transcript", response_model=CallTranscript)
def get_call_transcript(call_id: str, db: Database = Depends(get_db)) -> dict:
    _get_call_or_404(db, call_id)
    transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call_id})
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"No transcript stored for call '{call_id}'")
    return _strip_id(transcript)


@router.get("/{call_id}/outcome", response_model=CallOutcome)
def get_call_outcome(call_id: str, db: Database = Depends(get_db)) -> dict:
    _get_call_or_404(db, call_id)
    outcome = db[CALL_OUTCOMES].find_one({"call_id": call_id})
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail=f"Call '{call_id}' has not been processed yet. POST to /process first.",
        )
    return _strip_id(outcome)


@router.post("/{call_id}/process", response_model=ProcessCallResponse)
def process_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    """Analyze a completed call's transcript and update the outcome + lead.

    Idempotent: call_outcomes is keyed uniquely on call_id, so re-processing
    overwrites the existing outcome rather than creating a duplicate.
    """
    call = _get_call_or_404(db, call_id)

    transcript = None
    if call.get("transcript_id"):
        transcript = db[CALL_TRANSCRIPTS].find_one({"transcript_id": call["transcript_id"]})
    if transcript is None:
        transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call_id})
    if transcript is None:
        raise HTTPException(
            status_code=422,
            detail=f"Call '{call_id}' has no transcript yet, so it cannot be analyzed.",
        )

    analyzer = get_analyzer()
    analysis = analyzer.analyze_call(
        transcript["transcript"],
        context={
            "call_ended_at": call.get("ended_at"),
            "lead_id": call["lead_id"],
            "call_id": call_id,
        },
    )

    result = followup_service.apply_analysis(db, call, analysis)
    lead = result["lead"] or {}

    if result["applied_to_lead"]:
        message = (
            "Call processed; outcome re-applied to the lead."
            if result["already_processed"]
            else "Call processed and lead updated."
        )
    else:
        message = (
            "Call processed. The lead was not updated because a more recent call exists "
            "for this lead."
        )

    return {
        "call_id": call_id,
        "lead_id": call["lead_id"],
        "outcome": analysis.outcome,
        "reason": analysis.reason,
        "follow_up": result["outcome_doc"]["follow_up"],
        "lead_status": lead.get("lead_status", "NEW"),
        "analyzer": analysis.analyzer,
        "already_processed": result["already_processed"],
        "applied_to_lead": result["applied_to_lead"],
        "message": message,
    }
