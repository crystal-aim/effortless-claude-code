"""Parse the awesome-claude-code CSV into a JSON-serializable catalog."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DESC_MAX = 400


def load_catalog(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {"items": [], "categories": []}
    items: list[dict] = []
    categories: dict[str, int] = {}
    try:
        with cache_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                active = (row.get("Active") or "").strip().upper() == "TRUE"
                removed = (row.get("Removed From Origin") or "").strip().upper() == "TRUE"
                if not active or removed:
                    continue
                cat = (row.get("Category") or "").strip() or "Other"
                sub = (row.get("Sub-Category") or "").strip()
                item = {
                    "id": (row.get("ID") or "").strip(),
                    "name": (row.get("Display Name") or "").strip(),
                    "category": cat,
                    "subcategory": sub,
                    "url": (row.get("Primary Link") or "").strip(),
                    "secondary_url": (row.get("Secondary Link") or "").strip(),
                    "author": (row.get("Author Name") or "").strip(),
                    "author_url": (row.get("Author Link") or "").strip(),
                    "description": _short(row.get("Description")),
                    "license": (row.get("License") or "").strip(),
                }
                if not item["id"] or not item["name"]:
                    continue
                items.append(item)
                categories[cat] = categories.get(cat, 0) + 1
    except csv.Error as e:
        logger.warning("failed to parse ACC CSV: %s", e)
        return {"items": [], "categories": []}
    cats = [{"name": k, "count": v} for k, v in sorted(categories.items(), key=lambda x: (-x[1], x[0]))]
    return {"items": items, "categories": cats}


def _short(s: object) -> str:
    if s is None:
        return ""
    return str(s).strip().replace("\r", " ")[:DESC_MAX]
