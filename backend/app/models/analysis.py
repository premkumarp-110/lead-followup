"""Analysis domain models -- the validation gate on LLM output.

`CallAnalysisResult` is the ONLY thing that turns raw model output into trusted
data. Anything that fails validation here never reaches the lead (spec S13).

Strictness is deliberately uneven, and the split matters:

  * `outcome` is strict. It drives lead_status, so an unrecognised value must
    fail loudly rather than be guessed at.
  * `customer_intent` is lenient -- an unknown label normalises to UNCLEAR.
    It is descriptive only, and discarding an otherwise-valid analysis because
    the model wrote "CURIOUS" instead of "INTERESTED" would lose real signal.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.call import Outcome

# Alias: AnalysisFollowUp has a field literally named "datetime" (per the spec),
# which would otherwise shadow the datetime class inside that class body.
DateTime = datetime

# Exact wording required by spec S12 when a follow-up is needed but the lead
# never gave a date. Kept as a constant so the LLM prompt and the server-side
# backfill can never drift apart.
NO_DATE_REASON = "Lead requested follow-up but did not specify a date/time."


class CustomerIntent(str, Enum):
    INTERESTED = "INTERESTED"
    READY_TO_ENROLL = "READY_TO_ENROLL"
    NOT_INTERESTED = "NOT_INTERESTED"
    NEEDS_TIME = "NEEDS_TIME"
    PRICE_SENSITIVE = "PRICE_SENSITIVE"
    UNCLEAR = "UNCLEAR"


class AnalysisStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _blank_to_none(value):
    """LLMs write 'null', 'N/A' and '' where they mean absent."""
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "n/a", "-"}:
        return None
    return value


class AnalysisFollowUp(BaseModel):
    """The follow-up block as the model returns it.

    `datetime` is allowed to be None even when a follow-up IS required -- that
    is the spec S12 "do not hallucinate dates" case, not an error.
    """

    date: str | None = None       # YYYY-MM-DD
    time: str | None = None       # HH:MM
    datetime: DateTime | None = None
    reason: str | None = None

    @field_validator("date", "time", "reason", "datetime", mode="before")
    @classmethod
    def _normalise_blanks(cls, value):
        return _blank_to_none(value)

    @model_validator(mode="after")
    def _derive_parts(self) -> "AnalysisFollowUp":
        """Keep date/time/datetime mutually consistent.

        The model is asked for all three, but often returns only some. Rather
        than reject, derive the missing ones -- these are different renderings
        of one fact, not independent claims.
        """
        if self.datetime is not None:
            when = self.datetime
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
                object.__setattr__(self, "datetime", when)
            # Render date/time in the offset the model returned (the lead's own
            # timezone), so "call me at 11 AM" is stored as time "11:00", not
            # its UTC equivalent. `datetime` stays the authoritative instant.
            object.__setattr__(self, "date", when.strftime("%Y-%m-%d"))
            object.__setattr__(self, "time", when.strftime("%H:%M"))
        elif self.date:
            # date without datetime: combine with time if present, else leave
            # datetime None so the lead lands in the UNSCHEDULED bucket.
            try:
                clock = self.time or "10:00"
                combined = datetime.strptime(f"{self.date} {clock}", "%Y-%m-%d %H:%M")
                object.__setattr__(self, "datetime", combined.replace(tzinfo=timezone.utc))
                object.__setattr__(self, "time", clock)
            except ValueError:
                object.__setattr__(self, "datetime", None)
        return self


class CallAnalysisResult(BaseModel):
    """Validated structured output from the conversation analysis.

    This is the contract an LLM implementation must satisfy. Swapping models or
    providers means producing this shape -- nothing downstream changes.
    """

    outcome: Outcome
    follow_up_required: bool = False
    follow_up: AnalysisFollowUp | None = None
    customer_intent: CustomerIntent = CustomerIntent.UNCLEAR
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("customer_intent", mode="before")
    @classmethod
    def _lenient_intent(cls, value):
        """Normalise an unrecognised intent to UNCLEAR instead of failing."""
        value = _blank_to_none(value)
        if value is None:
            return CustomerIntent.UNCLEAR
        if isinstance(value, CustomerIntent):
            return value
        try:
            return CustomerIntent(str(value).strip().upper().replace(" ", "_"))
        except ValueError:
            return CustomerIntent.UNCLEAR

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value):
        """Accept 94 or "0.94" where 0.94 was meant."""
        value = _blank_to_none(value)
        if value is None:
            return 0.5
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.5
        if number > 1.0:
            number = number / 100.0
        return min(max(number, 0.0), 1.0)

    @field_validator("key_points", mode="before")
    @classmethod
    def _coerce_key_points(cls, value):
        value = _blank_to_none(value)
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip(" -*\t") for line in value.splitlines() if line.strip()]
        return [str(item) for item in value if str(item).strip()]

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, value):
        return (_blank_to_none(value) or "") if not isinstance(value, str) else value.strip()

    @model_validator(mode="after")
    def _enforce_outcome_consistency(self) -> "CallAnalysisResult":
        """Make outcome and follow-up agree (spec S12 / S15).

        A CONVERTED lead with a follow-up date, or a FOLLOW_UP_REQUIRED lead
        with follow_up_required=false, is internally contradictory. The outcome
        is authoritative because it is what updates lead_status.
        """
        if self.outcome in (Outcome.CONVERTED, Outcome.DROPPED):
            object.__setattr__(self, "follow_up_required", False)
            object.__setattr__(self, "follow_up", None)
            return self

        # FOLLOW_UP_REQUIRED
        object.__setattr__(self, "follow_up_required", True)
        follow_up = self.follow_up or AnalysisFollowUp()
        if not follow_up.reason:
            object.__setattr__(
                follow_up,
                "reason",
                NO_DATE_REASON if follow_up.datetime is None else "Lead requested a follow-up.",
            )
        object.__setattr__(self, "follow_up", follow_up)
        return self


class CallAnalysis(BaseModel):
    """A stored analysis document (the `call_analyses` collection)."""

    analysis_id: str
    call_id: str
    lead_id: str
    caller_id: str | None = None
    model: str
    status: AnalysisStatus = AnalysisStatus.COMPLETED

    outcome: Outcome | None = None
    follow_up_required: bool = False
    follow_up: AnalysisFollowUp | None = None
    customer_intent: CustomerIntent | None = None
    summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    confidence: float | None = None

    # Kept for every attempt, successful or not, so a bad response can be
    # debugged after the fact (spec S13).
    raw_response: str | None = None
    error: str | None = None
    # True when the keyword fallback produced this, not the LLM.
    degraded: bool = False
    created_at: datetime
