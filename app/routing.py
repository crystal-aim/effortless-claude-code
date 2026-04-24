from typing import Any, List, Optional

from app.config import get_config


def _extract_system_text(system_field: Any) -> str:
    """Anthropic /v1/messages 'system' can be a string or a list of content blocks."""
    if system_field is None:
        return ""
    if isinstance(system_field, str):
        return system_field
    if isinstance(system_field, list):
        parts: List[str] = []
        for block in system_field:
            if isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


def choose_model(body: dict, requested_model: Optional[str]) -> str:
    """Pick a model based on system prompt keywords; fallthrough to requested or default."""
    cfg = get_config()
    system_text = _extract_system_text(body.get("system")).lower()

    if system_text:
        for rule in cfg.routing_rules:
            for kw in rule.keywords:
                if kw.lower() in system_text:
                    return rule.model

    if requested_model:
        return requested_model
    return cfg.default_model
