from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "ubpet"
PACKAGE = "ubpet_coordinator_test"


class _DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval, always_update=True) -> None:
        self.hass = hass
        self.data = None
        self.listener_updates = 0

    def __class_getitem__(cls, _item):
        return cls

    def async_update_listeners(self) -> None:
        self.listener_updates += 1


class _UpdateFailed(Exception):
    pass


class _UbpetApiError(Exception):
    pass


def _load_coordinator_module():
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
    update_coordinator.UpdateFailed = _UpdateFailed

    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(PACKAGE_DIR)]
    api = types.ModuleType(f"{PACKAGE}.api")
    api.UbpetApiError = _UbpetApiError
    api.UbpetClient = object
    const = types.ModuleType(f"{PACKAGE}.const")
    const.DEFAULT_SCAN_INTERVAL = None
    const.DOMAIN = "ubpet"

    modules = {
        "homeassistant": homeassistant,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.update_coordinator": update_coordinator,
        PACKAGE: package,
        f"{PACKAGE}.api": api,
        f"{PACKAGE}.const": const,
    }
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.coordinator", PACKAGE_DIR / "coordinator.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


coordinator_module = _load_coordinator_module()

SERIAL = "SN1"


class FakeHass:
    def async_add_executor_job(self, func, *args):
        return asyncio.get_running_loop().run_in_executor(None, func, *args)

    def async_create_task(self, coro):
        return asyncio.get_running_loop().create_task(coro)


def _state_result(mode: str, state: str) -> dict:
    return {
        "received_messages": [
            {"decoded": {"risp_cmd": 1021, "w_mode_app_name": mode, "w_state_app_name": state}},
        ]
    }


def _make_coordinator(client=None):
    coordinator = coordinator_module.UbpetDataUpdateCoordinator(FakeHass(), client)
    coordinator.data = {"devices": {SERIAL: {"device": {}}}}
    return coordinator


def _current_state(coordinator) -> dict | None:
    return coordinator.data["devices"][SERIAL].get("mqtt_state")


class PollDelayTest(unittest.TestCase):
    def test_idle_running_is_not_fast_polled(self) -> None:
        coordinator = _make_coordinator()
        coordinator.store_mqtt_result(SERIAL, _state_result("IDLE", "RUNNING"))
        self.assertEqual(
            coordinator._mqtt_state_poll_delay(SERIAL),
            coordinator_module.MQTT_STATE_PENDING_POLL_SECONDS,
        )

    def test_cleaning_running_is_fast_polled(self) -> None:
        coordinator = _make_coordinator()
        coordinator.store_mqtt_result(SERIAL, _state_result("CLEANING", "RUNNING"))
        self.assertEqual(
            coordinator._mqtt_state_poll_delay(SERIAL),
            coordinator_module.MQTT_STATE_RUNNING_POLL_SECONDS,
        )


class OptimisticStateTest(unittest.TestCase):
    def test_unconfirmed_optimistic_state_reverts_to_last_real_state(self) -> None:
        coordinator = _make_coordinator()
        coordinator.store_mqtt_result(SERIAL, _state_result("IDLE", "RUNNING"))
        coordinator.set_mqtt_work_state(SERIAL, {"w_mode_app_name": "CLEANING", "w_state_app_name": "RUNNING"})
        self.assertEqual(_current_state(coordinator)["w_mode_app_name"], "CLEANING")

        self.assertFalse(coordinator._expire_optimistic_mqtt_state(SERIAL))

        future = time.time() + coordinator_module.MQTT_OPTIMISTIC_STATE_TTL_SECONDS + 1
        with patch.object(coordinator_module.time, "time", return_value=future):
            self.assertTrue(coordinator._expire_optimistic_mqtt_state(SERIAL))

        state = _current_state(coordinator)
        self.assertEqual(state["w_mode_app_name"], "IDLE")
        self.assertNotIn("optimistic", state)

    def test_unconfirmed_optimistic_state_without_prior_state_is_cleared(self) -> None:
        coordinator = _make_coordinator()
        coordinator.set_mqtt_work_state(SERIAL, {"w_mode_app_name": "CLEANING", "w_state_app_name": "RUNNING"})
        future = time.time() + coordinator_module.MQTT_OPTIMISTIC_STATE_TTL_SECONDS + 1
        with patch.object(coordinator_module.time, "time", return_value=future):
            coordinator._expire_optimistic_mqtt_state(SERIAL)
        self.assertIsNone(_current_state(coordinator))

    def test_confirmed_state_is_not_reverted(self) -> None:
        coordinator = _make_coordinator()
        coordinator.store_mqtt_result(SERIAL, _state_result("IDLE", "RUNNING"))
        coordinator.set_mqtt_work_state(SERIAL, {"w_mode_app_name": "CLEANING", "w_state_app_name": "RUNNING"})
        coordinator.store_mqtt_result(SERIAL, _state_result("CLEANING", "RUNNING"))
        future = time.time() + coordinator_module.MQTT_OPTIMISTIC_STATE_TTL_SECONDS + 1
        with patch.object(coordinator_module.time, "time", return_value=future):
            self.assertFalse(coordinator._expire_optimistic_mqtt_state(SERIAL))
        self.assertEqual(_current_state(coordinator)["w_mode_app_name"], "CLEANING")


class SlowClient:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.calls = 0

    def send_mqtt_service(self, serial, service_id):
        self.calls += 1
        self.release.wait(5)
        return _state_result("IDLE", "RUNNING")


class OverlapTest(unittest.IsolatedAsyncioTestCase):
    async def test_timed_out_request_blocks_new_requests_until_it_finishes(self) -> None:
        client = SlowClient()
        coordinator = _make_coordinator(client)
        with patch.object(coordinator_module, "MQTT_STATE_REQUEST_TIMEOUT_SECONDS", 0.05):
            first = asyncio.create_task(coordinator.async_request_mqtt_state(SERIAL))
            await asyncio.sleep(0.2)  # past the timeout, executor job still running
            self.assertTrue(coordinator._mqtt_state_request_in_progress(SERIAL))

            await coordinator.async_request_mqtt_state(SERIAL)
            self.assertEqual(client.calls, 1)

            client.release.set()
            await first

        self.assertFalse(coordinator._mqtt_state_request_in_progress(SERIAL))
        self.assertEqual(_current_state(coordinator)["w_mode_app_name"], "IDLE")


if __name__ == "__main__":
    unittest.main()
