from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.config import get_config


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_api_dict(cls, payload: Dict[str, Any]) -> "TokenUsage":
        u = payload.get("usage") or {}
        return cls(
            input_tokens=int(u.get("input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0) or 0),
            cache_creation_input_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
            cache_read_input_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        )


def calc_cost(model: str, usage: TokenUsage) -> float:
    prices = get_config().pricing
    p = prices.get(model)
    if p is None:
        return 0.0
    total = (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_creation_input_tokens * p.cache_write
        + usage.cache_read_input_tokens * p.cache_read
    )
    return total / 1_000_000.0
