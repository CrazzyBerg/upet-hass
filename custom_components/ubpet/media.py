"""Visit history and video playback for UPET litter boxes.

- ``ubpet/records`` websocket command: recent cat visits (paged).
- ``/api/ubpet/video/<serial>/<record_id>``: the visit video as MP4. The vendor
  AVI is fetched with a fresh signed URL, converted with ffmpeg and kept in a
  temporary folder until it has not been watched for ``VIDEO_TTL``.
- The same videos appear in the Home Assistant media browser (media_source.py).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from http import HTTPStatus
import logging
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .video import (
    CAT_RECORDS,
    avi_frame_rate,
    download_video,
    image_type,
    is_allowed_video_url,
    mjpeg_frames,
    signed_url_is_fresh,
    summarize_record,
)

_LOGGER = logging.getLogger(__name__)

DATA_MEDIA = f"{DOMAIN}_media"
VIDEO_TTL = timedelta(minutes=15)
CLEANUP_INTERVAL = timedelta(minutes=5)
RECORDS_PAGE_SIZE = 30
RECORDS_CACHE_SECONDS = 60
FIND_MAX_PAGES = 10
VIDEO_URL = "/api/ubpet/video/{serial}/{record_id}"
PHOTO_URL = "/api/ubpet/cat_photo/{cat_id}"
PHOTO_CACHE_SECONDS = 3000
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def video_path(serial: str, record_id: int | str) -> str:
    return VIDEO_URL.format(serial=serial, record_id=record_id)


class UbpetMedia:
    """Shared record cache and MP4 cache for all UPET entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.video_dir = Path(tempfile.gettempdir()) / "ubpet_videos"
        self._records: dict[tuple[str, int], dict[str, Any]] = {}
        self._pages: dict[tuple[str, int, int, str], tuple[float, list[dict[str, Any]]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._photos: dict[int, tuple[float, bytes, str]] = {}

    # ------------------------------------------------------------ helpers
    def coordinator_for(self, serial: str | None):
        coordinators = list(self.hass.data.get(DOMAIN, {}).values())
        for coordinator in coordinators:
            devices = (coordinator.data or {}).get("devices", {})
            if serial is None and devices:
                return coordinator, next(iter(devices))
            if serial in devices:
                return coordinator, serial
        return None, None

    async def async_records(self, serial: str | None, size: int, before: int | None) -> dict[str, Any]:
        coordinator, serial = self.coordinator_for(serial)
        if coordinator is None:
            raise LookupError("no UPET litter box")
        records = await self._async_page(coordinator, serial, size, before or 0)
        visits = [summarize_record(r) for r in records]
        return {
            "serial": serial,
            "records": visits,
            "next_before": records[-1].get("eventTime") if len(records) >= size else None,
        }

    async def _async_page(self, coordinator, serial: str, size: int, before: int) -> list[dict[str, Any]]:
        key = (serial, CAT_RECORDS, size, str(before))
        cached = self._pages.get(key)
        if cached and time.monotonic() - cached[0] < RECORDS_CACHE_SECONDS:
            return cached[1]
        records = await self.hass.async_add_executor_job(
            coordinator.client.get_box_records, serial, CAT_RECORDS, size, before
        )
        self._pages[key] = (time.monotonic(), records)
        if len(self._pages) > 50:
            self._pages.pop(next(iter(self._pages)))
        for record in records:
            if isinstance(record.get("recordId"), int):
                self._records[(serial, record["recordId"])] = record
        return records

    async def async_find_record(self, serial: str, record_id: int) -> dict[str, Any] | None:
        record = self._records.get((serial, record_id))
        if record and signed_url_is_fresh(record.get("videoPath") or ""):
            return record
        coordinator, serial = self.coordinator_for(serial)
        if coordinator is None:
            return None
        self._pages.clear()  # need fresh signed URLs
        before = 0
        for _ in range(FIND_MAX_PAGES):
            records = await self._async_page(coordinator, serial, 50, before)
            for record in records:
                if record.get("recordId") == record_id:
                    return record
            if len(records) < 50:
                break
            before = records[-1].get("eventTime") or 0
        return None

    # ------------------------------------------------------------ cats
    def cats(self) -> list[tuple[Any, dict[str, Any]]]:
        out = []
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            for cat in (coordinator.data or {}).get("cats") or []:
                if isinstance(cat, dict) and cat.get("catInfoId"):
                    out.append((coordinator, cat))
        return out

    async def async_cat_photo(self, cat_id: int) -> tuple[bytes, str]:
        cached = self._photos.get(cat_id)
        if cached and time.monotonic() - cached[0] < PHOTO_CACHE_SECONDS:
            return cached[1], cached[2]
        for coordinator, cat in self.cats():
            if cat.get("catInfoId") != cat_id or not is_allowed_video_url(cat.get("icon")):
                continue
            signed = await self.hass.async_add_executor_job(coordinator.client.sign_private_url, cat["icon"])
            data = await self.hass.async_add_executor_job(
                lambda: download_video(signed, timeout=20, max_bytes=MAX_PHOTO_BYTES)
            )
            mime = image_type(data)
            if mime is None:
                raise ValueError("not an image")
            self._photos[cat_id] = (time.monotonic(), data, mime)
            return data, mime
        raise FileNotFoundError("cat has no photo")

    async def async_assign(self, serial: str, record_id: int, cat_id: int) -> dict[str, Any]:
        coordinator, serial = self.coordinator_for(serial)
        if coordinator is None:
            raise LookupError("no UPET litter box")
        if not any(c.get("catInfoId") == cat_id for _, c in self.cats()):
            raise ValueError("unknown cat")
        record = await self.async_find_record(serial, record_id)
        if record is None:
            raise FileNotFoundError("record not found")
        replace = bool(record.get("catInfoId"))
        await self.hass.async_add_executor_job(
            lambda: coordinator.client.set_record_cat(record_id, cat_id, replace=replace)
        )
        name = next((c.get("nickname") for _, c in self.cats() if c.get("catInfoId") == cat_id), None)
        record["catInfoId"] = cat_id
        record["catNickname"] = name
        self._pages.clear()
        self.hass.async_create_task(coordinator.async_request_refresh())
        return summarize_record(record)

    # ------------------------------------------------------------ videos
    async def async_video_file(self, serial: str, record_id: int) -> Path:
        path = self.video_dir / f"{serial}_{record_id}.mp4"
        lock = self._locks.setdefault(path.name, asyncio.Lock())
        async with lock:
            if path.exists():
                await self.hass.async_add_executor_job(path.touch)
                return path
            record = await self.async_find_record(serial, record_id)
            if record is None:
                raise FileNotFoundError("record not found")
            url = record.get("videoPath") or ""
            data = await self.hass.async_add_executor_job(download_video, url)
            frames = mjpeg_frames(data)
            if not frames:
                raise ValueError("no frames in video")
            await self._async_encode(frames, avi_frame_rate(data), path)
            _LOGGER.debug("Converted UPET video %s (%s frames)", path.name, len(frames))
        self._locks.pop(path.name, None)
        return path

    async def _async_encode(self, frames: list[bytes], fps: float, path: Path) -> None:
        binary = _ffmpeg_binary(self.hass)
        await self.hass.async_add_executor_job(lambda: self.video_dir.mkdir(parents=True, exist_ok=True))
        tmp = path.with_suffix(".part.mp4")
        process = await asyncio.create_subprocess_exec(
            binary, "-hide_banner", "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-y", str(tmp),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(b"".join(frames)), timeout=120)
        except TimeoutError:
            process.kill()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[:300]}")
        await self.hass.async_add_executor_job(tmp.replace, path)

    @callback
    def async_cleanup(self, *_: Any) -> None:
        self.hass.async_add_executor_job(self._cleanup, VIDEO_TTL.total_seconds())

    def _cleanup(self, max_age: float) -> None:
        if not self.video_dir.exists():
            return
        cutoff = time.time() - max_age
        for file in self.video_dir.iterdir():
            try:
                if max_age <= 0 or file.stat().st_mtime < cutoff:
                    file.unlink()
            except OSError:
                pass

    def purge(self) -> None:
        shutil.rmtree(self.video_dir, ignore_errors=True)


def _ffmpeg_binary(hass: HomeAssistant) -> str:
    try:
        from homeassistant.components.ffmpeg import get_ffmpeg_manager

        return get_ffmpeg_manager(hass).binary
    except Exception:  # noqa: BLE001 - ffmpeg integration not loaded
        return shutil.which("ffmpeg") or "ffmpeg"


# ---------------------------------------------------------------- setup
async def async_setup_media(hass: HomeAssistant) -> UbpetMedia:
    """Register the view, websocket commands and cleanup once."""
    if (media := hass.data.get(DATA_MEDIA)) is not None:
        return media
    media = UbpetMedia(hass)
    hass.data[DATA_MEDIA] = media
    await hass.async_add_executor_job(media.purge)
    hass.http.register_view(UbpetVideoView(media))
    hass.http.register_view(UbpetCatPhotoView(media))
    websocket_api.async_register_command(hass, ws_records)
    websocket_api.async_register_command(hass, ws_video)
    websocket_api.async_register_command(hass, ws_cats)
    websocket_api.async_register_command(hass, ws_assign)
    async_track_time_interval(hass, media.async_cleanup, CLEANUP_INTERVAL)
    return media


class UbpetVideoView(HomeAssistantView):
    """Serve a visit video as MP4 (auth or signed path required)."""

    url = "/api/ubpet/video/{serial}/{record_id}"
    name = "api:ubpet:video"
    requires_auth = True

    def __init__(self, media: UbpetMedia) -> None:
        self.media = media

    async def get(self, request: web.Request, serial: str, record_id: str) -> web.StreamResponse:
        try:
            rid = int(record_id.removesuffix(".mp4"))
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        try:
            path = await self.media.async_video_file(serial, rid)
        except FileNotFoundError:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("UPET video %s/%s failed: %s", serial, rid, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        return web.FileResponse(
            path, headers={"Content-Type": "video/mp4", "Cache-Control": "private, max-age=600"}
        )


class UbpetCatPhotoView(HomeAssistantView):
    """Serve a cat's profile photo (auth or signed path required)."""

    url = "/api/ubpet/cat_photo/{cat_id}"
    name = "api:ubpet:cat_photo"
    requires_auth = True

    def __init__(self, media: UbpetMedia) -> None:
        self.media = media

    async def get(self, request: web.Request, cat_id: str) -> web.StreamResponse:
        try:
            cid = int(cat_id)
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        try:
            data, mime = await self.media.async_cat_photo(cid)
        except FileNotFoundError:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("UPET cat photo %s failed: %s", cid, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        return web.Response(body=data, content_type=mime, headers={"Cache-Control": "private, max-age=3000"})


@websocket_api.websocket_command({vol.Required("type"): "ubpet/cats"})
@callback
def ws_cats(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Cats with a signed photo path for <img> elements."""
    media: UbpetMedia = hass.data[DATA_MEDIA]
    cats = []
    for _, cat in media.cats():
        cid = cat["catInfoId"]
        photo = None
        if is_allowed_video_url(cat.get("icon")):
            photo = async_sign_path(
                hass, PHOTO_URL.format(cat_id=cid), timedelta(hours=12), refresh_token_id=connection.refresh_token_id
            )
        cats.append({"id": cid, "name": cat.get("nickname"), "photo": photo})
    connection.send_result(msg["id"], cats)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ubpet/assign",
        vol.Required("serial"): str,
        vol.Required("record_id"): int,
        vol.Required("cat_id"): int,
    }
)
@websocket_api.async_response
async def ws_assign(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Change which cat a visit belongs to."""
    media: UbpetMedia = hass.data[DATA_MEDIA]
    try:
        record = await media.async_assign(msg["serial"], msg["record_id"], msg["cat_id"])
    except (LookupError, FileNotFoundError, ValueError) as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], record)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "ubpet/records",
        vol.Optional("serial"): str,
        vol.Optional("size", default=RECORDS_PAGE_SIZE): vol.All(int, vol.Range(min=1, max=50)),
        vol.Optional("before"): vol.Any(None, int),
    }
)
@websocket_api.async_response
async def ws_records(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    media: UbpetMedia = hass.data[DATA_MEDIA]
    try:
        result = await media.async_records(msg.get("serial"), msg["size"], msg.get("before"))
    except LookupError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): "ubpet/video", vol.Required("serial"): str, vol.Required("record_id"): int}
)
@websocket_api.async_response
async def ws_video(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Prepare a video and return a signed URL for a <video> element."""
    media: UbpetMedia = hass.data[DATA_MEDIA]
    try:
        await media.async_video_file(msg["serial"], msg["record_id"])
    except FileNotFoundError:
        connection.send_error(msg["id"], "not_found", "video not found")
        return
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "failed", str(err))
        return
    url = async_sign_path(
        hass,
        video_path(msg["serial"], msg["record_id"]),
        VIDEO_TTL,
        refresh_token_id=connection.refresh_token_id,
    )
    connection.send_result(msg["id"], {"url": url})
