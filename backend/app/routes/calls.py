"""Call endpoints: read a call, analyse it, stream its recording.

There is no ingestion here any more. Calls arrive from the Lead Call API (the
CRM), which already holds the recording and, for most analyzable calls, the
transcript too. What is left is reading them and running our own analysis --
the one thing the CRM does not do, which is turning "call back tomorrow at
11 AM" into a scheduled follow-up datetime.

Route ordering matters: literal paths are declared before `/{call_id}` so
`/{call_id}` cannot swallow them.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pymongo.database import Database

from app.config import settings
from app.database import CALL_ANALYSES, CALL_TRANSCRIPTS, CALLS, get_db
from app.models.analysis import CallAnalysis
from app.models.call import Call, CallStatus, CallTranscript
from app.models.schemas import AnalyzeCallResponse
from app.services import audio_service, call_analysis_service
from app.services.audio_service import RecordingNotAvailable
from app.services.call_analysis_service import CallProcessingError
from app.services.followup_service import compute_bucket
from app.services.lead_call_client import LeadCallAPIError, LeadCallUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calls", tags=["calls"])

# Statuses that mean the pipeline is mid-flight for this call.
IN_FLIGHT = {
    CallStatus.PROCESSING.value,
    CallStatus.TRANSCRIBING.value,
    CallStatus.ANALYZING.value,
}


def _strip_id(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _get_call_or_404(db: Database, call_id: str) -> dict:
    call = db[CALLS].find_one({"call_id": call_id})
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' was not found.")
    return call


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


@router.post("/{call_id}/analyze", response_model=AnalyzeCallResponse)
def analyze_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    """Analyse one existing call and apply the outcome to its lead.

    Safe to re-run: a stored transcript is reused, so a retry only repeats the
    step that actually failed, and re-analysing a call that is no longer the
    lead's latest will not overwrite a newer outcome.
    """
    call = _get_call_or_404(db, call_id)

    if call.get("status") in IN_FLIGHT:
        raise HTTPException(status_code=409, detail=f"Call '{call_id}' is already being analysed.")

    # Refuse up front rather than burning an LLM call on a call that cannot
    # produce anything -- not connected, zero duration, or no source material.
    if not (call.get("has_transcript") or call.get("has_recording") or call.get("transcript_id")):
        raise HTTPException(
            status_code=409,
            detail="This call has neither a transcript nor a recording, so there is nothing to analyse.",
        )

    try:
        result = call_analysis_service.process_call(db, call)
    except CallProcessingError as exc:
        # The service already recorded the failure on the call itself.
        if exc.stage is CallStatus.NOT_ANALYZABLE:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        status_code = 503 if exc.stage in (CallStatus.TRANSCRIBING, CallStatus.ANALYZING) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    analysis = _strip_id(result.analysis)
    lead = result.lead or {}
    follow_up = dict(lead.get("follow_up") or {"required": False})
    follow_up["bucket"] = compute_bucket(follow_up).value

    if result.applied_to_lead:
        message = "Call analysed and lead updated."
    else:
        message = ("Call analysed. The lead was not updated because a more recent call "
                   "exists for this lead.")
    if result.degraded:
        message += " The analysis model was unavailable, so the deterministic fallback was used."

    return {
        "call_id": call_id,
        "lead_id": call["lead_id"],
        "caller_id": call.get("caller_id"),
        "status": result.call.get("status"),
        "outcome": analysis.get("outcome") if analysis else None,
        "analysis": analysis,
        "transcript": _strip_id(result.transcript),
        "follow_up": follow_up if result.applied_to_lead else None,
        "lead_status": lead.get("lead_status"),
        "applied_to_lead": result.applied_to_lead,
        "degraded": result.degraded,
        "message": message,
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@router.get("", response_model=list[Call])
@router.get("/", response_model=list[Call], include_in_schema=False)
def list_calls(lead_id: str | None = None, db: Database = Depends(get_db)) -> list[dict]:
    query = {"lead_id": lead_id} if lead_id else {}
    return [_strip_id(c) for c in db[CALLS].find(query).sort("call_time", -1)]


@router.get("/{call_id}", response_model=Call)
def get_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    return _strip_id(_get_call_or_404(db, call_id))


@router.get("/{call_id}/transcript", response_model=CallTranscript)
def get_call_transcript(call_id: str, db: Database = Depends(get_db)) -> dict:
    _get_call_or_404(db, call_id)
    transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call_id}, sort=[("created_at", -1)])
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' has no transcript.")
    return _strip_id(transcript)


@router.get("/{call_id}/analysis", response_model=CallAnalysis)
def get_call_analysis(call_id: str, db: Database = Depends(get_db)) -> dict:
    """The winning analysis for the call (failed attempts are in /analyses)."""
    call = _get_call_or_404(db, call_id)
    analysis = None
    if call.get("analysis_id"):
        analysis = db[CALL_ANALYSES].find_one({"analysis_id": call["analysis_id"]})
    if analysis is None:
        analysis = db[CALL_ANALYSES].find_one(
            {"call_id": call_id, "status": "COMPLETED"}, sort=[("created_at", -1)]
        )
    if analysis is None:
        raise HTTPException(
            status_code=404,
            detail=f"Call '{call_id}' has not been analysed yet.",
        )
    return _strip_id(analysis)


@router.get("/{call_id}/analyses", response_model=list[CallAnalysis])
def get_call_analyses(call_id: str, db: Database = Depends(get_db)) -> list[dict]:
    """Every attempt for the call, newest first -- including failed ones with raw output."""
    _get_call_or_404(db, call_id)
    return [_strip_id(a) for a in db[CALL_ANALYSES].find({"call_id": call_id}).sort("created_at", -1)]


@router.get("/{call_id}/audio", include_in_schema=True)
def get_call_audio(call_id: str, db: Database = Depends(get_db)):
    """Stream the recording, fetching it from the CRM on first request.

    Deliberately NOT a redirect to the CRM: a browser cannot attach the
    `X-API-Key` header to an <audio src>, so a redirect yields 401. The first
    request buffers the audio to disk and every later one serves the cached
    file, which also restores the duration and seeking that upstream's missing
    content-length and Range support would otherwise cost.
    """
    if not settings.audio_playback_enabled:
        raise HTTPException(
            status_code=404, detail="Audio playback is disabled (AUDIO_PLAYBACK_ENABLED=false)."
        )

    call = _get_call_or_404(db, call_id)

    if not call.get("has_recording"):
        raise HTTPException(status_code=404, detail="This call has no recording.")

    try:
        recording = audio_service.fetch_recording(call_id, call.get("crm_call_id"))
    except RecordingNotAvailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LeadCallUnavailable as exc:
        # The CRM or its telephony provider was unreachable -- retryable.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LeadCallAPIError as exc:
        # Missing/invalid/revoked key: an operator problem, never a 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return FileResponse(
        recording.path,
        media_type=audio_service.AUDIO_MIME,
        filename=f"{call_id}.mp3",
        content_disposition_type="inline",
    )
