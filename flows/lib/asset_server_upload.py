"""Download MUSIT/Unimus media and upload originals to the Specify asset server."""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from os.path import splitext
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree
from urllib.parse import urlparse

import hmac
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

UNIMUS_ORIGINAL_URL = (
    "https://www.unimus.no/felles/bilder/web_hent_bilde.php?id={media_group_id}&type=orig"
)

# Intermittent asset-server/S3 failures during large TIFF uploads (~0.3% in staging).
UPLOAD_MAX_ATTEMPTS = 4
UPLOAD_RETRY_BACKOFF_S = (5, 15, 30)

_server_urls: dict[str, str] | None = None
_server_time_delta: int = 0


class AssetServerError(Exception):
    pass


class AssetServerNotConfigured(AssetServerError):
    pass


def _generate_token(timestamp: int, filename: str) -> str:
    key = settings.WEB_ATTACHMENT_KEY
    if not key:
        raise AssetServerError("WEB_ATTACHMENT_KEY / ATTACHMENT_KEY is not set")
    msg = str(timestamp).encode() + filename.encode()
    mac = hmac.new(key.encode(), msg, "md5")
    return ":".join((mac.hexdigest(), str(timestamp)))


def _get_timestamp() -> int:
    return int(time.time()) + _server_time_delta


def _update_time_delta(response: requests.Response) -> None:
    global _server_time_delta
    timestamp = response.headers.get("X-Timestamp")
    if timestamp is not None:
        _server_time_delta = int(timestamp) - int(time.time())


def make_attachment_filename(filename: str) -> str:
    _name, extension = splitext(filename)
    if not extension:
        extension = ".bin"
    return str(uuid4()) + extension


def asset_server_collection_name(*, fallback_collection_name: str) -> str:
    if getattr(settings, "WEB_ATTACHMENT_COLLECTION", None):
        return str(settings.WEB_ATTACHMENT_COLLECTION)
    return fallback_collection_name


def _rewrite_url_for_internal_upload(url: str) -> str:
    """Prefer in-cluster asset server for uploads when ASSET_SERVER_INTERNAL_BASE is set."""
    internal_base = (os.environ.get("ASSET_SERVER_INTERNAL_BASE") or "").strip().rstrip("/")
    if not internal_base:
        return url
    path = urlparse(url).path or "/fileupload"
    return f"{internal_base}{path}"


def _ensure_server_urls() -> dict[str, str]:
    global _server_urls

    if _server_urls is not None:
        return _server_urls

    url = getattr(settings, "WEB_ATTACHMENT_URL", None)
    if not url:
        raise AssetServerNotConfigured("WEB_ATTACHMENT_URL / ASSET_SERVER_URL is not set")

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    _update_time_delta(response)

    try:
        urls_xml = ElementTree.fromstring(response.text)
    except ElementTree.ParseError as exc:
        raise AssetServerError(f"Failed to parse asset server XML: {exc}") from exc

    parsed = {node.attrib["type"]: (node.text or "").strip() for node in urls_xml.findall("url")}
    if not parsed.get("write"):
        raise AssetServerError("Asset server XML is missing a write URL")

    parsed["write"] = _rewrite_url_for_internal_upload(parsed["write"])
    _server_urls = parsed
    return parsed


def reset_asset_server_cache() -> None:
    """Clear cached server URLs (for tests)."""
    global _server_urls, _server_time_delta
    _server_urls = None
    _server_time_delta = 0


def validate_media_bytes(file_bytes: bytes, *, source: str) -> str:
    """Reject HTML/empty downloads; return a coarse media kind for logging."""
    if not file_bytes:
        raise AssetServerError(f"Empty response from {source}")
    head = file_bytes[:512].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml")):
        snippet = file_bytes[:200].decode("utf-8", errors="replace")
        raise AssetServerError(
            f"Expected media bytes but got HTML/XML from {source}: {snippet[:120]}"
        )
    if file_bytes.startswith((b"II", b"MM")):
        return "tiff"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if file_bytes.startswith(b"\x89PNG"):
        return "png"
    if file_bytes[:4] == b"%PDF":
        return "pdf"
    return "unknown"


def download_unimus_original(
    media_group_id: int,
    *,
    timeout_s: int = 600,
) -> tuple[bytes, str | None]:
    """Download the original file bytes for a MUSIT media group."""
    url = UNIMUS_ORIGINAL_URL.format(media_group_id=int(media_group_id))
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    if not response.content:
        raise AssetServerError(f"Empty response from Unimus for media_group_id={media_group_id}")
    content_type = response.headers.get("Content-Type")
    return response.content, content_type


