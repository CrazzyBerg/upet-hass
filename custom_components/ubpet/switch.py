from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class UbpetSwitchDescription(SwitchEntityDescription):
    config_list_key: str | None = None
    function_type: int | None = None


DEODORANT_ALERT = UbpetSwitchDescription(
    key="deodorant_alert",
    name="Deodorant alert",
    icon="mdi:air-filter",
    translation_key="deodorant_alert",
    entity_category=EntityCategory.CONFIG,
)

AUTO_CLEAN = UbpetSwitchDescription(
    key="auto_clean",
    name="Auto clean",
    icon="mdi:broom",
    translation_key="auto_clean",
    entity_category=EntityCategory.CONFIG,
)

EMPTY_WASTE_BIN_REMINDER = UbpetSwitchDescription(
    key="empty_waste_bin_reminder",
    name="Empty waste bin reminder",
    icon="mdi:bell-ring-outline",
    translation_key="empty_waste_bin_reminder",
    entity_category=EntityCategory.CONFIG,
)

PERIOD_SWITCHES: tuple[UbpetSwitchDescription, ...] = (
    UbpetSwitchDescription(
        key="do_not_disturb",
        name="Do not disturb",
        icon="mdi:minus-circle-outline",
        translation_key="do_not_disturb",
        entity_category=EntityCategory.CONFIG,
        config_list_key="disturbTimeConfigList",
        function_type=1,
    ),
    UbpetSwitchDescription(
        key="light_schedule",
        name="Light schedule",
        icon="mdi:lightbulb",
        translation_key="light_schedule",
        entity_category=EntityCategory.CONFIG,
        config_list_key="lightTimeConfigList",
        function_type=0,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for serial, item in coordinator.data.get("devices", {}).items():
        entities.append(UbpetAutoCleanSwitch(coordinator, entry.entry_id, serial))
        entities.append(UbpetDeodorantAlertSwitch(coordinator, entry.entry_id, serial))
        config = item.get("config", {})
        if _empty_waste_bin_reminder_config(config):
            entities.append(UbpetEmptyWasteBinReminderSwitch(coordinator, entry.entry_id, serial))
        for description in PERIOD_SWITCHES:
            if _first_period_config(config, description.config_list_key):
                entities.append(UbpetPeriodSwitch(coordinator, entry.entry_id, serial, description))
    async_add_entities(entities)


class UbpetBaseSwitch(CoordinatorEntity[UbpetDataUpdateCoordinator], SwitchEntity):
    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str, key: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{entry_id}_{serial}_{key}"

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
    def _device_item(self) -> dict[str, Any] | None:
        return self.coordinator.data.get("devices", {}).get(self._serial)


class UbpetDeodorantAlertSwitch(UbpetBaseSwitch):
    entity_description = DEODORANT_ALERT

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator, entry_id, serial, DEODORANT_ALERT.key)

    @property
    def is_on(self) -> bool | None:
        item = self._device_item
        if not item:
            return None
        value = item.get("deodorant", {}).get("alertSwitch")
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_deodorant_alert(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_deodorant_alert(False)

    async def _set_deodorant_alert(self, enabled: bool) -> None:
        await self.hass.async_add_executor_job(self.coordinator.client.set_deodorant_alert, self._serial, enabled)
        await self.coordinator.async_request_refresh()


class UbpetAutoCleanSwitch(UbpetBaseSwitch):
    entity_description = AUTO_CLEAN

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator, entry_id, serial, AUTO_CLEAN.key)

    @property
    def is_on(self) -> bool | None:
        item = self._device_item
        if not item:
            return None
        value = item.get("config", {}).get("sensorSwitch")
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_auto_clean(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_auto_clean(False)

    async def _set_auto_clean(self, enabled: bool) -> None:
        item = self._device_item or {}
        lazy_time = item.get("config", {}).get("lazyTime")
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_auto_clean_enabled,
            self._serial,
            enabled,
            lazy_time,
        )
        await self.coordinator.async_request_refresh()


class UbpetEmptyWasteBinReminderSwitch(UbpetBaseSwitch):
    entity_description = EMPTY_WASTE_BIN_REMINDER

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator, entry_id, serial, EMPTY_WASTE_BIN_REMINDER.key)

    @property
    def is_on(self) -> bool | None:
        config = self._reminder_config
        if not config:
            return None
        value = config.get("openSwitch")
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_reminder_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_reminder_enabled(False)

    async def _set_reminder_enabled(self, enabled: bool) -> None:
        config = self._reminder_config
        if not config:
            return
        await self.hass.async_add_executor_job(
            partial(
                self.coordinator.client.set_time_point,
                self._serial,
                config_id=config["notifyConfigId"],
                function_type=1,
                open_switch=1 if enabled else 0,
                time_hour=config.get("timeHour", 0),
                time_minute=config.get("timeMinute", 0),
                option_int=config.get("period", 0),
            )
        )
        await self.coordinator.async_request_refresh()

    @property
    def _reminder_config(self) -> dict[str, Any] | None:
        item = self._device_item
        if not item:
            return None
        return _empty_waste_bin_reminder_config(item.get("config", {}))


class UbpetPeriodSwitch(UbpetBaseSwitch):
    entity_description: UbpetSwitchDescription

    def __init__(
        self,
        coordinator: UbpetDataUpdateCoordinator,
        entry_id: str,
        serial: str,
        description: UbpetSwitchDescription,
    ) -> None:
        super().__init__(coordinator, entry_id, serial, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        config = self._period_config
        if not config:
            return None
        value = config.get("openSwitch")
        return None if value is None else value == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set_period_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set_period_enabled(False)

    async def _set_period_enabled(self, enabled: bool) -> None:
        config = self._period_config
        if not config:
            return
        config_id = _period_config_id(config, self.entity_description.config_list_key)
        if config_id is None:
            return
        await self.hass.async_add_executor_job(
            partial(
                self.coordinator.client.set_time_period,
                self._serial,
                config_id=config_id,
                function_type=self.entity_description.function_type,
                open_switch=1 if enabled else 0,
                start_hour=config.get("startTimeHour", 0),
                start_minute=config.get("startTimeMinute", 0),
                end_hour=config.get("endTimeHour", 0),
                end_minute=config.get("endTimeMinute", 0),
            )
        )
        await self.coordinator.async_request_refresh()

    @property
    def _period_config(self) -> dict[str, Any] | None:
        item = self._device_item
        if not item:
            return None
        return _first_period_config(item.get("config", {}), self.entity_description.config_list_key)


def _first_period_config(config: dict[str, Any], key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    periods = config.get(key)
    if not isinstance(periods, list) or not periods:
        return None
    return periods[0] if isinstance(periods[0], dict) else None


def _empty_waste_bin_reminder_config(config: dict[str, Any]) -> dict[str, Any] | None:
    for item in config.get("notifyConfigList", []):
        if isinstance(item, dict) and item.get("type") == 4 and item.get("notifyConfigId") is not None:
            return item
    return None


def _period_config_id(config: dict[str, Any], list_key: str | None) -> int | None:
    for key in ("configId", "disturbTimeConfigId", "lightTimeConfigId"):
        value = config.get(key)
        if value is not None:
            return value
    if list_key == "disturbTimeConfigList":
        return config.get("disturbTimeConfigId")
    if list_key == "lightTimeConfigList":
        return config.get("lightTimeConfigId")
    return None
