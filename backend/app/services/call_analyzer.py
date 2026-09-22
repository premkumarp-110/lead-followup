"""Call analysis service -- THE LLM SWAP POINT.

Today this is a deterministic keyword analyzer. Tomorrow an LLM implementation
takes its place. Everything downstream (routes, services, dashboard) depends
only on:

    AnalysisResult          -- the structured output contract
    CallAnalyzer            -- the one-method interface
    get_analyzer()          -- returns the configured implementation

To plug in an LLM later:

    1. Add app/services/llm_call_analyzer.py with:

           class LLMCallAnalyzer:
               name = "llm-gpt-x"
               def analyze_call(self, transcript, *, context=None) -> AnalysisResult:
                   ...call the model, parse its JSON into AnalysisResult...

    2. Register it in _ANALYZERS below.
    3. Set ANALYZER_BACKEND=llm in .env.

No route, service, schema or frontend file changes. The LLM's JSON output
(outcome / reason / follow_up_required / follow_up_datetime) maps 1:1 onto
AnalysisResult, which is why the contract is shaped this way.

NOTE: this MVP makes NO network or LLM calls of any kind.
"""

import re
from datetime import datetime, time, timedelta, timezone
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.config import settings
from app.models.call import Outcome

# --------------------------------------------------------------------------
# Output contract -- identical in shape to the future LLM's structured JSON.
# --------------------------------------------------------------------------


class AnalysisResult(BaseModel):
    outcome: Outcome
    reason: str
    follow_up_required: bool = False
    follow_up_datetime: datetime | None = None

    # Provenance / quality signals. An LLM implementation fills these too.
    analyzer: str = "keyword-v1"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    matched_phrase: str | None = None


@runtime_checkable
class CallAnalyzer(Protocol):
    """The single interface an analyzer must satisfy."""

    name: str

    def analyze_call(
        self, transcript: str, *, context: dict | None = None
    ) -> AnalysisResult: ...


# --------------------------------------------------------------------------
# Deterministic keyword rules (MVP)
# --------------------------------------------------------------------------

# Evaluated in this order. DROPPED wins over CONVERTED so a transcript like
# "I paid for another course, not interested in this one" resolves correctly.
DROPPED_PHRASES = [
    "not interested anymore",
    "no longer interested",
    "not interested",
    "don't want the course",
    "dont want the course",
    "do not want the course",
    "please don't call",
    "please dont call",
    "don't contact me",
    "dont contact me",
    "cancel my",
    "cancel the",
    "cancel",
]

CONVERTED_PHRASES = [
    "i have completed the payment",
    "payment completed",
    "completed the payment",
    "i want to enroll",
    "want to enroll",
    "i want to proceed",
    "want to proceed",
    "i have registered",
    "decided to join",
    "send me the enrollment details",
]

FOLLOW_UP_PHRASES = [
    "call me tomorrow",
    "call me later",
    "call me next week",
    "contact me tomorrow",
    "follow up tomorrow",
    "get back to me",
    "i will discuss and let you know",
    "discuss and let you know",
    "please call again",
    "call me back",
    "call me on",
    "call me at",
]

DEFAULT_FOLLOW_UP_TIME = time(hour=10, minute=0)
DEFAULT_FOLLOW_UP_DAYS = 1

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "2026-09-22"
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# "22 Sep", "22 September 2026", "22nd Sep 2026"
_TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)
# "11 AM", "2:30 pm", "14:00"
_TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(\d{1,2}):(\d{2})\b", re.IGNORECASE
)


def _extract_time(text: str) -> time | None:
    match = _TIME_RE.search(text)
    if not match:
        return None
    if match.group(3):  # 12-hour form with am/pm
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).lower()
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
    else:  # 24-hour form
        hour = int(match.group(4))
        minute = int(match.group(5))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def _extract_date(text: str, reference: datetime) -> datetime | None:
    """Explicit dates first, then simple relative keywords.

    Deliberately simple -- the MVP demonstrates the architecture, not a
    natural-language date parser. An LLM will handle the hard cases later.
    """
    iso = _ISO_DATE_RE.search(text)
    if iso:
        try:
            return reference.replace(
                year=int(iso.group(1)), month=int(iso.group(2)), day=int(iso.group(3))
            )
        except ValueError:
            pass

    textual = _TEXT_DATE_RE.search(text)
    if textual:
        day = int(textual.group(1))
        month = _MONTHS[textual.group(2).lower()[:3]]
        year = int(textual.group(3)) if textual.group(3) else reference.year
        try:
            return reference.replace(year=year, month=month, day=day)
        except ValueError:
            pass

    lowered = text.lower()
    if "day after tomorrow" in lowered:
        return reference + timedelta(days=2)
    if "tomorrow" in lowered:
        return reference + timedelta(days=1)
    if "next week" in lowered:
        return reference + timedelta(days=7)
    if "next month" in lowered:
        return reference + timedelta(days=30)
    if "this evening" in lowered or "later today" in lowered or "today" in lowered:
        return reference
    return None


