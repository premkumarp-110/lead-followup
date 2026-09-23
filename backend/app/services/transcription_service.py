"""Transcription: audio in, text out. Nothing else.

This service converts a recording into a transcript and returns it. It makes
no business decisions -- it does not know what a lead is, what "converted"
means, or that an analysis will follow. That separation is deliberate (spec
S8): the transcription provider can be replaced without touching analysis, and
vice versa.

Audio comes from the CRM now, fetched and cached by `audio_service`. The only
calls that reach here are the ones with a recording but no CRM transcript --
measured live, that is a small minority, because ~55% of analyzable calls
already ship a transcript and transcription is the most expensive step.

Interface:

    transcribe_audio(source) -> TranscriptResult   # {text, language, duration_seconds}

Providers are selected by TRANSCRIPTION_PROVIDER. Only "vertex" is implemented
in this MVP; the others are registered so the configuration is discoverable
and fail with a clear message explaining what to install/configure.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.services import audio_service
from app.services.lead_call_client import LeadCallAPIError
from app.services.vertex_client import VertexUnavailable, describe_api_error, get_client

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Transcription failed for a reason the operator can act on."""


class TranscriptionNotConfigured(TranscriptionError):
    """The selected provider is not available in this deployment."""


@dataclass
class AudioSource:
    """What the transcriber needs to fetch and describe one call's audio."""

    call_id: str
    crm_call_id: str | None = None
    mime_type: str = audio_service.AUDIO_MIME
    duration_seconds: int | None = None


@dataclass
class TranscriptResult:
    text: str
    language: str | None
    duration_seconds: int | None
    provider: str


class Transcriber(Protocol):
    name: str

    def transcribe(self, source: AudioSource) -> TranscriptResult: ...


# --------------------------------------------------------------------------
# Audio loading (shared by providers)
# --------------------------------------------------------------------------


def _load_audio_bytes(source: AudioSource) -> tuple[bytes, str]:
    """Fetch the call's audio, going through the CRM cache.

    Every failure mode upstream -- no recording, an unconfigured key, a CRM
    outage -- becomes a TranscriptionError with the operator-facing wording the
    client already produced, so the pipeline records one clear reason.
    """
    try:
        data = audio_service.load_audio_bytes(source.call_id, source.crm_call_id)
    except audio_service.RecordingNotAvailable as exc:
        raise TranscriptionError(
            "This call has no recording to transcribe."
        ) from exc
    except audio_service.LeadCallUnavailable as exc:
        raise TranscriptionError(str(exc)) from exc
    except LeadCallAPIError as exc:
        raise TranscriptionNotConfigured(str(exc)) from exc
    return data, source.mime_type


# --------------------------------------------------------------------------
# Provider: Vertex AI Gemini (audio understanding)
# --------------------------------------------------------------------------

_TRANSCRIBE_PROMPT = """You are a precise transcription engine for sales phone calls between a
business-development executive at an EdTech company and a prospective student.

Transcribe the audio verbatim. Rules:
- Output the spoken words only. No commentary, no summary, no timestamps.
- Preserve the original language. If the speech is Tamil, Hindi or another language,
  transcribe in that language's script; do not translate.
- If speakers are distinguishable, prefix each turn with "BD:" or "Lead:". If not, omit prefixes.
- If a passage is unintelligible, write [inaudible].
- If there is no speech at all, return an empty transcript.

Return JSON only, in this exact shape:
{"transcript": "...", "language": "<BCP-47 code such as en, ta, hi, or en-IN>"}"""


class VertexGeminiTranscriber:
    """Uses Gemini's native audio understanding to produce a transcript.

    This is a single, transcription-only model call. It is intentionally a
    different call, with a different prompt, from the analysis in
    llm_service.py -- the two stay independently replaceable.
    """

    name = "vertex"

    def transcribe(self, source: AudioSource) -> TranscriptResult:
        try:
            client = get_client()
        except VertexUnavailable as exc:
            raise TranscriptionNotConfigured(str(exc)) from exc

        from google.genai import errors as genai_errors
        from google.genai import types

        audio_bytes, mime_type = _load_audio_bytes(source)
        if not audio_bytes:
            raise TranscriptionError("The audio file is empty.")

        # Duration comes from the CRM's durationSec; there is no ffprobe step
        # any more and nothing here needs one.
        duration = source.duration_seconds

        try:
            response = client.models.generate_content(
                model=settings.vertex_ai_model,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    _TRANSCRIBE_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
        except genai_errors.APIError as exc:
            raise TranscriptionError(describe_api_error(exc)) from exc
        except Exception as exc:  # network, auth refresh, etc.
            raise TranscriptionError(describe_api_error(exc)) from exc

        raw = (response.text or "").strip()
        text, language = _parse_transcript_json(raw)
        if not text.strip():
            raise TranscriptionError(
                "No speech was detected in the recording, so there is nothing to analyze."
            )
        return TranscriptResult(
            text=text.strip(), language=language, duration_seconds=duration, provider=self.name
        )


def _parse_transcript_json(raw: str) -> tuple[str, str | None]:
    """Accept the requested JSON, or fall back to treating the body as plain text."""
    import json

    candidate = raw
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return str(data.get("transcript") or ""), (data.get("language") or None)
    except json.JSONDecodeError:
        pass
    return raw, None


# --------------------------------------------------------------------------
# Unimplemented providers -- registered so the error is specific, not generic
# --------------------------------------------------------------------------


class _NotConfiguredTranscriber:
    name = "unconfigured"
    message = "This transcription provider is not available."

    def transcribe(self, source: AudioSource) -> TranscriptResult:
        raise TranscriptionNotConfigured(self.message)


class LocalWhisperTranscriber(_NotConfiguredTranscriber):
    name = "local"
    message = (
        "TRANSCRIPTION_PROVIDER=local is not implemented in this MVP. Set "
        "TRANSCRIPTION_PROVIDER=vertex to transcribe with Gemini on Vertex AI, or add a "
        "faster-whisper implementation to transcription_service.py."
    )


class GoogleSpeechTranscriber(_NotConfiguredTranscriber):
    name = "gcp-stt"
    message = (
        "TRANSCRIPTION_PROVIDER=gcp-stt (Cloud Speech-to-Text) is not implemented in this MVP. "
        "Set TRANSCRIPTION_PROVIDER=vertex to transcribe with Gemini on Vertex AI."
    )


_PROVIDERS: dict[str, type] = {
    "vertex": VertexGeminiTranscriber,
    "gemini": VertexGeminiTranscriber,  # alias
    "local": LocalWhisperTranscriber,
    "whisper": LocalWhisperTranscriber,
    "gcp-stt": GoogleSpeechTranscriber,
}

_instances: dict[str, Transcriber] = {}


def get_transcriber(provider: str | None = None) -> Transcriber:
    key = (provider or settings.transcription_provider or "vertex").strip().lower()
    if key not in _PROVIDERS:
        raise TranscriptionNotConfigured(
            f"Unknown TRANSCRIPTION_PROVIDER '{key}'. Available: {', '.join(sorted(_PROVIDERS))}."
        )
    if key not in _instances:
        _instances[key] = _PROVIDERS[key]()
    return _instances[key]


def transcribe_audio(source: AudioSource) -> TranscriptResult:
    """The one function the orchestrator calls."""
    return get_transcriber().transcribe(source)
