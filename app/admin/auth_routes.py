from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    get_current_user,
    hash_password,
    issue_session,
    verify_password,
)
from app.config import get_config
from app.db import get_db
from app.models import User
from app.rate_limit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginReq(BaseModel):
    email: str
    password: str


class Me(BaseModel):
    id: int
    email: str
    role: str


def _login_limit() -> str:
    return f"{get_config().rate_limit.login_per_minute}/minute"


class ChangePwReq(BaseModel):
    current_password: str
    new_password: str


@router.post("/login", response_model=Me)
@limiter.limit(_login_limit)
def login(request: Request, req: LoginReq, response: Response, db: Session = Depends(get_db)) -> Me:
    user = db.query(User).filter(User.email == req.email).one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = issue_session(user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return Me(id=user.id, email=user.email, role=user.role)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me", response_model=Me)
def me(user: User = Depends(get_current_user)) -> Me:
    return Me(id=user.id, email=user.email, role=user.role)


@router.post("/change_password")
def change_password(
    req: ChangePwReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is wrong")
    if len(req.new_password) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password too short (min 6)")
    user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"ok": True}
