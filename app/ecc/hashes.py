"""File/directory content hashing for install tracking + upstream-diff detection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def hash_path(p: Path) -> str:
    """SHA-256 digest of a file or directory tree. Returns 'sha256:<hex>' or ''."""
    if not p.exists():
        return ""
    if p.is_file():
        return "sha256:" + _hash_file(p).hexdigest()
    if p.is_dir():
        h = hashlib.sha256()
        for f in sorted(p.rglob("*")):
            if f.is_file():
                rel = f.relative_to(p).as_posix().encode("utf-8")
                h.update(rel + b"\x00")
                h.update(_hash_file(f).digest())
        return "sha256:" + h.hexdigest()
    return ""


def _hash_file(path: Path) -> "hashlib._Hash":
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h


def hash_dict(d: Any) -> str:
    """Canonical SHA-256 of a JSON-serializable value. Returns 'sha256:<hex>'."""
    s = json.dumps(d, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def extra_with_dict_hash(existing_extra: str | None, value: Any) -> str:
    """Merge hash-of-value into tracker.extra JSON blob."""
    try:
        data = json.loads(existing_extra) if existing_extra else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["install_hash"] = hash_dict(value)
    return json.dumps(data)


def extra_with_hash(existing_extra: str | None, dest: Path) -> str:
    """Merge install hash into tracker.extra JSON blob."""
    try:
        data = json.loads(existing_extra) if existing_extra else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["install_hash"] = hash_path(dest)
    return json.dumps(data)


def read_install_hash(extra: str | None) -> str:
    if not extra:
        return ""
    try:
        data = json.loads(extra)
    except json.JSONDecodeError:
        return ""
    return data.get("install_hash", "") if isinstance(data, dict) else ""
