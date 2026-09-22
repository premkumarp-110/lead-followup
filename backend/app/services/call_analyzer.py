"""Deterministic keyword analyzer -- the FALLBACK when Gemini is unavailable.

The primary analysis path is Vertex AI Gemini in `llm_service.py`. This module
exists so the pipeline can still complete (clearly flagged as `degraded`) when
Vertex is unreachable, misconfigured, or returns output that fails validation.

It produces the same `CallAnalysisResult` contract as the LLM, so nothing
downstream can tell the two apart except by the `model` / `degraded` fields on
the stored analysis. It makes no network calls.

Rule order is DROPPED -> CONVERTED -> FOLLOW_UP -> fallback, and that order is
load-bearing: "I paid for another course, not interested in this one" must
resolve to DROPPED.

Per spec S12 this analyzer never invents a follow-up date. If the transcript
contains no explicit or relative date, the follow-up is required but
unscheduled (datetime=None) and the lead lands in the UNSCHEDULED bucket.
"""

import re
from datetime import datetime, time, timedelta, timezone

from app.models.analysis import NO_DATE_REASON, AnalysisFollowUp, CallAnalysisResult, CustomerIntent
from app.models.call import Outcome

KEYWORD_MODEL_NAME = "keyword-fallback-v1"

# --------------------------------------------------------------------------
# Phrase tables
# --------------------------------------------------------------------------

DROPPED_PHRASES = [
    "not interested anymore",
    "no longer interested",
    "not interested",
    "decided not to join",
    "don't want the course",
    "dont want the course",
    "do not want the course",
    "don't want to join",
    "dont want to join",
    "please don't call",
    "please dont call",
    "don't contact me",
    "dont contact me",
    "do not contact me",
    "cancel my",
    "cancel the",
    "cancel",
]

CONVERTED_PHRASES = [
    "i have completed the payment",
    "payment completed",
    "completed the payment",
    "payment is done",
    "paid the fees",
    "i have paid",
    "i want to enroll",
    "want to enroll",
    "i want to proceed",
    "want to proceed",
    "i have registered",
    "decided to join",
    "send me the enrollment details",
    "confirm my enrollment",
]

FOLLOW_UP_PHRASES = [
    "call me tomorrow",
    "call me later",
    "call me next week",
    "call me this evening",
    "call me in the evening",
    "contact me tomorrow",
    "follow up tomorrow",
    "get back to you",
    "get back to me",
    "i will discuss and let you know",
    "discuss and let you know",
    "discuss it with my family",
    "discuss with my parents",
    "discuss the fees with my parents",
    "please call again",
    "call me back",
    "call me on",
    "call me at",
    "haven't made the payment yet",
    "have not made the payment yet",
]

# Phrases that signal payment is pending but intent is positive.
PAYMENT_PENDING_PHRASES = [
    "haven't made the payment",
    "have not made the payment",
    "will make the payment",
    "will pay",
    "not yet paid",
]

DEFAULT_FOLLOW_UP_TIME = time(hour=10, minute=0)

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


# --------------------------------------------------------------------------
# Date / time extraction
# --------------------------------------------------------------------------


def _extract_time(text: str) -> time | None:
    match = _TIME_RE.search(text)
    if match:
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
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour=hour, minute=minute)

    lowered = text.lower()
    if "evening" in lowered:
        return time(hour=18, minute=0)
    if "afternoon" in lowered:
        return time(hour=14, minute=0)
    if "morning" in lowered:
        return time(hour=10, minute=0)
    return None


def _extract_date(text: str, reference: datetime) -> datetime | None:
    """Explicit dates first, then simple relative keywords. Returns None when
    the transcript gives no usable date -- it is never guessed."""
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


def _resolve_follow_up_datetime(transcript: str, reference: datetime) -> datetime | None:
    """Combine any date and time found into one instant, or None if no date."""
    base = _extract_date(transcript, reference)
    if base is None:
        return None
    clock = _extract_time(transcript) or DEFAULT_FOLLOW_UP_TIME
    resolved = base.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    # A bare time that has already passed today means tomorrow.
    if resolved <= reference:
        resolved += timedelta(days=1)
    return resolved


