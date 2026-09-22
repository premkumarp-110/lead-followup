"""Shared Vertex AI client construction.

Both the transcription and the analysis service talk to Gemini through here so
authentication and configuration errors are handled in exactly one place.
Nothing in this module makes a network call at import time.
"""

import logging
import os
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)


class VertexUnavailable(RuntimeError):
    """Vertex AI cannot be used: not configured, not authenticated, or the call failed.

    The message is always safe to show to a user -- it names the setting or
    condition to fix, never a stack trace.
    """


def assert_vertex_configured() -> None:
    error = settings.vertex_config_error()
    if error:
        raise VertexUnavailable(error)


@lru_cache
def get_client():
    """Build the google-genai client in Vertex mode, once per process."""
    assert_vertex_configured()
    if settings.google_application_credentials.strip():
        # The SDK reads this env var via google-auth; set it only when the
        # operator provided a path, so ADC keeps working otherwise.
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            os.path.expanduser(settings.google_application_credentials.strip()),
        )
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise VertexUnavailable(
            "The google-genai package is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project.strip(),
            location=settings.google_cloud_location.strip(),
        )
    except Exception as exc:  # credentials not found, malformed, etc.
        raise VertexUnavailable(
            "Could not authenticate to Google Cloud. Run `gcloud auth application-default login` "
            f"or set GOOGLE_APPLICATION_CREDENTIALS to a service-account key. ({exc.__class__.__name__})"
        ) from exc


def describe_api_error(exc: Exception) -> str:
    """Turn an SDK exception into a one-line, user-safe explanation."""
    name = exc.__class__.__name__
    text = str(exc)
    lowered = text.lower()
    if "permission" in lowered or "403" in lowered:
        return (
            "Vertex AI refused the request (permission denied). Check that the Vertex AI API is "
            f"enabled on project '{settings.google_cloud_project}' and the credentials have the "
            "'Vertex AI User' role."
        )
    if "not found" in lowered or "404" in lowered:
        return (
            f"Model '{settings.vertex_ai_model}' was not found in location "
            f"'{settings.google_cloud_location}'. Check VERTEX_AI_MODEL and GOOGLE_CLOUD_LOCATION."
        )
    if "quota" in lowered or "429" in lowered or "resource exhausted" in lowered:
        return "Vertex AI quota exceeded. Retry shortly or raise the project's quota."
    if "credential" in lowered or "authenticat" in lowered or "401" in lowered:
        return (
            "Google Cloud authentication failed. Run `gcloud auth application-default login` or "
            "set GOOGLE_APPLICATION_CREDENTIALS."
        )
    if "api has not been used" in lowered or "is disabled" in lowered:
        return (
            f"The Vertex AI API is not enabled on project '{settings.google_cloud_project}'. Run: "
            f"gcloud services enable aiplatform.googleapis.com --project={settings.google_cloud_project}"
        )
    short = text.splitlines()[0][:200] if text else name
    return f"Vertex AI request failed ({name}): {short}"
