from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACTED = "REDACTED"
REDACT_KEYS = {
    "account",
    "authorization",
    "device_id",
    "deviceId",
    "icon",
    "password",
    "refreshToken",
    "token",
    "userEmail",
    "userImage",
    "userName",
    "userPhone",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return {
        "entry": _redact(
            {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            }
        ),
        "last_update_success": coordinator.last_update_success,
        "data": _redact(coordinator.data or {}),
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if key in REDACT_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
