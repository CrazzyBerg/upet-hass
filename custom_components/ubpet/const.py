from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "ubpet"
DEFAULT_SCAN_INTERVAL = timedelta(minutes=3)

try:
    from .secrets import REGIONS
except ImportError:
    REGIONS: dict[str, dict[str, str]] = {}

REGION_LABELS = {
    "eu": "Europe",
    "na": "North America",
}
DEFAULT_REGION = next(iter(REGIONS), "")

CONF_APP_KEY = "app_key"
CONF_APP_ID = "app_id"
CONF_DEVICE_ID = "device_id"
CONF_BASE_URL = "base_url"
CONF_PRODUCT = "product"
CONF_REGION = "region"

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TIME,
    Platform.SELECT,
    Platform.BUTTON,
]
