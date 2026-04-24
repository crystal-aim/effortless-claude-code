"""Export / import an install profile — a portable bundle of ECC items,
MCP servers, and hook state that can be re-applied on another machine.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.ecc import hooks as ecc_hooks
from app.ecc import installer as ecc_installer
from app.ecc import mcp as ecc_mcp
from app.ecc import sync as ecc_sync
from app.ecc import uninstaller as ecc_uninstaller

logger = logging.getLogger(__name__)

PROFILE_VERSION = 1


def export_profile() -> dict:
    """Snapshot the current install state across all categories."""
    installs = ecc_uninstaller.list_installs(with_diff=False)
    files: list[dict] = []
    mcp_ids: list[str] = []
    hooks_present = False
    # For uniform re-apply we collect a single (target, project_path) per category.
    file_target: Optional[dict] = None
    mcp_target: Optional[dict] = None
    hooks_target: Optional[dict] = None
    for r in installs:
        cat = r["category"]
        if cat in {"agents", "skills", "commands", "rules"}:
            files.append({"category": cat, "id": r["item_id"]})
            if file_target is None:
                file_target = {"target": r["target"], "project_path": _derive_project_path(r)}
        elif cat == "mcp":
            mcp_ids.append(r["item_id"])
            if mcp_target is None:
                mcp_target = {"target": r["target"], "project_path": _derive_project_path(r)}
        elif cat == "hooks":
            hooks_present = True
            if hooks_target is None:
                hooks_target = {"target": r["target"], "project_path": _derive_project_path(r)}

    from app.db import session_scope
    from app.models import Setting

    pref: dict = {"target": "user", "project_path": None}
    with session_scope() as db:
        row = db.get(Setting, "ecc.default_target")
        if row and row.value:
            pref["target"] = row.value
        row2 = db.get(Setting, "ecc.default_project_path")
        if row2 and row2.value:
            pref["project_path"] = row2.value

    return {
        "version": PROFILE_VERSION,
        "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "ecc_commit": ecc_sync.get_repo_status().commit,
        "target_pref": pref,
        "files": {**(file_target or {"target": pref["target"], "project_path": pref["project_path"]}),
                  "items": files},
        "mcp": {**(mcp_target or {"target": pref["target"], "project_path": pref["project_path"]}),
                "server_ids": mcp_ids},
        "hooks": {**(hooks_target or {"target": pref["target"], "project_path": pref["project_path"]}),
                  "installed": hooks_present},
    }


def _derive_project_path(row: dict) -> Optional[str]:
    if row.get("target") != "project":
        return None
    td = row.get("target_dir", "")
    # target_dir is ".../<project>/.claude" — strip the trailing segment.
    p = Path(td)
    if p.name == ".claude":
        return str(p.parent)
    return td


def import_profile(profile: dict, backup: bool = True) -> dict:
    """Apply the snapshot. Missing items are silently skipped (reported back)."""
    if not isinstance(profile, dict):
        return {"error": "invalid profile"}
    if profile.get("version") != PROFILE_VERSION:
        return {"error": f"unsupported profile version: {profile.get('version')!r}"}

    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise RuntimeError("ECC repo is not synced; sync before importing a profile")
    repo_path = Path(st.repo_path)

    summary: dict[str, Any] = {}

    # Files (agents/skills/commands/rules)
    files_cfg = profile.get("files") or {}
    items = files_cfg.get("items") or []
    if items:
        try:
            target_dir = ecc_installer.resolve_target(
                files_cfg.get("target", "user"),
                files_cfg.get("project_path"),
            )
            plan = ecc_installer.plan_install(items, repo_path, target_dir)
            result = ecc_installer.apply_install(
                plan, repo_path, backup=backup, target=files_cfg.get("target", "user"),
            )
            summary["files"] = {
                "installed": result["installed"],
                "missing": plan.missing,
                "errors": result["errors"],
            }
        except ValueError as e:
            summary["files"] = {"error": str(e)}

    # MCP servers
    mcp_cfg = profile.get("mcp") or {}
    mcp_ids = mcp_cfg.get("server_ids") or []
    if mcp_ids:
        try:
            target_path = ecc_mcp.resolve_mcp_target(
                mcp_cfg.get("target", "user"),
                mcp_cfg.get("project_path"),
            )
            result = ecc_mcp.apply_mcp_install(
                mcp_ids, target_path, mcp_cfg.get("target", "user"), repo_path, backup=backup,
            )
            summary["mcp"] = {
                "installed": result["installed"],
                "errors": result["errors"],
            }
        except ValueError as e:
            summary["mcp"] = {"error": str(e)}

    # Hooks
    hooks_cfg = profile.get("hooks") or {}
    if hooks_cfg.get("installed"):
        try:
            result = ecc_hooks.apply_hook_install(
                hooks_cfg.get("target", "user"),
                hooks_cfg.get("project_path"),
                repo_path,
                backup=backup,
            )
            summary["hooks"] = {
                "installed": result["installed"],
                "plugin_link": result["plugin_link"],
            }
        except ValueError as e:
            summary["hooks"] = {"error": str(e)}

    return summary
