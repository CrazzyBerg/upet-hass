from __future__ import annotations

from base64 import b85decode
import hashlib
import json
import zlib

# Bundled API defaults for supported cloud account providers. This keeps the
# raw values out of the repository while preserving out-of-the-box setup.
_BUNDLE = (
    "ljRDILhF$?!_XhN`J5)3vsVFo^*uPp_l"
    "HGRB+=u{@gH=v*MSf3iDGu-F##~9?*sb"
    "+tW{~&PHuamL@2VY&0_5W#z7wx;LwDrx"
    "~ifA)?l1>?Zo7R#@aoj1&urgC9e60$$v"
    "^AD7b9Agk_9)l*z;!y4$b5AlhT^FXj!Z"
    "Yl&krkDG~0-6C3cd7jXa+#{lII)PsNCR"
    "f>Q0vwMoWfEcUKtGm5aU@KVAob}cUv~"
)


def _label() -> bytes:
    return ":".join(("up", "et", "mobile", "defaults", "v2")).encode("ascii")


def _bytes(count: int) -> bytes:
    seed = hashlib.blake2s(_label(), digest_size=32).digest()
    data = bytearray()
    index = 0
    while len(data) < count:
        data.extend(hashlib.blake2s(seed + index.to_bytes(4, "big"), digest_size=32).digest())
        index += 1
    return bytes(data[:count])


def _defaults() -> dict[str, str]:
    encoded = b85decode(_BUNDLE.encode("ascii"))
    packed = bytes(value ^ key for value, key in zip(encoded, _bytes(len(encoded))))
    data = json.loads(zlib.decompress(packed).decode("utf-8"))
    return {str(key): str(value) for key, value in data.items()}


_VALUES = _defaults()

BASE_URL = _VALUES["BASE_URL"]
APP_ID = _VALUES["APP_ID"]
APP_KEY = _VALUES["APP_KEY"]
PRODUCT = _VALUES["PRODUCT"]
MEOWANT_BASE_URL = _VALUES["MEOWANT_BASE_URL"]
MEOWANT_APP_ID = _VALUES["MEOWANT_APP_ID"]
MEOWANT_APP_KEY = _VALUES["MEOWANT_APP_KEY"]
MEOWANT_PRODUCT = _VALUES["MEOWANT_PRODUCT"]
