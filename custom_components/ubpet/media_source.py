"""Expose UPET visit videos in the Home Assistant media browser."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .media import DATA_MEDIA, UbpetMedia, async_setup_media, video_path

BROWSE_LIMIT = 50


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    await async_setup_media(hass)
    return UbpetMediaSource(hass)


class UbpetMediaSource(MediaSource):
    name = "UPET kedi tuvaleti"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    @property
    def _media(self) -> UbpetMedia:
        return self.hass.data[DATA_MEDIA]

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        try:
            serial, record_id = item.identifier.split("/", 1)
            rid = int(record_id)
        except (AttributeError, ValueError) as err:
            raise Unresolvable(f"Unknown UPET media {item.identifier}") from err
        return PlayMedia(video_path(serial, rid), "video/mp4")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        if not item.identifier:
            return self._root()
        serial = item.identifier.split("/", 1)[0]
        result = await self._media.async_records(serial, BROWSE_LIMIT, None)
        children = []
        for visit in result["records"]:
            if not visit["video"]:
                continue
            when = datetime.fromtimestamp((visit["time"] or 0) / 1000).strftime("%d.%m %H:%M")
            parts = [visit["cat"] or "Bilinmeyen", when]
            if visit.get("weight"):
                parts.append(f"{visit['weight']:.1f} kg".replace(".", ","))
            if visit.get("duration"):
                parts.append(f"{visit['duration']} sn")
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{serial}/{visit['id']}",
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=" · ".join(parts),
                    can_play=True,
                    can_expand=False,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=serial,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=self._device_name(serial),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.VIDEO,
        )

    def _device_name(self, serial: str) -> str:
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            item = ((coordinator.data or {}).get("devices") or {}).get(serial)
            if item:
                device = item.get("device") or {}
                return str(device.get("deviceName") or device.get("name") or serial)
        return serial

    def _root(self) -> BrowseMediaSource:
        children = []
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            for serial, item in ((coordinator.data or {}).get("devices") or {}).items():
                name = self._device_name(serial)
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=serial,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="",
                        title=str(name),
                        can_play=False,
                        can_expand=True,
                    )
                )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=self.name,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )
