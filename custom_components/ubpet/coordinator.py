from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UbpetApiError, UbpetClient
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

MQTT_STATE_RUNNING_POLL_SECONDS = 1
MQTT_STATE_PAUSED_POLL_SECONDS = 10
MQTT_STATE_PENDING_POLL_SECONDS = 60
MQTT_STATE_POLL_HEARTBEAT_SECONDS = 1
MQTT_STATE_REQUEST_TIMEOUT_SECONDS = 30
MQTT_STATE_IN_PROGRESS_STALE_SECONDS = 45


class UbpetDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, client: UbpetClient, *, update_interval: timedelta = DEFAULT_SCAN_INTERVAL) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self._mqtt_state_by_serial: dict[str, dict[str, Any]] = {}
        self._mqtt_state_poll_task: asyncio.Task[None] | None = None
        self._mqtt_state_request_started_at_by_serial: dict[str, datetime] = {}
        self._mqtt_state_polls_enabled = False
        self._last_rest_update_at: datetime | None = None
        self._last_mqtt_update_at_by_serial: dict[str, datetime] = {}
        self._next_mqtt_poll_at_by_serial: dict[str, datetime] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        started = time.monotonic()
        _LOGGER.info("Starting UPET data update")
        try:
            data = await self.hass.async_add_executor_job(self.client.get_dashboard)
        except (OSError, RuntimeError, UbpetApiError) as err:
            _LOGGER.exception("UPET data update failed")
            raise UpdateFailed(str(err)) from err

        devices = data.get("devices", {})
        cats = data.get("cats", [])
        _LOGGER.info(
            "Finished UPET data update in %.2fs: devices=%s cats=%s",
            time.monotonic() - started,
            len(devices) if isinstance(devices, dict) else 0,
            len(cats) if isinstance(cats, list) else 0,
        )
        self._last_rest_update_at = datetime.now(UTC)
        data = self._with_runtime_state(data)
        if self._mqtt_state_polls_enabled:
            self._ensure_mqtt_state_poll_task()
            self._ensure_mqtt_state_polls(data)
        return data

    def store_mqtt_result(self, serial: str, result: dict[str, Any] | object) -> bool:
        state = _latest_mqtt_state(result)
        if state is None:
            return False
        self._mqtt_state_by_serial[serial] = state
        self._last_mqtt_update_at_by_serial[serial] = datetime.now(UTC)
        self.async_set_updated_data(self._with_runtime_state(self.data or {}))
        return True

    def set_mqtt_work_state(self, serial: str, state: dict[str, Any]) -> None:
        mqtt_state = deepcopy(self._mqtt_state_by_serial.get(serial, {}))
        mqtt_state.update(state)
        mqtt_state["optimistic"] = True
        mqtt_state["optimistic_updated_at"] = time.time()
        self._mqtt_state_by_serial[serial] = mqtt_state
        self.async_set_updated_data(self._with_runtime_state(self.data or {}))

    def start_mqtt_state_poll(self, serial: str, *, fast: bool = False) -> None:
        _LOGGER.info("Starting UPET MQTT state polling for %s fast=%s", serial, fast)
        self._schedule_mqtt_state_poll(serial, 0 if fast else self._mqtt_state_poll_delay(serial))
        self._ensure_mqtt_state_poll_task()

    def enable_mqtt_state_polls(self) -> None:
        self._mqtt_state_polls_enabled = True
        self._ensure_mqtt_state_poll_task()
        self._ensure_mqtt_state_polls(self.data or {})

    def cancel_mqtt_state_polls(self) -> None:
        self._mqtt_state_polls_enabled = False
        if self._mqtt_state_poll_task is not None:
            self._mqtt_state_poll_task.cancel()
            self._mqtt_state_poll_task = None
        self._next_mqtt_poll_at_by_serial.clear()
        self._mqtt_state_request_started_at_by_serial.clear()

    def _ensure_mqtt_state_poll_task(self) -> None:
        if self._mqtt_state_poll_task is None or self._mqtt_state_poll_task.done():
            if self._mqtt_state_poll_task is not None and self._mqtt_state_poll_task.done():
                try:
                    self._mqtt_state_poll_task.result()
                except asyncio.CancelledError:
                    _LOGGER.warning("UPET MQTT state poll loop was cancelled; restarting")
                except Exception:
                    _LOGGER.exception("UPET MQTT state poll loop died; restarting")
            self._mqtt_state_poll_task = self.hass.async_create_task(self._mqtt_state_poll_loop())

    async def _mqtt_state_poll_loop(self) -> None:
        _LOGGER.info("Starting UPET MQTT state poll loop")
        try:
            while self._mqtt_state_polls_enabled:
                try:
                    self._run_due_mqtt_state_polls()
                except Exception:
                    _LOGGER.exception("Unexpected UPET MQTT state poll loop failure")
                await asyncio.sleep(MQTT_STATE_POLL_HEARTBEAT_SECONDS)
        except asyncio.CancelledError:
            raise
        finally:
            _LOGGER.info("Stopped UPET MQTT state poll loop")

    async def async_request_mqtt_state(self, serial: str) -> None:
        if serial in self._mqtt_state_request_started_at_by_serial and not self._mqtt_state_request_is_stale(serial):
            _LOGGER.debug("UPET MQTT state request already in progress for %s", serial)
            return
        self._next_mqtt_poll_at_by_serial.pop(serial, None)
        self._mqtt_state_request_started_at_by_serial[serial] = datetime.now(UTC)
        _LOGGER.info("Requesting UPET MQTT state for %s", serial)
        try:
            async with asyncio.timeout(MQTT_STATE_REQUEST_TIMEOUT_SECONDS):
                result = await self.hass.async_add_executor_job(self.client.send_mqtt_service, serial, "request_state")
            if isinstance(result, dict):
                result.setdefault("service_id", "request_state")
            updated = self.store_mqtt_result(serial, result)
            item = self.data.get("devices", {}).get(serial) if self.data else None
            _LOGGER.info(
                "Finished UPET MQTT state request for %s updated=%s state=%s mode=%s received=%s",
                serial,
                updated,
                _mqtt_work_state(item),
                _mqtt_work_mode(item),
                result.get("received_count") if isinstance(result, dict) else None,
            )
        except TimeoutError:
            _LOGGER.warning(
                "UPET MQTT state poll timed out for %s after %ss",
                serial,
                MQTT_STATE_REQUEST_TIMEOUT_SECONDS,
            )
        except (OSError, RuntimeError, UbpetApiError) as err:
            _LOGGER.warning("UPET MQTT state poll failed for %s: %s", serial, err)
        except Exception:
            _LOGGER.exception("Unexpected UPET MQTT state poll failure for %s", serial)
        finally:
            self._mqtt_state_request_started_at_by_serial.pop(serial, None)
            self._schedule_next_mqtt_state_poll(serial)

    def _with_runtime_state(self, data: dict[str, Any]) -> dict[str, Any]:
        if (
            not self._mqtt_state_by_serial
            and self._last_rest_update_at is None
            and not self._last_mqtt_update_at_by_serial
        ):
            return data
        merged = deepcopy(data)
        devices = merged.get("devices")
        if not isinstance(devices, dict):
            return merged
        for serial, item in devices.items():
            if not isinstance(item, dict):
                continue
            if state := self._mqtt_state_by_serial.get(serial):
                item["mqtt_state"] = deepcopy(state)
            item["runtime_state"] = {
                "last_rest_update_at": self._last_rest_update_at,
                "last_mqtt_update_at": self._last_mqtt_update_at_by_serial.get(serial),
            }
        return merged

    def _schedule_next_mqtt_state_poll(self, serial: str) -> None:
        self._schedule_mqtt_state_poll(serial, self._mqtt_state_poll_delay(serial))

    def _schedule_mqtt_state_poll(self, serial: str, delay: int) -> None:
        _LOGGER.info("Scheduling UPET MQTT state poll for %s in %ss", serial, delay)
        self._next_mqtt_poll_at_by_serial[serial] = datetime.now(UTC) + timedelta(seconds=delay)

    def _ensure_mqtt_state_polls(self, data: dict[str, Any]) -> None:
        devices = data.get("devices")
        if not isinstance(devices, dict):
            return
        for serial in devices:
            self._next_mqtt_poll_at_by_serial.setdefault(serial, datetime.now(UTC))

    def _run_due_mqtt_state_polls(self) -> None:
        devices = self.data.get("devices", {}) if self.data else {}
        if not isinstance(devices, dict):
            return
        now = datetime.now(UTC)
        for serial in devices:
            if serial in self._mqtt_state_request_started_at_by_serial:
                if self._mqtt_state_request_is_stale(serial):
                    self._mqtt_state_request_started_at_by_serial.pop(serial, None)
                else:
                    continue
            due_at = self._next_mqtt_poll_at_by_serial.get(serial)
            if due_at is not None and due_at > now:
                continue
            _LOGGER.info("Running due UPET MQTT state poll for %s", serial)
            self.hass.async_create_task(self.async_request_mqtt_state(serial))

    def _mqtt_state_poll_delay(self, serial: str) -> int:
        state = _mqtt_work_state(self.data.get("devices", {}).get(serial) if self.data else None)
        if state == "RUNNING":
            return MQTT_STATE_RUNNING_POLL_SECONDS
        if state == "PAUSED":
            return MQTT_STATE_PAUSED_POLL_SECONDS
        return MQTT_STATE_PENDING_POLL_SECONDS

    def _mqtt_state_request_is_stale(self, serial: str) -> bool:
        started_at = self._mqtt_state_request_started_at_by_serial.get(serial)
        if started_at is None:
            return True
        age = (datetime.now(UTC) - started_at).total_seconds()
        if age <= MQTT_STATE_IN_PROGRESS_STALE_SECONDS:
            return False
        _LOGGER.warning(
            "UPET MQTT state request for %s is stale in_progress: age=%.1fs stale_after=%ss",
            serial,
            age,
            MQTT_STATE_IN_PROGRESS_STALE_SECONDS,
        )
        return True


def _latest_mqtt_state(result: dict[str, Any] | object) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    messages = result.get("received_messages")
    if not isinstance(messages, list):
        return None

    latest: dict[str, Any] | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        decoded = message.get("decoded")
        if not isinstance(decoded, dict):
            continue
        if decoded.get("risp_cmd") != 1021:
            continue
        if "w_mode_app_name" not in decoded and "w_state_app_name" not in decoded:
            continue
        latest = {
            "cid": decoded.get("cid"),
            "from_uid": decoded.get("from_uid"),
            "to_uid": decoded.get("to_uid"),
            "received_phase": message.get("phase"),
            "received_topic": message.get("topic"),
            "received_qos": message.get("qos"),
            "mqtt_service": result.get("service_id"),
            "command_cid": result.get("cid"),
            "command_gid": result.get("gid"),
            "command_created_at_ms": result.get("created_at_ms"),
        }
        for key, value in decoded.items():
            if key in {
                "payload_hex",
                "raw_hex",
            }:
                continue
            if value not in (None, ""):
                latest[key] = value
    return latest


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
