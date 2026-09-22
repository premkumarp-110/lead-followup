"""The call-processing pipeline orchestrator.

This is the ONLY module that knows the order of the steps:

    load call -> get audio -> TRANSCRIBING -> store transcript
              -> ANALYZING -> validate -> store analysis -> update lead -> COMPLETED

Each step is delegated to a single-purpose service. The orchestrator persists
the call's `status` at every transition, so a client can poll
GET /api/calls/{id}/status while POST /process is still running.

Running this in a background worker later means calling `process_call()` from
the worker instead of the request handler. Nothing else changes.
"""

import logging
from dataclasses import dataclass

from pymongo.database import Database

from app.database import CALL_ANALYSES, CALL_TRANSCRIPTS, CALLERS, CALLS, LEADS
from app.models.analysis import AnalysisStatus, CallAnalysisResult
from app.models.call import CallStatus
from app.services import followup_service, llm_service, transcription_service
from app.services.audio_service import AudioSource, AudioValidationError, new_id
from app.services.followup_service import utcnow
from app.services.llm_service import AnalysisAttempt, LLMAnalysisError
from app.services.transcription_service import TranscriptionError

logger = logging.getLogger(__name__)


class CallProcessingError(RuntimeError):
    """The pipeline stopped. The message is user-safe; `stage` says where."""

    def __init__(self, message: str, stage: CallStatus):
        super().__init__(message)
        self.stage = stage


@dataclass
class ProcessResult:
    call: dict
    transcript: dict | None
    analysis: dict | None
    lead: dict | None
    applied_to_lead: bool
    degraded: bool


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _set_status(db: Database, call_id: str, status: CallStatus, **extra) -> None:
    db[CALLS].update_one(
        {"call_id": call_id}, {"$set": {"status": status.value, **extra}}
    )


def _fail(db: Database, call_id: str, stage: CallStatus, message: str) -> CallProcessingError:
    _set_status(db, call_id, CallStatus.FAILED, error=message, failed_stage=stage.value)
    return CallProcessingError(message, stage)


def _audio_source(call: dict) -> AudioSource:
    return AudioSource(
        source_type=call.get("source_type") or "UPLOAD",
        file_path=call.get("audio_file_path"),
        url=call.get("audio_url"),
        mime_type=call.get("audio_mime") or "audio/mpeg",
        size_bytes=call.get("audio_bytes"),
        filename=call.get("audio_filename"),
        duration_seconds=call.get("duration_seconds"),
    )


