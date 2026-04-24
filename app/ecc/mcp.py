"""Merge MCP server definitions from ECC into the user's MCP config file.

Target files:
- target='user'    -> ~/.claude.json           (top-level `mcpServers` key)
- target='project' -> <project>/.mcp.json      (standalone, same schema)
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.ecc.hashes import extra_with_dict_hash

logger = logging.getLogger(__name__)

MCP_FILENAME_USER = ".claude.json"
MCP_FILENAME_PROJECT = ".mcp.json"
PLACEHOLDER_TOKEN = "YOUR_"


@dataclass
class McpPlanEntry:
    id: str
    exists: bool             # key already in target file
    has_placeholders: bool   # definition contains YOUR_*_HERE
    definition: dict         # incoming definition

    def to_dict(self) -> dict:
        return asdict(self)


def source_servers(repo_path: Path) -> dict[str, dict]:
    f = repo_path / "mcp-configs" / "mcp-servers.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("mcp-servers.json invalid: %s", e)
        return {}
    return data.get("mcpServers") or {}


def resolve_mcp_target(target: str, project_path: Optional[str]) -> Path:
    if target == "user":
        return (Path.home() / MCP_FILENAME_USER).resolve()
    if target == "project":
        if not project_path:
            raise ValueError("project_path is required when target='project'")
        p = Path(project_path).expanduser()
        if not p.is_absolute():
            raise ValueError(f"project_path must be absolute: {project_path}")
        p = p.resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"project_path does not exist or is not a directory: {p}")
        return (p / MCP_FILENAME_PROJECT).resolve()
    raise ValueError(f"invalid target: {target!r}")


def _load_json(path: Path) -> dict:
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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _has_placeholders(definition: Any) -> bool:
    if isinstance(definition, dict):
        return any(_has_placeholders(v) for v in definition.values())
    if isinstance(definition, list):
        return any(_has_placeholders(v) for v in definition)
    if isinstance(definition, str):
        return PLACEHOLDER_TOKEN in definition
    return False


def plan_mcp_install(
    server_ids: list[str],
    target_path: Path,
    repo_path: Path,
) -> dict:
    src = source_servers(repo_path)
    existing = _load_json(target_path).get("mcpServers") or {}
    entries: list[McpPlanEntry] = []
    missing: list[str] = []
    merged = dict(existing)
    for sid in server_ids:
        defn = src.get(sid)
        if defn is None:
            missing.append(sid)
            continue
        entries.append(McpPlanEntry(
            id=sid,
            exists=sid in existing,
            has_placeholders=_has_placeholders(defn),
            definition=defn,
        ))
        merged[sid] = defn
    return {
        "target_path": str(target_path),
        "entries": [e.to_dict() for e in entries],
        "missing": missing,
        "creates_count": sum(1 for e in entries if not e.exists),
        "overwrites_count": sum(1 for e in entries if e.exists),
        "preview_merged": {"mcpServers": merged},
    }


def apply_mcp_install(
    server_ids: list[str],
    target_path: Path,
    target: str,
    repo_path: Path,
    backup: bool,
) -> dict:
    src = source_servers(repo_path)
    data = _load_json(target_path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path: Optional[str] = None
    has_prior_tracker = _target_has_tracker(target_path.parent, "mcp")
    if backup and target_path.exists() and not has_prior_tracker:
        bak = Path(str(target_path) + f".bak.{ts}")
        shutil.copy2(target_path, bak)
        backup_path = str(bak)

    installed = 0
    errors: list[dict] = []
    tracker_rows: list[dict] = []
    for sid in server_ids:
        defn = src.get(sid)
        if defn is None:
            errors.append({"id": sid, "error": "server not found in ECC catalog"})
            continue
        prior = servers.get(sid)
        servers[sid] = defn
        installed += 1
        base_extra = json.dumps({"prior": prior}) if prior is not None else None
        tracker_rows.append({
            "source": "ecc",
            "category": "mcp",
            "item_id": sid,
            "target": target,
            "target_dir": str(target_path.parent),
            "dest": f"{target_path}#mcpServers.{sid}",
            "backup_path": backup_path,
            "extra": extra_with_dict_hash(base_extra, defn),
        })

    data["mcpServers"] = servers
    _write_json(target_path, data)

    if tracker_rows:
        _insert_tracker(tracker_rows)

    return {
        "installed": installed,
        "backup_path": backup_path,
        "errors": errors,
        "target_path": str(target_path),
    }


def uninstall_mcp(rows: list[dict]) -> dict:
    """Remove MCP entries from target files. `rows` from EccInstall tracker."""
    by_target: dict[str, list[dict]] = {}
    for r in rows:
        # dest format: "<path>#mcpServers.<sid>"
        target_path = r["dest"].split("#", 1)[0]
        by_target.setdefault(target_path, []).append(r)

    removed = 0
    errors: list[dict] = []
    for target_str, rrows in by_target.items():
        p = Path(target_str)
        try:
            data = _load_json(p)
        except ValueError as e:
            errors.append({"target": target_str, "error": str(e)})
            continue
        servers = data.get("mcpServers") or {}
        for r in rrows:
            sid = r["item_id"]
            prior = None
            if r.get("extra"):
                try:
                    prior = json.loads(r["extra"]).get("prior")
                except json.JSONDecodeError:
                    prior = None
            if sid in servers:
                if prior is not None:
                    servers[sid] = prior
                else:
                    del servers[sid]
                removed += 1
        data["mcpServers"] = servers
        try:
            _write_json(p, data)
        except OSError as e:
            errors.append({"target": target_str, "error": str(e)})
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
    """Upsert tracker; preserve prior state but update install_hash."""
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
