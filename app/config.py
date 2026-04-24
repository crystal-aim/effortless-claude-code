import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class UpstreamCfg(BaseModel):
    base_url: str = "https://api.anthropic.com"
    timeout_seconds: int = 600


class ServerCfg(BaseModel):
    host: str = "0.0.0.0"
    port: int = 4000
    session_secret: str = "change-me"


class RateLimitCfg(BaseModel):
    proxy_per_minute: int = 60
    login_per_minute: int = 10


class RoutingRule(BaseModel):
    keywords: List[str]
    model: str


class ModelPrice(BaseModel):
    input: float
    output: float
    cache_write: float = 0.0
    cache_read: float = 0.0


class BedrockCfg(BaseModel):
    model_config = {"protected_namespaces": ()}
    region: str = "us-east-1"
    aws_profile: Optional[str] = None
    model_map: Dict[str, str] = {
        "claude-opus-4-7": "anthropic.claude-opus-4-5",
        "claude-sonnet-4-6": "anthropic.claude-sonnet-4-5-20241022-v2:0",
        "claude-haiku-4-5-20251001": "anthropic.claude-haiku-3-5",
    }
    sso_start_url: str = ""
    sso_state_file: str = "sso_state.json"


class MlxCfg(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_url: str = "http://localhost:8899"
    timeout_seconds: int = 300
    port: int = 8899
    model_map: Dict[str, str] = {}


class BackendCfg(BaseModel):
    # "auto" tries claude first, falls back to bedrock on 529
    provider: Literal["claude", "bedrock", "mlx", "auto"] = "claude"
    bedrock: BedrockCfg = BedrockCfg()
    mlx: MlxCfg = MlxCfg()


class AppConfig(BaseModel):
    upstream: UpstreamCfg = UpstreamCfg()
    server: ServerCfg = ServerCfg()
    rate_limit: RateLimitCfg = RateLimitCfg()
    backend: BackendCfg = BackendCfg()
    default_model: str = "claude-sonnet-4-6"
    routing_rules: List[RoutingRule] = []
    pricing: Dict[str, ModelPrice] = {}


_CONFIG: Optional[AppConfig] = None


_PLACEHOLDER_SECRETS = {
    "",
    "change-me",
    "change-me-to-a-long-random-string",
    "replace-with-a-random-string",
}


def load_config(path: str = "config.yaml") -> AppConfig:
    global _CONFIG
    p = Path(path)
    # If relative path not found in CWD, try relative to the package root (claude-proxy/)
    if not p.is_absolute() and not p.exists():
        pkg_root = Path(__file__).parent.parent  # app/ -> claude-proxy/
        p = pkg_root / path
    if not p.exists():
        _CONFIG = AppConfig()
        return _CONFIG
    raw: Dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    _CONFIG = AppConfig(**raw)
    if _CONFIG.server.session_secret.strip() in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            f"server.session_secret in {p} is still the placeholder value. "
            "Replace it with a long random string before starting the server "
            "(e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)."
        )
    return _CONFIG


def get_config() -> AppConfig:
    if _CONFIG is None:
        return load_config()
    return _CONFIG


PERSISTED_KEYS = {
    "backend.provider",
    "backend.bedrock.aws_profile",
    "backend.bedrock.sso_start_url",
    "backend.bedrock.region",
    "backend.mlx.base_url",
}

# Prefixes used by feature modules for app state (not config overrides).
# apply_db_overrides ignores these silently instead of warning.
_APP_STATE_PREFIXES = ("ecc.", "acc.", "autosync.")


def _split_path(key: str) -> tuple[list[str], str]:
    parts = key.split(".")
    return parts[:-1], parts[-1]


def _resolve_parent(cfg: AppConfig, parents: list[str]):
    obj: Any = cfg
    for p in parents:
        obj = getattr(obj, p)
    return obj


def apply_db_overrides(cfg: AppConfig) -> None:
    """Merge persisted settings rows on top of `cfg` (YAML defaults).

    Silently ignores keys outside PERSISTED_KEYS. Logs + skips values that
    fail pydantic validation so a bad row can't break startup.
    """
    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        rows = db.query(Setting).all()
        items = [(r.key, r.value) for r in rows]

    for key, value in items:
        if key not in PERSISTED_KEYS:
            if not key.startswith(_APP_STATE_PREFIXES):
                logger.warning("ignoring unknown persisted setting %r", key)
            continue
        parents, leaf = _split_path(key)
        try:
            parent = _resolve_parent(cfg, parents)
            setattr(parent, leaf, value)
        except Exception as e:
            logger.warning("failed to apply setting %s=%r: %s", key, value, e)


def upsert_setting(key: str, value: Optional[str]) -> None:
    """Persist an override. `value=None` deletes the row (reverts to YAML default)."""
    if key not in PERSISTED_KEYS:
        raise ValueError(f"setting {key!r} is not in the persisted whitelist")

    from app.db import session_scope
    from app.models import Setting

    with session_scope() as db:
        row = db.get(Setting, key)
        if value is None:
            if row is not None:
                db.delete(row)
            return
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
