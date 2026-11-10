# Handoff

Last updated: 2026-11-02

The current state of the work, written for whoever picks it up next: another person, another account, or another tool such as Devin. It is short on purpose and describes facts that are not already obvious from the code. The rules for working here are in [AGENTS.md](../AGENTS.md); the full plan is in [TODO.md](TODO.md).

**Update this file at the end of every working session**, and change the date above. A test checks that the task ids and files it mentions exist, so it cannot quietly point at things that are gone.

## Where things stand

The project is an early build. Pull requests 1 to 26 are merged to `main`, and no pull request is open at the time of writing apart from the one carrying this file.

**Built, tested and merged**

- **Schemas** for events, tasks, decision cards, policy, tool registry, wiki notes, project config, actions and verdicts, with a versioning policy and changelog ([schema-changelog.md](schema-changelog.md)).
- **Audit ledger**: a hash-chained JSONL file, with `agentctl log`, `verify` and `head`, anchors, and signed checkpoints (`agentctl checkpoint --key`, `verify --checkpoints`) kept outside the ledger to detect truncation and rewrites ([ADR 0010](adr/0010-identity-and-signing.md), `P2.9b`).
- **Policy gate** (`agentctl check`): allow, council, human or deny, with built-in guardrails, taint tracking, the lethal-trifecta rule and cumulative limits that hold under concurrency. It fails closed ([ADR 0002](adr/0002-policy-evaluation-and-fail-closed-rules.md)).
- **Claude Code hook** (`agentctl hook run`, `snippet`, `doctor`, `report`): builds the gate's action from the real tool call. Log-only by default ([ADR 0003](adr/0003-claude-code-hook-interface.md), [the guide](claude-code-hook.md)). `agentctl halt` and `resume` are a kill switch, and the policy can carry call budgets, a loop breaker and anomaly rules ([ADR 0005](adr/0005-budgets-loop-breaker-and-kill-switch.md), [ADR 0006](adr/0006-anomaly-rules.md), `P2.8`, `P2.8a`). `agentctl approvals list|show|approve|reject` answer approval requests the gate raises ([ADR 0009](adr/0009-approval-flow.md), `P2.7`), and with `approvals.require_signatures` an approval is an SSH signature checked against an allowed-signers file ([ADR 0010](adr/0010-identity-and-signing.md), `P2.9a`). `agentctl otel export` writes the ledger as OpenTelemetry GenAI traces without any content (`--endpoint` also sends it to an OTLP/HTTP collector, never through a proxy from the environment), and the hook gives every event of a session one trace id and each tool call one span id shared by its decision and outcome (`P2.3`, `P2.3a`, `P2.3b`). `agentctl tokens record` and `report` keep a token and cost ledger ([ADR 0007](adr/0007-token-and-cost-ledger.md), `P2.2`); the Claude Code hook fills it from the session transcript on `Stop`, `SubagentStop` and `SessionEnd` ([ADR 0008](adr/0008-token-capture-from-transcripts.md), `P2.2a`). `agentctl ledger rotate` moves the active ledger into an archive and continues the same hash chain, so a long ledger can be split without breaking `verify` or signed checkpoints ([ADR 0014](adr/0014-ledger-segments.md), `P2.1b`); the hook rotates at 1,000,000 bytes and the gate then reads only the active file plus a summary carried in the link record, so a decision costs about the same at 40,000 records as at 100 (`P2.4a`). The ledger redacts secrets and personal data in an event's `data` as it writes, and the hook asks a person when a secret appears in the input of a tool that writes, runs commands or sends data ([ADR 0013](adr/0013-redaction-and-secret-scanning.md), `P2.12`). `agentctl hook run --server` asks a local hook server for the decision instead of starting a full process, and decides in-process if it cannot ([ADR 0012](adr/0012-hook-server.md), `P2.6f`); the hook is opt-in until your settings use `--server`. It can read policy from a git commit or an outside directory, and enforce mode refuses the agent-writable working tree ([ADR 0004](adr/0004-trusted-policy-source.md), `P2.4b`).
- **CI**: Linux and Windows, Python 3.12 and 3.13, lint, format, about 700 tests, a secret scan over the full history, actions pinned to commit SHAs, and a single required check, `ci-ok`.
- **Docs**: the [charter](framework-charter.md), [threat model](threat-model.md), [traceability matrix](traceability.md), [dogfood log](dogfood-log.md), ADRs 0001 to 0015.

**Not built yet:** park and resume of tasks and the diff in an approval card (`P2.7d`, `P2.7e`), authenticated roles for direct callers and signed task credentials (`P2.9c`) and signed routine ledger events, anomaly rules for domains and data volumes (`P2.8c`), per-stage and token or cost budgets (`P2.8b`), a check of the OpenTelemetry export against a real collector (`P2.3c`), memory (the wiki), the orchestrator and pipeline, brownfield tooling, evals, the council and the visual layer. See [TODO.md](TODO.md).

## Live on the maintainer's machine

- The hook is switched on for this repository through `.claude/settings.local.json` (git-ignored). It runs in **log-only mode**: it records what the gate would decide and never blocks. It records to `.agents/ledger/events.jsonl` (git-ignored); per-session state is kept outside the repository.
- First dogfood round: 55 decisions in one session. 76% would have been flagged before four false-positive causes were fixed; after one web fetch, 62% of later calls were flagged by the taint rule. The numbers and the decisions still open are in the [dogfood log](dogfood-log.md).
- To stop it: delete `.claude/settings.local.json`. To see what it recorded: `uv run agentctl hook report`.

