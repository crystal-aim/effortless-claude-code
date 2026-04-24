"""Clone/pull the everything-claude-code repo into a local cache dir."""
from __future__ import annotations

import logging
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ECC_REPO_URL = "https://github.com/affaan-m/everything-claude-code.git"
CACHE_DIR = Path.home() / ".cache" / "claude-croxy" / "ecc-repo"

SETTING_SYNCED_AT = "ecc.last_synced_at"
SETTING_LAST_COMMIT = "ecc.last_commit"


@dataclass
class RepoStatus:
    cached: bool
    commit: Optional[str]
    commit_short: Optional[str]
    synced_at: Optional[str]
    repo_path: str

    def to_dict(self) -> dict:
        return asdict(self)


def get_repo_status() -> RepoStatus:
    if not (CACHE_DIR / ".git").exists():
        return RepoStatus(False, None, None, None, str(CACHE_DIR))
    sha = _git(["rev-parse", "HEAD"]).strip() or None
    synced_at = _read_setting(SETTING_SYNCED_AT)
    return RepoStatus(
        cached=True,
        commit=sha,
        commit_short=sha[:7] if sha else None,
        synced_at=synced_at,
        repo_path=str(CACHE_DIR),
    )


def sync_repo(timeout_seconds: int = 180) -> RepoStatus:
    _ensure_git()
    CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    if (CACHE_DIR / ".git").exists():
        _run(["git", "-C", str(CACHE_DIR), "fetch", "--depth", "1", "origin", "HEAD"], timeout_seconds)
        _run(["git", "-C", str(CACHE_DIR), "reset", "--hard", "FETCH_HEAD"], timeout_seconds)
    else:
        _run(
            ["git", "clone", "--depth", "1", ECC_REPO_URL, str(CACHE_DIR)],
            timeout_seconds,
        )
    status = get_repo_status()
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    _write_setting(SETTING_SYNCED_AT, now)
    if status.commit:
        _write_setting(SETTING_LAST_COMMIT, status.commit)
    return RepoStatus(
        cached=status.cached,
        commit=status.commit,
        commit_short=status.commit_short,
        synced_at=now,
        repo_path=status.repo_path,
    )


def _ensure_git() -> None:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise RuntimeError("git is not available on PATH. Install git to sync the ECC repo.") from e


def _run(cmd: list[str], timeout_seconds: int) -> str:
    logger.info("ecc sync: %s", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def _git(args: list[str]) -> str:
    r = subprocess.run(
        ["git", "-C", str(CACHE_DIR)] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.stdout if r.returncode == 0 else ""


from app.db import read_setting as _read_setting, write_setting as _write_setting
