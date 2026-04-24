"""Background auto-sync task for ECC (git) + ACC (CSV) catalogs.

State is persisted in the `settings` table so it survives restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.acc import sync as acc_sync
from app.ecc import sync as ecc_sync

logger = logging.getLogger(__name__)

SETTING_ENABLED = "autosync.enabled"
SETTING_INTERVAL = "autosync.interval_hours"
SETTING_LAST_RUN = "autosync.last_run_at"
SETTING_LAST_ERROR = "autosync.last_error"

DEFAULT_INTERVAL_HOURS = 24
MIN_INTERVAL_HOURS = 1

_task: Optional[asyncio.Task] = None
_loop: Optional[asyncio.AbstractEventLoop] = None


def get_config() -> dict:
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        def g(k: str) -> Optional[str]:
            r = db.get(Setting, k)
            return r.value if r else None
        raw_interval = g(SETTING_INTERVAL)
        try:
            interval = int(raw_interval) if raw_interval else DEFAULT_INTERVAL_HOURS
        except ValueError:
            interval = DEFAULT_INTERVAL_HOURS
        return {
            "enabled": (g(SETTING_ENABLED) or "false").lower() == "true",
            "interval_hours": max(MIN_INTERVAL_HOURS, interval),
            "last_run_at": g(SETTING_LAST_RUN),
            "last_error": g(SETTING_LAST_ERROR) or None,
            "running": _task is not None and not _task.done(),
        }


def set_config(enabled: Optional[bool] = None, interval_hours: Optional[int] = None) -> dict:
    from app.db import session_scope

    with session_scope() as db:
        if enabled is not None:
            _upsert(db, SETTING_ENABLED, "true" if enabled else "false")
        if interval_hours is not None:
            _upsert(db, SETTING_INTERVAL, str(max(MIN_INTERVAL_HOURS, int(interval_hours))))
    restart()
    return get_config()


def _upsert(db, key: str, value: str) -> None:
    from app.models import Setting

    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


async def run_once() -> dict:
    """Run one sync pass (both sources). Returns {errors: [...]}."""
    errors: list[str] = []
    try:
        await asyncio.to_thread(ecc_sync.sync_repo)
    except Exception as e:
        logger.warning("auto-sync ECC failed: %s", e)
        errors.append(f"ecc: {e}")
    try:
        await asyncio.to_thread(acc_sync.sync)
    except Exception as e:
        logger.warning("auto-sync ACC failed: %s", e)
        errors.append(f"acc: {e}")
    from app.db import session_scope

    with session_scope() as db:
        _upsert(db, SETTING_LAST_RUN, datetime.utcnow().replace(microsecond=0).isoformat() + "Z")
        _upsert(db, SETTING_LAST_ERROR, "; ".join(errors) if errors else "")
    return {"errors": errors}


async def _tick() -> None:
    try:
        while True:
            cfg = get_config()
            if not cfg["enabled"]:
                return
            interval_s = cfg["interval_hours"] * 3600
            await asyncio.sleep(interval_s)
            cfg = get_config()
            if not cfg["enabled"]:
                return
            await run_once()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("auto-sync loop crashed; task exiting")


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called from the FastAPI lifespan so we can schedule tasks from
    sync request handlers (which run in a threadpool)."""
    global _loop
    _loop = loop


def _schedule_task() -> None:
    global _task
    if _loop is None:
        logger.warning("auto-sync: no loop bound; call bind_loop() from lifespan")
        return
    def _create():
        global _task
        _task = _loop.create_task(_tick(), name="ecc-auto-sync")
    if _loop.is_running():
        _loop.call_soon_threadsafe(_create)
    else:
        _create()


def start() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    cfg = get_config()
    if not cfg["enabled"]:
        return
    _schedule_task()
    logger.info("auto-sync scheduled (interval=%dh)", cfg["interval_hours"])


def stop() -> None:
    global _task
    if _task is not None and not _task.done():
        if _loop is not None and _loop.is_running():
            _loop.call_soon_threadsafe(_task.cancel)
        else:
            _task.cancel()
    _task = None


def restart() -> None:
    stop()
    start()