## Waiting for a decision (do not decide these without the owner)

| Task | Decision | Why it matters |
|---|---|---|
| `P0.1` | Name the framework, policy and security owners | The RACI and CODEOWNERS use one placeholder person |
| `P0.4` | Choose the pilot repositories (one brownfield, one greenfield) | Blocks phases 4 and 5 |
| `P0.5` | Data policy: which models may see which repositories | Needed before real data flows through the framework |
| `P0.6` | Compliance scope, including the EU AI Act dates on EUR-Lex | Retention and oversight requirements |
| `P0.10a` | Check the employment contract's IP clause | The repository is public under Apache-2.0 |
| `P0.13` | Final project name and trademark check | Renaming gets harder once the hook is installed in other projects |
| `P0.16` | Create each approver's SSH signing key (a hardware security key for anyone approving no-go zones) and add the public keys by pull request | One software key is listed (2026-11-05); it proves a name, not a person, because an agent running as that user could use it. A hardware key and other approvers are still missing, and `P2.9d` cannot test the touch requirement without a real one |
| `P2.10b` | After a few days of recorded command prefixes: which shell prefixes may be allowed, and what to do about writes outside the project (`P2.10a` is decided: taint rule kept, edit cap 2,000, safe chains recognised, prefixes recorded; [ADR 0015](adr/0015-prompt-rate-decisions.md)) | 59% of decisions are shell commands and the intervention rate is too high to enforce today |
| `P2.10` | Switch the hook to enforce mode | Needs `P2.9a`, Claude Code 2.1.295 or later, and a low intervention rate first |

## Recommended next steps

In this order, and why:

1. **`P0.16` then `P2.9d`**: create the approvers' signing keys (ideally one hardware security key) so the touch requirement can be tested for real. Signed approvals are built (`P2.9a`, [ADR 0010](adr/0010-identity-and-signing.md)) but the hardware path has never run against a real key. Then **`P2.9c`** signed task credentials with the orchestrator, (milestone checkpoints, the stale warning and the CI check on committed checkpoints are built, `P2.9e` and `P2.9f`; signing stays manual). After those, enforce mode (`P2.10`) is a decision rather than a risk.
2. **`P2.7d`, `P2.7e`** park and resume (needs the orchestrator, `P4.1`), then the real change in a card and approval-fatigue metrics.
3. **`P2.8b`** token and cost budgets. Build it once you have chosen limits and supplied a real price table. **`P2.3`** and **`P2.3a`** (OpenTelemetry export, with real trace ids from the hook) and **`P2.3b`** (sending with `--endpoint`) are built ([ADR 0011](adr/0011-observability-and-vault.md)); what remains is **`P2.3c`**: run it against a real collector or viewer, which has not been done, plus authentication and a cursor.
4. **`P2.6g`** Claude Code `http` hooks for the tool events, which would remove the process start entirely (about 50 ms per call). It needs a live test on this Claude Code version first (the documentation does not say whether `localhost` is allowed), and Claude Code 2.1.295 or later for blocking on failure. The local server and tiny client are built (`P2.6f`) and roughly halve the per-call time today.

## Known gaps and risks

The five biggest, from the [threat model](threat-model.md): role and agent identity are claimed and not authenticated (G1); the policy files are agent-writable unless a git or directory source is used, and a shell can still move a git ref (G2, narrowed by P2.4b); ledger events are unsigned (G4); the kill switch and budgets only cover calls that go through the gate (G5, narrowed by P2.8); and the hook has only been tested with synthetic payloads and in log-only mode, never in enforce mode.

## Not verified

- Enforce mode on a real session (a call right after a server stop is covered, `P2.6h`, with real hook processes but not a live Claude Code session), how `ask` behaves in `-p` and in `default` mode (in the desktop app's `auto` mode it asks a person for a write and `deny` holds, observed once), a crashing hook, `bypassPermissions`, and the Windows Git Bash shell form (`P2.6c`).
- The full OWASP agentic document (only the ten risk names were checked), the EU Digital Omnibus dates on EUR-Lex, and the products in the charter's section 16 table marked unverified.

## Corrections already made (do not repeat these)

- OpenTelemetry's GenAI conventions are in **Development** status, not stable.
- The multi-agent failure study (MAST) reports roughly 44% system design, 32% inter-agent misalignment and 24% task verification; a 36.9% figure quoted elsewhere is not in the paper.
- "Least agency" is not a named OWASP principle in its announcement; it is our own principle.
- The claim that 18% of MCP deployments scope permissions could not be verified and is not used.
- A hook that returns `allow` skips Claude Code's own prompts, so ours never does. A hook that crashes lets the call through on Claude Code before 2.1.295.
- Piping `pytest` into `tail` without `set -o pipefail` once hid a failing test.
- A test that read the git-ignored `.claude/` folder passed locally and failed in CI. Check file-reading tests in a clean checkout (see [AGENTS.md](../AGENTS.md)).

## How to resume

```bash
uv sync
```

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

```bash
uv run agentctl hook report
```

Then pick the next task above, open a branch (see [AGENTS.md](../AGENTS.md)), and keep the TODO list, traceability matrix and this file in step with what you change.
