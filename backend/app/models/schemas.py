"""API request / response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.analysis import CallAnalysis, CallSentiment
from app.models.call import Call, CallStatus, CallTranscript, Outcome
from app.models.caller import Caller
from app.models.lead import (
    RETIRED_ACTIONS,
    FollowUp,
    FollowUpAction,
    FollowUpHistoryEntry,
    LeadStatus,
)


class LeadListItem(BaseModel):
    """One row in either lead table.

    Identity is `lead_id` + `external_id`: the CRM exposes no name, phone or
    email, so the table is keyed on ids with product/stage/owner as context.
    Everything else here is a CRM field the UI either shows or filters on.
    """

    lead_id: str
    external_id: str | None = None

    stage: str | None = None
    previous_stage: str | None = None
    product: str | None = None
    language: str | None = None
    win_probability: float | None = None
    segmentation: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    lead_source: str | None = None

    last_disposition_status: str | None = None
    last_sub_disposition_status: str | None = None
    last_call_attempted_at: datetime | None = None
    calls_connected: int = 0
    calls_missed: int = 0
    total_attempts: int = 0
    total_talktime_sec: int = 0
    first_contact_date: datetime | None = None

    owner_id: str | None = None
    owner_name: str | None = None
    owner_email: str | None = None

    # Ours, derived from call analysis.
    lead_status: LeadStatus
    follow_up: FollowUp
    last_call_at: datetime | None = None
    latest_call_id: str | None = None
    latest_outcome: Outcome | None = None
    latest_sentiment: CallSentiment | None = None
    latest_analysis_id: str | None = None
    updated_at: datetime


class LeadDetail(LeadListItem):
    """Everything the details modal shows, in one response."""

    created_at: datetime
    source_campaign: str | None = None
    source_medium: str | None = None
    source_content: str | None = None
    last_source: str | None = None
    last_medium: str | None = None
    nurturing: str | None = None
    sales_qualified: bool | None = None
    conversion_date: datetime | None = None
    sales_owner_id: str | None = None
    sales_owner_name: str | None = None

    follow_up_history: list[FollowUpHistoryEntry] = Field(default_factory=list)
    calls: list[Call] = Field(default_factory=list)
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
    # Calls that have something to analyze but no analysis yet.
    unanalyzed_calls: int = 0


class BDOption(BaseModel):
    """One entry in the searchable BD dropdown.

    Carries the email because names collide -- there are 100+ distinct owners
    and duplicate first names are common, so the email is what disambiguates
    them and what the `bd` filter actually matches on.
    """

    caller_id: str
    name: str
    email: str | None = None
    active: bool = True
    lead_count: int = 0


class FilterOptions(BaseModel):
    """Populates every dropdown in the filter bar, from real stored values."""

    bds: list[BDOption] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    lead_sources: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    segmentations: list[str] = Field(default_factory=list)
    dispositions: list[str] = Field(default_factory=list)
    sentiments: list[str] = Field(default_factory=list)
    sub_dispositions: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    follow_up_statuses: list[str] = Field(default_factory=list)


class UIConfig(BaseModel):
    """Non-secret settings the frontend needs. Nothing here is a credential."""

    audio_playback_enabled: bool
    # Whether a CRM key is configured at all. Lets the player explain why a
    # recording will not load instead of failing silently. Never the key itself.
    recording_source_configured: bool
    transcription_provider: str
    analysis_model: str
    vertex_configured: bool
    analysis_fallback_enabled: bool
    # Reminder digests. `email_configured` lets the UI disable the send button
    # *with a reason* instead of rendering a control that always fails.
    followup_alerts_enabled: bool
    followup_alerts_email_configured: bool
    followup_alert_hour: int
    followup_alert_minute: int


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

    @model_validator(mode="after")
    def _reject_retired_actions(self) -> "FollowUpActionRequest":
        """COMPLETE and CANCEL are valid in stored history, never new actions.

        Refused here rather than removed from the enum so leads acted on before
        they were retired still deserialise. A follow-up is closed by analysing
        a newer call, not by a BD declaring it done.
        """
        if self.action in RETIRED_ACTIONS:
            raise ValueError(
                f"'{self.action.value}' is no longer an available follow-up action. "
                "Reschedule the follow-up, or analyse a newer call for this lead to "
                "let its outcome close it."
            )
        return self


class AnalyzeCallResponse(BaseModel):
    """Result of analysing one existing call."""

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


# --------------------------------------------------------------------------
# Follow-up reminders
# --------------------------------------------------------------------------


class AlertLeadItem(BaseModel):
    """One lead as it appears in a reminder group."""

    lead_id: str
    product: str | None = None
    stage: str | None = None
    owner_name: str | None = None
    follow_up: FollowUp | None = None
    latest_outcome: Outcome | None = None


class AlertGroup(BaseModel):
    """What one BD currently owes, split by urgency.

    `due_today` holds follow-ups inside the 2-hour DUE window plus anything
    else falling on today's IST date -- an imminent follow-up is never dropped
    just because the clock is about to cross midnight.
    """

    bd_id: str
    bd_name: str = ""
    bd_email: str | None = None
    overdue: list[AlertLeadItem] = Field(default_factory=list)
    due_today: list[AlertLeadItem] = Field(default_factory=list)
    unscheduled_count: int = 0
    last_alert_at: datetime | None = None
    last_alert_status: str | None = None


class AlertPendingResponse(BaseModel):
    """Everything the reminder panel needs in one round trip."""

    groups: list[AlertGroup] = Field(default_factory=list)
    total_overdue: int = 0
    total_due_today: int = 0
    total_unscheduled: int = 0
    unassigned_count: int = 0
    orphaned_count: int = 0
    generated_at: datetime


class AlertSendResult(BaseModel):
    """Outcome of one BD's digest within a send run."""

    bd_id: str
    bd_name: str = ""
    bd_email: str | None = None
    status: str
    sent_for_date: str
    trigger: str
    overdue_count: int = 0
    due_today_count: int = 0
    unscheduled_count: int = 0
    lead_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    sent_at: datetime | None = None


