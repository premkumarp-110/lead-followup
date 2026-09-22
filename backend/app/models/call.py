"""Call, transcript and outcome domain models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.lead import FollowUp


class CallStatus(str, Enum):
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    IN_PROGRESS = "IN_PROGRESS"


class Outcome(str, Enum):
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    CONVERTED = "CONVERTED"
    DROPPED = "DROPPED"


class Call(BaseModel):
    call_id: str
    lead_id: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: int
    transcript_id: str | None = None
    status: CallStatus = CallStatus.COMPLETED


class CallTranscript(BaseModel):
    transcript_id: str
    call_id: str
    lead_id: str
    transcript: str
    created_at: datetime


class CallOutcome(BaseModel):
    call_id: str
    lead_id: str
    outcome: Outcome
    reason: str
    follow_up: FollowUp = Field(default_factory=FollowUp)
    processed_at: datetime

    # Provenance -- which analyzer produced this. Lets an LLM-analyzed outcome
    # be told apart from a keyword-analyzed one after the swap.
    analyzer: str | None = None
    confidence: float | None = None
