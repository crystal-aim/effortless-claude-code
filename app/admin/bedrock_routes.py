"""Admin API for Bedrock provider settings and AWS SSO device-auth flow."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import require_admin
from app.backend import bedrock
from app.config import get_config, upsert_setting
from app.models import User

router = APIRouter(prefix="/api/bedrock", tags=["admin:bedrock"])


# ---------------------------------------------------------------------------
# Provider config endpoints
# ---------------------------------------------------------------------------

class ProviderIn(BaseModel):
    provider: Literal["claude", "bedrock", "mlx", "auto"]


class ProfileIn(BaseModel):
    aws_profile: Optional[str] = None


@router.get("/provider")
def get_provider(_admin: User = Depends(require_admin)):
    cfg = get_config()
    return {"provider": cfg.backend.provider}


@router.post("/provider")
def set_provider(body: ProviderIn, _admin: User = Depends(require_admin)):
    cfg = get_config()
    cfg.backend.provider = body.provider
    upsert_setting("backend.provider", body.provider)
    return {"provider": cfg.backend.provider}


@router.get("/aws-profiles")
def list_aws_profiles(_admin: User = Depends(require_admin)):
    """Return SSO-capable profiles from ~/.aws/config so the UI can auto-fill."""
    return {"profiles": bedrock.read_aws_profiles()}


@router.post("/profile")
def set_profile(body: ProfileIn, _admin: User = Depends(require_admin)):
    cfg = get_config()
    cfg.backend.bedrock.aws_profile = body.aws_profile
    upsert_setting("backend.bedrock.aws_profile", body.aws_profile)
    # Clear SSO credentials when switching to profile mode
    bedrock.clear_sso_credentials(cfg.backend.bedrock.sso_state_file)
    return {"aws_profile": cfg.backend.bedrock.aws_profile}


# ---------------------------------------------------------------------------
# SSO device-auth flow endpoints
# ---------------------------------------------------------------------------

class SsoStartIn(BaseModel):
    start_url: str
    region: str = "us-east-1"
    account_id: Optional[str] = None
    role_name: Optional[str] = None


@router.post("/sso/start")
def sso_start(body: SsoStartIn, _admin: User = Depends(require_admin)):
    cfg = get_config()
    cfg.backend.bedrock.sso_start_url = body.start_url
    cfg.backend.bedrock.region = body.region
    upsert_setting("backend.bedrock.sso_start_url", body.start_url)
    upsert_setting("backend.bedrock.region", body.region)

    try:
        result = bedrock.start_device_auth(
            body.start_url,
            body.region,
            account_id=body.account_id,
            role_name=body.role_name,
        )
    except Exception as e:
        # Surface the full AWS error (ClientError includes response metadata).
        msg = str(e) or e.__class__.__name__
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            err = resp.get("Error", {})
            code = err.get("Code", "")
            detail = err.get("Message", "")
            if code or detail:
                msg = f"{code}: {detail}".strip(": ")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"AWS SSO error (region={body.region}, start_url={body.start_url!r}): {msg}",
        )

    return result


@router.get("/sso/poll")
def sso_poll(_admin: User = Depends(require_admin)):
    cfg = get_config()
    try:
        result = bedrock.poll_device_auth(cfg.backend.bedrock.sso_state_file)
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"AWS SSO error: {e}")
    return result


@router.get("/sso/status")
def sso_status(_admin: User = Depends(require_admin)):
    cfg = get_config()
    # Try loading from file if not in memory
    bedrock.load_sso_state(cfg.backend.bedrock.sso_state_file)
    creds = bedrock.get_sso_credentials()
    if creds is None:
        return {
            "connected": False,
            "provider": cfg.backend.provider,
            "aws_profile": cfg.backend.bedrock.aws_profile,
            "sso_start_url": cfg.backend.bedrock.sso_start_url,
            "region": cfg.backend.bedrock.region,
        }
    return {
        "connected": True,
        "provider": cfg.backend.provider,
        "account_id": creds.account_id,
        "role_name": creds.role_name,
        "region": creds.region,
        "expires_at": creds.expires_at.isoformat(),
        "aws_profile": cfg.backend.bedrock.aws_profile,
        "sso_start_url": cfg.backend.bedrock.sso_start_url,
    }


@router.delete("/sso/disconnect")
def sso_disconnect(_admin: User = Depends(require_admin)):
    cfg = get_config()
    bedrock.clear_sso_credentials(cfg.backend.bedrock.sso_state_file)
    return {"disconnected": True}
