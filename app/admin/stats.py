from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.db import get_db
from app.models import UsageLog, User, VirtualKey

router = APIRouter(prefix="/api/stats", tags=["stats"])


class DailyPoint(BaseModel):
    date: str
    cost_usd: float
    requests: int
    prompt_tokens: int
    completion_tokens: int


class ModelBreakdown(BaseModel):
    model: str
    cost_usd: float
    requests: int


class KeyUsageRes(BaseModel):
    key_id: int
    total_cost_usd: float
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cache_write_tokens: int
    total_cache_read_tokens: int
    daily: List[DailyPoint]
    by_model: List[ModelBreakdown]


def _default_window(days: int = 30):
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    return start, end


def _check_access(user: User, key: VirtualKey) -> None:
    if user.role != "admin" and key.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your key")


@router.get("/keys/{key_id}", response_model=KeyUsageRes)
def key_usage(
    key_id: int,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = db.get(VirtualKey, key_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    _check_access(user, k)

    start, end = _default_window(days)
    base = db.query(UsageLog).filter(
        UsageLog.key_id == key_id,
        UsageLog.created_at >= start,
        UsageLog.created_at <= end,
    )
    totals = base.with_entities(
        func.coalesce(func.sum(UsageLog.cost_usd), 0.0),
        func.count(UsageLog.id),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0),
        func.coalesce(func.sum(UsageLog.cache_write_tokens), 0),
        func.coalesce(func.sum(UsageLog.cache_read_tokens), 0),
    ).one()

    day_rows = (
        base.with_entities(
            func.date(UsageLog.created_at).label("d"),
            func.sum(UsageLog.cost_usd),
            func.count(UsageLog.id),
            func.sum(UsageLog.prompt_tokens),
            func.sum(UsageLog.completion_tokens),
        )
        .group_by("d")
        .order_by("d")
        .all()
    )
    daily = [
        DailyPoint(
            date=str(r[0]),
            cost_usd=float(r[1] or 0.0),
            requests=int(r[2] or 0),
            prompt_tokens=int(r[3] or 0),
            completion_tokens=int(r[4] or 0),
        )
        for r in day_rows
    ]

    model_rows = (
        base.with_entities(
            UsageLog.model,
            func.sum(UsageLog.cost_usd),
            func.count(UsageLog.id),
        )
        .group_by(UsageLog.model)
        .order_by(func.sum(UsageLog.cost_usd).desc())
        .all()
    )
    by_model = [
        ModelBreakdown(model=r[0], cost_usd=float(r[1] or 0.0), requests=int(r[2] or 0))
        for r in model_rows
    ]

    return KeyUsageRes(
        key_id=key_id,
        total_cost_usd=float(totals[0] or 0.0),
        total_requests=int(totals[1] or 0),
        total_prompt_tokens=int(totals[2] or 0),
        total_completion_tokens=int(totals[3] or 0),
        total_cache_write_tokens=int(totals[4] or 0),
        total_cache_read_tokens=int(totals[5] or 0),
        daily=daily,
        by_model=by_model,
    )


class OverviewPoint(BaseModel):
    date: str
    cost_usd: float
    requests: int


class OverviewRes(BaseModel):
    total_cost_usd: float
    total_requests: int
    daily: List[OverviewPoint]
    by_user: List[dict]
    by_model: List[ModelBreakdown]


admin_router = APIRouter(prefix="/admin/stats", tags=["admin:stats"])


@admin_router.get("/overview", response_model=OverviewRes)
def overview(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    start, end = _default_window(days)
    q = db.query(UsageLog).join(VirtualKey, VirtualKey.id == UsageLog.key_id).filter(
        UsageLog.created_at >= start, UsageLog.created_at <= end
    )
    if user_id is not None:
        q = q.filter(VirtualKey.user_id == user_id)

    totals = q.with_entities(
        func.coalesce(func.sum(UsageLog.cost_usd), 0.0), func.count(UsageLog.id)
    ).one()

    day_rows = (
        q.with_entities(
            func.date(UsageLog.created_at).label("d"),
            func.sum(UsageLog.cost_usd),
            func.count(UsageLog.id),
        )
        .group_by("d")
        .order_by("d")
        .all()
    )
    daily = [
        OverviewPoint(
            date=str(r[0]), cost_usd=float(r[1] or 0.0), requests=int(r[2] or 0)
        )
        for r in day_rows
    ]

    by_user_rows = (
        q.with_entities(
            User.id,
            User.email,
            func.sum(UsageLog.cost_usd),
            func.count(UsageLog.id),
        )
        .join(User, User.id == VirtualKey.user_id)
        .group_by(User.id, User.email)
        .order_by(func.sum(UsageLog.cost_usd).desc())
        .all()
    )
    by_user = [
        {
            "user_id": r[0],
            "email": r[1],
            "cost_usd": float(r[2] or 0.0),
            "requests": int(r[3] or 0),
        }
        for r in by_user_rows
    ]

    by_model_rows = (
        q.with_entities(
            UsageLog.model, func.sum(UsageLog.cost_usd), func.count(UsageLog.id)
        )
        .group_by(UsageLog.model)
        .order_by(func.sum(UsageLog.cost_usd).desc())
        .all()
    )
    by_model = [
        ModelBreakdown(model=r[0], cost_usd=float(r[1] or 0.0), requests=int(r[2] or 0))
        for r in by_model_rows
    ]

    return OverviewRes(
        total_cost_usd=float(totals[0] or 0.0),
        total_requests=int(totals[1] or 0),
        daily=daily,
        by_user=by_user,
        by_model=by_model,
    )