def _analysis_doc(call: dict, attempt: AnalysisAttempt) -> dict:
    """Build the call_analyses document for one attempt (spec S14)."""
    now = utcnow()
    doc = {
        "analysis_id": new_id("AN"),
        "call_id": call["call_id"],
        "lead_id": call["lead_id"],
        "caller_id": call.get("caller_id"),
        "model": attempt.model,
        "status": (AnalysisStatus.COMPLETED if attempt.succeeded else AnalysisStatus.FAILED).value,
        "raw_response": attempt.raw_response,
        "error": attempt.error,
        "degraded": attempt.degraded,
        "created_at": now,
    }
    if attempt.result is not None:
        result: CallAnalysisResult = attempt.result
        doc.update(
            {
                "outcome": result.outcome.value,
                "follow_up_required": result.follow_up_required,
                "follow_up": result.follow_up.model_dump() if result.follow_up else None,
                "customer_intent": result.customer_intent.value,
                "summary": result.summary,
                "key_points": result.key_points,
                "confidence": result.confidence,
            }
        )
    else:
        doc.update(
            {
                "outcome": None,
                "follow_up_required": False,
                "follow_up": None,
                "customer_intent": None,
                "summary": None,
                "key_points": [],
                "confidence": None,
            }
        )
    return doc


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def process_call(db: Database, call: dict) -> ProcessResult:
    """Run the full pipeline for one call record. Synchronous."""
    call_id = call["call_id"]
    lead = db[LEADS].find_one({"lead_id": call["lead_id"]})
    if lead is None:
        raise _fail(db, call_id, CallStatus.PROCESSING, f"Lead '{call['lead_id']}' no longer exists.")
    caller = db[CALLERS].find_one({"caller_id": call.get("caller_id")}) if call.get("caller_id") else None

    _set_status(db, call_id, CallStatus.PROCESSING, error=None, failed_stage=None)

    # ---- 1. Transcribe -----------------------------------------------------
    _set_status(db, call_id, CallStatus.TRANSCRIBING)
    existing_transcript = (
        db[CALL_TRANSCRIPTS].find_one({"transcript_id": call["transcript_id"]})
        if call.get("transcript_id") else None
    )
    if existing_transcript is not None:
        # Re-processing an already-transcribed call: reuse the transcript so a
        # retry only repeats the step that failed.
        transcript_doc = existing_transcript
    else:
        try:
            result = transcription_service.transcribe_audio(_audio_source(call))
        except (TranscriptionError, AudioValidationError) as exc:
            raise _fail(db, call_id, CallStatus.TRANSCRIBING, str(exc)) from exc
        except Exception as exc:  # pragma: no cover - unexpected
            logger.exception("Unexpected transcription failure for %s", call_id)
            raise _fail(
                db, call_id, CallStatus.TRANSCRIBING,
                f"Transcription failed unexpectedly ({exc.__class__.__name__}).",
            ) from exc

        transcript_doc = {
            "transcript_id": new_id("TR"),
            "call_id": call_id,
            "lead_id": call["lead_id"],
            "caller_id": call.get("caller_id"),
            "transcript": result.text,
            "language": result.language,
            "duration_seconds": result.duration_seconds or call.get("duration_seconds"),
            "provider": result.provider,
            "created_at": utcnow(),
        }
        db[CALL_TRANSCRIPTS].insert_one(transcript_doc)
        updates = {"transcript_id": transcript_doc["transcript_id"]}
        if not call.get("duration_seconds") and result.duration_seconds:
            updates["duration_seconds"] = result.duration_seconds
        db[CALLS].update_one({"call_id": call_id}, {"$set": updates})
        call = {**call, **updates}

    # ---- 2. Analyze ----------------------------------------------------------
    _set_status(db, call_id, CallStatus.ANALYZING)
    lead_data = {k: lead.get(k) for k in ("lead_id", "name", "course", "lead_status", "assigned_bd")}
    caller_data = {k: caller.get(k) for k in ("caller_id", "name", "role")} if caller else None
    try:
        run = llm_service.analyze_conversation(
            transcript_doc["transcript"], lead_data, caller_data,
            call_ended_at=call.get("ended_at") or call.get("created_at"),
        )
    except LLMAnalysisError as exc:
        # Persist the failed attempt(s) is handled below via run; here nothing
        # succeeded and fallback is disabled, so record the failure and stop.
        failed_doc = _analysis_doc(
            call, AnalysisAttempt(model=llm_service.settings.call_analysis_model, result=None,
                                  raw_response=None, error=str(exc)),
        )
        db[CALL_ANALYSES].insert_one(failed_doc)
        raise _fail(db, call_id, CallStatus.ANALYZING, str(exc)) from exc

    # ---- 3. Store every attempt (failed Gemini attempt + fallback, or just one)
    winning_doc = None
    for attempt in run.attempts:
        doc = _analysis_doc(call, attempt)
        db[CALL_ANALYSES].insert_one(doc)
        if attempt is run.final:
            winning_doc = doc

    if winning_doc is None or run.final is None:  # defensive; analyze_conversation raises otherwise
        raise _fail(db, call_id, CallStatus.ANALYZING, "Analysis produced no valid result.")

    # ---- 4. Update the lead --------------------------------------------------
    applied = followup_service.apply_analysis_to_lead(
        db, call, run.final.result, winning_doc["analysis_id"]
    )

    # ---- 5. Complete ---------------------------------------------------------
    now = utcnow()
    _set_status(
        db, call_id, CallStatus.COMPLETED,
        analysis_id=winning_doc["analysis_id"], processed_at=now, error=None,
    )

    return ProcessResult(
        call=db[CALLS].find_one({"call_id": call_id}),
        transcript=transcript_doc,
        analysis=winning_doc,
        lead=db[LEADS].find_one({"lead_id": call["lead_id"]}),
        applied_to_lead=applied,
        degraded=run.final.degraded,
    )
