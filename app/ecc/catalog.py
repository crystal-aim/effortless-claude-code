"""Scan the ECC repo cache and build a catalog of installable items."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

import yaml

logger = logging.getLogger(__name__)

MAX_DESC_LEN = 300


def build_catalog(repo_path: Path) -> dict:
    """Return {"agents": [...], "skills": [...], "commands": [...], "rules": [...]}."""
    if not repo_path.exists():
        return {"agents": [], "skills": [], "commands": [], "rules": []}
    return {
        "agents": _scan_flat(repo_path / "agents", "agents"),
        "skills": _scan_skills(repo_path / "skills"),
        "commands": _scan_flat(repo_path / "commands", "commands"),
        "rules": _scan_rules(repo_path / "rules"),
    }


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    sep = "\n---\n"
    end = text.find(sep, 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + len(sep):]
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), body


def _short(val: object) -> str:
    if val is None:
        return ""
    return str(val).strip().replace("\n", " ")[:MAX_DESC_LEN]


def _read_md(f: Path) -> dict:
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("could not read %s: %s", f, e)
        return {}
    fm, _ = _parse_frontmatter(text)
    return fm


def _scan_flat(dir_path: Path, category: str) -> list[dict]:
    """agents/ and commands/ — one .md per item, possibly nested."""
    if not dir_path.exists():
        return []
    items: list[dict] = []
    for f in sorted(dir_path.rglob("*.md")):
        rel = f.relative_to(dir_path).as_posix()
        if rel.upper().startswith("README"):
            continue
        item_id = rel[:-3] if rel.endswith(".md") else rel
        fm = _read_md(f)
        items.append({
            "id": item_id,
            "name": _short(fm.get("name") or item_id),
            "description": _short(fm.get("description")),
            "category": category,
            "source": f"{category}/{rel}",
        })
    return items


def _scan_skills(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    items: list[dict] = []
    for skill_md in sorted(dir_path.glob("*/SKILL.md")):
        skill_dir = skill_md.parent
        sid = skill_dir.name
        fm = _read_md(skill_md)
        items.append({
            "id": sid,
            "name": _short(fm.get("name") or sid),
            "description": _short(fm.get("description")),
            "category": "skills",
            "source": f"skills/{sid}/",
        })
    return items


def _scan_rules(dir_path: Path) -> list[dict]:
    if not dir_path.exists():
        return []
    items: list[dict] = []
    for f in sorted(dir_path.rglob("*.md")):
        rel = f.relative_to(dir_path).as_posix()
        if rel.upper().startswith("README"):
            continue
        parts = rel.split("/")
        language = parts[0] if len(parts) > 1 else "common"
        item_id = rel[:-3] if rel.endswith(".md") else rel
        fm = _read_md(f)
        items.append({
            "id": item_id,
            "name": _short(fm.get("name") or item_id),
            "description": _short(fm.get("description")),
            "category": "rules",
            "language": language,
            "source": f"rules/{rel}",
        })
    return items
