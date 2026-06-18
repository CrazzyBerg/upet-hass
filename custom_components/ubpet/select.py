from __future__ import annotations

from functools import partial
from itertools import combinations
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator

WEEKDAYS: tuple[tuple[int, str], ...] = (
    (1, "Monday"),
    (2, "Tuesday"),
    (4, "Wednesday"),
    (8, "Thursday"),
    (16, "Friday"),
    (32, "Saturday"),
    (64, "Sunday"),
)


def _build_day_options() -> dict[int, str]:
    options = {0: "Off", 127: "Every day"}
    values = list(WEEKDAYS)
    for count in range(1, len(values)):
        for combo in combinations(values, count):
            bitmask = sum(bit for bit, _name in combo)
            options[bitmask] = ", ".join(name for _bit, name in combo)
    return dict(sorted(options.items(), key=lambda item: (bin(item[0]).count("1"), item[0])))


OPTIONS_BY_VALUE = _build_day_options()
VALUES_BY_OPTION = {label: value for value, label in OPTIONS_BY_VALUE.items()}

EMPTY_WASTE_BIN_REMINDER_DAYS = SelectEntityDescription(
    key="empty_waste_bin_reminder_days_select",
    name="Empty waste bin reminder days",
    icon="mdi:calendar-week",
    translation_key="empty_waste_bin_reminder_days_select",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for serial, item in coordinator.data.get("devices", {}).items():
        if _empty_waste_bin_reminder_config(item.get("config", {})):
            entities.append(UbpetEmptyWasteBinReminderDaysSelect(coordinator, entry.entry_id, serial))
    async_add_entities(entities)


class UbpetEmptyWasteBinReminderDaysSelect(CoordinatorEntity[UbpetDataUpdateCoordinator], SelectEntity):
    entity_description = EMPTY_WASTE_BIN_REMINDER_DAYS
    _attr_options = list(VALUES_BY_OPTION)

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{entry_id}_{serial}_empty_waste_bin_reminder_days_select"

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
    def current_option(self) -> str | None:
        config = self._reminder_config
        if not config:
            return None
        period = _period_value(config)
        return OPTIONS_BY_VALUE.get(period) or _days_label(period)

    async def async_select_option(self, option: str) -> None:
        config = self._reminder_config
        if not config:
            return
        period = VALUES_BY_OPTION.get(option)
        if period is None:
            return
        await self.hass.async_add_executor_job(
            partial(
                self.coordinator.client.set_time_point,
                self._serial,
                config_id=config["notifyConfigId"],
                function_type=1,
                open_switch=config.get("openSwitch", 1),
                time_hour=config.get("timeHour", 0),
                time_minute=config.get("timeMinute", 0),
                option_int=period,
            )
        )
        await self.coordinator.async_request_refresh()

    @property
    def _reminder_config(self) -> dict[str, Any] | None:
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        if not item:
            return None
        return _empty_waste_bin_reminder_config(item.get("config", {}))


def _days_label(period: int) -> str:
    if period == 0:
        return "Off"
    if period == 127:
        return "Every day"
    return ", ".join(name for bit, name in WEEKDAYS if period & bit)


def _period_value(config: dict[str, Any]) -> int:
    try:
        return int(config.get("period") or 0)
    except (TypeError, ValueError):
        return 0


def _empty_waste_bin_reminder_config(config: dict[str, Any]) -> dict[str, Any] | None:
    for item in config.get("notifyConfigList", []):
        if isinstance(item, dict) and item.get("type") == 4 and item.get("notifyConfigId") is not None:
            return item
    return None