def _resolve_follow_up_datetime(transcript: str, reference: datetime) -> datetime:
    """Combine any date and time found in the transcript into one UTC instant."""
    base = _extract_date(transcript, reference)
    clock = _extract_time(transcript)

    if base is None:
        base = reference + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)

    resolved = base.replace(
        hour=(clock or DEFAULT_FOLLOW_UP_TIME).hour,
        minute=(clock or DEFAULT_FOLLOW_UP_TIME).minute,
        second=0,
        microsecond=0,
    )

    # If the transcript gave only a time and it has already passed, roll forward
    # a day so the follow-up is never scheduled in the past.
    if resolved <= reference:
        resolved += timedelta(days=1)
    return resolved


def _find_phrase(text: str, phrases: list[str]) -> str | None:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


class KeywordCallAnalyzer:
    """Deterministic rule-based analyzer used for this MVP."""

    name = "keyword-v1"

    def analyze_call(
        self, transcript: str, *, context: dict | None = None
    ) -> AnalysisResult:
        text = (transcript or "").lower().strip()
        context = context or {}

        reference = context.get("call_ended_at") or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        if not text:
            return AnalysisResult(
                outcome=Outcome.FOLLOW_UP_REQUIRED,
                reason="Transcript was empty, so the lead is kept in the follow-up queue for a manual callback.",
                follow_up_required=True,
                follow_up_datetime=_resolve_follow_up_datetime("", reference),
                analyzer=self.name,
                confidence=0.1,
            )

        dropped = _find_phrase(text, DROPPED_PHRASES)
        if dropped:
            return AnalysisResult(
                outcome=Outcome.DROPPED,
                reason=f'Lead indicated they are no longer interested ("{dropped}"). No follow-up scheduled.',
                follow_up_required=False,
                analyzer=self.name,
                confidence=0.9,
                matched_phrase=dropped,
            )

        converted = _find_phrase(text, CONVERTED_PHRASES)
        if converted:
            return AnalysisResult(
                outcome=Outcome.CONVERTED,
                reason=f'Lead confirmed they are moving forward with the course ("{converted}"). No follow-up needed.',
                follow_up_required=False,
                analyzer=self.name,
                confidence=0.9,
                matched_phrase=converted,
            )

        follow_up = _find_phrase(text, FOLLOW_UP_PHRASES)
        if follow_up:
            when = _resolve_follow_up_datetime(text, reference)
            return AnalysisResult(
                outcome=Outcome.FOLLOW_UP_REQUIRED,
                reason=f'Lead asked to be contacted again ("{follow_up}"). Callback scheduled for {when:%d %b %Y %H:%M} UTC.',
                follow_up_required=True,
                follow_up_datetime=when,
                analyzer=self.name,
                confidence=0.85,
                matched_phrase=follow_up,
            )

        # Nothing matched: keep the lead in the queue rather than letting it
        # silently disappear from the BD's worklist. Low confidence flags it as
        # a case the future LLM analyzer should handle better.
        when = _resolve_follow_up_datetime(text, reference)
        return AnalysisResult(
            outcome=Outcome.FOLLOW_UP_REQUIRED,
            reason="No clear outcome detected in the transcript; kept for follow-up so the lead is not lost.",
            follow_up_required=True,
            follow_up_datetime=when,
            analyzer=self.name,
            confidence=0.3,
        )


# --------------------------------------------------------------------------
# Registry -- add "llm" here when the LLM analyzer lands.
# --------------------------------------------------------------------------

_ANALYZERS: dict[str, type] = {
    "keyword": KeywordCallAnalyzer,
    # "llm": LLMCallAnalyzer,
}

_instances: dict[str, CallAnalyzer] = {}


def get_analyzer(backend: str | None = None) -> CallAnalyzer:
    """Return the configured analyzer. Callers never name a concrete class."""
    key = (backend or settings.analyzer_backend or "keyword").lower()
    if key not in _ANALYZERS:
        raise ValueError(
            f"Unknown ANALYZER_BACKEND '{key}'. Available: {', '.join(sorted(_ANALYZERS))}"
        )
    if key not in _instances:
        _instances[key] = _ANALYZERS[key]()
    return _instances[key]


def analyze_call(transcript: str, *, context: dict | None = None) -> AnalysisResult:
    """Module-level convenience wrapper matching the spec's analyze_call(transcript)."""
    return get_analyzer().analyze_call(transcript, context=context)