class AlertSendResponse(BaseModel):
    """Summary of a send run. Sending nothing is success, not an error."""

    trigger: str
    sent_for_date: str
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[AlertSendResult] = Field(default_factory=list)
    message: str = ""


class AlertHistoryItem(AlertSendResult):
    """A stored delivery record."""

    alert_id: str


# --------------------------------------------------------------------------
# Insights + operations
# --------------------------------------------------------------------------


class AgeingBucket(BaseModel):
    """One half-open ageing band. Labels are server-side so every client agrees."""

    key: str
    label: str
    count: int = 0


class OutcomeMix(BaseModel):
    """Counts plus the denominator.

    No percentages are computed here: with a zero denominator there is no
    honest number to send, and the UI can render a dash instead of "0%".
    """

    converted: int = 0
    dropped: int = 0
    follow_up_required: int = 0
    not_analyzed: int = 0
    total: int = 0


class SentimentMix(BaseModel):
    """How the analysed leads sounded. `not_assessed` covers both leads with no
    analysis yet and ones the fallback could not judge -- they are the same
    thing to a reader: no tone was measured."""

    positive: int = 0
    neutral: int = 0
    negative: int = 0
    mixed: int = 0
    not_assessed: int = 0
    declining: int = 0
    total: int = 0


class InsightsOverview(BaseModel):
    total_leads: int = 0
    pending_total: int = 0
    # These four are disjoint and sum to pending_total.
    overdue_total: int = 0
    due_total: int = 0
    upcoming_total: int = 0
    unscheduled_total: int = 0
    # Deliberately OVERLAPPING: everything worth acting on today, which
    # includes overdue items still dated today. Never add it to the four above.
    due_today_total: int = 0
    unassigned_total: int = 0
    ageing: list[AgeingBucket] = Field(default_factory=list)
    outcome_mix: OutcomeMix = Field(default_factory=OutcomeMix)
    sentiment_mix: SentimentMix = Field(default_factory=SentimentMix)
    # Analyses produced by the keyword fallback rather than the LLM. Worth
    # surfacing: a degraded result is still written to the lead.
    degraded_analyses: int = 0
    # Calls with a transcript or a recording that have not been analysed yet.
    # Excludes NOT_ANALYZABLE -- that is a finished state, not a backlog item.
    unanalyzed_calls: int = 0
    generated_at: datetime


class BDInsightRow(BaseModel):
    """One BD's load. `rescheduled_all_time` is all-time, not a period."""

    bd_id: str
    bd_name: str
    bd_email: str | None = None
    active: bool = True
    orphaned: bool = False
    assigned: int = 0
    # Disjoint follow-up buckets.
    overdue: int = 0
    due: int = 0
    upcoming: int = 0
    unscheduled: int = 0
    # Overlaps the above -- what this BD should action today.
    due_today: int = 0
    rescheduled_all_time: int = 0
    converted: int = 0
    dropped: int = 0


class InsightsByBD(BaseModel):
    rows: list[BDInsightRow] = Field(default_factory=list)
    total_leads: int = 0
    generated_at: datetime
