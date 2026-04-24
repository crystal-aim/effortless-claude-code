from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import generate_virtual_key, get_current_user, require_admin
from app.db import get_db
from app.models import User, VirtualKey

router = APIRouter(prefix="/api/keys", tags=["keys"])


class KeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    user_id: int
    user_email: Optional[str] = None
    max_budget_usd: Optional[float] = None
    spend_usd: float
    is_active: bool
    expires_at: Optional[datetime] = None
    created_at: datetime
    budget_reset_period: Optional[str] = None
    budget_last_reset_at: Optional[datetime] = None


class KeyCreateReq(BaseModel):
    name: str
    max_budget_usd: Optional[float] = None
    budget_reset_period: Optional[str] = None
    expires_in_days: Optional[int] = None
    user_id: Optional[int] = None


class KeyUpdateReq(BaseModel):
    name: Optional[str] = None
    max_budget_usd: Optional[float] = None
    is_active: Optional[bool] = None
    expires_at: Optional[datetime] = None
    budget_reset_period: Optional[str] = None


class KeyCreateRes(BaseModel):
    key: str
    info: KeyOut


def _emails_for_keys(db: Session, keys: list) -> dict:
    if not keys:
        return {}
    return {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_([k.user_id for k in keys])).all()
    }


def _serialize(k: VirtualKey, user_email: Optional[str] = None) -> KeyOut:
    return KeyOut(
        id=k.id,
        name=k.name,
        key_prefix=k.key_prefix,
        user_id=k.user_id,
        user_email=user_email,
        max_budget_usd=k.max_budget_usd,
        spend_usd=k.spend_usd,
        is_active=k.is_active,
        expires_at=k.expires_at,
        created_at=k.created_at,
        budget_reset_period=k.budget_reset_period,
        budget_last_reset_at=k.budget_last_reset_at,
    )


@router.get("", response_model=List[KeyOut])
def list_my_keys(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    q = db.query(VirtualKey)
    if user.role != "admin":
        q = q.filter(VirtualKey.user_id == user.id)
    keys = q.order_by(VirtualKey.id.desc()).all()
    emails = _emails_for_keys(db, keys)
    return [_serialize(k, emails.get(k.user_id)) for k in keys]


@router.post("", response_model=KeyCreateRes)
def create_key(
    req: KeyCreateReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_user_id = user.id
    if req.user_id is not None and req.user_id != user.id:
        if user.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only admin can assign keys")
        if db.get(User, req.user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "target user not found")
        target_user_id = req.user_id

    raw, h, prefix = generate_virtual_key()
    expires_at = None
    if req.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=req.expires_in_days)

    k = VirtualKey(
        key_hash=h,
        key_prefix=prefix,
        user_id=target_user_id,
        name=req.name,
        max_budget_usd=req.max_budget_usd,
        budget_reset_period=req.budget_reset_period,
        expires_at=expires_at,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    owner_email = db.get(User, k.user_id).email if k.user_id else None
    return KeyCreateRes(key=raw, info=_serialize(k, owner_email))


@router.patch("/{key_id}", response_model=KeyOut)
def update_key(
    key_id: int,
    req: KeyUpdateReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = db.get(VirtualKey, key_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if user.role != "admin" and k.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your key")
    fs = req.model_fields_set
    if 'name' in fs:
        k.name = req.name
    if 'max_budget_usd' in fs:
        k.max_budget_usd = req.max_budget_usd
    if 'is_active' in fs:
        k.is_active = req.is_active
    if 'expires_at' in fs:
        k.expires_at = req.expires_at
    if 'budget_reset_period' in fs:
        k.budget_reset_period = req.budget_reset_period
        k.budget_last_reset_at = None
    db.commit()
    db.refresh(k)
    return _serialize(k, db.get(User, k.user_id).email if k.user_id else None)


@router.delete("/{key_id}")
def delete_key(
    key_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = db.get(VirtualKey, key_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    if user.role != "admin" and k.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not your key")
    db.delete(k)
    db.commit()
    return {"ok": True}


# Admin view of ALL keys (alias, same handler behavior)
admin_keys_router = APIRouter(prefix="/admin/keys", tags=["admin:keys"])


@admin_keys_router.get("", response_model=List[KeyOut])
def admin_list_all(db: Session = Depends(get_db), _=Depends(require_admin)):
    keys = db.query(VirtualKey).order_by(VirtualKey.id.desc()).all()
    emails = _emails_for_keys(db, keys)
    return [_serialize(k, emails.get(k.user_id)) for k in keys]
