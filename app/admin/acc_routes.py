"""Admin API for the awesome-claude-code catalog browser."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.acc import catalog as acc_catalog
from app.acc import sync as acc_sync
from app.auth import require_admin
from app.models import User

router = APIRouter(prefix="/api/acc", tags=["admin:acc"])


@router.get("/status")
def acc_status(_admin: User = Depends(require_admin)):
    return acc_sync.get_status().to_dict()


@router.post("/sync")
def acc_do_sync(_admin: User = Depends(require_admin)):
    try:
        st = acc_sync.sync()
    except RuntimeError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"awesome-claude-code sync failed: {e}")
    return {"ok": True, **st.to_dict()}


@router.get("/catalog")
def acc_catalog_endpoint(
    category: Optional[str] = None,
    q: Optional[str] = None,
    _admin: User = Depends(require_admin),
):
    cat = acc_catalog.load_catalog(Path(acc_sync.CACHE_FILE))
    items = cat["items"]
    if category and category != "all":
        items = [i for i in items if i["category"] == category]
    if q:
        needle = q.lower().strip()
        if needle:
            items = [i for i in items
                     if needle in i["name"].lower()
                     or needle in i.get("description", "").lower()
                     or needle in i.get("subcategory", "").lower()
                     or needle in i.get("author", "").lower()]
    return {"items": items, "categories": cat["categories"], "total": len(items)}
