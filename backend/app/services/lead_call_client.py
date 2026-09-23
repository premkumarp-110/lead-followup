"""Client for the Lead Call API (the CRM), the upstream source of leads and calls.

Read-only. Authenticates with `X-API-Key`. The documented rate limit is
60 requests / 10 seconds per key, so a shared token bucket paces every caller --
a single worker fetching transcripts can trivially exceed 6 rps on its own.

The CRM's error bodies are machine-readable (`{"status","message","errorCode"}`),
so `LeadCallAPIError` carries the `errorCode` and the routes map it onto the
existing handler taxonomy in `main.py` rather than leaking a 500.

One pooled `httpx.Client` is reused for the process. Constructing a client per
call pays a TLS handshake every time.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 60 requests / 10 seconds, expressed as a steady rate with a small burst.
RATE_PER_SECOND = 6.0
BURST = 6
REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3

# Reasons that mean "this one record has nothing, move on" rather than "the
# sync is broken". Matched against the CRM's `message`, which is where the
# specific name lives; the HTTP status is the fallback.
SKIPPABLE_CODES = {
    "RECORDING_NOT_AVAILABLE",
    "TRANSCRIPT_NOT_AVAILABLE",
    "CALL_NOT_FOUND",
    "LEAD_NOT_FOUND",
}

# Reasons that mean the operator has to fix configuration.
AUTH_CODES = {"INVALID_API_KEY", "API_KEY_REVOKED"}


class LeadCallAPIError(RuntimeError):
    """A CRM request failed.

    The CRM reports a failure in two places, and they carry different things:

        {"status": "error", "message": "CALL_NOT_FOUND", "errorCode": "E404"}

    `errorCode` is a coarse HTTP-shaped code; `message` is the specific reason.
    Both are kept, because the useful distinctions live in the name -- E502
    covers both RECORDING_ACCESS_DENIED and CRM_UNAVAILABLE, which mean
    different things to a caller.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        reason: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code          # e.g. "E404"
        self.reason = reason      # e.g. "CALL_NOT_FOUND"

    def _matches(self, names: frozenset[str]) -> bool:
        return (self.reason or "") in names or (self.code or "") in names

    @property
    def is_auth_error(self) -> bool:
        return self._matches(AUTH_CODES) or self.status_code == 401

    @property
    def is_skippable(self) -> bool:
        return self._matches(SKIPPABLE_CODES) or self.status_code == 404


class LeadCallUnavailable(LeadCallAPIError):
    """The CRM was unreachable or returned 5xx. Retryable; surfaces as a 503."""


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------

def from_epoch_ms(value: Any) -> datetime | None:
    """Epoch milliseconds -> tz-aware UTC datetime.

    Every CRM timestamp is milliseconds and everything we store is tz-aware UTC.
    A seconds/milliseconds mix-up silently puts records in 1970, so this is the
    single conversion point.
    """
    if value is None or value == "":
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

class _TokenBucket:
    """Shared across threads so concurrent callers cannot collectively exceed the limit."""

    def __init__(self, rate: float, burst: int):
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def take(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)


_bucket = _TokenBucket(RATE_PER_SECOND, BURST)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

class LeadCallClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                # Python-urllib's default UA is blocked by the edge with a 403;
                # httpx's own UA is fine, but be explicit so it cannot regress.
                "User-Agent": "lead-followup-manager/1.0",
            },
            follow_redirects=True,
        )

    # -- plumbing ---------------------------------------------------------

    def _raise_for_body(self, response: httpx.Response) -> None:
        """Turn a CRM error body into a typed exception."""
        code: str | None = None
        reason: str | None = None
        message = f"CRM returned HTTP {response.status_code}"
        try:
            body = response.json()
            if isinstance(body, dict):
                code = body.get("errorCode")
                reason = body.get("message")
                message = reason or message
        except Exception:  # noqa: BLE001 - a non-JSON error body is still an error
            pass

        if response.status_code in (401, 403):
            raise LeadCallAPIError(
                "The Lead Call API rejected the configured key "
                f"({message}). Check LEAD_CALL_API_KEY in backend/.env.",
                status_code=response.status_code,
                code=code,
                reason=reason or "INVALID_API_KEY",
            )
        if response.status_code == 404:
            raise LeadCallAPIError(message, status_code=404, code=code, reason=reason)
        if response.status_code >= 500:
            raise LeadCallUnavailable(
                f"The Lead Call API is unavailable ({message}).",
                status_code=response.status_code,
                code=code,
                reason=reason,
            )
        raise LeadCallAPIError(
            message, status_code=response.status_code, code=code, reason=reason
        )

    def _request(self, method: str, path: str, *, json_body: dict | None = None, stream: bool = False):
        attempt = 0
        while True:
            attempt += 1
            _bucket.take()
            try:
                if stream:
                    request = self._client.build_request(method, path, json=json_body)
                    response = self._client.send(request, stream=True)
                else:
                    response = self._client.request(method, path, json=json_body)
            except httpx.RequestError as exc:
                if attempt >= MAX_RETRIES:
                    raise LeadCallUnavailable(
                        f"Could not reach the Lead Call API: {exc}"
                    ) from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code == 429:
                # Honour Retry-After rather than guessing; the CRM tells us.
                retry_after = response.headers.get("Retry-After")
                if stream:
                    response.close()
                delay = 2.0
                try:
                    if retry_after:
                        delay = max(0.5, float(retry_after))
                except ValueError:
                    pass
                if attempt >= MAX_RETRIES:
                    raise LeadCallAPIError(
                        "The Lead Call API rate limit was exceeded. Try again shortly.",
                        status_code=429,
                        code="RATE_LIMITED",
                    )
                logger.warning("CRM rate limited; retrying in %.1fs", delay)
                time.sleep(delay)
                continue

            if response.status_code >= 400:
                if stream:
                    response.read()
                    response.close()
                if response.status_code >= 500 and attempt < MAX_RETRIES:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                self._raise_for_body(response)

            return response

    # -- endpoints --------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/health").json()

    def search_leads(
        self,
        *,
        filters: dict | None = None,
        ai_query: str | None = None,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        limit: int = 100,
        batch: int = 0,
        with_total: bool = False,
    ) -> dict:
        body: dict[str, Any] = {"limit": limit, "batch": batch}
        if filters:
            body["filters"] = filters
        if ai_query:
            body["aiQuery"] = ai_query
        if sort_by:
            body["sortBy"] = sort_by
        if sort_dir:
            body["sortDir"] = sort_dir
        if with_total:
            body["withTotal"] = True
        return self._request("POST", "/leads/search", json_body=body).json()

    def lead_calls(
        self,
        lead_id: str,
        *,
        filters: dict | None = None,
        limit: int = 100,
        batch: int = 0,
    ) -> dict:
        body: dict[str, Any] = {"leadId": lead_id, "limit": limit, "batch": batch}
        if filters:
            body["filters"] = filters
        return self._request("POST", "/lead/calls", json_body=body).json()

    def transcript(self, call_id: str) -> dict:
        return self._request("GET", f"/transcript/{call_id}").json()

    def stream_recording(self, call_id: str) -> Iterator[bytes]:
        """Yield the raw audio bytes for a call.

        Upstream sends no content-length and ignores Range, so the caller
        buffers the whole body (we cache it to disk on first fetch).
        """
        response = self._request("GET", f"/recording/{call_id}", stream=True)
        try:
            yield from response.iter_bytes()
        finally:
            response.close()

    def close(self) -> None:
        self._client.close()


@lru_cache(maxsize=1)
def get_lead_call_client() -> LeadCallClient:
    """The process-wide pooled client. Raises if the CRM is not configured."""
    error = settings.lead_call_config_error()
    if error:
        raise LeadCallAPIError(error, code="NOT_CONFIGURED")
    return LeadCallClient(settings.lead_call_api_url, settings.lead_call_api_key)
