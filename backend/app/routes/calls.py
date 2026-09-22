"""Call ingestion, processing and read endpoints.

Routes stay thin: validate the request, delegate to a service, map service
errors onto HTTP status codes. The pipeline itself lives in
services/call_analysis_service.py.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pymongo.database import Database

from app.config import settings
from app.database import CALL_ANALYSES, CALL_TRANSCRIPTS, CALLERS, CALLS, LEADS, get_db
from app.models.analysis import CallAnalysis
from app.models.call import Call, CallStatus, CallTranscript, SourceType
from app.models.schemas import (
    CallCreatedResponse,
    CallFromTextRequest,
    CallFromUrlRequest,
    CallStatusResponse,
    ProcessCallResponse,
    ValidateUrlRequest,
    ValidateUrlResponse,
)
from app.services import audio_service, call_analysis_service
from app.services.audio_service import AudioValidationError
from app.services.call_analysis_service import CallProcessingError
from app.services.followup_service import compute_bucket, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calls", tags=["calls"])


def _strip_id(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _require_analyzer_enabled() -> None:
    """Guard for every ingestion/processing endpoint.

    Read endpoints (status, transcript, analysis, audio, list) stay open even
    when disabled, so history already produced while the feature was on
    remains viewable -- only new ingestion/processing is blocked.
    """
    if not settings.call_analyzer_enabled:
        raise HTTPException(
            status_code=403,
            detail="Call analysis is disabled (CALL_ANALYZER_ENABLED is not set to true in "
            "backend/.env).",
        )


def _get_call_or_404(db: Database, call_id: str) -> dict:
    call = db[CALLS].find_one({"call_id": call_id})
    if call is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' not found")
    return call


def _require_lead_and_caller(db: Database, lead_id: str, caller_id: str) -> tuple[dict, dict]:
    lead = db[LEADS].find_one({"lead_id": lead_id})
    if lead is None:
        raise HTTPException(status_code=404, detail=f"Lead '{lead_id}' not found")
    caller = db[CALLERS].find_one({"caller_id": caller_id})
    if caller is None:
        raise HTTPException(status_code=404, detail=f"Caller '{caller_id}' not found")
    return lead, caller


def _new_call_doc(lead_id: str, caller_id: str, source: audio_service.AudioSource) -> dict:
    now = utcnow()
    return {
        "call_id": audio_service.new_call_id(),
        "lead_id": lead_id,
        "caller_id": caller_id,
        "source_type": source.source_type,
        "audio_url": source.url,
        "audio_file_path": source.file_path,
        "audio_mime": source.mime_type,
        "audio_bytes": source.size_bytes,
        "audio_filename": source.filename,
        "duration_seconds": source.duration_seconds,
        "status": CallStatus.UPLOADED.value,
        "error": None,
        "transcript_id": None,
        "analysis_id": None,
        # The recording is submitted right after the call, so "now" is the
        # best available end time; the analyzer uses it to resolve "tomorrow".
        "started_at": None,
        "ended_at": now,
        "created_at": now,
        "processed_at": None,
    }


def _created(call: dict, message: str) -> dict:
    return {
        "call_id": call["call_id"],
        "lead_id": call["lead_id"],
        "caller_id": call["caller_id"],
        "source_type": call["source_type"],
        "status": call["status"],
        "duration_seconds": call.get("duration_seconds"),
        "audio_bytes": call.get("audio_bytes"),
        "audio_filename": call.get("audio_filename"),
        "message": message,
    }


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


@router.post("/upload", response_model=CallCreatedResponse, status_code=201)
def upload_call(
    file: UploadFile = File(..., description="Audio recording (mp3, wav, m4a, ogg, webm)"),
    lead_id: str = Form(...),
    caller_id: str = Form(...),
    db: Database = Depends(get_db),
) -> dict:
    """Accept an uploaded recording, store it, and create the call record."""
    _require_analyzer_enabled()
    _require_lead_and_caller(db, lead_id, caller_id)
    if settings.audio_storage_mode != "local":
        raise HTTPException(
            status_code=409,
            detail="AUDIO_STORAGE_MODE is 'url'; file uploads are disabled. Use the Audio URL option.",
        )

    call_id = audio_service.new_call_id()
    try:
        source = audio_service.store_upload(file, call_id)
    except AudioValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    call = _new_call_doc(lead_id, caller_id, source)
    call["call_id"] = call_id
    db[CALLS].insert_one(call)
    return _created(call, "Audio uploaded and call record created. POST /process to analyze.")


@router.post("/validate-url", response_model=ValidateUrlResponse)
def validate_url(request: ValidateUrlRequest) -> dict:
    """Confirm a URL is public, reachable and looks like audio -- without downloading it."""
    _require_analyzer_enabled()
    try:
        return audio_service.validate_audio_url(request.audio_url)
    except AudioValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/from-url", response_model=CallCreatedResponse, status_code=201)
def create_call_from_url(request: CallFromUrlRequest, db: Database = Depends(get_db)) -> dict:
    """Create a call record that references a remote recording."""
    _require_analyzer_enabled()
    _require_lead_and_caller(db, request.lead_id, request.caller_id)
    try:
        meta = audio_service.validate_audio_url(request.audio_url)
    except AudioValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    source = audio_service.source_from_url(request.audio_url, meta)
    call = _new_call_doc(request.lead_id, request.caller_id, source)
    db[CALLS].insert_one(call)
    return _created(call, "Audio URL accepted and call record created. POST /process to analyze.")


@router.post("/from-text", response_model=CallCreatedResponse, status_code=201)
def create_call_from_text(request: CallFromTextRequest, db: Database = Depends(get_db)) -> dict:
    """Create a call record from a transcript typed/pasted directly.

    No audio and no transcription: the transcript document is created up
    front and `transcript_id` is set on the call before it's even inserted,
    so /process's existing transcript-reuse check skips straight to analysis.
    """
    _require_analyzer_enabled()
    _require_lead_and_caller(db, request.lead_id, request.caller_id)

    source = audio_service.AudioSource(
        source_type=SourceType.TEXT.value,
        file_path=None,
        url=None,
        mime_type="text/plain",
        size_bytes=len(request.transcript.encode("utf-8")),
        filename=None,
        duration_seconds=None,
    )
    call = _new_call_doc(request.lead_id, request.caller_id, source)

    transcript_doc = {
        "transcript_id": audio_service.new_id("TR"),
        "call_id": call["call_id"],
        "lead_id": request.lead_id,
        "caller_id": request.caller_id,
        "transcript": request.transcript,
        "language": None,
        "duration_seconds": None,
        "provider": "user_text",
        "created_at": call["created_at"],
    }
    db[CALL_TRANSCRIPTS].insert_one(transcript_doc)
    call["transcript_id"] = transcript_doc["transcript_id"]
    db[CALLS].insert_one(call)
    return _created(call, "Transcript received and call record created. POST /process to analyze.")


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


@router.post("/{call_id}/process", response_model=ProcessCallResponse)
def process_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    """Transcribe, analyze, store and update the lead. Synchronous.

    Poll GET /api/calls/{call_id}/status while this runs to follow progress.
    Safe to re-run: a failed call retries from the failed step, and a completed
    call is re-analyzed (its transcript is reused).
    """
    _require_analyzer_enabled()
    call = _get_call_or_404(db, call_id)
    if call.get("status") in {CallStatus.PROCESSING.value, CallStatus.TRANSCRIBING.value, CallStatus.ANALYZING.value}:
        raise HTTPException(status_code=409, detail=f"Call '{call_id}' is already being processed.")

    try:
        result = call_analysis_service.process_call(db, call)
    except CallProcessingError as exc:
        # The service already recorded FAILED + the message on the call.
        status_code = 503 if exc.stage in (CallStatus.TRANSCRIBING, CallStatus.ANALYZING) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    analysis = _strip_id(result.analysis)
    lead = result.lead or {}
    follow_up = dict(lead.get("follow_up") or {"required": False})
    follow_up["bucket"] = compute_bucket(follow_up).value

    if result.applied_to_lead:
        message = "Call processed and lead updated."
    else:
        message = ("Call processed. The lead was not updated because a more recent call "
                   "exists for this lead.")
    if result.degraded:
        message += " Gemini was unavailable, so the deterministic fallback analyzer was used."

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


@router.get("/{call_id}/status", response_model=CallStatusResponse)
def get_call_status(call_id: str, db: Database = Depends(get_db)) -> dict:
    """Lightweight poll target for the processing stepper."""
    call = _get_call_or_404(db, call_id)
    return {
        "call_id": call_id,
        "status": call.get("status"),
        "error": call.get("error"),
        "failed_stage": call.get("failed_stage"),
        "transcript_id": call.get("transcript_id"),
        "analysis_id": call.get("analysis_id"),
        "processed_at": call.get("processed_at"),
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


@router.get("", response_model=list[Call])
@router.get("/", response_model=list[Call], include_in_schema=False)
def list_calls(lead_id: str | None = None, db: Database = Depends(get_db)) -> list[dict]:
    query = {"lead_id": lead_id} if lead_id else {}
    return [_strip_id(c) for c in db[CALLS].find(query).sort("created_at", -1)]


@router.get("/{call_id}", response_model=Call)
def get_call(call_id: str, db: Database = Depends(get_db)) -> dict:
    return _strip_id(_get_call_or_404(db, call_id))


@router.get("/{call_id}/transcript", response_model=CallTranscript)
def get_call_transcript(call_id: str, db: Database = Depends(get_db)) -> dict:
    _get_call_or_404(db, call_id)
    transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call_id}, sort=[("created_at", -1)])
    if transcript is None:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' has no transcript yet.")
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
            detail=f"Call '{call_id}' has not been analyzed yet. POST to /process first.",
        )
    return _strip_id(analysis)


@router.get("/{call_id}/analyses", response_model=list[CallAnalysis])
def get_call_analyses(call_id: str, db: Database = Depends(get_db)) -> list[dict]:
    """Every attempt for the call, newest first -- including failed ones with raw output."""
    _get_call_or_404(db, call_id)
    return [_strip_id(a) for a in db[CALL_ANALYSES].find({"call_id": call_id}).sort("created_at", -1)]


@router.get("/{call_id}/audio", include_in_schema=True)
def get_call_audio(call_id: str, db: Database = Depends(get_db)):
    """Serve the recording for playback. The browser never sees a filesystem path."""
    if not settings.audio_playback_enabled:
        raise HTTPException(status_code=404, detail="Audio playback is disabled (AUDIO_PLAYBACK_ENABLED=false).")
    call = _get_call_or_404(db, call_id)

    if call.get("source_type") == SourceType.URL.value and call.get("audio_url"):
        return RedirectResponse(call["audio_url"], status_code=307)

    stored = call.get("audio_file_path")
    if not stored:
        raise HTTPException(status_code=404, detail="This call has no stored audio.")
    path = Path(stored).resolve()
    # Defence in depth: only serve files that live inside the upload directory.
    upload_root = settings.upload_path.resolve()
    if upload_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is not available.")
    return FileResponse(
        path,
        media_type=call.get("audio_mime") or "audio/mpeg",
        filename=call.get("audio_filename") or path.name,
        content_disposition_type="inline",
    )
