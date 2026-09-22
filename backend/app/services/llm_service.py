"""Conversation analysis with Vertex AI Gemini -- THE LLM SWAP POINT.

    analyze_conversation(transcript, lead_data, caller_data, call_ended_at) -> AnalysisRun

This is the only module that knows how to talk to a language model about a
sales conversation. Replacing Gemini with another model means changing this
file; the models, follow-up logic, routes and dashboard stay as they are.

Flow for one call:
  1. One Gemini request with a structured system prompt, JSON-only output.
  2. Validate the JSON with `CallAnalysisResult` (spec S13). Never trust raw output.
  3. If parsing/validation fails, ONE repair retry that quotes the error back.
  4. If Gemini is unavailable or still invalid, and ANALYSIS_FALLBACK_ENABLED,
     run the deterministic keyword analyzer instead, flagged `degraded`.

Every attempt -- failed or not -- is returned so the orchestrator can persist
the raw response for debugging.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from pydantic import ValidationError

from app.config import settings
from app.models.analysis import NO_DATE_REASON, CallAnalysisResult
from app.services import call_analyzer
from app.services.vertex_client import VertexUnavailable, describe_api_error, get_client

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


class LLMAnalysisError(RuntimeError):
    """Gemini could not produce a valid analysis and no fallback was allowed."""


@dataclass
class AnalysisAttempt:
    """One try at analysing a transcript, by one model."""

    model: str
    result: CallAnalysisResult | None
    raw_response: str | None
    error: str | None
    degraded: bool = False

    @property
    def succeeded(self) -> bool:
        return self.result is not None


@dataclass
class AnalysisRun:
    """All attempts for one call. `final` is the one that updates the lead."""

    attempts: list[AnalysisAttempt] = field(default_factory=list)

    @property
    def final(self) -> AnalysisAttempt | None:
        for attempt in reversed(self.attempts):
            if attempt.succeeded:
                return attempt
        return None

    @property
    def failed_attempts(self) -> list[AnalysisAttempt]:
        return [a for a in self.attempts if not a.succeeded]


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You analyse transcripts of phone calls between a business-development (BD)
executive at an EdTech company and a prospective student ("the lead"). Your job is to
decide the NEXT ACTION for this lead and return it as strict JSON.

Decide exactly one outcome:
- "DROPPED": the lead explicitly says they are not interested, will not join, chose another
  provider, or asks not to be contacted.
- "CONVERTED": the lead clearly confirms enrollment or confirms that payment is complete.
  Intent to pay later is NOT conversion.
- "FOLLOW_UP_REQUIRED": the lead remains interested (or undecided) and another interaction is
  needed -- including "I will pay later", "let me discuss with family", "call me next week".
  This is also the outcome when the conversation is unclear; never drop a lead by guesswork.

Follow-up date/time rules -- these matter most:
- NEVER invent a date or time. Only use what the lead said or what can be derived from it
  together with the call date you are given.
- Relative phrases are derived from the call date: "tomorrow" = call date + 1 day;
  "day after tomorrow" = + 2 days; "next week" = + 7 days; "this evening" = same day 18:00.
- "Morning" = 10:00, "afternoon" = 14:00, "evening" = 18:00 when no exact time is given.
- If the lead gives a day but no time, use 10:00.
- All times are the lead's local time, Asia/Kolkata (UTC+05:30). Return `datetime` as an
  ISO-8601 string WITH the +05:30 offset, e.g. "2026-09-23T11:00:00+05:30".
- If a follow-up is needed but NO date/time is stated or reasonably inferable, set
  follow_up_required = true, follow_up.date/time/datetime = null, and
  follow_up.reason = "{NO_DATE_REASON}"
- For CONVERTED and DROPPED, follow_up_required = false and follow_up = null.

customer_intent must be one of: INTERESTED, READY_TO_ENROLL, NOT_INTERESTED, NEEDS_TIME,
PRICE_SENSITIVE, UNCLEAR.

Return ONLY a JSON object -- no prose, no markdown fences -- in exactly this shape:
{{
  "outcome": "FOLLOW_UP_REQUIRED" | "CONVERTED" | "DROPPED",
  "follow_up_required": true | false,
  "follow_up": {{
    "date": "YYYY-MM-DD" | null,
    "time": "HH:MM" | null,
    "datetime": "YYYY-MM-DDTHH:MM:SS+05:30" | null,
    "reason": "one sentence: why a follow-up is needed"
  }} | null,
  "customer_intent": "...",
  "summary": "one or two sentences describing the conversation and the lead's position",
  "key_points": ["short bullet", "short bullet", "..."],
  "confidence": 0.0 to 1.0
}}"""

# A JSON schema handed to Gemini so it constrains its own output. Validation
# still happens in Pydantic afterwards -- this just raises the hit rate.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "outcome": {"type": "STRING", "enum": ["FOLLOW_UP_REQUIRED", "CONVERTED", "DROPPED"]},
        "follow_up_required": {"type": "BOOLEAN"},
        "follow_up": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {
                "date": {"type": "STRING", "nullable": True},
                "time": {"type": "STRING", "nullable": True},
                "datetime": {"type": "STRING", "nullable": True},
                "reason": {"type": "STRING", "nullable": True},
            },
        },
        "customer_intent": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["outcome", "follow_up_required", "customer_intent", "summary", "key_points", "confidence"],
}


