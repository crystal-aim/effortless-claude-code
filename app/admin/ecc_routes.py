"""Admin API for the everything-claude-code local installer."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import require_admin
from app.ecc import auto_sync as ecc_auto_sync
from app.ecc import catalog as ecc_catalog
from app.ecc import hooks as ecc_hooks
from app.ecc import installer as ecc_installer
from app.ecc import mcp as ecc_mcp
from app.ecc import presets as ecc_presets
from app.ecc import profile as ecc_profile
from app.ecc import sync as ecc_sync
from app.ecc import token_filter as ecc_token_filter
from app.ecc import uninstaller as ecc_uninstaller
from app.models import User

router = APIRouter(prefix="/api/ecc", tags=["admin:ecc"])


SETTING_DEFAULT_TARGET = "ecc.default_target"
SETTING_DEFAULT_PROJECT = "ecc.default_project_path"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ItemRef(BaseModel):
    category: Literal["agents", "skills", "commands", "rules"]
    id: str


class InstallIn(BaseModel):
    items: list[ItemRef]
    target: Literal["user", "project"] = "user"
    project_path: Optional[str] = None


class ApplyIn(InstallIn):
    backup: bool = True


class TargetPrefIn(BaseModel):
    target: Literal["user", "project"]
    project_path: Optional[str] = None


class McpInstallIn(BaseModel):
    server_ids: list[str]
    target: Literal["user", "project"] = "user"
    project_path: Optional[str] = None


class McpApplyIn(McpInstallIn):
    backup: bool = True


class HookInstallIn(BaseModel):
    target: Literal["user", "project"] = "user"
    project_path: Optional[str] = None


class HookApplyIn(HookInstallIn):
    backup: bool = True


class UninstallIn(BaseModel):
    install_ids: list[int]


class AutoSyncIn(BaseModel):
    enabled: Optional[bool] = None
    interval_hours: Optional[int] = None


class ProfileImportIn(BaseModel):
    profile: dict
    backup: bool = True


class TokenFilterConfigIn(BaseModel):
    max_lines: int = ecc_token_filter.DEFAULT_MAX_LINES
    tail_lines: int = ecc_token_filter.DEFAULT_TAIL_LINES
    mlx_enabled: bool = ecc_token_filter.DEFAULT_MLX_ENABLED
    mlx_threshold: int = ecc_token_filter.DEFAULT_MLX_THRESHOLD
    mlx_url: str = ecc_token_filter.DEFAULT_MLX_URL


class TokenFilterInstallIn(BaseModel):
    target: Literal["user", "project"] = "user"
    project_path: Optional[str] = None
    backup: bool = True
    max_lines: int = ecc_token_filter.DEFAULT_MAX_LINES
    tail_lines: int = ecc_token_filter.DEFAULT_TAIL_LINES
    mlx_enabled: bool = ecc_token_filter.DEFAULT_MLX_ENABLED
    mlx_threshold: int = ecc_token_filter.DEFAULT_MLX_THRESHOLD
    mlx_url: str = ecc_token_filter.DEFAULT_MLX_URL


# ---------------------------------------------------------------------------
# Sync + status
# ---------------------------------------------------------------------------

@router.get("/status")
def ecc_status(_admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    pref_target = _read_setting(SETTING_DEFAULT_TARGET) or "user"
    pref_project = _read_setting(SETTING_DEFAULT_PROJECT)
    return {
        **st.to_dict(),
        "repo_url": ecc_sync.ECC_REPO_URL,
        "default_target": pref_target,
        "default_project_path": pref_project,
    }


@router.post("/sync")
def ecc_do_sync(_admin: User = Depends(require_admin)):
    try:
        st = ecc_sync.sync_repo()
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ECC sync failed: {e}")
    return {"ok": True, **st.to_dict()}


# ---------------------------------------------------------------------------
# Catalog + presets
# ---------------------------------------------------------------------------

@router.get("/catalog")
def ecc_get_catalog(_admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        return {"cached": False, "agents": [], "skills": [], "commands": [], "rules": []}
    cat = ecc_catalog.build_catalog(Path(st.repo_path))
    return {"cached": True, **cat}


@router.get("/presets")
def ecc_get_presets(_admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        return {"cached": False, "presets": []}
    cat = ecc_catalog.build_catalog(Path(st.repo_path))
    return {"cached": True, "presets": ecc_presets.preset_summary(cat)}


@router.get("/presets/{preset_id}/items")
def ecc_preset_items(preset_id: str, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    if preset_id not in ecc_presets.PRESETS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown preset: {preset_id}")
    cat = ecc_catalog.build_catalog(Path(st.repo_path))
    return {"items": ecc_presets.resolve_preset(preset_id, cat)}


# ---------------------------------------------------------------------------
# Install plan + apply
# ---------------------------------------------------------------------------

@router.post("/install/plan")
def ecc_plan(body: InstallIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        target_dir = ecc_installer.resolve_target(body.target, body.project_path)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    items = [it.model_dump() for it in body.items]
    plan = ecc_installer.plan_install(items, Path(st.repo_path), target_dir)
    return plan.to_dict()


@router.post("/install/apply")
def ecc_apply(body: ApplyIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        target_dir = ecc_installer.resolve_target(body.target, body.project_path)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    items = [it.model_dump() for it in body.items]
    plan = ecc_installer.plan_install(items, Path(st.repo_path), target_dir)
    result = ecc_installer.apply_install(plan, Path(st.repo_path), backup=body.backup, target=body.target)
    # Persist user's target choice for next visit.
    _write_setting(SETTING_DEFAULT_TARGET, body.target)
    if body.target == "project" and body.project_path:
        _write_setting(SETTING_DEFAULT_PROJECT, body.project_path)
    return {"plan": plan.to_dict(), "result": result}


# ---------------------------------------------------------------------------
# Target preference (persisted)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

@router.get("/mcp/servers")
def ecc_mcp_servers(_admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        return {"cached": False, "servers": []}
    src = ecc_mcp.source_servers(Path(st.repo_path))
    servers = []
    for sid, defn in src.items():
        servers.append({
            "id": sid,
            "description": (defn.get("description") or "")[:400],
            "type": defn.get("type", "stdio"),
            "has_placeholders": ecc_mcp._has_placeholders(defn),
            "definition": defn,
        })
    return {"cached": True, "servers": servers}


@router.post("/mcp/install/plan")
def ecc_mcp_plan(body: McpInstallIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        target_path = ecc_mcp.resolve_mcp_target(body.target, body.project_path)
        return ecc_mcp.plan_mcp_install(body.server_ids, target_path, Path(st.repo_path))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/mcp/install/apply")
def ecc_mcp_apply(body: McpApplyIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        target_path = ecc_mcp.resolve_mcp_target(body.target, body.project_path)
        return ecc_mcp.apply_mcp_install(
            body.server_ids, target_path, body.target, Path(st.repo_path), backup=body.backup,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

@router.get("/hooks/list")
def ecc_hooks_list(_admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        return {"cached": False, "events": {}}
    src = ecc_hooks.load_source_hooks(Path(st.repo_path))
    events = {}
    for event, entries in src.items():
        events[event] = [{
            "id": e.get("id"),
            "matcher": e.get("matcher", ""),
            "description": e.get("description", ""),
        } for e in entries]
    return {
        "cached": True,
        "events": events,
        "plugin_link": {
            "path": str(ecc_hooks.PLUGIN_LINK_DIR),
            "exists": ecc_hooks.PLUGIN_LINK_DIR.exists(),
            "is_symlink": ecc_hooks.PLUGIN_LINK_DIR.is_symlink(),
        },
    }


@router.post("/hooks/install/plan")
def ecc_hooks_plan(body: HookInstallIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        return ecc_hooks.plan_hook_install(body.target, body.project_path, Path(st.repo_path))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/hooks/install/apply")
def ecc_hooks_apply(body: HookApplyIn, _admin: User = Depends(require_admin)):
    st = ecc_sync.get_repo_status()
    if not st.cached:
        raise HTTPException(status.HTTP_409_CONFLICT, "ECC repo is not synced yet")
    try:
        return ecc_hooks.apply_hook_install(
            body.target, body.project_path, Path(st.repo_path), backup=body.backup,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ---------------------------------------------------------------------------
# Token filter
# ---------------------------------------------------------------------------

@router.get("/token-filter/status")
def token_filter_status(
    target: str = "user",
    project_path: Optional[str] = None,
    _admin: User = Depends(require_admin),
):
    try:
        return ecc_token_filter.get_status(target, project_path)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/token-filter/config")
def token_filter_config(body: TokenFilterConfigIn, _admin: User = Depends(require_admin)):
    return ecc_token_filter.save_config(
        body.max_lines, body.tail_lines,
        mlx_enabled=body.mlx_enabled,
        mlx_threshold=body.mlx_threshold,
        mlx_url=body.mlx_url,
    )


@router.post("/token-filter/install")
def token_filter_install(body: TokenFilterInstallIn, _admin: User = Depends(require_admin)):
    try:
        return ecc_token_filter.install(
            body.target, body.project_path, backup=body.backup,
            max_lines=body.max_lines, tail_lines=body.tail_lines,
            mlx_enabled=body.mlx_enabled,
            mlx_threshold=body.mlx_threshold,
            mlx_url=body.mlx_url,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.post("/token-filter/uninstall")
def token_filter_uninstall(body: HookInstallIn, _admin: User = Depends(require_admin)):
    try:
        return ecc_token_filter.uninstall(body.target, body.project_path)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


# ---------------------------------------------------------------------------
# Auto-sync
# ---------------------------------------------------------------------------

@router.get("/autosync")
def ecc_autosync_get(_admin: User = Depends(require_admin)):
    return ecc_auto_sync.get_config()


@router.post("/autosync")
def ecc_autosync_set(body: AutoSyncIn, _admin: User = Depends(require_admin)):
    return ecc_auto_sync.set_config(enabled=body.enabled, interval_hours=body.interval_hours)


@router.post("/autosync/run")
async def ecc_autosync_run(_admin: User = Depends(require_admin)):
    return await ecc_auto_sync.run_once()


# ---------------------------------------------------------------------------
# Installed tracker + uninstall
# ---------------------------------------------------------------------------

@router.get("/installs")
def ecc_list_installs(
    source: Optional[str] = None,
    target_dir: Optional[str] = None,
    _admin: User = Depends(require_admin),
):
    return {"installs": ecc_uninstaller.list_installs(source=source, target_dir=target_dir)}


@router.post("/uninstall")
def ecc_uninstall(body: UninstallIn, _admin: User = Depends(require_admin)):
    return ecc_uninstaller.uninstall(body.install_ids)


# ---------------------------------------------------------------------------
# Profile export / import
# ---------------------------------------------------------------------------

@router.get("/profile/export")
def ecc_profile_export(_admin: User = Depends(require_admin)):
    return ecc_profile.export_profile()


@router.post("/profile/import")
def ecc_profile_import(body: ProfileImportIn, _admin: User = Depends(require_admin)):
    try:
        return ecc_profile.import_profile(body.profile, backup=body.backup)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


# ---------------------------------------------------------------------------
# Target preference
# ---------------------------------------------------------------------------

@router.post("/target-pref")
def ecc_set_target(body: TargetPrefIn, _admin: User = Depends(require_admin)):
    _write_setting(SETTING_DEFAULT_TARGET, body.target)
    if body.target == "project" and body.project_path:
        _write_setting(SETTING_DEFAULT_PROJECT, body.project_path)
    return {"target": body.target, "project_path": body.project_path}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from app.db import read_setting as _read_setting, write_setting as _write_setting