def _resolve_upload_mime(
    orig_filename: str,
    mime_type: str,
    content_type: str | None,
    media_kind: str,
) -> str:
    if mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(orig_filename)
    if guessed:
        return guessed
    if content_type:
        return content_type.split(";", 1)[0].strip()
    if media_kind == "jpeg":
        return "image/jpeg"
    if media_kind == "tiff":
        return "image/tiff"
    if media_kind == "png":
        return "image/png"
    if media_kind == "pdf":
        return "application/pdf"
    return "application/octet-stream"


def _is_retryable_upload_status(status_code: int) -> bool:
    """True for transient asset-server/proxy failures seen during bulk migration."""
    return status_code in {400, 408, 429, 502, 503, 504}


def _upload_failure_message(
    *,
    status_code: int | None,
    orig_filename: str,
    file_bytes: bytes,
    response_text: str,
    attempt: int,
    max_attempts: int,
    exc: Exception | None = None,
) -> str:
    size_mb = len(file_bytes) / (1024 * 1024)
    prefix = f"Asset server upload failed (attempt {attempt}/{max_attempts})"
    if status_code is not None:
        return (
            f"{prefix} ({status_code}) for {orig_filename} "
            f"({size_mb:.1f} MB): {response_text[:500]}"
        )
    return f"{prefix} for {orig_filename} ({size_mb:.1f} MB): {exc}"


def upload_original_to_asset_server(
    *,
    file_bytes: bytes,
    orig_filename: str,
    mime_type: str,
    collection_name: str,
    timeout_s: int = 600,
    max_attempts: int = UPLOAD_MAX_ATTEMPTS,
    retry_backoff_s: tuple[int, ...] = UPLOAD_RETRY_BACKOFF_S,
) -> str:
    """Upload bytes to the asset server and return ``attachmentlocation`` (stored filename)."""
    server_urls = _ensure_server_urls()
    write_url = server_urls["write"]
    resolved_mime = mime_type or "application/octet-stream"
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        attachment_location = make_attachment_filename(orig_filename)
        token = _generate_token(_get_timestamp(), attachment_location)
        try:
            response = requests.post(
                write_url,
                data={
                    "token": token,
                    "store": attachment_location,
                    "type": "O",
                    "coll": collection_name,
                },
                files={
                    "file": (orig_filename, file_bytes, resolved_mime),
                },
                timeout=timeout_s,
            )
        except requests.RequestException as exc:
            msg = _upload_failure_message(
                status_code=None,
                orig_filename=orig_filename,
                file_bytes=file_bytes,
                response_text="",
                attempt=attempt,
                max_attempts=max_attempts,
                exc=exc,
            )
            errors.append(msg)
            if attempt < max_attempts:
                delay = retry_backoff_s[min(attempt - 1, len(retry_backoff_s) - 1)]
                logger.warning(
                    "%s; retrying in %ss",
                    msg,
                    delay,
                )
                time.sleep(delay)
                continue
            raise AssetServerError(
                f"Asset server upload failed after {max_attempts} attempts for "
                f"{orig_filename}: " + "; ".join(errors)
            ) from exc

        _update_time_delta(response)
        if response.status_code == 200:
            return attachment_location

        msg = _upload_failure_message(
            status_code=response.status_code,
            orig_filename=orig_filename,
            file_bytes=file_bytes,
            response_text=response.text,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        errors.append(msg)
        if (
            attempt < max_attempts
            and _is_retryable_upload_status(response.status_code)
        ):
            delay = retry_backoff_s[min(attempt - 1, len(retry_backoff_s) - 1)]
            logger.warning(
                "%s; retrying in %ss",
                msg,
                delay,
            )
            time.sleep(delay)
            continue

        raise AssetServerError(
            f"Asset server upload failed after {attempt} attempt(s) for "
            f"{orig_filename}: " + "; ".join(errors)
        )

    raise AssetServerError(
        f"Asset server upload failed after {max_attempts} attempts for "
        f"{orig_filename}: " + "; ".join(errors)
    )


def migrate_unimus_original_to_asset_server(
    *,
    media_group_id: int,
    orig_filename: str,
    mime_type: str,
    collection_name: str,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Download the Unimus original and upload it to the asset server."""
    source = f"unimus id={media_group_id} type=orig"
    try:
        file_bytes, content_type = download_unimus_original(
            media_group_id, timeout_s=timeout_s
        )
    except requests.RequestException as exc:
        raise AssetServerError(
            f"Unimus download failed for media_group_id={media_group_id}: {exc}"
        ) from exc

    media_kind = validate_media_bytes(file_bytes, source=source)
    resolved_mime = _resolve_upload_mime(
        orig_filename, mime_type, content_type, media_kind
    )
    attachment_location = upload_original_to_asset_server(
        file_bytes=file_bytes,
        orig_filename=orig_filename,
        mime_type=resolved_mime,
        collection_name=collection_name,
        timeout_s=timeout_s,
    )
    return {
        "attachmentlocation": attachment_location,
        "bytes": len(file_bytes),
        "mime_type": resolved_mime,
        "media_kind": media_kind,
    }
