"""Lead domain models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# Alias: the FollowUp model has a field literally named "datetime" (per the spec),
# which would otherwise shadow the datetime class inside that class body.
DateTime = datetime


class LeadStatus(str, Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    INTERESTED = "INTERESTED"
    FOLLOW_UP = "FOLLOW_UP"
    CONVERTED = "CONVERTED"
    DROPPED = "DROPPED"


class FollowUpStatus(str, Enum):
    """Durable follow-up state. Time-sensitive buckets are derived, not stored."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"  # accepted as a filter value; derived, never written
    CANCELLED = "CANCELLED"


class FollowUpBucket(str, Enum):
    """Derived at read time from follow_up.datetime vs. now."""

    UPCOMING = "UPCOMING"
    DUE = "DUE"
    OVERDUE = "OVERDUE"
    # Follow-up is required but the lead never named a date/time. The analyzer
    # must not invent one, so these leads still need action -- they just cannot
    # be placed on the timeline.
    UNSCHEDULED = "UNSCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NONE = "NONE"


class FollowUpAction(str, Enum):
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    RESCHEDULE = "RESCHEDULE"


class AssignedBD(BaseModel):
    id: str
    name: str


class FollowUp(BaseModel):
    required: bool = False
    date: str | None = None       # YYYY-MM-DD
    time: str | None = None       # HH:MM (24h)
    datetime: DateTime | None = None
    status: FollowUpStatus | None = None
    reason: str | None = None     # why a follow-up is needed, from the analysis
    bucket: FollowUpBucket | None = None  # derived on read, never persisted


class FollowUpHistoryEntry(BaseModel):
    action: FollowUpAction
    reason: str
    from_status: FollowUpStatus | None = None
    to_status: FollowUpStatus | None = None
    previous_datetime: datetime | None = None
    new_datetime: datetime | None = None
    at: datetime


class Lead(BaseModel):
    lead_id: str
    name: str
    phone: str
    email: str
    course: str
    assigned_bd: AssignedBD
    lead_status: LeadStatus = LeadStatus.NEW
    follow_up: FollowUp = Field(default_factory=FollowUp)
    follow_up_history: list[FollowUpHistoryEntry] = Field(default_factory=list)

    # Denormalized for table rendering without extra queries.
    last_call_at: datetime | None = None
    latest_call_id: str | None = None
    latest_outcome: str | None = None
    latest_analysis_id: str | None = None

    created_at: datetime
    updated_at: datetime
