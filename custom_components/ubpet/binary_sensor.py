from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator


@dataclass(frozen=True, kw_only=True)
class UbpetBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


BINARY_SENSORS: tuple[UbpetBinarySensorDescription, ...] = (
    UbpetBinarySensorDescription(
        key="online",
        name="Online",
        icon="mdi:wifi",
        translation_key="online",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: item["online"].get("online"),
    ),
    UbpetBinarySensorDescription(
        key="deodorant_expired",
        name="Deodorant expired",
        icon="mdi:air-filter",
        translation_key="deodorant_expired",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: item["deodorant"].get("expired"),
    ),
    UbpetBinarySensorDescription(
        key="sensor_switch",
        name="Sensor enabled",
        icon="mdi:motion-sensor",
        translation_key="sensor_switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: item["config"].get("sensorSwitch") == 1,
    ),
    UbpetBinarySensorDescription(
        key="camera_switch",
        name="Camera enabled",
        icon="mdi:camera",
        translation_key="camera_switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: item["config"].get("cameraSwitch") == 1,
    ),
    UbpetBinarySensorDescription(
        key="control_board_switch",
        name="Child lock",
        icon="mdi:lock",
        translation_key="control_board_switch",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda item: item["config"].get("controlBoardSwitch") == 1,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        UbpetDeviceBinarySensor(coordinator, entry.entry_id, serial, description)
        for serial in coordinator.data.get("devices", {})
        for description in BINARY_SENSORS
    ]
    async_add_entities(entities)


class UbpetDeviceBinarySensor(CoordinatorEntity[UbpetDataUpdateCoordinator], BinarySensorEntity):
    entity_description: UbpetBinarySensorDescription

    def __init__(self, coordinator: UbpetDataUpdateCoordinator, entry_id: str, serial: str, description: UbpetBinarySensorDescription) -> None:
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
    def is_on(self) -> bool | None:
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        if not item:
            return None
        value = self.entity_description.value_fn(item)
        return None if value is None else bool(value)
