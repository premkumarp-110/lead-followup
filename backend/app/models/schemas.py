"""API request / response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.call import Call, CallOutcome, CallTranscript, Outcome
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
    updated_at: datetime


class LeadDetail(LeadListItem):
    """Everything the details modal shows, in one response."""

    created_at: datetime
    follow_up_history: list[FollowUpHistoryEntry] = Field(default_factory=list)
    latest_call: Call | None = None
    latest_transcript: CallTranscript | None = None
    latest_call_outcome: CallOutcome | None = None


class DashboardSummary(BaseModel):
    total_leads: int
    follow_ups_required: int
    due_today: int
    overdue: int
    converted: int
    dropped: int
    # Extra context, cheap to compute and useful on the cards.
    upcoming: int = 0
    unprocessed_calls: int = 0


class FilterOptions(BaseModel):
    """Populates the BD / course / outcome dropdowns in the filter bar."""

    bds: list[str]
    courses: list[str]
    outcomes: list[str]
    follow_up_statuses: list[str]


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


class ProcessCallResponse(BaseModel):
    """Result of the 'call completed' processing flow."""

    call_id: str
    lead_id: str
    outcome: Outcome
    reason: str
    follow_up: FollowUp
    lead_status: LeadStatus
    analyzer: str
    already_processed: bool = False
    applied_to_lead: bool = True
    message: str
