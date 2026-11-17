"""Dependency-free helpers for UPET visit records and their videos.

The litter box camera uploads each visit as a Motion-JPEG AVI (240x240, ~5 fps)
whose audio stream header is broken (sample rate 0), so ffmpeg cannot open the
file directly. We pull the JPEG frames out of the AVI ourselves and let ffmpeg
encode them to H.264/MP4, which every browser and the Home Assistant apps play.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse
import urllib.request

ALLOWED_VIDEO_HOST_SUFFIXES = (".aliyuncs.com",)
MAX_VIDEO_BYTES = 30 * 1024 * 1024
DEFAULT_FPS = 5.0
CAT_RECORDS = 0
DEVICE_RECORDS = 1


def is_allowed_video_url(url: Any) -> bool:
    """Only fetch HTTPS URLs on the vendor's object storage."""
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host.endswith(suffix) for suffix in ALLOWED_VIDEO_HOST_SUFFIXES)


def signed_url_expires_at(url: str) -> float | None:
    """Return the Unix time an OSS signed URL expires, if it says."""
    try:
        values = parse_qs(urlparse(url).query).get("Expires")
        return float(values[0]) if values else None
    except (TypeError, ValueError):
        return None


def signed_url_is_fresh(url: str, *, margin_seconds: float = 300, now: float | None = None) -> bool:
    expires_at = signed_url_expires_at(url)
    if expires_at is None:
        return True
    return expires_at - (time.time() if now is None else now) > margin_seconds


def download_video(url: str, *, timeout: float = 60, max_bytes: int = MAX_VIDEO_BYTES) -> bytes:
    if not is_allowed_video_url(url):
        raise ValueError("URL is not on the vendor storage")
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - host checked above
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("file is too large")
    return data


def image_type(data: bytes) -> str | None:
    """MIME type of a JPEG/PNG/WebP image, None for anything else."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def avi_frame_rate(data: bytes) -> float:
    """Frame rate from the AVI main header (dwMicroSecPerFrame)."""
    index = data.find(b"avih")
    if index < 0 or len(data) < index + 12:
        return DEFAULT_FPS
    micro = int.from_bytes(data[index + 8 : index + 12], "little")
    if 10_000 <= micro <= 2_000_000:
        return round(1_000_000 / micro, 3)
    return DEFAULT_FPS


def mjpeg_frames(data: bytes) -> list[bytes]:
    """Extract the JPEG frames of a Motion-JPEG AVI."""
    frames = _frames_from_movi(data)
    if frames:
        return frames
    # Fallback: scan for JPEG start/end markers.
    frames, index = [], 0
    while True:
        start = data.find(b"\xff\xd8\xff", index)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start)
        if end < 0:
            break
        frames.append(data[start : end + 2])
        index = end + 2
    return frames


def _frames_from_movi(data: bytes) -> list[bytes]:
    movi = data.find(b"movi")
    if movi < 0:
        return []
    frames: list[bytes] = []
    pos = movi + 4
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = data[pos + 8 : pos + 8 + size]
        if chunk_id == b"LIST":
            pos += 12  # descend into rec lists
            continue
        if chunk_id == b"idx1":
            break
        if chunk_id[2:4] in (b"dc", b"db") and body.startswith(b"\xff\xd8"):
            frames.append(body)
        if size <= 0 and chunk_id[2:4] not in (b"wb",):
            pos += 8
        else:
            pos += 8 + size + (size & 1)
    return frames


def summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """The part of a vendor record the frontend needs (no signed URLs)."""
    return {
        "id": record.get("recordId"),
        "cat_id": record.get("catInfoId") or None,
        "cat": record.get("catNickname"),
        "time": record.get("eventTime"),
        "weight": record.get("weight"),
        "duration": record.get("costTime"),
        "event_type": record.get("eventType"),
        "confirmed": record.get("confirmed"),
        "video": is_allowed_video_url(record.get("videoPath")),
    }
