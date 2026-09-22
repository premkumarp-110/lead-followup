"""API request / response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.analysis import CallAnalysis
from app.models.call import Call, CallStatus, CallTranscript, Outcome
from app.models.caller import Caller
from app.models.lead import (
    AssignedBD,
    FollowUp,
    FollowUpAction,
    FollowUpHistoryEntry,
    LeadStatus,
)


class LeadListItem(BaseModel):
    """One row in either dashboard table."""

    lead_id: str
    name: str
    phone: str
    email: str
    course: str
    assigned_bd: AssignedBD
    lead_status: LeadStatus
    follow_up: FollowUp
    last_call_at: datetime | None = None
    latest_call_id: str | None = None
    latest_outcome: Outcome | None = None
    latest_analysis_id: str | None = None
    updated_at: datetime


class LeadDetail(LeadListItem):
    """Everything the details modal shows, in one response."""

    created_at: datetime
    follow_up_history: list[FollowUpHistoryEntry] = Field(default_factory=list)
    latest_call: Call | None = None
    latest_caller: Caller | None = None
    latest_transcript: CallTranscript | None = None
    latest_analysis: CallAnalysis | None = None


class DashboardSummary(BaseModel):
    total_leads: int
    follow_ups_required: int
    due_today: int
    overdue: int
    converted: int
    dropped: int
    # Extra context, cheap to compute and useful on the cards.
    upcoming: int = 0
    unscheduled: int = 0
    unprocessed_calls: int = 0


class FilterOptions(BaseModel):
    """Populates the BD / course / outcome dropdowns in the filter bar."""

    bds: list[str]
    courses: list[str]
    outcomes: list[str]
    follow_up_statuses: list[str]


class UIConfig(BaseModel):
    """Non-secret settings the frontend needs. Nothing here is a credential."""

    call_analyzer_enabled: bool
    audio_storage_mode: str
    audio_playback_enabled: bool
    max_audio_mb: int
    allowed_audio_types: list[str]
    transcription_provider: str
    analysis_model: str
    vertex_configured: bool
    analysis_fallback_enabled: bool


class FollowUpActionRequest(BaseModel):
    action: FollowUpAction
    reason: str
    new_datetime: datetime | None = None

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("A reason is required for every follow-up action.")
        return cleaned

    @model_validator(mode="after")
    def _reschedule_needs_datetime(self) -> "FollowUpActionRequest":
        if self.action is FollowUpAction.RESCHEDULE and self.new_datetime is None:
            raise ValueError("new_datetime is required when rescheduling a follow-up.")
        return self


class CallFromUrlRequest(BaseModel):
    audio_url: str
    lead_id: str
    caller_id: str


class CallFromTextRequest(BaseModel):
    transcript: str
    lead_id: str
    caller_id: str

    @field_validator("transcript")
    @classmethod
    def _transcript_required(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("A transcript is required.")
        return cleaned


class ValidateUrlRequest(BaseModel):
    audio_url: str


class ValidateUrlResponse(BaseModel):
    url: str
    reachable: bool
    content_type: str | None = None
    size_bytes: int | None = None
    filename: str | None = None


class CallCreatedResponse(BaseModel):
    """Returned by /upload and /from-url. The next step is POST /process."""

    call_id: str
    lead_id: str
    caller_id: str
    source_type: str
    status: CallStatus
    duration_seconds: int | None = None
    audio_bytes: int | None = None
    audio_filename: str | None = None
    message: str


class CallStatusResponse(BaseModel):
    call_id: str
    status: CallStatus
    error: str | None = None
    failed_stage: CallStatus | None = None
    transcript_id: str | None = None
    analysis_id: str | None = None
    processed_at: datetime | None = None


class ProcessCallResponse(BaseModel):
    """Result of the 'call completed' processing flow."""

    call_id: str
    lead_id: str
    caller_id: str | None = None
    status: CallStatus
    outcome: Outcome | None = None
    analysis: CallAnalysis | None = None
    transcript: CallTranscript | None = None
    follow_up: FollowUp | None = None
    lead_status: LeadStatus | None = None
    applied_to_lead: bool = False
    degraded: bool = False
    message: str
