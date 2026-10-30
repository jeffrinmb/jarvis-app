# ADR 0011: Observability convention and vault location

- Status: Accepted (decision; the export and the vault are not built yet)
- Date: 2026-10-29
- Deciders: project owner (task P0.3). Each choice below was made from options put to the owner on 2026-10-29.

## Context

Two parts of P0.3 were still open: which observability convention the OpenTelemetry export (P2.3) follows, and where the memory vault lives. The charter assumes OpenTelemetry GenAI traces sharing a trace id with the audit log (section 8.7) and a vault per repository (section 7.1, open decision 4), but neither had been decided.

**What was checked, and how.** The OpenTelemetry pages at opentelemetry.io are now "moved" stubs, so the conventions were read from [the new repository](https://github.com/open-telemetry/semantic-conventions-genai) and OpenInference from [its spec](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md). Both were read through a fetching agent: the OpenTelemetry spans and attribute pages were mostly verbatim, parts were model-extracted summaries, and the agent-spans page body could not be retrieved.

- *OpenTelemetry GenAI (primary):* status **Development**, not stable. No version number for the conventions themselves was found. No `OTEL_SEMCONV_STABILITY_OPT_IN` switch was found. Attributes include `gen_ai.operation.name` (`chat`, `invoke_agent`, `execute_tool`), `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens`, `gen_ai.conversation.id`, `gen_ai.agent.id` and `gen_ai.agent.name`. Content (`gen_ai.input.messages`, `gen_ai.output.messages`) is Opt-In with a warning about personal data, and `*_ref` attributes carry a URI to content in external storage. MCP tool-call spans are documented as compatible with `execute_tool`.
- *OpenInference (primary):* maintained by Arize, "complementary to OpenTelemetry", natively supported by Arize Phoenix and AX. Attributes include `llm.token_count.prompt`, `llm.token_count.completion`, `llm.token_count.prompt_details.cache_read` and `.cache_write`, `session.id`, `agent.name` and a required `openinference.span.kind`. No stability or versioning promise was found.
- **A mapping catch:** OpenTelemetry's `gen_ai.usage.input_tokens` "SHOULD include all types of input tokens, including cached tokens", whereas our ledger's `tokens_in` excludes cached tokens ([ADR 0007](0007-token-and-cost-ledger.md)). An exporter must add them back.

## Decision

1. **The convention is OpenTelemetry GenAI, pinned.** The exporter lives in one module with a constant naming the exact commit of the conventions it follows, chosen when P2.3 is built. Our own ledger schema remains the source of truth and the exporter is an adapter, so a change in the Development-status conventions touches one file.
2. **Mapping.** A `model_call` event becomes a `chat` span with `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens` (our `tokens_cached`) and `gen_ai.usage.cache_write.input_tokens` (from `data.cache_creation_tokens` when present). `gen_ai.usage.input_tokens` is our `tokens_in` **plus** `tokens_cached`. Ledger `trace_id` and `span_id` are the span ids, so traces and the audit log share one trace id. Tool calls map to `execute_tool`, and a session to `invoke_agent`, with `gen_ai.conversation.id` from the session id.
3. **Content is never exported.** Traces carry model names, token counts, cost, tool names, decisions, rule ids and hashes, and never prompt or answer text, not inline and not by reference. This matches the ledger, which never records content, and it holds until the data policy (P0.5) allows more. `*_ref` attributes may be revisited then.
4. **Transport: a local OTLP/JSON file first.** The export writes OTLP JSON that any OpenTelemetry tool can open, with no collector or backend to run. Sending to a collector is a later, configuration-level change. Whether to hand-write the small OTLP JSON structure or add the OpenTelemetry SDK as a dependency is decided when P2.3 is built, because the second changes `pyproject.toml` and `uv.lock`.
5. **The vault is per repository, in `.agents/vault/`.** The charter's default stands. Agents write only to the inbox folder, nothing is promoted without a human, and the vault is reviewed in pull requests and covered by CODEOWNERS. There is no global vault. Lessons that apply across projects are copied by hand for now.

## Consequences

- Any OTLP-capable tool can read the traces. An OpenInference mapper stays possible later as a second adapter; it is not built.
- Because the conventions are Development status, attribute names can change. The pinned commit makes a change visible and bounded, and a test can compare the mapping against it.
- A vault in the repository is agent-writable like the rest of the working tree. The inbox rule and the human promotion step (P3.2, P3.4) are what keep an agent from writing trusted knowledge; the policy source (ADR 0004) protects `.agents/` policy files, not the vault.
- Per-repo vaults mean a lesson learned in one project does not reach another until someone copies it. That is a deliberate cost while the data policy (P0.5) is undecided.
- Nothing is exported yet. `agentctl tokens report` and `agentctl hook report` remain the way to look at the ledger.

## Alternatives considered

- **OpenInference.** Best supported by Phoenix and AX today, but controlled by one vendor, with no stability promise found, and different names from OpenTelemetry's, so switching later would be a rewrite.
- **Both from the start.** More structure now for a backend that has not been chosen.
- **No export yet.** Nothing to keep up with a moving specification, but no traces.
- **One central vault.** One place to back up, but it mixes knowledge from repositories that may have different data rules and sits outside each repository's history and review.
- **A per-repo vault plus a global one.** Flexible, with two places to keep clean and a rule needed for what may go in the global one. Can be added later without changing the per-repo layout.
- **Content opt-in by reference or inline.** Useful for debugging, but prompts can hold secrets and personal data, and the data policy that says what may be sent where does not exist yet.
