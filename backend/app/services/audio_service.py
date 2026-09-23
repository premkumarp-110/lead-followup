"""Call recordings: fetch from the CRM, cache on disk, serve to the browser.

There is no upload path any more -- every recording lives in the Lead Call API
and is fetched with the `X-API-Key` header. That header is the whole reason this
module exists rather than the browser pointing an <audio> tag upstream:

  * A browser cannot attach `X-API-Key` to an <audio src>, so redirecting to the
    CRM yields 401. Measured, not theoretical.
  * Upstream sends no `content-length` and ignores `Range`, so a streamed
    passthrough gives the player no duration and no seeking.

So the first request buffers the whole body to disk and every request after that
is served from the cache with proper headers. Recordings are immutable, so the
cache never expires.

Cached filenames derive from our own `call_id`, never from anything upstream,
and `resolve_cached_path` refuses any path outside the cache directory.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services.lead_call_client import (
    LeadCallAPIError,
    LeadCallUnavailable,
    get_lead_call_client,
)

logger = logging.getLogger(__name__)

AUDIO_MIME = "audio/mpeg"
CACHE_SUFFIX = ".mp3"
# A recording that comes back implausibly small is an error page, not audio.
MIN_PLAUSIBLE_BYTES = 512
# Upstream sends no content-length, so cap what we are willing to buffer.
MAX_RECORDING_BYTES = 64 * 1024 * 1024

# call_id is ours and always matches this, but assert it before it reaches a path.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class AudioValidationError(ValueError):
    """Raised for an audio request we refuse. Mapped to 422 in main.py."""


class RecordingNotAvailable(LookupError):
    """This call has no recording to serve. Mapped to 404."""


@dataclass
class CachedRecording:
    path: Path
    size_bytes: int
    from_cache: bool


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def cache_dir() -> Path:
    path = settings.upload_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_path_for(call_id: str) -> Path:
    """Where a call's audio is cached. Derived from call_id, never from upstream."""
    if not call_id or not _SAFE_ID.match(call_id):
        raise AudioValidationError(f"Unsafe call id: {call_id!r}")
    return cache_dir() / f"{call_id}{CACHE_SUFFIX}"


def resolve_cached_path(call_id: str) -> Path | None:
    """The cached file for a call, or None. Refuses anything outside the cache dir."""
    path = cached_path_for(call_id)
    try:
        resolved = path.resolve()
        root = cache_dir().resolve()
    except OSError:
        return None
    if root not in resolved.parents:
        logger.warning("Refusing audio path outside the cache directory: %s", resolved)
        return None
    if not resolved.is_file():
        return None
    return resolved


def delete_cached_file(call_id: str) -> None:
    try:
        path = resolve_cached_path(call_id)
        if path:
            path.unlink()
    except (OSError, AudioValidationError):  # pragma: no cover - best effort
        logger.debug("Could not delete cached audio for %s", call_id, exc_info=True)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch_recording(call_id: str, crm_call_id: str | None) -> CachedRecording:
    """Return the cached audio for a call, fetching it from the CRM if needed.

    Raises:
        RecordingNotAvailable -- no crm_call_id, or the CRM has no recording.
        LeadCallAPIError      -- the key is missing/invalid (operator must fix).
        LeadCallUnavailable   -- the CRM or its telephony provider was unreachable.
    """
    existing = resolve_cached_path(call_id)
    if existing:
        return CachedRecording(path=existing, size_bytes=existing.stat().st_size, from_cache=True)

    if not crm_call_id:
        raise RecordingNotAvailable(
            "This call has no recording stored in the CRM."
        )

    client = get_lead_call_client()  # raises LeadCallAPIError when unconfigured

    target = cached_path_for(call_id)
    # Write to a temp name first so an interrupted fetch cannot leave a
    # truncated file that later reads would happily serve as a complete one.
    partial = target.with_suffix(f".{secrets.token_hex(4)}.partial")
    total = 0
    try:
        with partial.open("wb") as handle:
            for chunk in client.stream_recording(crm_call_id):
                total += len(chunk)
                if total > MAX_RECORDING_BYTES:
                    raise AudioValidationError(
                        "The recording exceeded the maximum size this server will buffer."
                    )
                handle.write(chunk)
    except LeadCallAPIError as exc:
        partial.unlink(missing_ok=True)
        if exc.is_skippable:
            raise RecordingNotAvailable(
                "The CRM has no recording for this call."
            ) from exc
        raise
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    if total < MIN_PLAUSIBLE_BYTES:
        partial.unlink(missing_ok=True)
        raise RecordingNotAvailable(
            "The CRM returned an empty recording for this call."
        )

    partial.replace(target)
    logger.info("Cached recording for %s (%s bytes) from CRM call %s", call_id, total, crm_call_id)
    return CachedRecording(path=target, size_bytes=total, from_cache=False)


def load_audio_bytes(call_id: str, crm_call_id: str | None) -> bytes:
    """The raw audio for a call, for transcription. Goes through the same cache."""
    return fetch_recording(call_id, crm_call_id).path.read_bytes()


# --------------------------------------------------------------------------
# Ids
# --------------------------------------------------------------------------

def new_call_id() -> str:
    return f"CALL-{secrets.token_hex(4).upper()}"


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


__all__ = [
    "AUDIO_MIME",
    "AudioValidationError",
    "CachedRecording",
    "LeadCallUnavailable",
    "RecordingNotAvailable",
    "cache_dir",
    "cached_path_for",
    "delete_cached_file",
    "fetch_recording",
    "load_audio_bytes",
    "new_call_id",
    "new_id",
    "resolve_cached_path",
]
