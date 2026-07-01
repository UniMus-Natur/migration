"""Download MUSIT/Unimus media and upload originals to the Specify asset server."""

from __future__ import annotations

import logging
import mimetypes
import time
from os.path import splitext
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

import hmac
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

UNIMUS_ORIGINAL_URL = (
    "https://www.unimus.no/felles/bilder/web_hent_bilde.php?id={media_group_id}&type=orig"
)

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

    _server_urls = parsed
    return parsed


def reset_asset_server_cache() -> None:
    """Clear cached server URLs (for tests)."""
    global _server_urls, _server_time_delta
    _server_urls = None
    _server_time_delta = 0


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


def upload_original_to_asset_server(
    *,
    file_bytes: bytes,
    orig_filename: str,
    mime_type: str,
    collection_name: str,
    timeout_s: int = 600,
) -> str:
    """Upload bytes to the asset server and return ``attachmentlocation`` (stored filename)."""
    server_urls = _ensure_server_urls()
    attachment_location = make_attachment_filename(orig_filename)
    token = _generate_token(_get_timestamp(), attachment_location)

    response = requests.post(
        server_urls["write"],
        data={
            "token": token,
            "store": attachment_location,
            "type": "O",
            "coll": collection_name,
        },
        files={
            "file": (orig_filename, file_bytes, mime_type or "application/octet-stream"),
        },
        timeout=timeout_s,
    )
    _update_time_delta(response)
    if response.status_code != 200:
        raise AssetServerError(
            f"Asset server upload failed ({response.status_code}): {response.text[:500]}"
        )
    return attachment_location


def migrate_unimus_original_to_asset_server(
    *,
    media_group_id: int,
    orig_filename: str,
    mime_type: str,
    collection_name: str,
    timeout_s: int = 600,
) -> dict[str, Any]:
    """Download from Unimus and upload to the asset server."""
    file_bytes, content_type = download_unimus_original(media_group_id, timeout_s=timeout_s)
    if not mime_type:
        guessed, _ = mimetypes.guess_type(orig_filename)
        mime_type = guessed or (content_type.split(";", 1)[0].strip() if content_type else "application/octet-stream")

    attachment_location = upload_original_to_asset_server(
        file_bytes=file_bytes,
        orig_filename=orig_filename,
        mime_type=mime_type,
        collection_name=collection_name,
        timeout_s=timeout_s,
    )
    return {
        "attachmentlocation": attachment_location,
        "bytes": len(file_bytes),
        "mime_type": mime_type,
    }
