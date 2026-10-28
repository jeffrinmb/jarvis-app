# ADR 0007: Token and cost ledger

- Status: Accepted
- Date: 2026-10-28
- Deciders: project owner (task P2.2)

## Context

The charter (section 8.2) asks for one ledger row per model call, with agent, accountable human, task, model, tokens in and out, cached tokens, cost and outcome, and for cost to be a first-class metric (cost per merged PR, cache hit rate). Budgets by token or cost (P2.8b) and the OpenTelemetry export (P2.3) both need these rows to exist first.

The audit event already had `model` and `usage` fields and a `model_call` kind, and `agentctl log` could write them by hand. What was missing was a convention for what the numbers mean, a way to price a call, and a report.

## Decision

1. **A row is a `model_call` event in the same hash-chained ledger.** There is no second store. Rows are tamper-evident like every other event and share `task_id`, `stage`, `trace_id` and `span_id` with the decisions around them.
2. **Field meaning is fixed:** `tokens_in` are input tokens **not** served from a cache, `tokens_cached` are input tokens that were, `tokens_out` are output tokens. Cache hit rate is `cached / (in + cached)`. An adapter whose provider counts differently must convert before recording.
3. **Cost has a recorded source.** `data.cost_source` is `reported` (the caller gave a cost, normally the provider's own figure, and it wins), `computed` (from a price table) or `unknown`. An unknown cost is stored as 0 but is **counted and flagged** by the report, which then calls the total a lower bound. It is never presented as free.
4. **Prices are data, not code.** A `PriceTable` (schema `price-table`) lists dollars per million tokens per model, with the date the rates were copied and where from. The repository ships only an illustrative example that says it is not real. Cached tokens without a cached rate are billed at the input rate, which overstates and so errs on the safe side.
5. **Nothing about content is stored.** A row holds counts, the model name, an outcome and a cost source. No prompt, answer, or tool text.
6. **`agentctl tokens record` and `agentctl tokens report`** write a row and summarise by model, task, agent, stage or day. The report verifies the ledger first and refuses a broken one.

## Consequences

- Cost per task, per model and per day is available from the ledger today for any caller that records rows. P2.8b can add token and cost budgets on top without a new store.
- **Nothing records rows automatically yet.** The Claude Code hook never sees token counts. Capturing them is task P2.2a: Claude Code's session transcript (its path is in every hook input) carries per-message `usage` with input, output, cache-read and cache-creation token counts, observed in a local transcript. That format is not documented by Anthropic, so it must be verified against the documentation and a version check before relying on it.
- **Cache-creation tokens have no field.** Anthropic bills cache writes at a different rate. Until `Usage` grows a field for them, an adapter should either report `cost_usd` from the provider or fold them into `tokens_in`, which understates their price.
- Reasoning or thinking tokens, batch discounts, and per-region prices are not modelled. Pass `cost_usd` when the provider reports it.
- A price table that is out of date gives a wrong cost with `computed` on it. The table carries `as_of` so a reader can tell; nothing warns yet when it is old.
- OpenTelemetry attribute names for these rows are decided with P2.3, which waits for the owner's observability choice (P0.3).

## Alternatives considered

- **A separate SQLite table.** Better for ad-hoc queries, but it is a second source of truth without the hash chain. SQLite stays reserved for indexes built from the ledger (ADR 0001).
- **Hard-coded prices.** Stale the day a provider changes them and silently wrong afterwards. Rejected in favour of a dated table the owner supplies.
- **Treat unknown cost as zero.** Simple, and wrong in the dangerous direction: an unpriced expensive model would look free.
