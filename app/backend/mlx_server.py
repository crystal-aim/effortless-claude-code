"""Manage an mlx-vlm server subprocess (start / stop / status / logs)."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("ccm.mlx_server")

_process: Optional[subprocess.Popen] = None
_current_model: Optional[str] = None
_status: str = "stopped"  # stopped | downloading | starting | running | error
_error_message: str = ""
_logs: deque[str] = deque(maxlen=500)
_lock = threading.Lock()

# Last model the user selected — persisted to settings.mlx.last_model so it
# survives stop/start and full process restarts. Holds the short name (e.g.
# "gemma-4-e4b-it") used by the admin UI dropdown.
_last_selected_model: Optional[str] = None
_last_selected_loaded: bool = False
_DB_SETTING_KEY = "mlx.last_model"


def _db_load_last_selected() -> Optional[str]:
    try:
        from app.db import session_scope
        from app.models import Setting
        with session_scope() as db:
            row = db.get(Setting, _DB_SETTING_KEY)
            return row.value if row else None
    except Exception as e:
        log.warning("mlx: failed to load last model from db: %s", e)
        return None


def _db_save_last_selected(name: str) -> None:
    try:
        from app.db import session_scope
        from app.models import Setting
        with session_scope() as db:
            row = db.get(Setting, _DB_SETTING_KEY)
            if row is None:
                db.add(Setting(key=_DB_SETTING_KEY, value=name))
            else:
                row.value = name
    except Exception as e:
        log.warning("mlx: failed to save last model to db: %s", e)


def _ensure_last_selected_loaded() -> None:
    global _last_selected_model, _last_selected_loaded
    if _last_selected_loaded:
        return
    _last_selected_model = _db_load_last_selected()
    _last_selected_loaded = True


def get_status() -> dict:
    _ensure_last_selected_loaded()
    with _lock:
        alive = _process is not None and _process.poll() is None
        if _status == "running" and not alive:
            _set_status("stopped")

    return {
        "status": _status,
        "model": _current_model,
        "last_model": _last_selected_model,
        "pid": _process.pid if _process and _process.poll() is None else None,
        "error": _error_message if _status == "error" else None,
    }


def get_logs(n: int = 100) -> list[str]:
    with _lock:
        return list(_logs)[-n:]


def is_model_downloaded(model_id: str) -> bool:
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    folder_name = "models--" + model_id.replace("/", "--")
    model_dir = cache_dir / folder_name
    if not model_dir.exists():
        return False
    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return False
    return any(snapshots.iterdir())


def _set_status(new_status: str, error: str = "") -> None:
    global _status, _error_message
    _status = new_status
    _error_message = error
    log.info("mlx server status -> %s %s", new_status, f"({error})" if error else "")


def _append_log(line: str) -> None:
    with _lock:
        _logs.append(line)


def _reader_thread(stream, prefix: str) -> None:
    try:
        for raw_line in iter(stream.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            _append_log(f"[{prefix}] {line}")
    except Exception:
        pass


def _download_model(model_id: str) -> bool:
    _set_status("downloading")
    _append_log(f"[download] Downloading {model_id} ...")
    try:
        proc = subprocess.Popen(
            ["hf", "download", model_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ,
        )
        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            _append_log(f"[download] {line}")
        proc.wait()
        if proc.returncode != 0:
            _set_status("error", f"Model download failed (exit code {proc.returncode})")
            return False
        _append_log(f"[download] Done.")
        return True
    except Exception as e:
        _set_status("error", str(e))
        return False


def _health_check(port: int, timeout: float = 120) -> bool:
    _set_status("starting")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=3)
            if resp.status_code == 200:
                _set_status("running")
                _append_log("[health] Server is ready.")
                return True
        except Exception:
            pass
        time.sleep(2)
    _set_status("error", "Server failed to become ready within timeout")
    return False


def _start_thread(model_id: str, port: int) -> None:
    global _process, _current_model

    if not is_model_downloaded(model_id):
        if not _download_model(model_id):
            return

    _set_status("starting")
    _append_log(f"[server] Starting mlx-vlm server: model={model_id} port={port}")

    try:
        _process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "mlx_vlm",
                "server",
                "--model",
                model_id,
                "--host",
                "0.0.0.0",
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _current_model = model_id

        threading.Thread(
            target=_reader_thread, args=(_process.stdout, "stdout"), daemon=True
        ).start()
        threading.Thread(
            target=_reader_thread, args=(_process.stderr, "stderr"), daemon=True
        ).start()

        if not _health_check(port):
            stop_server()
            return

    except Exception as e:
        _set_status("error", str(e))
        _append_log(f"[server] Failed to start: {e}")


def start_server(model_id: str, port: int = 8899, display_name: Optional[str] = None) -> None:
    """Start mlx-vlm server.

    `model_id` is the HuggingFace id passed to the subprocess.
    `display_name` is the short name shown in the UI dropdown; if omitted,
    falls back to `model_id`. Persisted to DB so UI can re-preselect across
    stop/start and process restarts.
    """
    global _process, _last_selected_model, _last_selected_loaded
    with _lock:
        if _status in ("downloading", "starting"):
            return
        if _process is not None and _process.poll() is None:
            stop_server()

    _last_selected_model = display_name or model_id
    _last_selected_loaded = True
    _db_save_last_selected(_last_selected_model)

    threading.Thread(target=_start_thread, args=(model_id, port), daemon=True).start()


def stop_server() -> None:
    global _process, _current_model
    with _lock:
        proc = _process
    if proc is None:
        _set_status("stopped")
        return

    _append_log("[server] Stopping ...")
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception as e:
        _append_log(f"[server] Error stopping: {e}")

    with _lock:
        _process = None
        _current_model = None
    _set_status("stopped")
    _append_log("[server] Stopped.")
