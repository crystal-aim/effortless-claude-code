from datetime import datetime

from sqlalchemy import update

from app.db import session_scope
from app.models import UsageLog, VirtualKey
from app.tracking.pricing import TokenUsage, calc_cost


def record_usage(
    key_id: int,
    model: str,
    usage: TokenUsage,
    latency_ms: int,
    status_code: int = 200,
) -> float:
    cost = calc_cost(model, usage)
    with session_scope() as db:
        log = UsageLog(
            key_id=key_id,
            model=model,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            status_code=status_code,
            created_at=datetime.utcnow(),
        )
        db.add(log)
        # atomic increment to avoid lost updates
        db.execute(
            update(VirtualKey)
            .where(VirtualKey.id == key_id)
            .values(spend_usd=VirtualKey.spend_usd + cost)
        )
    return cost
