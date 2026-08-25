from __future__ import annotations

from dataclasses import dataclass

try:
    from . import secrets as _secrets
except ImportError:
    _secrets = None


PROFILE_UPET = "upet_airrobo"
PROFILE_MEOWANT = "meowant"
LEGACY_PROFILE_MEOWANT = "air_pet_meowant"
DEFAULT_PROFILE = PROFILE_UPET


@dataclass(frozen=True, slots=True)
class AccountProfile:
    """Vendor identity used to select and sign against a cloud tenant."""

    profile_id: str
    label: str
    base_url: str
    app_id: str
    app_key: str
    product: str


def _secret(name: str) -> str:
    if _secrets is None:
        return ""
    return str(getattr(_secrets, name, ""))


ACCOUNT_PROFILES = {
    PROFILE_UPET: AccountProfile(
        profile_id=PROFILE_UPET,
        label="UPET / Airrobo",
        base_url=_secret("BASE_URL"),
        app_id=_secret("APP_ID"),
        app_key=_secret("APP_KEY"),
        product=_secret("PRODUCT"),
    ),
    PROFILE_MEOWANT: AccountProfile(
        profile_id=PROFILE_MEOWANT,
        label="Meowant",
        base_url=_secret("MEOWANT_BASE_URL"),
        app_id=_secret("MEOWANT_APP_ID"),
        app_key=_secret("MEOWANT_APP_KEY"),
        product=_secret("MEOWANT_PRODUCT"),
    ),
}


def get_profile(profile_id: str) -> AccountProfile:
    """Return a configured account profile or reject an unknown identifier."""

    if profile_id == LEGACY_PROFILE_MEOWANT:
        profile_id = PROFILE_MEOWANT
    try:
        return ACCOUNT_PROFILES[profile_id]
    except KeyError as err:
        raise ValueError(f"unknown account profile: {profile_id}") from err
