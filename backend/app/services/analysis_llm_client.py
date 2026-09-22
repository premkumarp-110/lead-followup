"""HTTP client for the OpenAI-compatible call-outcome analysis endpoint.

Only llm_service.py talks to this module. It knows nothing about Gemini,
Vertex, transcription, or CallAnalysisResult -- it posts one system+user turn
and returns the assistant's text content.
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class AnalysisLLMUnavailable(RuntimeError):
    """The outcome-analysis endpoint cannot be used or the call failed.

    The message is always safe to show a user.
    """


def assert_analysis_llm_configured() -> None:
    error = settings.call_analysis_config_error()
    if error:
        raise AnalysisLLMUnavailable(error)


def describe_analysis_api_error(response: httpx.Response) -> str:
    """Turn a non-2xx HTTP response into a one-line, user-safe explanation."""
    status = response.status_code
    if status in (401, 403):
        return (
            f"Call-outcome analysis endpoint rejected the API key (HTTP {status}). "
            "Check CALL_ANALYSIS_API_KEY."
        )
    if status == 404:
        return (
            "Call-outcome analysis endpoint or model not found (HTTP 404). Check "
            f"CALL_ANALYSIS_API_URL and CALL_ANALYSIS_MODEL ('{settings.call_analysis_model}')."
        )
    if status == 429:
        return "Call-outcome analysis endpoint rate-limited the request (HTTP 429). Retry shortly."
    if status >= 500:
        return f"Call-outcome analysis endpoint had a server error (HTTP {status})."
    snippet = response.text[:200] if response.text else ""
    return f"Call-outcome analysis endpoint returned HTTP {status}: {snippet}"


def call_chat_completion(system_prompt: str, user_content: str) -> str:
    """POST a single system+user turn to the chat-completions endpoint.

    Returns choices[0].message.content, stripped. Raises AnalysisLLMUnavailable
    on any failure (config, network, non-2xx, malformed response shape) with a
    user-safe message.
    """
    assert_analysis_llm_configured()

    url = settings.call_analysis_api_url.strip()
    headers = {
        "Authorization": f"Bearer {settings.call_analysis_api_key.strip()}",
        "Content-Type": "application/json",
    }
    body = {
        "model": settings.call_analysis_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        # Optimistic: honored by many OpenAI-compatible backends. If this
        # endpoint ignores/rejects it, llm_service's extract_json() fence
        # stripping and repair-retry still recover the JSON -- no extra
        # retry tier needed here.
        "response_format": {"type": "json_object"},
    }

    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise AnalysisLLMUnavailable(
            f"Call-outcome analysis endpoint timed out ({url})."
        ) from exc
    except httpx.HTTPError as exc:
        raise AnalysisLLMUnavailable(
            f"Could not reach the call-outcome analysis endpoint ({url}): "
            f"{exc.__class__.__name__}."
        ) from exc

    if response.status_code >= 400:
        raise AnalysisLLMUnavailable(describe_analysis_api_error(response))

    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AnalysisLLMUnavailable(
            "Call-outcome analysis endpoint returned an unexpected response shape "
            f"(missing choices/message/content): {exc.__class__.__name__}."
        ) from exc

    return (content or "").strip()
