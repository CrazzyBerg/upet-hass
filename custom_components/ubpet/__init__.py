from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from .api import UbpetClient
from .const import (
    CONF_APP_ID,
    CONF_APP_KEY,
    CONF_BASE_URL,
    CONF_DEVICE_ID,
    CONF_PRODUCT,
    DEFAULT_APP_ID,
    DEFAULT_APP_KEY,
    DEFAULT_BASE_URL,
    DEFAULT_PRODUCT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import UbpetDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
MQTT_POLL_START_DELAY_SECONDS = 30


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    app_key = entry.data.get(CONF_APP_KEY, DEFAULT_APP_KEY)
    app_id = entry.data.get(CONF_APP_ID, DEFAULT_APP_ID)
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    product = entry.data.get(CONF_PRODUCT, DEFAULT_PRODUCT)
    missing = [
        field
        for field, value in (
            (CONF_APP_KEY, app_key),
            (CONF_APP_ID, app_id),
            (CONF_BASE_URL, base_url),
            (CONF_PRODUCT, product),
        )
        if not value
    ]
    if missing:
        _LOGGER.error("UPET setup has missing API field(s): %s", ", ".join(missing))

    client = UbpetClient(
        account=entry.data["account"],
        password=entry.data["password"],
        app_key=app_key,
        device_id=entry.data[CONF_DEVICE_ID],
        app_id=app_id,
        base_url=base_url,
        product=product,
    )
    coordinator = UbpetDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        async_call_later(
            hass,
            MQTT_POLL_START_DELAY_SECONDS,
            lambda _now: coordinator.enable_mqtt_state_polls(),
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            coordinator.cancel_mqtt_state_polls()
    return unload_ok
