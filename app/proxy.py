import json
import logging
import time
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth import get_current_virtual_key
from app.backend import call_messages
from app.backend.bedrock import load_sso_state
from app.backend.claude import build_forward_headers
from app.config import get_config
from app.models import VirtualKey
from app.rate_limit import limiter
from app.routing import choose_model
from app.tracking.pricing import TokenUsage
from app.tracking.usage_logger import record_usage

log = logging.getLogger(__name__)

_SSE_BUF_MAX = 1 * 1024 * 1024  # 1 MB safety cap

router = APIRouter()


def _proxy_limit() -> str:
    return f"{get_config().rate_limit.proxy_per_minute}/minute"


class SseUsageAccumulator:
    def __init__(self) -> None:
        self.usage = TokenUsage()
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        if len(self._buf) > _SSE_BUF_MAX:
            self._buf.clear()
            return
        while b"\n\n" in self._buf:
            idx = self._buf.index(b"\n\n")
            event = bytes(self._buf[:idx])
            del self._buf[:idx + 2]
            self._parse_event(event)

    def _parse_event(self, raw_event: bytes) -> None:
        data_lines = []
        for line in raw_event.splitlines():
            if line.startswith(b"data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return
        try:
            payload = json.loads(b"\n".join(data_lines).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        etype = payload.get("type")
        if etype == "message_start":
            msg = payload.get("message") or {}
            u = msg.get("usage") or {}
            self.usage.input_tokens = int(u.get("input_tokens", 0) or 0)
            self.usage.cache_creation_input_tokens = int(u.get("cache_creation_input_tokens", 0) or 0)
            self.usage.cache_read_input_tokens = int(u.get("cache_read_input_tokens", 0) or 0)
            self.usage.output_tokens = int(u.get("output_tokens", 0) or 0)
        elif etype == "message_delta":
            u = payload.get("usage") or {}
            if "output_tokens" in u:
                self.usage.output_tokens = int(u.get("output_tokens") or 0)


@router.post("/v1/messages")
@limiter.limit(_proxy_limit)
async def messages(request: Request, vk: VirtualKey = Depends(get_current_virtual_key)):
    cfg = get_config()
    load_sso_state(cfg.backend.bedrock.sso_state_file)

    try:
        body_bytes = await request.body()
        body = json.loads(body_bytes) if body_bytes else {}
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid JSON body")

    requested_model = body.get("model")
    chosen_model = choose_model(body, requested_model)
    body["model"] = chosen_model
    forward_bytes = json.dumps(body).encode("utf-8")

    is_stream = bool(body.get("stream"))
    started = time.monotonic()

    result = await call_messages(
        body_bytes=forward_bytes,
        body=body,
        headers=dict(request.headers),
        is_stream=is_stream,
        cfg=cfg,
    )

    if not is_stream:
        latency_ms = int((time.monotonic() - started) * 1000)
        if result.status_code < 400 and result.body:
            try:
                record_usage(
                    key_id=vk.id,
                    model=chosen_model,
                    usage=result.usage or TokenUsage(),
                    latency_ms=latency_ms,
                    status_code=result.status_code,
                )
            except Exception:
                log.warning("record_usage failed", exc_info=True)
        return JSONResponse(
            content=result.body or {"error": {"message": "upstream error"}},
            status_code=result.status_code,
            headers=result.headers,
        )

    # streaming — if the backend returned an error body instead of a stream, fall back to JSON
    if result.stream is None:
        return JSONResponse(
            content=result.body or {"error": {"message": "upstream error"}},
            status_code=result.status_code,
            headers=result.headers,
        )

    acc = SseUsageAccumulator()

    async def stream_with_tracking() -> AsyncIterator[bytes]:
        async for chunk in result.stream:
            acc.feed(chunk)
            yield chunk
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            record_usage(
                key_id=vk.id,
                model=chosen_model,
                usage=acc.usage,
                latency_ms=latency_ms,
                status_code=200,
            )
        except Exception:
            log.warning("record_usage failed", exc_info=True)

    return StreamingResponse(
        stream_with_tracking(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache"},
    )


@router.post("/v1/messages/count_tokens")
@limiter.limit(_proxy_limit)
async def count_tokens(request: Request, vk: VirtualKey = Depends(get_current_virtual_key)):
    cfg = get_config()
    body_bytes = await request.body()
    headers = {**build_forward_headers(dict(request.headers)), "content-type": "application/json"}
    url = cfg.upstream.base_url.rstrip("/") + "/v1/messages/count_tokens"
    async with httpx.AsyncClient(timeout=cfg.upstream.timeout_seconds) as client:
        resp = await client.post(url, content=body_bytes, headers=headers)
    is_json = resp.headers.get("content-type", "").startswith("application/json")
    return JSONResponse(
        content=resp.json() if is_json else {"error": {"message": resp.text}},
        status_code=resp.status_code,
    )
