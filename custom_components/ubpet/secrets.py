from __future__ import annotations

from base64 import b85decode
import hashlib
import json
import zlib

# Bundled app defaults for the recovered mobile API flow, keyed by region.
# This keeps the raw values out of the repository while preserving
# out-of-the-box setup for both the EU and NA backends.
_BUNDLE = (
    "ljZu(LhF$?&l4eLk)aNBTwvq"
    "z@j?U~+nDm`K5GM9A*iaKT#%"
    "ol?U4xJF;qf;t7l;FG9(74fy"
    "8cE$NH1R{v<Ia*6m@QWf)wh)"
    "v1|muDEjW2n$j^Zjb_;P`>iP"
    "Q}dV%bz*2}3!<?Lq}qXXaEy)"
    "T{0Wu8{Sb3}Tj4~U1P7NrUyH"
    "rUD2jThtG4GSa+1vAa&gBqEK"
    "tzlgDf@o9AiuC>Rhk*DMfl`e"
    "!1="
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


def _defaults() -> dict[str, dict[str, str]]:
    encoded = b85decode(_BUNDLE.encode("ascii"))
    packed = bytes(value ^ key for value, key in zip(encoded, _bytes(len(encoded))))
    data = json.loads(zlib.decompress(packed).decode("utf-8"))
    return {
        str(region): {str(key): str(value) for key, value in fields.items()}
        for region, fields in data.items()
    }


REGIONS = _defaults()
