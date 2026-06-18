from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator

AUTO_CLEAN_DELAY = NumberEntityDescription(
    key="auto_clean_delay",
    name="Auto clean delay",
    icon="mdi:broom",
    translation_key="auto_clean_delay",
    native_min_value=1,
    native_max_value=60,
    native_step=1,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    entity_category=EntityCategory.CONFIG,
    mode=NumberMode.SLIDER,
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for serial in coordinator.data.get("devices", {}):
        entities.append(UbpetAutoCleanDelayNumber(coordinator, entry.entry_id, serial))
    async_add_entities(entities)


class UbpetAutoCleanDelayNumber(CoordinatorEntity[UbpetDataUpdateCoordinator], NumberEntity):
    entity_description = AUTO_CLEAN_DELAY

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{entry_id}_{serial}_auto_clean_delay"

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
    def native_value(self) -> int | float | None:
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        if not item:
            return None
        return _seconds_to_minutes(item.get("config", {}).get("lazyTime"))

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(round(value))
        await self.hass.async_add_executor_job(
            self.coordinator.client.set_auto_clean_delay,
            self._serial,
            minutes,
        )
        await self.coordinator.async_request_refresh()


def _seconds_to_minutes(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    minutes = seconds / 60
    return int(minutes) if minutes.is_integer() else minutes
