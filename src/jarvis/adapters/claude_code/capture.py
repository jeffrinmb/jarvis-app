"""Turn a session transcript's token usage into token-ledger rows (task P2.2a, ADR 0008).

Runs on ``Stop``, ``SubagentStop`` and ``SessionEnd``. Re-reading the transcript is safe
because a row is only written for a message id that has no row yet, so a late or repeated
event, or a transcript that lagged behind the last turn, simply catches up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError
from yaml import YAMLError

from jarvis.core.ledger import Ledger
from jarvis.core.schemas import EventKind
from jarvis.core.schemas.loader import parse_yaml_model
from jarvis.core.schemas.price_table import PriceTable
from jarvis.core.tokens import model_call_event

from .mapping import agent_identity, safe_session_id
from .source import SourceError
from .transcript import MessageUsage, read_transcript_usage

if TYPE_CHECKING:
    from .handler import HookContext
    from .payload import HookInput

SOURCE = "claude-code-transcript"
MAX_NEW_ROWS = 20000


def _load_prices(ctx: HookContext) -> tuple[PriceTable | None, str | None]:
    """The price table and its hash, or ``(None, None)``: costs are then recorded as unknown.

    It is read through the policy source, so an agent cannot lower a price in the working tree
    to understate cost. An agent-writable source is not trusted in enforce mode.
    """
    path = ctx.cfg.prices_path
    source = ctx.policy_source
    if not path or (ctx.enforcing and not source.protected):
        return None, None
    try:
        found = source.read(path)
        if found is None:
            return None, None
        return parse_yaml_model(found.text, PriceTable), found.sha256
    except (SourceError, ValidationError, YAMLError, ValueError):
        return None, None


def _event(
    message: MessageUsage,
    inp: HookInput,
    ctx: HookContext,
    path: str,
    prices: PriceTable | None,
    prices_sha256: str | None,
    session: str,
    now: datetime,
):
    agent_id, behalf = agent_identity(inp, ctx.cfg)
    if inp.hook_event_name != "SubagentStop":
        agent_id = ctx.cfg.agent_name  # the main transcript belongs to the session, not a subagent
    ts = message.ts if message.ts is not None and message.ts <= now else None
    extra = {
        "source": SOURCE,
        "message_id": message.message_id,
        "session_id": session,
        "cache_creation_tokens": message.cache_creation_tokens,
        "sidechain": message.sidechain,
        # Which event and file this came from, to diagnose how Claude Code passes transcripts.
        # Only the file name: a full path would reveal the user's directory layout.
        "hook_event": inp.hook_event_name,
        "transcript_file": Path(path).name[:120],
    }
    if prices_sha256 and prices is not None and message.model in prices.models:
        extra["prices_sha256"] = prices_sha256
    return model_call_event(
        agent_id=agent_id,
        on_behalf_of=behalf,
        model=message.model,
        provider="anthropic",
        # Cache writes have no field of their own (ADR 0007): folding them into tokens_in
        # keeps them counted and understates their price a little.
        tokens_in=message.input_tokens + message.cache_creation_tokens,
        tokens_out=message.output_tokens,
        tokens_cached=message.cache_read_tokens,
        prices=prices,
        task_id=f"claude-{session}",
        outcome="ok",
        ts=ts,
        extra_data=extra,
    )


def capture_usage(inp: HookInput, ctx: HookContext, ledger: Ledger, path: str) -> int:
    """Append a row for each message in the transcript at ``path`` that has none yet.
    Returns how many."""
    transcript = read_transcript_usage(path)
    session = safe_session_id(inp.session_id)
    prices, prices_sha256 = _load_prices(ctx)
    now = datetime.now(UTC)
    written = 0
    with ledger.transaction() as tx:
        recorded = set()
        for record in tx.records():
            event = record["event"]
            data = event.get("data") or {}
            if (
                event.get("kind") == EventKind.MODEL_CALL.value
                and data.get("source") == SOURCE
                and data.get("session_id") == session
            ):
                recorded.add(data.get("message_id"))
        for message in transcript.messages:
            if message.message_id in recorded or written >= MAX_NEW_ROWS:
                continue
            tx.append(_event(message, inp, ctx, path, prices, prices_sha256, session, now))
            written += 1
    return written
