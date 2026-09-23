"""Lead domain models.

The field set mirrors the Lead Call API's lead schema 1:1 -- every field the CRM
returns is stored, because filtering is driven from that data alone. Only the
casing is localised (snake_case, matching the rest of the codebase).

Note what the CRM does *not* have: no name, no phone, no email. A lead is
identified by `lead_id` (Superleap) and `external_id` (the LeadSquared GUID).
Anything that wants to address a person has to go through the CRM.

`follow_up`, `follow_up_history`, `lead_status` and the `latest_*` denormalized
fields are ours, derived from call analysis -- the CRM supplies no follow-up
datetime anywhere.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.models.analysis import CallSentiment

# Alias: the FollowUp model has a field literally named "datetime" (per the spec),
# which would otherwise shadow the datetime class inside that class body.
DateTime = datetime


class LeadStatus(str, Enum):
    """Our coarse status. Derived from the CRM's free-text `stage` -- see
    services/stage_mapping.py. The raw stage is always stored alongside it."""

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
    """Actions a BD can take on a follow-up.

    **RESCHEDULE is the only one still available.** COMPLETE and CANCEL are
    retired: a BD must not be able to close a follow-up by asserting it is
    done, because that clears the queue without anything having happened. The
    outcome of a lead is whatever the *call analysis* concluded, so a follow-up
    now leaves the worklist exactly one way -- a newer call for that lead is
    analysed and yields CONVERTED/DROPPED (clearing `follow_up`) or a fresh
    FOLLOW_UP_REQUIRED with a new datetime.

    Both values stay in the enum because `follow_up_history` entries written
    before they were retired still carry `action: "COMPLETE"` / `"CANCEL"`, and
    removing them here would make those leads fail to serialise -- a 500 on the
    detail endpoint for exactly the leads with the most history.
    """

    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    RESCHEDULE = "RESCHEDULE"


# Accepted when reading stored history, refused when performing a new action.
RETIRED_ACTIONS = frozenset({FollowUpAction.COMPLETE, FollowUpAction.CANCEL})


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
    """A CRM lead plus our derived follow-up state.

    Everything the CRM sends is optional except `lead_id`: measured against the
    live API, `city`/`state`/`language`/`segmentation`/`win_probability` and
    most source fields are null on the majority of leads, and
    `conversion_date` / `sales_qualified` / `sales_owner_*` are null on *every*
    lead. Requiring any of them rejects real records.
    """

    # ---- Identity ----------------------------------------------------------
    lead_id: str
    external_id: str | None = None          # the LeadSquared GUID

    # ---- Funnel ------------------------------------------------------------
    stage: str | None = None                # free text, ~30 values
    previous_stage: str | None = None
    product: str | None = None              # free text, ~40 values
    language: str | None = None
    win_probability: float | None = None
    segmentation: str | None = None         # e.g. M1
    sales_qualified: bool | None = None

    # ---- Location ----------------------------------------------------------
    city: str | None = None
    state: str | None = None
    country: str | None = None

    # ---- Source / marketing attribution ------------------------------------
    lead_source: str | None = None
    source_campaign: str | None = None
    source_medium: str | None = None
    source_content: str | None = None
    last_source: str | None = None
    last_medium: str | None = None
    nurturing: str | None = None

    # ---- Call activity (kept on the lead, so no need to page the call log) --
    last_disposition_status: str | None = None
    last_sub_disposition_status: str | None = None
    last_call_attempted_at: datetime | None = None
    calls_connected: int = 0
    calls_missed: int = 0
    total_attempts: int = 0
    total_talktime_sec: int = 0

    # ---- Dates -------------------------------------------------------------
    first_contact_date: datetime | None = None
    conversion_date: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # ---- Ownership ---------------------------------------------------------
    # The owning BDA. Always populated in the CRM. `sales_owner_*` is in the
    # schema but null on every lead measured -- kept so nothing is discarded.
    owner_id: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None
    sales_owner_id: str | None = None
    sales_owner_name: str | None = None

    # ---- Ours, not the CRM's -----------------------------------------------
    lead_status: LeadStatus = LeadStatus.NEW
    follow_up: FollowUp = Field(default_factory=FollowUp)
    follow_up_history: list[FollowUpHistoryEntry] = Field(default_factory=list)

    # Denormalized for table rendering without extra queries.
    last_call_at: datetime | None = None
    latest_call_id: str | None = None
    latest_outcome: str | None = None
    # A copy of the winning analysis's sentiment, so the worklist can order by
    # it and the list can filter on it without touching call_analyses.
    latest_sentiment: CallSentiment | None = None
    latest_analysis_id: str | None = None
