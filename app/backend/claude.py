"""Forward requests to Anthropic API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional

import httpx

from app.config import AppConfig
from app.tracking.pricing import TokenUsage

_STRIP_REQUEST_HEADERS = {
    "host",
    "content-length",
    "x-cc-api-key",
    "x-ccm-key",
    "cookie",
}

_STRIP_RESPONSE_HEADERS = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
}


@dataclass
class BackendResult:
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[dict] = None
    stream: Optional[AsyncIterator[bytes]] = None
    usage: Optional[TokenUsage] = None


def build_forward_headers(raw_headers: Dict[str, str]) -> Dict[str, str]:
    return {
        k: v for k, v in raw_headers.items() if k.lower() not in _STRIP_REQUEST_HEADERS
    }


def clean_response_headers(src: httpx.Headers) -> Dict[str, str]:
    return {k: v for k, v in src.items() if k.lower() not in _STRIP_RESPONSE_HEADERS}


async def forward(
    body_bytes: bytes,
    headers: Dict[str, str],
    cfg: AppConfig,
    is_stream: bool,
) -> BackendResult:
    url = cfg.upstream.base_url.rstrip("/") + "/v1/messages"
    fwd_headers = {**build_forward_headers(headers), "content-type": "application/json"}
    client = httpx.AsyncClient(timeout=cfg.upstream.timeout_seconds)

    if not is_stream:
        try:
            resp = await client.post(url, content=body_bytes, headers=fwd_headers)
        finally:
            await client.aclose()

        is_json = resp.headers.get("content-type", "").startswith("application/json")
        body = resp.json() if is_json else {"error": {"message": resp.text}}
        usage = (
            TokenUsage.from_api_dict(body)
            if resp.status_code < 400 and is_json
            else None
        )
        return BackendResult(
            status_code=resp.status_code,
            headers=clean_response_headers(resp.headers),
            body=body,
            usage=usage,
        )

    # streaming: peek status before returning iterator
    async def _stream_gen(c: httpx.AsyncClient) -> AsyncIterator[bytes]:
        try:
            async with c.stream(
                "POST", url, content=body_bytes, headers=fwd_headers
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk
        finally:
            await c.aclose()

    # Open the stream to read the status code before handing off
    cm = client.stream("POST", url, content=body_bytes, headers=fwd_headers)
    resp_stream = await cm.__aenter__()
    status_code = resp_stream.status_code
    resp_headers = clean_response_headers(resp_stream.headers)

    if status_code == 529:
        # quota exceeded — close immediately so caller can retry
        await resp_stream.aclose()
        await cm.__aexit__(None, None, None)
        await client.aclose()
        return BackendResult(status_code=529, headers=resp_headers)

    async def _consume() -> AsyncIterator[bytes]:
        try:
            async for chunk in resp_stream.aiter_bytes():
                yield chunk
        finally:
            await cm.__aexit__(None, None, None)
            await client.aclose()

    return BackendResult(
        status_code=status_code,
        headers=resp_headers,
        stream=_consume(),
    )
