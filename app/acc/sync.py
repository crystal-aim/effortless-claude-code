"""Fetch the awesome-claude-code resources CSV into local cache."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ACC_CSV_URL = "https://raw.githubusercontent.com/hesreallyhim/awesome-claude-code/main/THE_RESOURCES_TABLE.csv"
CACHE_FILE = Path.home() / ".cache" / "claude-croxy" / "acc-resources.csv"

SETTING_SYNCED_AT = "acc.last_synced_at"


@dataclass
class AccStatus:
    cached: bool
    synced_at: Optional[str]
    cache_path: str
    size_bytes: int

    def to_dict(self) -> dict:
        return asdict(self)


def get_status() -> AccStatus:
    if not CACHE_FILE.exists():
        return AccStatus(False, None, str(CACHE_FILE), 0)
    return AccStatus(
        cached=True,
        synced_at=_read_setting(SETTING_SYNCED_AT),
        cache_path=str(CACHE_FILE),
        size_bytes=CACHE_FILE.stat().st_size,
    )


def sync(timeout_seconds: int = 60) -> AccStatus:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = httpx.get(ACC_CSV_URL, timeout=timeout_seconds, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"download failed: {e}")
    CACHE_FILE.write_bytes(r.content)
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    _write_setting(SETTING_SYNCED_AT, now)
    return AccStatus(True, now, str(CACHE_FILE), CACHE_FILE.stat().st_size)


def _read_setting(key: str) -> Optional[str]:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        row = db.get(Setting, key)
        return row.value if row else None


def _write_setting(key: str, value: str) -> None:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
