import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import get_config
from app.db import get_db
from app.models import User, VirtualKey

KEY_PREFIX = "sk-ccm-"
SESSION_COOKIE = "ccm_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        get_config().server.session_secret, salt="ccm-session"
    )


def generate_virtual_key() -> Tuple[str, str, str]:
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[: len(KEY_PREFIX) + 6]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), pw_hash.encode("utf-8"))
    except ValueError:
        return False


def issue_session(user_id: int) -> str:
    return _signer().dumps({"uid": user_id})


def read_session(token: str) -> Optional[int]:
    try:
        data = _signer().loads(token, max_age=SESSION_MAX_AGE)
        return int(data.get("uid"))
    except (BadSignature, SignatureExpired):
        return None


def _extract_key(request: Request) -> Optional[str]:
    """Claude Code sends x-cc-api-key; also accept x-api-key as fallback."""
    for h in ("x-cc-api-key", "x-ccm-key", "x-api-key"):
        v = request.headers.get(h)
        if not v:
            continue
        if v.lower().startswith("bearer "):
            v = v.split(" ", 1)[1].strip()
        return v
    return None


def _maybe_reset_budget(vk: VirtualKey, db: Session) -> None:
    if not vk.budget_reset_period or vk.max_budget_usd is None:
        return
    now = datetime.utcnow()
    last = vk.budget_last_reset_at or vk.created_at
    if vk.budget_reset_period == "daily":
        should = last.date() < now.date()
    elif vk.budget_reset_period == "weekly":
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        should = last < week_start
    elif vk.budget_reset_period == "monthly":
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        should = last < month_start
    else:
        return
    if should:
        vk.spend_usd = 0.0
        vk.budget_last_reset_at = now
        db.commit()


def get_current_virtual_key(
    request: Request, db: Session = Depends(get_db)
) -> VirtualKey:
    raw = _extract_key(request)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing x-cc-api-key header")
    vk = db.query(VirtualKey).filter(VirtualKey.key_hash == hash_key(raw)).one_or_none()
    if vk is None or not vk.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or inactive key")
    if vk.expires_at and vk.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Key expired")
    _maybe_reset_budget(vk, db)
    if vk.max_budget_usd is not None and vk.spend_usd >= vk.max_budget_usd:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Key budget exceeded")
    return vk


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    uid = read_session(token)
    if uid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return user
