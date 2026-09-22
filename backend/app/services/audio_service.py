"""Audio ingestion: validate, store and describe call recordings.

This module knows about files, URLs, MIME types and ffprobe. It knows nothing
about transcription or analysis -- it hands back an `AudioSource` and stops.
"""

import ipaddress
import json
import logging
import re
import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import UploadFile

from app.config import settings

logger = logging.getLogger(__name__)


class AudioValidationError(ValueError):
    """Raised for anything the caller can fix: bad type, too large, bad URL."""


# Extension -> canonical MIME type. Browsers and servers disagree on audio
# MIME types constantly, so the extension is the primary signal and the MIME
# is a sanity check, not the other way round.
MIME_BY_EXTENSION = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}

ACCEPTED_MIME_PREFIXES = (
    "audio/", "video/webm", "video/mp4", "video/ogg", "application/ogg",
    "application/octet-stream", "binary/octet-stream",
)
# Content types that mean "this is a web page, not a file" -- a 404 or login
# page served with HTTP 200, which many hosts do.
REJECTED_MIME_PREFIXES = ("text/html", "text/plain", "application/json", "application/xml")

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class AudioSource:
    """Where the audio lives, as the rest of the pipeline sees it."""

    source_type: str          # "UPLOAD" | "URL"
    file_path: str | None     # local path when stored locally
    url: str | None           # remote URL for URL-mode calls
    mime_type: str
    size_bytes: int | None
    filename: str | None
    duration_seconds: int | None


# --------------------------------------------------------------------------
# Shared validation
# --------------------------------------------------------------------------


def extension_of(name: str | None) -> str:
    if not name or "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower().strip()


def validate_extension(name: str | None) -> str:
    ext = extension_of(name)
    allowed = settings.allowed_audio_extensions
    if ext not in allowed:
        raise AudioValidationError(
            f"Unsupported audio type '.{ext or '?'}'. Allowed: {', '.join('.' + a for a in allowed)}."
        )
    return ext


def validate_size(size_bytes: int | None) -> None:
    if size_bytes is None:
        return
    if size_bytes <= 0:
        raise AudioValidationError("The audio file is empty.")
    if size_bytes > settings.max_audio_bytes:
        raise AudioValidationError(
            f"Audio is {size_bytes / (1024 * 1024):.1f} MB; the maximum is {settings.max_audio_mb} MB."
        )


def _mime_looks_like_audio(mime: str | None, *, extension_ok: bool = False) -> bool:
    """Servers and browsers disagree wildly on audio MIME types, so when the
    extension is already on the allowlist only an obvious web page is refused."""
    if not mime:
        return True  # absent is tolerated; the extension already passed
    mime = mime.split(";")[0].strip().lower()
    if mime.startswith(REJECTED_MIME_PREFIXES):
        return False
    if extension_ok:
        return True
    return mime.startswith(ACCEPTED_MIME_PREFIXES)


# --------------------------------------------------------------------------
# Upload path
# --------------------------------------------------------------------------


def store_upload(upload: UploadFile, call_id: str) -> AudioSource:
    """Validate an uploaded file and write it to the configured upload dir.

    The stored filename is derived from call_id, never from the client's
    filename, so a hostile name can't escape the upload directory.
    """
    ext = validate_extension(upload.filename)
    if not _mime_looks_like_audio(upload.content_type, extension_ok=True):
        raise AudioValidationError(
            f"File '{upload.filename}' has content type '{upload.content_type}', which is not audio."
        )

    upload_dir = settings.upload_path
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_id = _SAFE_ID.sub("_", call_id)
    target = upload_dir / f"{safe_id}.{ext}"

    # Stream to disk while counting bytes; abort past the cap rather than
    # buffering an oversized upload in memory first.
    written = 0
    limit = settings.max_audio_bytes
    with target.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                out.close()
                target.unlink(missing_ok=True)
                raise AudioValidationError(
                    f"Audio exceeds the maximum of {settings.max_audio_mb} MB."
                )
            out.write(chunk)
    if written == 0:
        target.unlink(missing_ok=True)
        raise AudioValidationError("The audio file is empty.")

    return AudioSource(
        source_type="UPLOAD",
        file_path=str(target),
        url=None,
        mime_type=MIME_BY_EXTENSION.get(ext, upload.content_type or "audio/mpeg"),
        size_bytes=written,
        filename=upload.filename,
        duration_seconds=probe_duration(target),
    )