def build_user_prompt(
    transcript: str, lead_data: dict, caller_data: dict | None, call_ended_at: datetime
) -> str:
    local = call_ended_at.astimezone(IST)
    caller = caller_data or {}
    return f"""CALL CONTEXT
- Call date and time (lead's local time, Asia/Kolkata): {local:%A, %d %B %Y at %H:%M} (+05:30)
- Lead: {lead_data.get('name', 'Unknown')} (id {lead_data.get('lead_id', '?')})
- Course of interest: {lead_data.get('course', 'Unknown')}
- Current lead status: {lead_data.get('lead_status', 'UNKNOWN')}
- Caller / BD: {caller.get('name', lead_data.get('assigned_bd', {}).get('name', 'Unknown'))}

TRANSCRIPT
\"\"\"
{transcript.strip()}
\"\"\"

Analyse the transcript and return the JSON object."""


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def extract_json(raw: str) -> dict:
    """Pull the first JSON object out of a model response, tolerating fences."""
    text = _FENCE_RE.sub("", (raw or "").strip()).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("Response contained no JSON object.")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Response JSON was not an object.")
    return data


def parse_and_validate(raw: str) -> CallAnalysisResult:
    data = extract_json(raw)
    return CallAnalysisResult.model_validate(data)


def _format_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        return "Invalid analysis JSON -- " + "; ".join(parts)
    return f"Invalid analysis JSON -- {exc}"


# --------------------------------------------------------------------------
# Gemini call
# --------------------------------------------------------------------------


def _call_gemini(client, contents: list) -> str:
    from google.genai import types

    response = client.models.generate_content(
        model=settings.vertex_ai_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
            max_output_tokens=2048,
        ),
    )
    return (response.text or "").strip()


def analyze_with_gemini(
    transcript: str, lead_data: dict, caller_data: dict | None, call_ended_at: datetime
) -> AnalysisAttempt:
    """One Gemini analysis with a single repair retry on invalid output."""
    model_name = settings.vertex_ai_model
    try:
        client = get_client()
    except VertexUnavailable as exc:
        return AnalysisAttempt(model=model_name, result=None, raw_response=None, error=str(exc))

    user_prompt = build_user_prompt(transcript, lead_data, caller_data, call_ended_at)
    raw = None
    try:
        raw = _call_gemini(client, [user_prompt])
        result = parse_and_validate(raw)
        return AnalysisAttempt(model=model_name, result=result, raw_response=raw, error=None)
    except (ValueError, ValidationError) as first_error:
        logger.warning("Gemini output failed validation; retrying once: %s", first_error)
        repair = (
            f"{user_prompt}\n\nYour previous response was rejected: "
            f"{_format_validation_error(first_error)}\nPrevious response:\n{raw}\n\n"
            "Return a corrected JSON object only."
        )
        try:
            raw2 = _call_gemini(client, [repair])
            result = parse_and_validate(raw2)
            return AnalysisAttempt(model=model_name, result=result, raw_response=raw2, error=None)
        except (ValueError, ValidationError) as second_error:
            return AnalysisAttempt(
                model=model_name,
                result=None,
                raw_response=f"--- attempt 1 ---\n{raw}\n--- attempt 2 ---\n{locals().get('raw2', '')}",
                error=_format_validation_error(second_error),
            )
        except Exception as exc:
            return AnalysisAttempt(
                model=model_name, result=None, raw_response=raw, error=describe_api_error(exc)
            )
    except Exception as exc:  # APIError, network, auth
        return AnalysisAttempt(
            model=model_name, result=None, raw_response=raw, error=describe_api_error(exc)
        )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def analyze_conversation(
    transcript: str,
    lead_data: dict,
    caller_data: dict | None = None,
    *,
    call_ended_at: datetime | None = None,
) -> AnalysisRun:
    """Analyse a transcript. Gemini first; keyword fallback only if Gemini fails.

    Raises LLMAnalysisError when nothing produced a valid result.
    """
    ended = call_ended_at or datetime.now(timezone.utc)
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)

    run = AnalysisRun()
    gemini = analyze_with_gemini(transcript, lead_data, caller_data, ended)
    run.attempts.append(gemini)
    if gemini.succeeded:
        return run

    logger.warning("Gemini analysis failed: %s", gemini.error)
    if not settings.analysis_fallback_enabled:
        raise LLMAnalysisError(gemini.error or "Gemini analysis failed.")

    fallback = call_analyzer.analyze_call(transcript, call_ended_at=ended)
    run.attempts.append(
        AnalysisAttempt(
            model=call_analyzer.KEYWORD_MODEL_NAME,
            result=fallback,
            raw_response=None,
            error=None,
            degraded=True,
        )
    )
    return run
