"""Query + revert installs tracked in the `ecc_installs` table."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Optional

import json
from app.db import session_scope
from app.ecc import hooks as ecc_hooks
from app.ecc import mcp as ecc_mcp
from app.ecc import sync as ecc_sync
from app.ecc.hashes import hash_dict, hash_path, read_install_hash
from app.models import EccInstall

logger = logging.getLogger(__name__)

FILE_CATEGORIES = {"agents", "skills", "commands", "rules"}


def list_installs(
    source: Optional[str] = None,
    target_dir: Optional[str] = None,
    with_diff: bool = True,
) -> list[dict]:
    with session_scope() as db:
        q = db.query(EccInstall)
        if source:
            q = q.filter(EccInstall.source == source)
        if target_dir:
            q = q.filter(EccInstall.target_dir == target_dir)
        rows = q.order_by(EccInstall.target_dir, EccInstall.category, EccInstall.item_id).all()
        dicts = [_row_to_dict(r) for r in rows]

    if not with_diff:
        return dicts

    # Compute diff fields (modified_by_user, upstream_changed) by hashing current
    # disk state + current repo state against the install_hash snapshotted at install.
    repo_path = Path(ecc_sync.CACHE_DIR) if ecc_sync.CACHE_DIR.exists() else None
    # Lazy-load MCP source + hook source from the repo.
    mcp_source: Optional[dict] = None
    hook_source: Optional[dict[str, list[dict]]] = None
    if repo_path:
        try:
            mcp_source = ecc_mcp.source_servers(repo_path)
        except Exception:
            mcp_source = None
        try:
            hook_source = ecc_hooks.load_source_hooks(repo_path)
        except Exception:
            hook_source = None

    # Cache for parsed target JSON files to avoid re-reading per item.
    json_cache: dict[str, Any] = {}

    def _load_json_cached(path: str) -> dict:
        if path not in json_cache:
            p = Path(path)
            if p.exists():
                try:
                    json_cache[path] = json.loads(p.read_text(encoding="utf-8") or "{}")
                except (json.JSONDecodeError, OSError):
                    json_cache[path] = {}
            else:
                json_cache[path] = {}
        return json_cache[path]

    for row in dicts:
        row["install_hash"] = read_install_hash(row.get("extra"))
        row["current_hash"] = ""
        row["upstream_hash"] = ""
        cat = row["category"]
        try:
            if cat in {"agents", "skills", "commands", "rules"}:
                row["current_hash"] = hash_path(Path(row["dest"]))
                if repo_path:
                    # Reconstruct upstream source path from dest relative to target_dir.
                    dest_p = Path(row["dest"])
                    rel = dest_p.relative_to(row["target_dir"]).as_posix()
                    up = repo_path / rel
                    if not up.exists() and dest_p.name and not dest_p.name.endswith(".md"):
                        # skills dir case — try with trailing /
                        up = repo_path / rel
                    row["upstream_hash"] = hash_path(up)
            elif cat == "mcp":
                target_file = row["dest"].split("#", 1)[0]
                servers = (_load_json_cached(target_file).get("mcpServers") or {})
                defn = servers.get(row["item_id"])
                if defn is not None:
                    row["current_hash"] = hash_dict(defn)
                if mcp_source is not None:
                    up_defn = mcp_source.get(row["item_id"])
                    if up_defn is not None:
                        row["upstream_hash"] = hash_dict(up_defn)
            elif cat == "hooks":
                target_file = row["dest"].split("#", 1)[0]
                # item_id = "<event>:<eid>"
                try:
                    event, eid = row["item_id"].split(":", 1)
                except ValueError:
                    event = eid = None
                if event and eid:
                    hooks_block = (_load_json_cached(target_file).get("hooks") or {})
                    current = _find_hook_by_id(hooks_block.get(event) or [], eid)
                    if current is not None:
                        row["current_hash"] = hash_dict(current)
                    if hook_source is not None:
                        up_entries = hook_source.get(event) or []
                        up = _find_hook_by_id(up_entries, eid)
                        if up is not None:
                            row["upstream_hash"] = hash_dict(up)
            elif cat == "token-filter":
                target_file = row["dest"].split("#", 1)[0]
                try:
                    event, eid = row["item_id"].split(":", 1)
                except ValueError:
                    event = eid = None
                if event and eid:
                    hooks_block = (_load_json_cached(target_file).get("hooks") or {})
                    current = _find_hook_by_id(hooks_block.get(event) or [], eid)
                    if current is not None:
                        row["current_hash"] = hash_dict(current)
                    from app.ecc.token_filter import _hook_entry
                    row["upstream_hash"] = hash_dict(_hook_entry())
            elif cat == "plugin":
                # plugin symlink — presence check only
                row["current_hash"] = "present" if Path(row["dest"]).exists() else ""
                row["upstream_hash"] = "present" if (repo_path and repo_path.exists()) else ""
        except Exception:
            # Best-effort; don't let a single bad row block the list.
            pass

        ih = row["install_hash"]
        ch = row["current_hash"]
        uh = row["upstream_hash"]
        row["modified_by_user"] = bool(ih and ch and ih != ch) if ch else False
        row["upstream_changed"] = bool(ih and uh and ih != uh) if uh else False

    return dicts


def _find_hook_by_id(entries: list, target_id: str) -> Optional[dict]:
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("id") == target_id:
            return e
    return None


def uninstall(install_ids: list[int]) -> dict:
    if not install_ids:
        return {"removed": 0, "errors": [], "per_category": {}}

    with session_scope() as db:
        rows = db.query(EccInstall).filter(EccInstall.id.in_(install_ids)).all()
        row_dicts = [_row_to_dict(r) for r in rows]

    by_category: dict[str, list[dict]] = {}
    for r in row_dicts:
        by_category.setdefault(r["category"], []).append(r)

    removed = 0
    errors: list[dict] = []
    per_category: dict[str, int] = {}

    # File-based items
    for cat in FILE_CATEGORIES:
        if cat in by_category:
            n, errs = _uninstall_files(by_category[cat])
            removed += n
            errors.extend(errs)
            per_category[cat] = n

    if "mcp" in by_category:
        result = ecc_mcp.uninstall_mcp(by_category["mcp"])
        removed += result["removed"]
        errors.extend(result["errors"])
        per_category["mcp"] = result["removed"]

    if "token-filter" in by_category:
        from app.ecc import token_filter
        result = token_filter.uninstall_from_tracker(by_category["token-filter"])
        removed += result["removed"]
        errors.extend(result["errors"])
        per_category["token-filter"] = result["removed"]

    if "hooks" in by_category or "plugin" in by_category:
        payload = by_category.get("hooks", []) + by_category.get("plugin", [])
        result = ecc_hooks.uninstall_hooks(payload)
        removed += result["removed"]
        errors.extend(result["errors"])
        # splitting counts heuristically — hooks removed equals payload length minus errors
        per_category["hooks"] = len(by_category.get("hooks", []))
        if "plugin" in by_category:
            per_category["plugin"] = len(by_category.get("plugin", []))

    # Remove tracker rows for items with no errors — all-or-nothing per id.
    err_ids: set[int] = set()
    for e in errors:
        if isinstance(e, dict) and "id" in e:
            try:
                err_ids.add(int(e["id"]))
            except (ValueError, TypeError):
                pass

    with session_scope() as db:
        db.query(EccInstall).filter(
            EccInstall.id.in_([r["id"] for r in row_dicts if r["id"] not in err_ids])
        ).delete(synchronize_session=False)

    return {
        "removed": removed,
        "errors": errors,
        "per_category": per_category,
    }


def _uninstall_files(rows: list[dict]) -> tuple[int, list[dict]]:
    removed = 0
    errors: list[dict] = []
    for r in rows:
        dest = Path(r["dest"])
        bak = Path(r["backup_path"]) if r.get("backup_path") else None
        try:
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            if bak and bak.exists():
                if bak.is_dir():
                    shutil.move(str(bak), str(dest))
                else:
                    bak.rename(dest)
            removed += 1
        except OSError as e:
            errors.append({"id": r["id"], "item": f"{r['category']}/{r['item_id']}", "error": str(e)})
    return removed, errors


def _row_to_dict(r: EccInstall) -> dict:
    return {
        "id": r.id,
        "source": r.source,
        "category": r.category,
        "item_id": r.item_id,
        "target": r.target,
        "target_dir": r.target_dir,
        "dest": r.dest,
        "backup_path": r.backup_path,
        "extra": r.extra,
        "installed_at": r.installed_at.isoformat() + "Z" if r.installed_at else None,
    }