def _find_phrase(text: str, phrases: list[str]) -> str | None:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


def _sentence_containing(text: str, phrase: str) -> str:
    """The sentence of the transcript that matched, for key_points."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if phrase in sentence.lower():
            return sentence.strip().rstrip(".")
    return phrase


# --------------------------------------------------------------------------
# Analyzer
# --------------------------------------------------------------------------


class KeywordCallAnalyzer:
    """Rule-based analyzer producing the shared CallAnalysisResult contract."""

    name = KEYWORD_MODEL_NAME

    def analyze(self, transcript: str, *, call_ended_at: datetime | None = None) -> CallAnalysisResult:
        original = (transcript or "").strip()
        text = original.lower()
        reference = call_ended_at or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)

        if not text:
            return CallAnalysisResult(
                outcome=Outcome.FOLLOW_UP_REQUIRED,
                follow_up_required=True,
                follow_up=AnalysisFollowUp(reason=NO_DATE_REASON),
                customer_intent=CustomerIntent.UNCLEAR,
                summary="The transcript was empty, so the lead was kept in the follow-up queue "
                        "for a manual callback.",
                key_points=["Empty transcript"],
                confidence=0.1,
            )

        dropped = _find_phrase(text, DROPPED_PHRASES)
        if dropped:
            return CallAnalysisResult(
                outcome=Outcome.DROPPED,
                customer_intent=CustomerIntent.NOT_INTERESTED,
                summary="Lead indicated they are no longer interested in the course.",
                key_points=[_sentence_containing(original, dropped)],
                confidence=0.85,
            )

        converted = _find_phrase(text, CONVERTED_PHRASES)
        pending_payment = _find_phrase(text, PAYMENT_PENDING_PHRASES)
        if converted and not pending_payment:
            return CallAnalysisResult(
                outcome=Outcome.CONVERTED,
                customer_intent=CustomerIntent.READY_TO_ENROLL,
                summary="Lead confirmed they are enrolling / have completed payment.",
                key_points=[_sentence_containing(original, converted)],
                confidence=0.85,
            )

        follow_up = _find_phrase(text, FOLLOW_UP_PHRASES) or pending_payment
        when = _resolve_follow_up_datetime(text, reference)
        matched = follow_up or "no clear outcome"
        if follow_up:
            intent = CustomerIntent.INTERESTED
            if "parents" in text or "family" in text or "discuss" in text:
                intent = CustomerIntent.NEEDS_TIME
            if "fee" in text or "emi" in text or "discount" in text:
                intent = CustomerIntent.PRICE_SENSITIVE
            if pending_payment:
                intent = CustomerIntent.READY_TO_ENROLL
            confidence = 0.75 if when else 0.6
            summary = (
                "Lead remains interested and asked to be contacted again."
                if when else
                "Lead remains interested and asked to be contacted again, but did not name a time."
            )
        else:
            intent = CustomerIntent.UNCLEAR
            confidence = 0.3
            summary = ("No clear outcome was detected in the transcript; the lead is kept in "
                       "the follow-up queue so it is not lost.")

        reason = (
            f"Lead asked to be contacted again (\"{matched}\")."
            if when else NO_DATE_REASON
        )
        key_points = [_sentence_containing(original, follow_up)] if follow_up else []
        if when:
            key_points.append(f"Requested callback around {when:%d %b %Y %H:%M} UTC")

        return CallAnalysisResult(
            outcome=Outcome.FOLLOW_UP_REQUIRED,
            follow_up_required=True,
            follow_up=AnalysisFollowUp(datetime=when, reason=reason),
            customer_intent=intent,
            summary=summary,
            key_points=key_points,
            confidence=confidence,
        )


_analyzer = KeywordCallAnalyzer()


def analyze_call(transcript: str, *, call_ended_at: datetime | None = None) -> CallAnalysisResult:
    return _analyzer.analyze(transcript, call_ended_at=call_ended_at)
