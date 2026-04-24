from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.auth import _extract_key, hash_key


def _virtual_key_identity(request: Request) -> str:
    """Rate-limit key for /v1/messages: hashed virtual key when present, else IP."""
    raw = _extract_key(request)
    if raw:
        return "vk:" + hash_key(raw)[:24]
    return "ip:" + get_remote_address(request)


# Storage defaults to in-memory ("memory://"); fine for a single-process deployment.
limiter = Limiter(key_func=_virtual_key_identity, default_limits=[])