def delete_stored_file(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - best effort cleanup
        logger.warning("Could not remove %s", path)


# --------------------------------------------------------------------------
# URL path
# --------------------------------------------------------------------------


def _assert_public_host(hostname: str) -> None:
    """Refuse URLs that resolve to loopback / private / link-local addresses.

    The backend fetches this URL server-side, so without this check a user
    could point it at the Mongo port or cloud metadata endpoint.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise AudioValidationError(f"Could not resolve host '{hostname}'.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise AudioValidationError(
                f"URL host '{hostname}' resolves to a non-public address and cannot be fetched."
            )


def validate_audio_url(url: str) -> dict:
    """Check a URL is well-formed, public and serves an audio file of acceptable size.

    Returns metadata for the UI (content type, size, filename) without
    downloading the body.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise AudioValidationError("Audio URL must start with http:// or https://.")
    if not parsed.netloc:
        raise AudioValidationError("Audio URL is missing a host.")
    if parsed.username or parsed.password:
        raise AudioValidationError("Audio URL must not embed credentials.")

    _assert_public_host(parsed.hostname or "")

    filename = Path(parsed.path).name or None
    ext = extension_of(filename)

    try:
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            response = client.head(url, headers={"User-Agent": "lead-followup/1.0"})
            if response.status_code == 405 or response.status_code >= 400:
                # Some hosts refuse HEAD; ask for the first byte instead.
                response = client.get(
                    url, headers={"Range": "bytes=0-0", "User-Agent": "lead-followup/1.0"}
                )
    except httpx.HTTPError as exc:
        raise AudioValidationError(f"Audio URL is not reachable: {exc.__class__.__name__}.") from exc

    if response.status_code >= 400:
        raise AudioValidationError(f"Audio URL returned HTTP {response.status_code}.")

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ext:
        validate_extension(filename)
    elif not content_type.startswith("audio/"):
        raise AudioValidationError(
            "Could not confirm the URL points at audio: no audio file extension and "
            f"Content-Type is '{content_type or 'unknown'}'."
        )
    if content_type and not _mime_looks_like_audio(content_type, extension_ok=bool(ext)):
        raise AudioValidationError(
            f"URL returned Content-Type '{content_type}', which is a web page rather than audio."
        )

    size = None
    length = response.headers.get("content-length")
    content_range = response.headers.get("content-range")
    if content_range and "/" in content_range:
        length = content_range.rsplit("/", 1)[-1]
    if length and length.isdigit():
        size = int(length)
        validate_size(size)

    return {
        "url": str(response.url),
        "content_type": content_type or MIME_BY_EXTENSION.get(ext, "audio/mpeg"),
        "size_bytes": size,
        "filename": filename,
        "reachable": True,
    }


def source_from_url(url: str, meta: dict) -> AudioSource:
    return AudioSource(
        source_type="URL",
        file_path=None,
        url=meta.get("url") or url,
        mime_type=meta.get("content_type") or "audio/mpeg",
        size_bytes=meta.get("size_bytes"),
        filename=meta.get("filename"),
        duration_seconds=None,  # unknown until transcribed
    )


def download_url_to_temp(url: str) -> tuple[bytes, str]:
    """Fetch a validated URL's body for transcription. Enforces the size cap."""
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        with client.stream("GET", url, headers={"User-Agent": "lead-followup/1.0"}) as response:
            response.raise_for_status()
            mime = (response.headers.get("content-type") or "audio/mpeg").split(";")[0].strip()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > settings.max_audio_bytes:
                    raise AudioValidationError(
                        f"Remote audio exceeds the maximum of {settings.max_audio_mb} MB."
                    )
                chunks.append(chunk)
    return b"".join(chunks), mime


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------


def probe_duration(path: Path | str) -> int | None:
    """Return the duration in whole seconds via ffprobe, or None if unavailable."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
        data = json.loads(completed.stdout or "{}")
        duration = float(data.get("format", {}).get("duration", 0))
        return int(round(duration)) if duration > 0 else None
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def new_call_id() -> str:
    return f"CALL-{uuid.uuid4().hex[:8].upper()}"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"
