"""Call and transcript domain models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    CONVERTED = "CONVERTED"
    DROPPED = "DROPPED"


class CallStatus(str, Enum):
    """Processing lifecycle of a call. Written by the orchestrator step by step
    so a client can poll it while /process is still running."""

    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SourceType(str, Enum):
    UPLOAD = "UPLOAD"
    URL = "URL"
    TEXT = "TEXT"


class Call(BaseModel):
    call_id: str
    lead_id: str
    caller_id: str | None = None

    source_type: SourceType = SourceType.UPLOAD
    audio_url: str | None = None
    # Path on the backend's disk. Never sent to the browser -- the API serves
    # audio by call_id through /api/calls/{call_id}/audio instead.
    audio_file_path: str | None = Field(default=None, exclude=True)
    audio_mime: str | None = None
    audio_bytes: int | None = None
    audio_filename: str | None = None
    duration_seconds: int | None = None

    status: CallStatus = CallStatus.UPLOADED
    error: str | None = None
    failed_stage: CallStatus | None = None  # which step failed, when status is FAILED

    transcript_id: str | None = None
    analysis_id: str | None = None

    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    processed_at: datetime | None = None


class CallTranscript(BaseModel):
    transcript_id: str
    call_id: str
    lead_id: str
    caller_id: str | None = None
    transcript: str
    language: str | None = None
    duration_seconds: int | None = None
    provider: str | None = None  # which transcription provider produced it
    created_at: datetime
