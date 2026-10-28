# Handoff

Last updated: 2026-10-28

The current state of the work, written for whoever picks it up next: another person, another account, or another tool such as Devin. It is short on purpose and describes facts that are not already obvious from the code. The rules for working here are in [AGENTS.md](../AGENTS.md); the full plan is in [TODO.md](TODO.md).

**Update this file at the end of every working session**, and change the date above. A test checks that the task ids and files it mentions exist, so it cannot quietly point at things that are gone.

## Where things stand

The project is an early build. Pull requests 1 to 14 are merged to `main`, and no pull request is open at the time of writing.

**Built, tested and merged**

- **Schemas** for events, tasks, decision cards, policy, tool registry, wiki notes, project config, actions and verdicts, with a versioning policy and changelog ([schema-changelog.md](schema-changelog.md)).
- **Audit ledger**: a hash-chained JSONL file, with `agentctl log`, `verify` and `head`, and anchors to detect truncation and rewrites.
- **Policy gate** (`agentctl check`): allow, council, human or deny, with built-in guardrails, taint tracking, the lethal-trifecta rule and cumulative limits that hold under concurrency. It fails closed ([ADR 0002](adr/0002-policy-evaluation-and-fail-closed-rules.md)).
- **Claude Code hook** (`agentctl hook run`, `snippet`, `doctor`, `report`): builds the gate's action from the real tool call. Log-only by default ([ADR 0003](adr/0003-claude-code-hook-interface.md), [the guide](claude-code-hook.md)). `agentctl halt` and `resume` are a kill switch, and the policy can carry call budgets and a loop breaker ([ADR 0005](adr/0005-budgets-loop-breaker-and-kill-switch.md), `P2.8`). It can read policy from a git commit or an outside directory, and enforce mode refuses the agent-writable working tree ([ADR 0004](adr/0004-trusted-policy-source.md), `P2.4b`).
- **CI**: Linux and Windows, Python 3.12 and 3.13, lint, format, about 390 tests, a secret scan over the full history, actions pinned to commit SHAs, and a single required check, `ci-ok`.
- **Docs**: the [charter](framework-charter.md), [threat model](threat-model.md), [traceability matrix](traceability.md), [dogfood log](dogfood-log.md), ADRs 0001 to 0005.

**Not built yet:** approvals and decision cards, identity and signed events, anomaly rules, per-stage and token budgets, the token ledger, OpenTelemetry export, memory (the wiki), the orchestrator and pipeline, brownfield tooling, evals, the council and the visual layer. See [TODO.md](TODO.md).

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
| `P2.10a` | How strict the taint rule is, and which shell prefixes are allowed | The intervention rate is too high to enforce today |
| `P2.10` | Switch the hook to enforce mode | Needs `P2.9a`, Claude Code 2.1.295 or later, and a low intervention rate first |

## Recommended next steps

In this order, and why:

1. **`P2.8a`** anomaly rules on the ledger (repeated denials with variations, a tool never used before, new domains). Budgets, the loop breaker and the kill switch are built (`P2.8`).
2. **`P2.2`** token and cost ledger. `P2.3` (OpenTelemetry) first needs the `P0.3` decision on the observability convention.
3. **`P2.9`, `P2.9a`, `P2.9b`** identity, authenticated roles and signed events (gaps G1 and G4). It needs a decision on how identity and signing work, which is tracked as `P0.15`.
4. **`P2.7`** the approval flow, then **`P2.10`**.
5. **`P2.6e`** reduce the hook's cost (about one second per call today) whenever convenient; it does not block the others.

## Known gaps and risks

The five biggest, from the [threat model](threat-model.md): role and agent identity are claimed and not authenticated (G1); the policy files are agent-writable unless a git or directory source is used, and a shell can still move a git ref (G2, narrowed by P2.4b); ledger events are unsigned (G4); the kill switch and budgets only cover calls that go through the gate (G5, narrowed by P2.8); and the hook has only been tested with synthetic payloads and in log-only mode, never in enforce mode.

## Not verified

- Enforce mode, how `ask` behaves in the desktop app, a crashing hook, `bypassPermissions`, and the Windows Git Bash shell form (`P2.6c`).
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
