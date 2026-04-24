"""Install ECC hooks into a target .claude/settings.json.

ECC hooks reference `CLAUDE_PLUGIN_ROOT` via a bootstrap that searches
`~/.claude/plugins/{ecc,everything-claude-code,...}`. We therefore
expose the ECC repo at one of those paths via symlink before merging
hook entries into the target `settings.json`.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.ecc.hashes import extra_with_dict_hash

logger = logging.getLogger(__name__)

PLUGIN_NAME = "everything-claude-code"
PLUGIN_LINK_DIR = Path.home() / ".claude" / "plugins" / PLUGIN_NAME
ECC_HOOKS_FILE = "hooks/hooks.json"


# ---------------------------------------------------------------------------
# Plugin symlink
# ---------------------------------------------------------------------------

def ensure_plugin_link(repo_path: Path) -> dict:
    """Create ~/.claude/plugins/everything-claude-code -> repo_path if missing.

    Returns {'created': bool, 'path': str, 'method': 'symlink'|'exists'|'copy'}.
    """
    PLUGIN_LINK_DIR.parent.mkdir(parents=True, exist_ok=True)
    if PLUGIN_LINK_DIR.is_symlink():
        # already linked — verify it still points somewhere valid; otherwise re-create
        try:
            target = PLUGIN_LINK_DIR.resolve(strict=True)
            if target == repo_path.resolve():
                return {"created": False, "path": str(PLUGIN_LINK_DIR), "method": "exists"}
        except (FileNotFoundError, RuntimeError):
            PLUGIN_LINK_DIR.unlink()
    elif PLUGIN_LINK_DIR.exists():
        # a real dir exists there — refuse to touch it
        return {"created": False, "path": str(PLUGIN_LINK_DIR), "method": "existing-dir"}
    try:
        os.symlink(str(repo_path), str(PLUGIN_LINK_DIR), target_is_directory=True)
        return {"created": True, "path": str(PLUGIN_LINK_DIR), "method": "symlink"}
    except OSError as e:
        # fallback: copy the repo (slow, but works on FS without symlink support)
        logger.warning("symlink failed (%s); falling back to copy", e)
        shutil.copytree(repo_path, PLUGIN_LINK_DIR)
        return {"created": True, "path": str(PLUGIN_LINK_DIR), "method": "copy"}


def remove_plugin_link() -> bool:
    if PLUGIN_LINK_DIR.is_symlink():
        PLUGIN_LINK_DIR.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# Hook source + target helpers
# ---------------------------------------------------------------------------

def load_source_hooks(repo_path: Path) -> dict[str, list[dict]]:
    """Return {event: [entry, ...]} from ECC hooks.json."""
    f = repo_path / ECC_HOOKS_FILE
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("hooks.json invalid: %s", e)
        return {}
    hooks = data.get("hooks") or {}
    # each value should be a list of {matcher, hooks:[...], id, description}
    out: dict[str, list[dict]] = {}
    for event, entries in hooks.items():
        if isinstance(entries, list):
            out[event] = entries
    return out


def resolve_settings_target(target: str, project_path: Optional[str]) -> Path:
    if target == "user":
        return (Path.home() / ".claude" / "settings.json").resolve()
    if target == "project":
        if not project_path:
            raise ValueError("project_path is required when target='project'")
        p = Path(project_path).expanduser()
        if not p.is_absolute():
            raise ValueError(f"project_path must be absolute: {project_path}")
        p = p.resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"project_path does not exist or is not a directory: {p}")
        return (p / ".claude" / "settings.json").resolve()
    raise ValueError(f"invalid target: {target!r}")


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON ({e.msg} at line {e.lineno} col {e.colno})")
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object at root")
    return data


def _write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _entry_id(entry: dict) -> Optional[str]:
    """Best-effort stable id for dedup. Prefer top-level 'id' (ECC convention)."""
    eid = entry.get("id")
    if isinstance(eid, str) and eid:
        return eid
    # fallback: hash of matcher + inner hooks commands
    parts = [entry.get("matcher", "")]
    for h in entry.get("hooks", []) or []:
        parts.append(str(h.get("command", "")))
    key = "|".join(parts)
    return f"auto:{hash(key) & 0xFFFFFFFF:x}" if key else None


# ---------------------------------------------------------------------------
# Plan + apply
# ---------------------------------------------------------------------------

def plan_hook_install(target: str, project_path: Optional[str], repo_path: Path) -> dict:
    target_path = resolve_settings_target(target, project_path)
    source = load_source_hooks(repo_path)
    try:
        existing_settings = _load_settings(target_path)
    except ValueError as e:
        raise ValueError(str(e))
    existing_hooks = existing_settings.get("hooks") or {}

    entries_by_event: dict[str, list[dict]] = {}
    overwrites = 0
    creates = 0
    for event, entries in source.items():
        existing_event = existing_hooks.get(event) or []
        existing_ids = {_entry_id(e) for e in existing_event if isinstance(e, dict)}
        out_entries = []
        for e in entries:
            eid = _entry_id(e)
            exists = eid in existing_ids
            if exists:
                overwrites += 1
            else:
                creates += 1
            out_entries.append({
                "id": eid,
                "matcher": e.get("matcher", ""),
                "description": e.get("description", ""),
                "exists": exists,
            })
        entries_by_event[event] = out_entries

    plugin_status = "linked" if PLUGIN_LINK_DIR.is_symlink() else ("existing-dir" if PLUGIN_LINK_DIR.exists() else "missing")

    return {
        "target_path": str(target_path),
        "plugin_link": {
            "path": str(PLUGIN_LINK_DIR),
            "status": plugin_status,
        },
        "entries_by_event": entries_by_event,
        "creates_count": creates,
        "overwrites_count": overwrites,
    }


def apply_hook_install(target: str, project_path: Optional[str], repo_path: Path, backup: bool) -> dict:
    target_path = resolve_settings_target(target, project_path)
    source = load_source_hooks(repo_path)

    link_result = ensure_plugin_link(repo_path)

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path: Optional[str] = None
    has_prior_tracker = _target_has_tracker(target_path.parent, "hooks")
    if backup and target_path.exists() and not has_prior_tracker:
        bak = Path(str(target_path) + f".bak.{ts}")
        shutil.copy2(target_path, bak)
        backup_path = str(bak)

    settings = _load_settings(target_path)
    hooks_block = settings.get("hooks")
    if not isinstance(hooks_block, dict):
        hooks_block = {}

    installed = 0
    tracker_rows: list[dict] = []
    target_dir = str(target_path.parent)

    # Track the plugin link itself on first install (so uninstall can remove).
    if link_result.get("created"):
        tracker_rows.append({
            "source": "ecc",
            "category": "plugin",
            "item_id": PLUGIN_NAME,
            "target": "user",
            "target_dir": str(PLUGIN_LINK_DIR.parent),
            "dest": str(PLUGIN_LINK_DIR),
            "backup_path": None,
            "extra": json.dumps({"method": link_result.get("method")}),
        })

    for event, entries in source.items():
        dst_list = hooks_block.get(event)
        if not isinstance(dst_list, list):
            dst_list = []
        existing_by_id: dict[str, int] = {}
        for i, e in enumerate(dst_list):
            if isinstance(e, dict):
                eid = _entry_id(e)
                if eid is not None:
                    existing_by_id[eid] = i
        for src_entry in entries:
            eid = _entry_id(src_entry)
            if eid is None:
                continue
            if eid in existing_by_id:
                dst_list[existing_by_id[eid]] = src_entry
            else:
                dst_list.append(src_entry)
            installed += 1
            tracker_rows.append({
                "source": "ecc",
                "category": "hooks",
                "item_id": f"{event}:{eid}",
                "target": target,
                "target_dir": target_dir,
                "dest": f"{target_path}#hooks.{event}:{eid}",
                "backup_path": backup_path,
                "extra": extra_with_dict_hash(None, src_entry),
            })
        hooks_block[event] = dst_list

    settings["hooks"] = hooks_block
    _write_settings(target_path, settings)

    if tracker_rows:
        _insert_tracker(tracker_rows)

    return {
        "installed": installed,
        "plugin_link": link_result,
        "backup_path": backup_path,
        "target_path": str(target_path),
    }


def uninstall_hooks(rows: list[dict]) -> dict:
    """rows = EccInstall dicts where category in {hooks, plugin}."""
    by_target: dict[str, list[dict]] = {}
    plugin_rows: list[dict] = []
    for r in rows:
        if r["category"] == "plugin":
            plugin_rows.append(r)
            continue
        target_str = r["dest"].split("#", 1)[0]
        by_target.setdefault(target_str, []).append(r)

    removed = 0
    errors: list[dict] = []

    for target_str, rrows in by_target.items():
        p = Path(target_str)
        try:
            settings = _load_settings(p)
        except ValueError as e:
            errors.append({"target": target_str, "error": str(e)})
            continue
        hooks_block = settings.get("hooks") or {}
        for r in rrows:
            # item_id = "<event>:<eid>"
            try:
                event, eid = r["item_id"].split(":", 1)
            except ValueError:
                continue
            dst_list = hooks_block.get(event) or []
            kept = [e for e in dst_list if _entry_id(e) != eid]
            if len(kept) != len(dst_list):
                removed += 1
            hooks_block[event] = kept
        settings["hooks"] = hooks_block
        try:
            _write_settings(p, settings)
        except OSError as e:
            errors.append({"target": target_str, "error": str(e)})

    # remove plugin symlink last (only if asked; leave if user keeps it)
    for r in plugin_rows:
        if remove_plugin_link():
            removed += 1

    return {"removed": removed, "errors": errors}


def _target_has_tracker(target_dir: Path, category: str) -> bool:
    from app.db import session_scope
    from app.models import EccInstall

    with session_scope() as db:
        return db.query(EccInstall).filter(
            EccInstall.target_dir == str(target_dir),
            EccInstall.category == category,
        ).first() is not None


def _insert_tracker(rows: list[dict]) -> None:
    """Upsert tracker; preserve backup_path but update install_hash."""
    from app.ecc.installer import _merge_extra
    from app.db import session_scope
    from app.models import EccInstall

    with session_scope() as db:
        for r in rows:
            existing = db.query(EccInstall).filter(
                EccInstall.source == r["source"],
                EccInstall.category == r["category"],
                EccInstall.item_id == r["item_id"],
                EccInstall.target_dir == r["target_dir"],
            ).first()
            if existing is not None:
                existing.dest = r["dest"]
                existing.target = r["target"]
                existing.extra = _merge_extra(existing.extra, r.get("extra"))
            else:
                db.add(EccInstall(**r))
