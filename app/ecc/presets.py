"""Curated preset bundles over the ECC catalog.

Matchers are resolved against the live catalog so presets keep working
even if upstream renames items. Each matcher is a dict:
    {"category": str, "ids": [exact ids], "keywords": [substrings]}
Use `ids=None, keywords=None` to select the whole category.
Exact ids win over keyword matching; both can be combined.
"""
from __future__ import annotations

from typing import Optional

PRESETS: dict[str, dict] = {
    "starter": {
        "name": "Starter",
        "description": "Essential agents + common rules. Small, safe default for any project.",
        "matchers": [
            {"category": "agents", "ids": ["planner", "code-reviewer", "architect", "debugger"]},
            {"category": "rules", "keywords": ["common/"]},
        ],
    },
    "web-dev": {
        "name": "Web Dev",
        "description": "Frontend + backend + TDD skills, review agents, TS/common rules.",
        "matchers": [
            {"category": "agents", "ids": ["planner", "code-reviewer", "architect"]},
            {"category": "skills", "keywords": ["frontend", "backend", "tdd", "react", "next", "api", "http", "rest"]},
            {"category": "commands", "keywords": ["test", "review", "lint"]},
            {"category": "rules", "keywords": ["typescript/", "common/"]},
        ],
    },
    "security": {
        "name": "Security",
        "description": "Security reviewer agent + security-focused skills/rules.",
        "matchers": [
            {"category": "agents", "keywords": ["security", "audit", "pentest"]},
            {"category": "skills", "keywords": ["security", "vuln", "auth", "owasp"]},
            {"category": "commands", "keywords": ["security", "audit"]},
            {"category": "rules", "keywords": ["security", "common/"]},
        ],
    },
    "full": {
        "name": "Full",
        "description": "Everything: all agents, skills, commands, rules. Large — proceed with care.",
        "matchers": [
            {"category": "agents"},
            {"category": "skills"},
            {"category": "commands"},
            {"category": "rules"},
        ],
    },
}


def _matches_item(item: dict, ids: Optional[list[str]], keywords: Optional[list[str]]) -> bool:
    if ids is None and keywords is None:
        return True
    if ids is not None and item.get("id") in ids:
        return True
    if keywords:
        blob = " ".join([
            str(item.get("id", "")),
            str(item.get("name", "")),
            str(item.get("description", "")),
            str(item.get("language", "")),
            str(item.get("source", "")),
        ]).lower()
        return any(k.lower() in blob for k in keywords)
    return False


def resolve_preset(preset_id: str, catalog: dict) -> list[dict]:
    """Return a de-duplicated list of {'category','id'} pointers."""
    preset = PRESETS.get(preset_id)
    if preset is None:
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for matcher in preset["matchers"]:
        cat = matcher["category"]
        ids = matcher.get("ids")
        keywords = matcher.get("keywords")
        for it in catalog.get(cat, []):
            if not _matches_item(it, ids, keywords):
                continue
            key = (it["category"], it["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"category": it["category"], "id": it["id"]})
    return out


def preset_summary(catalog: dict) -> list[dict]:
    out: list[dict] = []
    for pid, pdef in PRESETS.items():
        items = resolve_preset(pid, catalog)
        out.append({
            "id": pid,
            "name": pdef["name"],
            "description": pdef["description"],
            "items_count": len(items),
        })
    return out
