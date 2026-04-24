"""Dispatch requests to the configured backend."""
from __future__ import annotations

from typing import Dict

from app.config import AppConfig

from . import claude, bedrock, mlx
from .claude import BackendResult


async def call_messages(
    body_bytes: bytes,
    body: dict,
    headers: Dict[str, str],
    is_stream: bool,
    cfg: AppConfig,
) -> BackendResult:
    provider = cfg.backend.provider

    if provider == "bedrock":
        return await bedrock.forward(body, cfg, is_stream)

    if provider == "mlx":
        return await mlx.forward(body, cfg, is_stream)

    if provider == "auto":
        result = await claude.forward(body_bytes, headers, cfg, is_stream)
        if result.status_code == 529:
            return await bedrock.forward(body, cfg, is_stream)
        return result

    return await claude.forward(body_bytes, headers, cfg, is_stream)
