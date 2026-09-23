"""Call and transcript domain models.

Mirrors the Lead Call API's call schema, with one deliberate rename: the CRM's
`status` is a *telephony* status (connected / not_connected / missed_call) while
ours is a *pipeline* status (UPLOADED -> ... -> COMPLETED). They are unrelated
axes and both are needed, so the CRM's lands on `telephony_status`.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    CONVERTED = "CONVERTED"
    DROPPED = "DROPPED"


class CallStatus(str, Enum):
    """Processing lifecycle of a call. Written by the orchestrator step by step
    so a client can poll it while analysis is still running."""

    PENDING = "PENDING"        # ingested, never analyzed
    PROCESSING = "PROCESSING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # Nothing to analyze and nothing broken: not connected, zero duration, or
    # no transcript and no recording. ~30% of real calls. An honest state, not
    # a gap -- the UI offers no Analyze button for these.
    NOT_ANALYZABLE = "NOT_ANALYZABLE"


class CallDirection(str, Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


# Telephony values observed live. Stored as free text rather than an enum so an
# unseen value from the CRM cannot reject the record.
TELEPHONY_CONNECTED = "connected"
TELEPHONY_NOT_CONNECTED = "not_connected"
TELEPHONY_MISSED = "missed_call"


class Call(BaseModel):
    # ---- Identity ----------------------------------------------------------
    call_id: str
    lead_id: str
    caller_id: str | None = None
    # A real CRM callId, present only on calls that have a recording. Used
    # solely to resolve audio through GET /recording/{callId}; everything else
    # about a seeded call is local. Never sent to the browser -- audio is
    # served by our own call_id.
    crm_call_id: str | None = Field(default=None, exclude=True)

    # ---- CRM call fields ---------------------------------------------------
    call_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    direction: str | None = None
    duration_sec: int | None = None
    telephony_status: str | None = None     # connected / not_connected / missed_call
    final_status: str | None = None         # completed / customer_canceled / agent_unanswered
    pitch_score: float | None = None
    violations: int = 0
    # The CRM's own analysis. Documented as a string; in practice a JSON blob.
    # Both forms are kept -- see `analysis_summary_parsed`.
    analysis_summary: str | None = None
    analysis_summary_parsed: dict | None = None
    has_recording: bool = False
    has_transcript: bool = False
    recording_path: str | None = None
    transcript_path: str | None = None
    owner_id: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None

    # ---- Ours: the processing pipeline -------------------------------------
    status: CallStatus = CallStatus.PENDING
    error: str | None = None
    failed_stage: CallStatus | None = None  # which step failed, when status is FAILED

    transcript_id: str | None = None
    analysis_id: str | None = None

    created_at: datetime
    processed_at: datetime | None = None

    @property
    def is_connected(self) -> bool:
        return (self.telephony_status or "").lower() == TELEPHONY_CONNECTED

    @property
    def is_analyzable(self) -> bool:
        """Whether analysis could produce anything.

        Measured on live data: ~30% of calls fail this -- they are the
        not-connected / zero-duration rows, and they carry neither a transcript
        nor a recording. Never spend an LLM call on them.
        """
        if not self.is_connected:
            return False
        if not (self.duration_sec or 0):
            return False
        return bool(self.has_transcript or self.has_recording)


class CallTranscript(BaseModel):
    transcript_id: str
    call_id: str
    lead_id: str
    caller_id: str | None = None
    transcript: str
    language: str | None = None
    duration_seconds: int | None = None
    provider: str | None = None  # which transcription provider produced it
    # The CRM's own `source` field, stored verbatim: transcript,
    # transcript_content, transcript_content_vt or transcript_url. The formats
    # differ slightly, so keeping it makes format-specific parsing possible later.
    source: str | None = None
    created_at: datetime
