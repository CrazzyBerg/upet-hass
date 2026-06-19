from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import UbpetDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

REST_REQUEST_SERVICE = "__rest_request"
RESET_WASTE_BIN_SERVICE = "__reset_waste_bin"
DIAGNOSTIC_SERVICES = {"request_state", REST_REQUEST_SERVICE}


@dataclass(frozen=True, kw_only=True)
class UbpetMqttButtonDescription(ButtonEntityDescription):
    service_id: str


MQTT_BUTTONS: tuple[UbpetMqttButtonDescription, ...] = (
    UbpetMqttButtonDescription(
        key="rest_request",
        name="REST request",
        icon="mdi:cloud-refresh",
        translation_key="rest_request",
        entity_category=EntityCategory.DIAGNOSTIC,
        service_id=REST_REQUEST_SERVICE,
    ),
    UbpetMqttButtonDescription(
        key="mqtt_request_state",
        name="Request state",
        icon="mdi:refresh",
        translation_key="mqtt_request_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        service_id="request_state",
    ),
    UbpetMqttButtonDescription(
        key="reset_waste_bin",
        name="Reset waste bin",
        icon="mdi:delete-empty",
        translation_key="reset_waste_bin",
        entity_category=EntityCategory.CONFIG,
        service_id=RESET_WASTE_BIN_SERVICE,
    ),
    UbpetMqttButtonDescription(
        key="mqtt_start_clean_up",
        name="Start clean",
        icon="mdi:broom",
        translation_key="mqtt_start_clean_up",
        service_id="start_clean_up",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_pause_clean_up",
        name="Pause clean",
        icon="mdi:pause",
        translation_key="mqtt_pause_clean_up",
        service_id="pause_clean_up",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_resume_clean_up",
        name="Resume clean",
        icon="mdi:play",
        translation_key="mqtt_resume_clean_up",
        service_id="resume_clean_up",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_start_flatten",
        name="Flatten",
        icon="mdi:format-align-middle",
        translation_key="mqtt_start_flatten",
        service_id="start_flatten",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_pause_flatten",
        name="Pause flatten",
        icon="mdi:pause",
        translation_key="mqtt_pause_flatten",
        service_id="pause_flatten",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_resume_flatten",
        name="Resume flatten",
        icon="mdi:play",
        translation_key="mqtt_resume_flatten",
        service_id="resume_flatten",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_start_rise",
        name="Raise litter rake",
        icon="mdi:arrow-up-bold",
        translation_key="mqtt_start_rise",
        service_id="start_rise",
    ),
    UbpetMqttButtonDescription(
        key="mqtt_start_drop",
        name="Lower litter rake",
        icon="mdi:arrow-down-bold",
        translation_key="mqtt_start_drop",
        service_id="start_drop",
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: UbpetDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[UbpetMqttCommandButton] = []
    for serial in coordinator.data.get("devices", {}):
        for description in MQTT_BUTTONS:
            entity = UbpetMqttCommandButton(coordinator, entry.entry_id, serial, description)
            entities.append(entity)
    async_add_entities(entities)


class UbpetMqttCommandButton(CoordinatorEntity[UbpetDataUpdateCoordinator], ButtonEntity):
    entity_description: UbpetMqttButtonDescription

    def __init__(
        self,
        coordinator: UbpetDataUpdateCoordinator,
        entry_id: str,
        serial: str,
        description: UbpetMqttButtonDescription,
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
    def available(self) -> bool:
        if not super().available:
            return False
        service_id = self.entity_description.service_id
        if service_id in DIAGNOSTIC_SERVICES or service_id == RESET_WASTE_BIN_SERVICE:
            return True
        item = self.coordinator.data.get("devices", {}).get(self._serial)
        mode = _mqtt_work_mode(item)
        state = _mqtt_work_state(item)
        if state is None:
            return False
        if service_id == "start_drop":
            return mode == "RAKING_UP" and state == "PAUSED"
        if service_id in {"start_clean_up", "start_flatten", "start_rise"}:
            return state == "PENDING"
        if service_id == "pause_clean_up":
            return mode == "CLEANING" and state == "RUNNING"
        if service_id == "resume_clean_up":
            return mode == "CLEANING" and state == "PAUSED"
        if service_id == "pause_flatten":
            return mode == "SMOOTHING" and state == "RUNNING"
        if service_id == "resume_flatten":
            return mode == "SMOOTHING" and state == "PAUSED"
        return True

    async def async_press(self) -> None:
        if self.entity_description.service_id == REST_REQUEST_SERVICE:
            await self.coordinator.async_request_refresh()
            return
        if self.entity_description.service_id == RESET_WASTE_BIN_SERVICE:
            await self.hass.async_add_executor_job(self.coordinator.client.reset_box_use_times, self._serial)
            _LOGGER.info("Reset UPET waste-bin counter for serial=%s", self._serial)
            await self.coordinator.async_request_refresh()
            return
        if self.entity_description.service_id not in DIAGNOSTIC_SERVICES:
            optimistic_state = _optimistic_mqtt_state_for_service(self.entity_description.service_id)
            if optimistic_state:
                self.coordinator.set_mqtt_work_state(self._serial, optimistic_state)
        result = await self.hass.async_add_executor_job(
            self.coordinator.client.send_mqtt_service,
            self._serial,
            self.entity_description.service_id,
        )
        _LOGGER.info(
            "Sent UPET MQTT service=%s serial=%s seq=%s cid=%s received=%s",
            self.entity_description.service_id,
            self._serial,
            result.get("seq") if isinstance(result, dict) else None,
            result.get("cid") if isinstance(result, dict) else None,
            result.get("received_count") if isinstance(result, dict) else None,
        )
        if isinstance(result, dict):
            result.setdefault("service_id", self.entity_description.service_id)
        self.coordinator.store_mqtt_result(self._serial, result)
        if self.entity_description.service_id == "request_state":
            self.coordinator.start_mqtt_state_poll(self._serial)
        elif self.entity_description.service_id not in DIAGNOSTIC_SERVICES:
            self.coordinator.start_mqtt_state_poll(self._serial, fast=True)


def _mqtt_work_state(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    mqtt_state = item.get("mqtt_state")
    if not isinstance(mqtt_state, dict):
        return None
    state = mqtt_state.get("w_state_app_name")
    return state if isinstance(state, str) else None


def _mqtt_work_mode(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    mqtt_state = item.get("mqtt_state")
    if not isinstance(mqtt_state, dict):
        return None
    mode = mqtt_state.get("w_mode_app_name")
    return mode if isinstance(mode, str) else None


def _optimistic_mqtt_state_for_service(service_id: str) -> dict[str, Any]:
    mode_by_service = {
        "start_clean_up": "CLEANING",
        "pause_clean_up": "CLEANING",
        "resume_clean_up": "CLEANING",
        "start_flatten": "SMOOTHING",
        "pause_flatten": "SMOOTHING",
        "resume_flatten": "SMOOTHING",
        "start_rise": "RAKING_UP",
        "start_drop": "RESETTING",
    }
    mode = mode_by_service.get(service_id)
    if mode is None:
        return {}
    state_by_service = {
        "pause_clean_up": "PAUSED",
        "resume_clean_up": "RUNNING",
        "pause_flatten": "PAUSED",
        "resume_flatten": "RUNNING",
    }
    return {
        "mqtt_service": service_id,
        "w_mode_app_name": mode,
        "w_state_app_name": state_by_service.get(service_id, "RUNNING"),
    }
