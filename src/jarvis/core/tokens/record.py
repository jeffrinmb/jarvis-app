"""Write one ledger row per model call (task P2.2).

A row is a ``model_call`` event whose ``usage`` holds the token counts and cost. Nothing about
the prompt or the answer is stored: only the counts, the model and the outcome.

``data.cost_source`` says where the cost came from: ``reported`` (the caller supplied it,
normally the provider's own figure), ``computed`` (from a price table) or ``unknown`` (neither,
so ``cost_usd`` is 0 and must not be read as free).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from jarvis.core.ledger import Ledger
from jarvis.core.schemas import Event, EventKind
from jarvis.core.schemas.event import ModelRef, Usage

from .pricing import PriceTable

COST_SOURCES = ("reported", "computed", "unknown")


def model_call_event(
    *,
    agent_id: str,
    on_behalf_of: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int = 0,
    cost_usd: float | None = None,
    prices: PriceTable | None = None,
    provider: str | None = None,
    prompt_version: str | None = None,
    task_id: str | None = None,
    stage: str | None = None,
    outcome: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    ts: datetime | None = None,
    extra_data: dict[str, Any] | None = None,
) -> Event:
    """Build the row. A supplied ``cost_usd`` wins over the price table. ``ts`` is when the
    call happened, if that is known and not now; ``extra_data`` is merged into ``data`` (it must
    hold no content)."""
    if cost_usd is not None:
        cost, source = cost_usd, "reported"
    else:
        computed = prices.cost(model, tokens_in, tokens_out, tokens_cached) if prices else None
        cost, source = (computed, "computed") if computed is not None else (0.0, "unknown")
    fields: dict[str, Any] = {} if ts is None else {"ts": ts}
    return Event(
        **fields,
        kind=EventKind.MODEL_CALL,
        agent_id=agent_id,
        on_behalf_of=on_behalf_of,
        task_id=task_id,
        stage=stage,
        trace_id=trace_id,
        span_id=span_id,
        model=ModelRef(name=model, provider=provider, prompt_version=prompt_version),
        usage=Usage(
            tokens_in=tokens_in, tokens_out=tokens_out, tokens_cached=tokens_cached, cost_usd=cost
        ),
        outcome=outcome,
        data={**(extra_data or {}), "cost_source": source},
    )


def record_model_call(ledger: Ledger, **fields) -> dict:
    """Append a model-call row and return the ledger record."""
    return ledger.append(model_call_event(**fields))
