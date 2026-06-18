from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from functools import partial
from typing import Any

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class UbpetTimeDescription(TimeEntityDescription):
    config_list_key: str
    function_type: int
    boundary: str


TIME_ENTITIES: tuple[UbpetTimeDescription, ...] = (
    UbpetTimeDescription(
        key="do_not_disturb_start",
        name="Do not disturb start",
        icon="mdi:minus-circle-outline",
        translation_key="do_not_disturb_start",
        entity_category=EntityCategory.CONFIG,
        config_list_key="disturbTimeConfigList",
        function_type=1,
        boundary="start",
    ),
    UbpetTimeDescription(
        key="do_not_disturb_end",
        name="Do not disturb end",
        icon="mdi:minus-circle-outline",
        translation_key="do_not_disturb_end",
        entity_category=EntityCategory.CONFIG,
        config_list_key="disturbTimeConfigList",
        function_type=1,
        boundary="end",
    ),
    UbpetTimeDescription(
        key="light_schedule_start",
        name="Light schedule start",
        icon="mdi:lightbulb",
        translation_key="light_schedule_start",
        entity_category=EntityCategory.CONFIG,
        config_list_key="lightTimeConfigList",
        function_type=0,
        boundary="start",
    ),
    UbpetTimeDescription(
        key="light_schedule_end",
        name="Light schedule end",
        icon="mdi:lightbulb",
        translation_key="light_schedule_end",
        entity_category=EntityCategory.CONFIG,
        config_list_key="lightTimeConfigList",
        function_type=0,
        boundary="end",
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TimeEntity] = []
    for serial, item in coordinator.data.get("devices", {}).items():
        config = item.get("config", {})
        if _empty_waste_bin_reminder_config(config):
            entities.append(UbpetEmptyWasteBinReminderTime(coordinator, entry.entry_id, serial))
        for description in TIME_ENTITIES:
            if _first_period_config(config, description.config_list_key):
                entities.append(UbpetPeriodTime(coordinator, entry.entry_id, serial, description))
    async_add_entities(entities)


class UbpetPeriodTime(CoordinatorEntity[UbpetDataUpdateCoordinator], TimeEntity):
    entity_description: UbpetTimeDescription

    def __init__(
        self,
        coordinator: UbpetDataUpdateCoordinator,
        entry_id: str,
        serial: str,
        description: UbpetTimeDescription,
    ) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_{serial}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        item = self.coordinator.data.get("devices", {}).get(self._serial, {})
        device = item.get("device", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            manufacturer="Airrobo / UBT",
            name=device.get("deviceName") or self._serial,
            serial_number=self._serial,
        )

    @property
    def native_value(self) -> time | None:
        config = self._period_config
        if not config:
            return None
        hour_key = f"{self.entity_description.boundary}TimeHour"
        minute_key = f"{self.entity_description.boundary}TimeMinute"
        try:
            return time(int(config.get(hour_key, 0)), int(config.get(minute_key, 0)))
        except (TypeError, ValueError):
            return None

    async def async_set_value(self, value: time) -> None:
        config = self._period_config
        if not config:
            return
        config_id = _period_config_id(config, self.entity_description.config_list_key)
        if config_id is None:
            return

        start_hour = config.get("startTimeHour", 0)
        start_minute = config.get("startTimeMinute", 0)
        end_hour = config.get("endTimeHour", 0)
        end_minute = config.get("endTimeMinute", 0)
        if self.entity_description.boundary == "start":
            start_hour = value.hour
            start_minute = value.minute
        else:
            end_hour = value.hour
            end_minute = value.minute

        await self.hass.async_add_executor_job(
            partial(
                self.coordinator.client.set_time_period,
                self._serial,
                config_id=config_id,
                function_type=self.entity_description.function_type,
                open_switch=config.get("openSwitch", 1),
                start_hour=start_hour,
                start_minute=start_minute,
                end_hour=end_hour,
                end_minute=end_minute,
            )
        )
        await self.coordinator.async_request_refresh()

    @property
    def _period_config(self) -> dict[str, Any] | None:
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        if not item:
            return None
        return _first_period_config(item.get("config", {}), self.entity_description.config_list_key)


EMPTY_WASTE_BIN_REMINDER_TIME = TimeEntityDescription(
    key="empty_waste_bin_reminder_time",
    name="Empty waste bin reminder time",
    icon="mdi:bell-ring-outline",
    translation_key="empty_waste_bin_reminder_time",
    entity_category=EntityCategory.CONFIG,
)


class UbpetEmptyWasteBinReminderTime(CoordinatorEntity[UbpetDataUpdateCoordinator], TimeEntity):
    entity_description = EMPTY_WASTE_BIN_REMINDER_TIME

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{entry_id}_{serial}_empty_waste_bin_reminder_time"

    @property
    def device_info(self) -> DeviceInfo:
        item = self.coordinator.data.get("devices", {}).get(self._serial, {})
        device = item.get("device", {})
        return DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            manufacturer="Airrobo / UBT",
            name=device.get("deviceName") or self._serial,
            serial_number=self._serial,
        )

    @property
    def native_value(self) -> time | None:
        config = self._reminder_config
        if not config:
            return None
        try:
            return time(int(config.get("timeHour", 0)), int(config.get("timeMinute", 0)))
        except (TypeError, ValueError):
            return None

    async def async_set_value(self, value: time) -> None:
        config = self._reminder_config
        if not config:
            return
        await self.hass.async_add_executor_job(
            partial(
                self.coordinator.client.set_time_point,
                self._serial,
                config_id=config["notifyConfigId"],
                function_type=1,
                open_switch=config.get("openSwitch", 1),
                time_hour=value.hour,
                time_minute=value.minute,
                option_int=config.get("period", 0),
            )
        )
        await self.coordinator.async_request_refresh()

    @property
    def _reminder_config(self) -> dict[str, Any] | None:
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        if not item:
            return None
        return _empty_waste_bin_reminder_config(item.get("config", {}))


def _first_period_config(config: dict[str, Any], key: str) -> dict[str, Any] | None:
    periods = config.get(key)
    if not isinstance(periods, list) or not periods:
        return None
    return periods[0] if isinstance(periods[0], dict) else None


def _empty_waste_bin_reminder_config(config: dict[str, Any]) -> dict[str, Any] | None:
    for item in config.get("notifyConfigList", []):
        if isinstance(item, dict) and item.get("type") == 4 and item.get("notifyConfigId") is not None:
            return item
    return None


def _period_config_id(config: dict[str, Any], list_key: str) -> int | None:
    for key in ("configId", "disturbTimeConfigId", "lightTimeConfigId"):
        value = config.get(key)
        if value is not None:
            return value
    if list_key == "disturbTimeConfigList":
        return config.get("disturbTimeConfigId")
    if list_key == "lightTimeConfigList":
        return config.get("lightTimeConfigId")
    return None
