"""Forward requests to a local mlx-lm / mlx-vlm server (OpenAI-compatible API).

Handles Anthropic ↔ OpenAI format conversion. Two protocol paths:

  - Standard OpenAI tool calling (Qwen, Llama 3+, Mistral, ...):
    assistant.tool_calls field, role="tool" messages with tool_call_id,
    structured tool_calls in response and streaming deltas. Preferred
    path — works out-of-the-box with Claude Code tool use.

  - Gemma 4 custom-token protocol:
    Gemma's chat template does not support OpenAI tool_calls. Instead it
    emits/consumes special tokens inline in the `content` field:
      <|tool_call>call:func_name{key:<|"|>value<|"|>}<tool_call|>
      <|tool_response>...<tool_response|>
      <|channel>thought ... <channel|>
      <|"|>                                         (string delimiter)
    mlx-lm/mlx-vlm does not parse these into structured tool_calls (issue
    #1096), so this module parses them from raw text output and buffers
    streaming output so tokens never leak to the client.

Model detection: any model id containing "gemma" routes to the Gemma path.
Everything else uses the standard OpenAI path.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import AsyncIterator, Dict, List, Optional, Union

import httpx

from app.config import AppConfig
from app.tracking.pricing import TokenUsage

from .claude import BackendResult

log = logging.getLogger("ccm.mlx")

_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
}


# ---------------------------------------------------------------------------
# Model detection
# ---------------------------------------------------------------------------

def _is_gemma_model(model_id: Optional[str]) -> bool:
    return "gemma" in (model_id or "").lower()


# ---------------------------------------------------------------------------
# Gemma 4 special-token handling (only used on Gemma path)
# ---------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(
    r"<\|tool_call\>call:(\w+)\{(.*?)\}<tool_call\|>", re.DOTALL
)
_CHANNEL_RE = re.compile(r"<\|channel\>.*?<channel\|>", re.DOTALL)
_TOOL_RESPONSE_TAIL_RE = re.compile(r"<\|tool_response\>.*", re.DOTALL)

_PIPE_QUOTE = "<|\"|>"

_PAIRED_TOKENS: tuple[tuple[str, str], ...] = (
    ("<|tool_call>", "<tool_call|>"),
    ("<|channel>", "<channel|>"),
)
_STANDALONE_TOKENS: tuple[str, ...] = (_PIPE_QUOTE,)
_ALL_SPECIAL_TOKENS: tuple[str, ...] = tuple(
    [o for o, _ in _PAIRED_TOKENS]
    + [c for _, c in _PAIRED_TOKENS]
    + list(_STANDALONE_TOKENS)
)


def _safe_strip_prefix(text: str) -> str:
    """Strip Gemma 4 special tokens incrementally for streaming.

    Returns the prefix of `text` that is safe to emit to the client:
      - Complete paired blocks (tool_call, channel) are removed
      - Standalone `<|"|>` tokens are removed
      - If an opener has no closer yet, output stops before the opener
      - If trailing text could be a partial prefix of any special token,
        output stops before it (caller re-invokes with more text later)

    Because stripping is deterministic, repeated calls on a growing `text`
    return a monotonically-growing result — safe bytes are never retracted.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        nxt = text.find("<", i)
        if nxt == -1:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:nxt])
        suffix = text[nxt:]

        matched = False
        for opener, closer in _PAIRED_TOKENS:
            if suffix.startswith(opener):
                close_pos = suffix.find(closer, len(opener))
                if close_pos == -1:
                    return "".join(out)
                i = nxt + close_pos + len(closer)
                matched = True
                break
        if matched:
            continue

        for tok in _STANDALONE_TOKENS:
            if suffix.startswith(tok):
                i = nxt + len(tok)
                matched = True
                break
        if matched:
            continue

        if any(t.startswith(suffix) for t in _ALL_SPECIAL_TOKENS):
            return "".join(out)

        out.append("<")
        i = nxt + 1
    return "".join(out)


