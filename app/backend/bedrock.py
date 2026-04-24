"""AWS Bedrock backend + SSO device-auth flow."""
from __future__ import annotations

import asyncio
import configparser
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import boto3

from app.config import AppConfig
from app.tracking.pricing import TokenUsage

from .claude import BackendResult

log = logging.getLogger("ccm.bedrock")


# ---------------------------------------------------------------------------
# SSO credentials state
# ---------------------------------------------------------------------------

@dataclass
class SsoCredentials:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str
    expires_at: datetime
    account_id: str
    role_name: str
    region: str

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expires_at

    def to_dict(self) -> dict:
        return {
            "aws_access_key_id": self.aws_access_key_id,
            "aws_secret_access_key": self.aws_secret_access_key,
            "aws_session_token": self.aws_session_token,
            "expires_at": self.expires_at.isoformat(),
            "account_id": self.account_id,
            "role_name": self.role_name,
            "region": self.region,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SsoCredentials":
        return cls(
            aws_access_key_id=d["aws_access_key_id"],
            aws_secret_access_key=d["aws_secret_access_key"],
            aws_session_token=d["aws_session_token"],
            expires_at=datetime.fromisoformat(d["expires_at"]),
            account_id=d["account_id"],
            role_name=d["role_name"],
            region=d["region"],
        )


# In-memory singleton
_sso_credentials: Optional[SsoCredentials] = None
_sso_last_load: float = 0.0
_SSO_RELOAD_INTERVAL = 60.0  # re-read file at most once per minute

# Pending device auth state (one at a time)
_pending_device_auth: Optional[Dict[str, Any]] = None


def load_sso_state(path: str) -> Optional[SsoCredentials]:
    global _sso_credentials, _sso_last_load
    now = time.monotonic()
    if _sso_credentials and _sso_credentials.is_valid() and (now - _sso_last_load) < _SSO_RELOAD_INTERVAL:
        return _sso_credentials
    _sso_last_load = now
    try:
        d = json.loads(Path(path).read_text())
        creds = SsoCredentials.from_dict(d)
        if creds.is_valid():
            _sso_credentials = creds
            return creds
    except Exception:
        pass
    return None


def save_sso_state(creds: SsoCredentials, path: str) -> None:
    Path(path).write_text(json.dumps(creds.to_dict(), indent=2))


def get_sso_credentials() -> Optional[SsoCredentials]:
    return _sso_credentials if (_sso_credentials and _sso_credentials.is_valid()) else None


def set_sso_credentials(creds: SsoCredentials, state_file: str) -> None:
    global _sso_credentials
    _sso_credentials = creds
    save_sso_state(creds, state_file)


def clear_sso_credentials(state_file: str) -> None:
    global _sso_credentials, _bedrock_client_cache
    _sso_credentials = None
    _bedrock_client_cache = None
    try:
        Path(state_file).unlink()
    except FileNotFoundError:
        pass


def _get_boto3_session(cfg: AppConfig) -> boto3.Session:
    """Build boto3 session: SSO creds > aws_profile > default chain."""
    sso = get_sso_credentials()
    if sso:
        return boto3.Session(
            aws_access_key_id=sso.aws_access_key_id,
            aws_secret_access_key=sso.aws_secret_access_key,
            aws_session_token=sso.aws_session_token,
            region_name=sso.region,
        )
    profile = cfg.backend.bedrock.aws_profile
    return boto3.Session(profile_name=profile, region_name=cfg.backend.bedrock.region)


# ---------------------------------------------------------------------------
# Bedrock API calls — boto3 bedrock-runtime InvokeModel / InvokeModelWithResponseStream
# ---------------------------------------------------------------------------
# Claude on Bedrock accepts native Anthropic Messages API bodies (with
# `anthropic_version` injected) and returns native Anthropic responses, so
# we invoke bedrock-runtime directly and forward bytes through. This
# sidesteps the Anthropic SDK's 10-minute non-streaming safeguard.

_BEDROCK_STRIP_FIELDS = {
    "model",
    "stream",
    "anthropic_beta",
    # Anthropic native-only fields Bedrock rejects with ValidationException.
    "context_management",
    "mcp_servers",
}


def _build_native_body(body: dict) -> bytes:
    native = {k: v for k, v in body.items() if k not in _BEDROCK_STRIP_FIELDS}
    native["anthropic_version"] = "bedrock-2023-05-31"
    return json.dumps(native).encode("utf-8")


_bedrock_client_cache: Optional[tuple] = None


def _bedrock_runtime_client(cfg: AppConfig):
    global _bedrock_client_cache
    sso = get_sso_credentials()
    cache_key = sso.aws_session_token if sso else cfg.backend.bedrock.aws_profile
    if _bedrock_client_cache and _bedrock_client_cache[0] == cache_key:
        return _bedrock_client_cache[1]
    client = _get_boto3_session(cfg).client("bedrock-runtime", region_name=cfg.backend.bedrock.region)
    _bedrock_client_cache = (cache_key, client)
    return client


async def _invoke_stream_sse(client, model_id: str, body_bytes: bytes) -> AsyncIterator[bytes]:
    """Bridge boto3 EventStream (sync iterator) → async SSE bytes."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def worker():
        try:
            resp = client.invoke_model_with_response_stream(
                modelId=model_id,
                body=body_bytes,
                contentType="application/json",
                accept="application/json",
            )
            for event in resp["body"]:
                chunk = event.get("chunk")
                if chunk and "bytes" in chunk:
                    loop.call_soon_threadsafe(q.put_nowait, chunk["bytes"])
        except Exception as e:
            log.exception("bedrock stream failed (model_id=%s)", model_id)
            loop.call_soon_threadsafe(q.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, SENTINEL)

    threading.Thread(target=worker, daemon=True).start()

    while True:
        item = await q.get()
        if item is SENTINEL:
            break
        if isinstance(item, Exception):
            err = {"type": "error", "error": {"type": "bedrock_error", "message": str(item)}}
            yield f"event: error\ndata: {json.dumps(err)}\n\n".encode()
            break
        try:
            payload = json.loads(item)
        except ValueError:
            continue
        etype = payload.get("type", "message")
        yield f"event: {etype}\ndata: {json.dumps(payload)}\n\n".encode()

    yield b"data: [DONE]\n\n"


async def forward(body: dict, cfg: AppConfig, is_stream: bool) -> BackendResult:
    bedrock_cfg = cfg.backend.bedrock
    requested_model = body.get("model", "")
    model_id = bedrock_cfg.model_map.get(requested_model, requested_model)
    body_bytes = _build_native_body(body)

    try:
        client = _bedrock_runtime_client(cfg)
    except Exception as e:
        log.exception("bedrock client init failed (region=%s, profile=%s)",
                      bedrock_cfg.region, bedrock_cfg.aws_profile)
        return BackendResult(
            status_code=502,
            headers={"content-type": "application/json"},
            body={"type": "error", "error": {
                "type": "bedrock_client_init_error",
                "message": f"{type(e).__name__}: {e}",
            }},
        )

    log.info("bedrock invoke model=%s -> %s region=%s stream=%s bytes=%d",
             requested_model, model_id, bedrock_cfg.region, is_stream, len(body_bytes))

    if is_stream:
        return BackendResult(
            status_code=200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
            stream=_invoke_stream_sse(client, model_id, body_bytes),
        )

    try:
        resp = await asyncio.to_thread(
            client.invoke_model,
            modelId=model_id,
            body=body_bytes,
            contentType="application/json",
            accept="application/json",
        )
    except Exception as e:
        log.exception("bedrock invoke_model failed (model_id=%s, region=%s)",
                      model_id, bedrock_cfg.region)
        return BackendResult(
            status_code=502,
            headers={"content-type": "application/json"},
            body={"type": "error", "error": {
                "type": "bedrock_error",
                "message": f"{type(e).__name__}: {e}",
            }},
        )

    payload = json.loads(resp["body"].read())
    return BackendResult(
        status_code=200,
        headers={"content-type": "application/json"},
        body=payload,
        usage=TokenUsage.from_api_dict(payload),
    )


# ---------------------------------------------------------------------------
# ~/.aws/config parser — exposes SSO profiles to the admin UI
# ---------------------------------------------------------------------------

def read_aws_profiles() -> List[Dict[str, str]]:
    """Parse ~/.aws/config and return SSO-capable profiles.

    Handles both legacy profiles (sso_start_url inline) and newer
    [sso-session NAME] references.
    """
    path = Path(os.environ.get("AWS_CONFIG_FILE") or (Path.home() / ".aws" / "config"))
    if not path.exists():
        return []

    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error:
        return []

    sso_sessions: Dict[str, Dict[str, str]] = {}
    for section in parser.sections():
        if section.startswith("sso-session "):
            name = section[len("sso-session "):].strip()
            sso_sessions[name] = dict(parser[section])

    profiles: List[Dict[str, str]] = []
    seen: set[str] = set()
    for section in parser.sections():
        if section == "default":
            name = "default"
        elif section.startswith("profile "):
            name = section[len("profile "):].strip()
        else:
            continue
        if name in seen:
            continue
        seen.add(name)

        data = dict(parser[section])
        sess_name = data.get("sso_session")
        if sess_name and sess_name in sso_sessions:
            sess = sso_sessions[sess_name]
            data.setdefault("sso_start_url", sess.get("sso_start_url", ""))
            data.setdefault("sso_region", sess.get("sso_region", ""))

        start_url = (data.get("sso_start_url") or "").strip().rstrip("/")
        if not start_url:
            continue  # skip non-SSO profiles

        profiles.append({
            "name": name,
            "sso_start_url": start_url,
            "sso_region": data.get("sso_region", ""),
            "sso_account_id": data.get("sso_account_id", ""),
            "sso_role_name": data.get("sso_role_name", ""),
            "region": data.get("region", ""),
            "sso_session": sess_name or "",
        })

    return profiles


# ---------------------------------------------------------------------------
# SSO device auth flow (called from admin/bedrock_routes.py)
# ---------------------------------------------------------------------------

def start_device_auth(
    start_url: str,
    region: str,
    account_id: Optional[str] = None,
    role_name: Optional[str] = None,
) -> dict:
    """Register OIDC client and start device authorization. Returns state dict."""
    global _pending_device_auth

    start_url = (start_url or "").strip().rstrip("/")
    if not start_url.startswith(("http://", "https://")):
        raise ValueError(f"start_url must be a full https URL, got: {start_url!r}")

    oidc = boto3.client("sso-oidc", region_name=region)

    reg = oidc.register_client(
        clientName="claude-proxy",
        clientType="public",
        scopes=["sso:account:access"],
        grantTypes=["urn:ietf:params:oauth:grant-type:device_code"],
    )
    device = oidc.start_device_authorization(
        clientId=reg["clientId"],
        clientSecret=reg["clientSecret"],
        startUrl=start_url,
    )

    _pending_device_auth = {
        "clientId": reg["clientId"],
        "clientSecret": reg["clientSecret"],
        "deviceCode": device["deviceCode"],
        "interval": device.get("interval", 5),
        "expiresAt": time.time() + device.get("expiresIn", 600),
        "region": region,
        "startUrl": start_url,
        "targetAccountId": account_id,
        "targetRoleName": role_name,
    }

    return {
        "verificationUri": device.get("verificationUri"),
        "verificationUriComplete": device.get("verificationUriComplete"),
        "userCode": device.get("userCode"),
        "expiresIn": device.get("expiresIn", 600),
        "interval": device.get("interval", 5),
    }


def _list_all_account_roles(sso_client, access_token: str, account_id: str) -> List[str]:
    """Return every role name the SSO user is entitled to on `account_id`."""
    names: List[str] = []
    next_token: Optional[str] = None
    while True:
        kwargs = {"accessToken": access_token, "accountId": account_id, "maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = sso_client.list_account_roles(**kwargs)
        for r in resp.get("roleList", []):
            names.append(r["roleName"])
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return names


def poll_device_auth(state_file: str) -> dict:
    """Poll for token. Returns {status, message}. On success stores credentials."""
    global _pending_device_auth

    if not _pending_device_auth:
        return {"status": "error", "message": "No pending device auth"}

    state = _pending_device_auth
    if time.time() > state["expiresAt"]:
        _pending_device_auth = None
        return {"status": "error", "message": "Authorization expired"}

    oidc = boto3.client("sso-oidc", region_name=state["region"])

    try:
        token_resp = oidc.create_token(
            clientId=state["clientId"],
            clientSecret=state["clientSecret"],
            grantType="urn:ietf:params:oauth:grant-type:device_code",
            deviceCode=state["deviceCode"],
        )
    except oidc.exceptions.AuthorizationPendingException:
        return {"status": "pending", "message": "Waiting for user approval"}
    except oidc.exceptions.SlowDownException:
        return {"status": "pending", "message": "Please wait"}
    except Exception as e:
        _pending_device_auth = None
        return {"status": "error", "message": str(e)}

    access_token = token_resp["accessToken"]
    region = state["region"]
    target_account_id = state.get("targetAccountId")
    target_role_name = state.get("targetRoleName")

    sso_client = boto3.client("sso", region_name=region)

    # Resolve target account: use explicit target if provided, else first available.
    if target_account_id:
        account_id = target_account_id
    else:
        accounts = sso_client.list_accounts(accessToken=access_token, maxResults=1)
        if not accounts.get("accountList"):
            _pending_device_auth = None
            return {"status": "error", "message": "No AWS accounts found in SSO"}
        account_id = accounts["accountList"][0]["accountId"]

    # Resolve target role against the actual list of roles the SSO user is
    # entitled to on this account. This avoids a ForbiddenException from
    # GetRoleCredentials when the requested role isn't in the user's
    # permission sets — and lets us surface a useful error.
    try:
        entitled = _list_all_account_roles(sso_client, access_token, account_id)
    except Exception as e:
        _pending_device_auth = None
        return {"status": "error", "message": f"Cannot list roles for {account_id}: {e}"}

    if not entitled:
        _pending_device_auth = None
        return {"status": "error", "message": f"No roles available on account {account_id}"}

    if target_role_name and target_role_name in entitled:
        role_name = target_role_name
    elif target_role_name:
        _pending_device_auth = None
        return {
            "status": "error",
            "message": (
                f"Your SSO user is not entitled to '{target_role_name}' on account "
                f"{account_id}. Available roles: {', '.join(entitled)}"
            ),
        }
    else:
        role_name = entitled[0]

    try:
        role_creds = sso_client.get_role_credentials(
            accountId=account_id,
            roleName=role_name,
            accessToken=access_token,
        )["roleCredentials"]
    except Exception as e:
        _pending_device_auth = None
        return {
            "status": "error",
            "message": f"Cannot assume {role_name} @ {account_id}: {e}",
        }

    expires_ms = role_creds.get("expiration", 0)
    expires_at = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)

    creds = SsoCredentials(
        aws_access_key_id=role_creds["accessKeyId"],
        aws_secret_access_key=role_creds["secretAccessKey"],
        aws_session_token=role_creds["sessionToken"],
        expires_at=expires_at,
        account_id=account_id,
        role_name=role_name,
        region=region,
    )
    set_sso_credentials(creds, state_file)
    _pending_device_auth = None

    return {
        "status": "success",
        "account_id": account_id,
        "role_name": role_name,
        "expires_at": expires_at.isoformat(),
    }
