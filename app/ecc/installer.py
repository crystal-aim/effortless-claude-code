"""Plan and apply install of catalog items into a target .claude/ dir."""
from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.ecc.catalog import build_catalog
from app.ecc.hashes import extra_with_hash

logger = logging.getLogger(__name__)


@dataclass
class PlanEntry:
    category: str
    id: str
    source: str      # repo-relative
    dest: str        # absolute
    exists: bool
    is_dir: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InstallPlan:
    entries: list[PlanEntry] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)  # items not found in catalog
    target_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "target_dir": self.target_dir,
            "entries": [e.to_dict() for e in self.entries],
            "missing": self.missing,
            "creates_count": sum(1 for e in self.entries if not e.exists),
            "overwrites_count": sum(1 for e in self.entries if e.exists),
        }


def resolve_target(target: str, project_path: Optional[str]) -> Path:
    if target == "user":
        return (Path.home() / ".claude").resolve()
    if target == "project":
        if not project_path:
            raise ValueError("project_path is required when target='project'")
        p = Path(project_path).expanduser()
        if not p.is_absolute():
            raise ValueError(f"project_path must be absolute: {project_path}")
        p = p.resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"project_path does not exist or is not a directory: {p}")
        return (p / ".claude").resolve()
    raise ValueError(f"invalid target: {target!r} (expected 'user' or 'project')")


def _catalog_index(repo_path: Path) -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    for items in build_catalog(repo_path).values():
        for it in items:
            idx[(it["category"], it["id"])] = it
    return idx


def plan_install(items: list[dict], repo_path: Path, target_dir: Path) -> InstallPlan:
    """items: list of {'category', 'id'} — typically from preset or browse selection."""
    idx = _catalog_index(repo_path)
    plan = InstallPlan(target_dir=str(target_dir))
    for item in items:
        cat = item.get("category")
        iid = item.get("id")
        meta = idx.get((cat, iid))
        if meta is None:
            plan.missing.append({"category": cat, "id": iid})
            continue
        src_rel: str = meta["source"]
        if src_rel.endswith("/"):
            dest = target_dir / src_rel.rstrip("/")
            plan.entries.append(PlanEntry(
                category=meta["category"],
                id=meta["id"],
                source=src_rel,
                dest=str(dest),
                exists=dest.exists(),
                is_dir=True,
            ))
        else:
            dest = target_dir / src_rel
            plan.entries.append(PlanEntry(
                category=meta["category"],
                id=meta["id"],
                source=src_rel,
                dest=str(dest),
                exists=dest.exists(),
                is_dir=False,
            ))
    return plan


def apply_install(plan: InstallPlan, repo_path: Path, backup: bool, target: str = "user") -> dict:
    target_dir = Path(plan.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    installed = 0
    backed_up: list[str] = []
    errors: list[dict] = []
    tracker_rows: list[dict] = []

    existing_keys = _existing_tracker_keys(
        [(target_dir, e.category, e.id) for e in plan.entries]
    )

    for e in plan.entries:
        src = repo_path / e.source.rstrip("/")
        dest = Path(e.dest)
        bak_path: Optional[str] = None
        already_tracked = (str(target_dir), e.category, e.id) in existing_keys
        try:
            if dest.exists() and backup and not already_tracked:
                bak = Path(str(dest) + f".bak.{ts}")
                if dest.is_dir():
                    shutil.move(str(dest), str(bak))
                else:
                    dest.rename(bak)
                backed_up.append(str(bak))
                bak_path = str(bak)
            elif dest.exists() and already_tracked:
                # Prior ECC install — discard it (tracker still points to original backup).
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if e.is_dir:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            installed += 1
            tracker_rows.append({
                "source": "ecc",
                "category": e.category,
                "item_id": e.id,
                "target": target,
                "target_dir": str(target_dir),
                "dest": str(dest),
                "backup_path": bak_path,
                "extra": extra_with_hash(None, dest),
            })
        except Exception as ex:
            logger.exception("install failed for %s/%s", e.category, e.id)
            errors.append({"category": e.category, "id": e.id, "error": str(ex)})

    if tracker_rows:
        _insert_tracker(tracker_rows)

    return {
        "installed": installed,
        "backed_up": backed_up,
        "errors": errors,
        "target_dir": str(target_dir),
    }


def _existing_tracker_keys(keys: list[tuple[Path, str, str]]) -> set[tuple[str, str, str]]:
    """Return set of (target_dir_str, category, item_id) already tracked."""
    from app.db import session_scope
    from app.models import EccInstall

    out: set[tuple[str, str, str]] = set()
    if not keys:
        return out
    with session_scope() as db:
        rows = db.query(EccInstall.target_dir, EccInstall.category, EccInstall.item_id).filter(
            EccInstall.source == "ecc",
        ).all()
        tracked = {(r[0], r[1], r[2]) for r in rows}
    for td, cat, iid in keys:
        key = (str(td), cat, iid)
        if key in tracked:
            out.add(key)
    return out


def _insert_tracker(rows: list[dict]) -> None:
    """Upsert tracker rows; PRESERVE original backup_path + extra on re-install
    so uninstall can always restore the pre-ECC state, not a prior ECC state."""
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
                # Overwrite volatile fields, but keep original backup + prior.
                existing.dest = r["dest"]
                existing.target = r["target"]
                # Update install_hash within extra (latest content may have changed
                # upstream), but preserve any 'prior' value already recorded.
                existing.extra = _merge_extra(existing.extra, r.get("extra"))
                # backup_path intentionally NOT updated — original backup wins.
            else:
                db.add(EccInstall(**r))


def _merge_extra(existing: Optional[str], incoming: Optional[str]) -> Optional[str]:
    import json as _json
    try:
        e = _json.loads(existing) if existing else {}
    except _json.JSONDecodeError:
        e = {}
    try:
        i = _json.loads(incoming) if incoming else {}
    except _json.JSONDecodeError:
        i = {}
    if not isinstance(e, dict):
        e = {}
    if not isinstance(i, dict):
        i = {}
    # Incoming overrides, EXCEPT 'prior' which is the pre-ECC state and must stick.
    if "prior" in e:
        i_without_prior = {k: v for k, v in i.items() if k != "prior"}
        e.update(i_without_prior)
    else:
        e.update(i)
    return _json.dumps(e) if e else None