def _clean_gemma_text(text: str) -> str:
    """Strip all Gemma 4 special tokens from a complete string.

    Used at end-of-stream and for non-streaming responses. Handles unclosed
    openers (truncated output) by dropping from the opener to end.
    """
    text = _TOOL_CALL_RE.sub("", text)
    text = _CHANNEL_RE.sub("", text)
    text = _TOOL_RESPONSE_TAIL_RE.sub("", text)
    text = text.replace(_PIPE_QUOTE, "")
    for opener, _ in _PAIRED_TOKENS:
        idx = text.find(opener)
        if idx >= 0:
            text = text[:idx]
    return text


def _parse_gemma4_params(raw: str) -> dict:
    """Parse Gemma 4 tool call params: key:<|"|>val<|"|>,key2:123"""
    params: dict = {}
    if not raw.strip():
        return params

    i = 0
    while i < len(raw):
        while i < len(raw) and raw[i] in (",", " ", "\n"):
            i += 1
        if i >= len(raw):
            break

        colon = raw.find(":", i)
        if colon == -1:
            break
        key = raw[i:colon].strip()
        i = colon + 1

        if raw[i:].startswith(_PIPE_QUOTE):
            start = i + len(_PIPE_QUOTE)
            end = raw.find(_PIPE_QUOTE, start)
            if end == -1:
                params[key] = raw[start:]
                break
            params[key] = raw[start:end]
            i = end + len(_PIPE_QUOTE)
        elif raw[i:].startswith("{"):
            depth = 0
            j = i
            while j < len(raw):
                if raw[j] == "{":
                    depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0:
                        nested = raw[i:j+1]
                        try:
                            params[key] = json.loads(nested.replace(_PIPE_QUOTE, '"'))
                        except json.JSONDecodeError:
                            params[key] = nested
                        i = j + 1
                        break
                j += 1
            else:
                params[key] = raw[i:]
                break
        elif raw[i:].startswith("["):
            depth = 0
            j = i
            while j < len(raw):
                if raw[j] == "[":
                    depth += 1
                elif raw[j] == "]":
                    depth -= 1
                    if depth == 0:
                        arr = raw[i:j+1]
                        try:
                            params[key] = json.loads(arr.replace(_PIPE_QUOTE, '"'))
                        except json.JSONDecodeError:
                            params[key] = arr
                        i = j + 1
                        break
                j += 1
            else:
                params[key] = raw[i:]
                break
        else:
            end = raw.find(",", i)
            if end == -1:
                val_str = raw[i:].strip()
                i = len(raw)
            else:
                val_str = raw[i:end].strip()
                i = end
            if val_str.lower() == "true":
                params[key] = True
            elif val_str.lower() == "false":
                params[key] = False
            elif val_str.lower() == "null":
                params[key] = None
            else:
                try:
                    params[key] = int(val_str)
                except ValueError:
                    try:
                        params[key] = float(val_str)
                    except ValueError:
                        params[key] = val_str

    return params


def _parse_gemma_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Parse Gemma 4 tool calls from text. Returns (clean_text, tool_calls)."""
    tool_calls = []
    for m in _TOOL_CALL_RE.finditer(text):
        func_name = m.group(1)
        raw_params = m.group(2)
        params = _parse_gemma4_params(raw_params)
        tool_calls.append({
            "id": f"toolu_{uuid.uuid4().hex[:12]}",
            "name": func_name,
            "input": params,
        })
    return _clean_gemma_text(text).strip(), tool_calls


def _encode_gemma_value(v) -> str:
    """Encode a Python value into Gemma 4's custom param-value format."""
    if isinstance(v, str):
        return f'{_PIPE_QUOTE}{v}{_PIPE_QUOTE}'
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False).replace('"', _PIPE_QUOTE)
    return f'{_PIPE_QUOTE}{v}{_PIPE_QUOTE}'


def _encode_gemma_tool_call(name: str, params: dict) -> str:
    """Serialize a tool call in Gemma 4's native token format."""
    body = ",".join(f"{k}:{_encode_gemma_value(v)}" for k, v in params.items())
    return f"<|tool_call>call:{name}{{{body}}}<tool_call|>"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function format."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return result


def _convert_tool_result_content(content) -> str:
    """Extract text from Anthropic tool_result content field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def _extract_system_text(system: Union[str, list, None]) -> Optional[str]:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    parts = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts) if parts else None


def _convert_content(content: Union[str, list]) -> Union[str, List[dict]]:
    """Convert Anthropic user/text content to OpenAI content (text or multimodal list)."""
    if isinstance(content, str):
        return content
    parts: List[dict] = []
    for block in content:
        if isinstance(block, str):
            parts.append({"type": "text", "text": block})
        elif block.get("type") == "text":
            parts.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image":
            src = block.get("source", {})
            media = src.get("media_type", "image/jpeg")
            data = src.get("data", "")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media};base64,{data}"},
            })
    if all(p["type"] == "text" for p in parts):
        return " ".join(p["text"] for p in parts)
    return parts


# ---------------------------------------------------------------------------
# Message building — Gemma 4 path
# ---------------------------------------------------------------------------

def _build_gemma_messages(body: dict) -> list[dict]:
    """Anthropic messages → OpenAI messages for Gemma 4.

    Gemma's chat template doesn't understand OpenAI tool_calls. Instead we
    embed tool_use as `<|tool_call>...<tool_call|>` tokens inside the
    assistant's `content`, and tool_result as `<|tool_response>...<tool_response|>`
    inside the user's `content`. This matches Gemma's native turn format,
    so the model sees prior calls in history and won't loop.
    """
    messages: List[dict] = []

    sys_text = _extract_system_text(body.get("system"))
    if sys_text:
        messages.append({"role": "system", "content": sys_text})

    for msg in body.get("messages", []):
        content = msg.get("content", "")
        role = msg["role"]

        if role == "assistant" and isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        parts.append(
                            _encode_gemma_tool_call(
                                block["name"], block.get("input", {})
                            )
                        )
            messages.append({
                "role": "assistant",
                "content": " ".join(p for p in parts if p).strip(),
            })
            continue

        if role == "user" and isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_result":
                        result_text = _convert_tool_result_content(block.get("content"))
                        parts.append(f"<|tool_response>{result_text}<tool_response|>")
                    elif block.get("type") == "text":
                        parts.append(block["text"])
                elif isinstance(block, str):
                    parts.append(block)
            if parts:
                messages.append({
                    "role": "user",
                    "content": " ".join(p for p in parts if p).strip(),
                })
            continue

        messages.append({"role": role, "content": _convert_content(content)})

    return messages


# ---------------------------------------------------------------------------
# Message building — standard OpenAI path
# ---------------------------------------------------------------------------

def _build_standard_messages(body: dict) -> list[dict]:
    """Anthropic messages → OpenAI messages using standard tool calling spec.

    - Anthropic `tool_use` block → OpenAI assistant message `tool_calls` entry
      with `{id, type: "function", function: {name, arguments}}`.
    - Anthropic `tool_result` block → OpenAI `{role: "tool", tool_call_id, content}`
      message (one per tool_result, since OpenAI requires 1:1 with tool_calls).
    - Anthropic tool_use `id` is reused as OpenAI `tool_call_id`, preserving
      the link the client-side framework already set up.
    """
    messages: List[dict] = []

    sys_text = _extract_system_text(body.get("system"))
    if sys_text:
        messages.append({"role": "system", "content": sys_text})

    for msg in body.get("messages", []):
        content = msg.get("content", "")
        role = msg["role"]

        if role == "assistant" and isinstance(content, list):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", f"call_{uuid.uuid4().hex[:16]}"),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(
                                block.get("input", {}), ensure_ascii=False
                            ),
                        },
                    })
            entry: dict = {"role": "assistant"}
            text = "".join(text_parts).strip()
            # OpenAI spec: content may be null when tool_calls are present.
            entry["content"] = text if text else (None if tool_calls else "")
            if tool_calls:
                entry["tool_calls"] = tool_calls
            messages.append(entry)
            continue

        if role == "user" and isinstance(content, list):
            # Split blocks: tool_results become separate role=tool messages,
            # remaining blocks fold into a single role=user message.
            leftover_blocks: list = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": _convert_tool_result_content(block.get("content")),
                    })
                else:
                    leftover_blocks.append(block)
            if leftover_blocks:
                messages.append({
                    "role": "user",
                    "content": _convert_content(leftover_blocks),
                })
            continue

        messages.append({"role": role, "content": _convert_content(content)})

    return messages


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------

def _resolve_active_model(body: dict, cfg: AppConfig) -> str:
    """Pick the model id to send to the MLX server."""
    from app.backend import mlx_server
    active_model = mlx_server.get_status().get("model")
    if active_model:
        return active_model
    model_map = cfg.backend.mlx.model_map
    first_hf_id = next(iter(model_map.values()), None)
    return first_hf_id or body.get("model", "")


def _anthropic_to_openai(body: dict, cfg: AppConfig) -> tuple[dict, bool]:
    """Build the OpenAI-format request. Returns (body, is_gemma)."""
    active_model = _resolve_active_model(body, cfg)
    is_gemma = _is_gemma_model(active_model)

    if is_gemma:
        messages = _build_gemma_messages(body)
    else:
        messages = _build_standard_messages(body)

    req: dict = {
        "model": active_model,
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
    }

    if "temperature" in body:
        req["temperature"] = body["temperature"]
    if "top_p" in body:
        req["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        req["stop"] = body["stop_sequences"]

    tools = body.get("tools")
    if tools:
        req["tools"] = _anthropic_tools_to_openai(tools)
        # Anthropic tool_choice → OpenAI tool_choice
        tc = body.get("tool_choice")
        if isinstance(tc, dict):
            ttype = tc.get("type")
            if ttype == "auto":
                req["tool_choice"] = "auto"
            elif ttype == "any":
                req["tool_choice"] = "required"
            elif ttype == "tool" and tc.get("name"):
                req["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tc["name"]},
                }
            elif ttype == "none":
                req["tool_choice"] = "none"

    return req, is_gemma


# ---------------------------------------------------------------------------
# Non-streaming response conversion
# ---------------------------------------------------------------------------

def _openai_to_anthropic(openai_resp: dict, requested_model: str, is_gemma: bool) -> dict:
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})
    usage = openai_resp.get("usage", {})
    finish = choice.get("finish_reason", "stop")

    raw_content = message.get("content") or ""

    tool_calls: list[dict] = []

    # Structured OpenAI tool_calls (always parsed when present — works on both paths
    # since mlx-lm may emit them for any model that actually supports it).
    for tc in message.get("tool_calls") or []:
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {"raw": func.get("arguments", "")}
        tool_calls.append({
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
            "name": func.get("name", ""),
            "input": args,
        })

    # Gemma path: also parse custom tokens from content text.
    if is_gemma:
        clean_text, parsed = _parse_gemma_tool_calls(raw_content)
        tool_calls.extend(parsed)
    else:
        clean_text = raw_content

    content_blocks: list[dict] = []
    if clean_text:
        content_blocks.append({"type": "text", "text": clean_text})
    for tc in tool_calls:
        content_blocks.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop_reason = "tool_use" if tool_calls else _FINISH_REASON_MAP.get(finish, finish)

    return {
        "id": openai_resp.get("id", "msg_mlx"),
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _usage_from_openai(usage: dict) -> TokenUsage:
    return TokenUsage(
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


# ---------------------------------------------------------------------------
# Streaming — shared SSE helpers
# ---------------------------------------------------------------------------

def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


def _message_start(requested_model: str) -> bytes:
    return _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": "msg_mlx",
            "type": "message",
            "role": "assistant",
            "model": requested_model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })


def _text_block_start(idx: int) -> bytes:
    return _sse("content_block_start", {
        "type": "content_block_start",
        "index": idx,
        "content_block": {"type": "text", "text": ""},
    })


def _text_block_delta(idx: int, text: str) -> bytes:
    return _sse("content_block_delta", {
        "type": "content_block_delta",
        "index": idx,
        "delta": {"type": "text_delta", "text": text},
    })


def _tool_block_start(idx: int, tc_id: str, name: str) -> bytes:
    return _sse("content_block_start", {
        "type": "content_block_start",
        "index": idx,
        "content_block": {
            "type": "tool_use",
            "id": tc_id,
            "name": name,
            "input": {},
        },
    })


def _tool_block_delta(idx: int, partial_json: str) -> bytes:
    return _sse("content_block_delta", {
        "type": "content_block_delta",
        "index": idx,
        "delta": {"type": "input_json_delta", "partial_json": partial_json},
    })


def _block_stop(idx: int) -> bytes:
    return _sse("content_block_stop", {"type": "content_block_stop", "index": idx})


def _message_delta(stop_reason: str, output_tokens: int) -> bytes:
    return _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    })


def _message_stop() -> bytes:
    return _sse("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# Streaming — standard OpenAI path
# ---------------------------------------------------------------------------

async def _stream_standard_sse(
    client: httpx.AsyncClient,
    resp: httpx.Response,
    requested_model: str,
) -> AsyncIterator[bytes]:
    """Stream OpenAI chat-completion SSE → Anthropic messages SSE.

    Handles both text content and structured tool_calls. Emits content blocks
    in arrival order: text block stays open while text flows, closes when
    the first tool_call delta arrives.
    """
    yield _message_start(requested_model)

    input_tokens = 0
    output_tokens = 0
    finish_reason = "stop"

    text_idx: Optional[int] = None
    text_closed = False
    next_block_idx = 0
    # openai tool index -> {block_idx, id, name, started, args_buffered}
    tool_state: dict[int, dict] = {}

    try:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                text = delta.get("content")
                if text:
                    if text_idx is None:
                        text_idx = next_block_idx
                        next_block_idx += 1
                        yield _text_block_start(text_idx)
                    if not text_closed:
                        yield _text_block_delta(text_idx, text)

                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    func = tc_delta.get("function", {})

                    # First delta for this tool: close text block (if any)
                    # and allocate a new block_idx.
                    if idx not in tool_state:
                        if text_idx is not None and not text_closed:
                            yield _block_stop(text_idx)
                            text_closed = True
                        tool_state[idx] = {
                            "block_idx": next_block_idx,
                            "id": tc_delta.get("id", ""),
                            "name": func.get("name", ""),
                            "started": False,
                            "args_buffered": "",
                        }
                        next_block_idx += 1

                    st = tool_state[idx]
                    if tc_delta.get("id"):
                        st["id"] = tc_delta["id"]
                    if func.get("name"):
                        st["name"] = func["name"]

                    # Start the block as soon as we have a name.
                    if not st["started"] and st["name"]:
                        if not st["id"]:
                            st["id"] = f"toolu_{uuid.uuid4().hex[:12]}"
                        yield _tool_block_start(st["block_idx"], st["id"], st["name"])
                        st["started"] = True
                        if st["args_buffered"]:
                            yield _tool_block_delta(st["block_idx"], st["args_buffered"])
                            st["args_buffered"] = ""

                    args_chunk = func.get("arguments")
                    if args_chunk:
                        if st["started"]:
                            yield _tool_block_delta(st["block_idx"], args_chunk)
                        else:
                            st["args_buffered"] += args_chunk
    finally:
        if text_idx is not None and not text_closed:
            yield _block_stop(text_idx)
            text_closed = True

        for idx in sorted(tool_state.keys()):
            st = tool_state[idx]
            if not st["started"]:
                if not st["id"]:
                    st["id"] = f"toolu_{uuid.uuid4().hex[:12]}"
                yield _tool_block_start(st["block_idx"], st["id"], st["name"] or "unknown")
                if st["args_buffered"]:
                    yield _tool_block_delta(st["block_idx"], st["args_buffered"])
                st["started"] = True
            yield _block_stop(st["block_idx"])

        if text_idx is None and not tool_state:
            empty_idx = next_block_idx
            yield _text_block_start(empty_idx)
            yield _block_stop(empty_idx)

        stop_reason = (
            "tool_use" if tool_state
            else _FINISH_REASON_MAP.get(finish_reason, finish_reason)
        )
        yield _message_delta(stop_reason, output_tokens)
        yield _message_stop()

        await resp.aclose()
        await client.aclose()


# ---------------------------------------------------------------------------
# Streaming — Gemma 4 path
# ---------------------------------------------------------------------------

async def _stream_gemma_sse(
    client: httpx.AsyncClient,
    resp: httpx.Response,
    requested_model: str,
) -> AsyncIterator[bytes]:
    """Stream Gemma 4 chat-completion → Anthropic messages SSE.

    Buffers content text so Gemma's `<|tool_call>...<tool_call|>` and
    `<|channel>...<channel|>` tokens never leak as text_delta. Parses tool
    calls from the accumulated text at end-of-stream; also captures any
    structured tool_calls that happen to come through.
    """
    yield _message_start(requested_model)

    output_tokens = 0
    input_tokens = 0

    full_text: list[str] = []
    emitted_clean_len = 0
    text_block_started = False
    finish_reason = "stop"

    streaming_tool_calls: dict[int, dict] = {}

    try:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data.strip() == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                text = delta.get("content")
                if text:
                    full_text.append(text)
                    safe_emit = _safe_strip_prefix("".join(full_text))
                    if len(safe_emit) > emitted_clean_len:
                        new_chunk = safe_emit[emitted_clean_len:]
                        emitted_clean_len = len(safe_emit)
                        if not text_block_started:
                            yield _text_block_start(0)
                            text_block_started = True
                        yield _text_block_delta(0, new_chunk)

                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    if idx not in streaming_tool_calls:
                        streaming_tool_calls[idx] = {
                            "id": tc_delta.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
                            "name": "",
                            "arguments_parts": [],
                        }
                    tc_acc = streaming_tool_calls[idx]
                    if tc_delta.get("id"):
                        tc_acc["id"] = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if func.get("name"):
                        tc_acc["name"] = func["name"]
                    if func.get("arguments"):
                        tc_acc["arguments_parts"].append(func["arguments"])
    finally:
        block_idx = 0

        combined_raw = "".join(full_text)
        final_clean = _clean_gemma_text(combined_raw)
        if len(final_clean) > emitted_clean_len:
            if not text_block_started:
                yield _text_block_start(0)
                text_block_started = True
            yield _text_block_delta(0, final_clean[emitted_clean_len:])
            emitted_clean_len = len(final_clean)

        if text_block_started:
            yield _block_stop(0)
            block_idx = 1

        tool_calls: list[dict] = []
        for idx in sorted(streaming_tool_calls.keys()):
            tc_acc = streaming_tool_calls[idx]
            args_str = "".join(tc_acc["arguments_parts"])
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {"raw": args_str}
            tool_calls.append({
                "id": tc_acc["id"],
                "name": tc_acc["name"],
                "input": args,
            })

        if not tool_calls:
            _, parsed = _parse_gemma_tool_calls(combined_raw)
            tool_calls = parsed

        if not text_block_started and not tool_calls:
            yield _text_block_start(0)
            yield _block_stop(0)
            block_idx = 1

        for tc in tool_calls:
            yield _tool_block_start(block_idx, tc["id"], tc["name"])
            yield _tool_block_delta(block_idx, json.dumps(tc["input"]))
            yield _block_stop(block_idx)
            block_idx += 1

        stop_reason = (
            "tool_use" if tool_calls
            else _FINISH_REASON_MAP.get(finish_reason, finish_reason)
        )
        yield _message_delta(stop_reason, output_tokens)
        yield _message_stop()

        await resp.aclose()
        await client.aclose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def forward(body: dict, cfg: AppConfig, is_stream: bool) -> BackendResult:
    mlx_cfg = cfg.backend.mlx
    requested_model = body.get("model", "")
    openai_body, is_gemma = _anthropic_to_openai(body, cfg)
    url = mlx_cfg.base_url.rstrip("/") + "/v1/chat/completions"

    log.info(
        "mlx forward model=%s -> %s stream=%s tools=%d path=%s",
        requested_model, openai_body["model"], is_stream,
        len(openai_body.get("tools", [])),
        "gemma" if is_gemma else "openai",
    )
    if openai_body.get("tools"):
        log.debug("mlx tools: %s", json.dumps(openai_body["tools"][:2], default=str)[:500])
    log.debug("mlx messages (last 2): %s", json.dumps(openai_body["messages"][-2:], default=str)[:1000])

    def _mlx_conn_error(exc: Exception) -> BackendResult:
        return BackendResult(
            status_code=502,
            headers={"content-type": "application/json"},
            body={
                "type": "error",
                "error": {
                    "type": "mlx_connection_error",
                    "message": f"Cannot connect to MLX server at {mlx_cfg.base_url}: {exc}",
                },
            },
        )

    def _mlx_upstream_error(status_code: int, detail: str) -> BackendResult:
        return BackendResult(
            status_code=status_code,
            headers={"content-type": "application/json"},
            body={
                "type": "error",
                "error": {"type": "mlx_error", "message": detail},
            },
        )

    if not is_stream:
        client = httpx.AsyncClient(timeout=mlx_cfg.timeout_seconds)
        try:
            resp = await client.post(url, json=openai_body)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            await client.aclose()
            return _mlx_conn_error(exc)
        finally:
            await client.aclose()

        openai_resp = resp.json()
        log.debug(
            "mlx response content: %s",
            json.dumps(openai_resp.get("choices", [{}])[0].get("message", {}), default=str)[:1000],
        )
        if resp.status_code >= 400:
            return _mlx_upstream_error(resp.status_code, json.dumps(openai_resp))

        anthropic_resp = _openai_to_anthropic(openai_resp, requested_model, is_gemma)
        usage = _usage_from_openai(openai_resp.get("usage", {}))
        return BackendResult(
            status_code=200,
            headers={"content-type": "application/json"},
            body=anthropic_resp,
            usage=usage,
        )

    # Streaming
    openai_body["stream"] = True
    openai_body["stream_options"] = {"include_usage": True}
    client = httpx.AsyncClient(timeout=mlx_cfg.timeout_seconds)
    try:
        req = client.build_request("POST", url, json=openai_body)
        resp = await client.send(req, stream=True)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        await client.aclose()
        return _mlx_conn_error(exc)

    if resp.status_code >= 400:
        try:
            error_body = await resp.aread()
            detail = error_body.decode("utf-8", errors="replace")
        except Exception:
            detail = f"MLX server returned HTTP {resp.status_code}"
        await resp.aclose()
        await client.aclose()
        log.warning("mlx stream error %d: %s", resp.status_code, detail[:500])
        return _mlx_upstream_error(resp.status_code, detail)

    stream = (
        _stream_gemma_sse(client, resp, requested_model) if is_gemma
        else _stream_standard_sse(client, resp, requested_model)
    )
    return BackendResult(
        status_code=200,
        headers={"content-type": "text/event-stream", "cache-control": "no-cache"},
        stream=stream,
    )
