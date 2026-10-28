# TODO: phases and tasks

Derived from the [framework charter](framework-charter.md) (section 10 roadmap, section 12 open decisions). Check boxes as work completes. Task IDs are stable references (for example `P2.4`). Owners are roles, to be replaced with names in phase 0.

Rules for working through this list:
- Do not start phase N+1 work that depends on a phase N exit check before that check passes.
- Do not start the council, the learned router or the 3D world before the ledger and gates exist.
- Keep human gates strict at first. Loosen only from ledger data.
- Each phase ends with a demo, not a feature list.

Roles: **FO** framework owner, **PS** policy and security engineer, **ME** memory and evals engineer, **PL** platform engineer, **CO** compliance owner, **TL** pilot team lead.

---

## Phase 0: Decisions and setup

Goal: agree what we are building, who owns it, and where it is piloted.

- [ ] **P0.1** Name the framework owner, policy owner and security reviewer (FO)
- [ ] **P0.2** Review and approve the charter as v1.0 (FO, PS, CO)
- [ ] **P0.3** Lock the core decisions. _Decided in ADR 0001: Python, uv, SQLite plus JSONL, the `src/jarvis` layout; Claude Code is the first harness._ Still open: the vault location (default: per repo) and the observability convention (OTel GenAI or OpenInference) (FO, PL)
- [ ] **P0.4** Choose the pilot repos: one brownfield and one small greenfield, with their stacks, ticketing system and CI (FO, TL)
- [ ] **P0.5** Write the data policy: which models and endpoints may see which repos, retention terms (PS, CO)
- [ ] **P0.6** Scope compliance with the compliance owner: EU AI Act applicability, log retention (at least six months where it applies), confirm the Digital Omnibus dates on EUR-Lex (CO)
- [ ] **P0.7** Close the charter's unverified items: read the full OWASP agentic document, confirm OTel opt-in and `execute_tool` details, check Tessl, MetaGPT, CrewAI, AutoGen, LangGraph, the OpenAI Agents SDK and ISO/IEC 42001 (PS, ME)
- [ ] **P0.8** Capture baselines for both pilots: cycle time, defect rate, build time, cost, review time (TL)
- [ ] **P0.9** Run the DORA readiness checklist for each pilot team (clear AI stance, version control, small batches, internal data access, platform quality) (TL)
- [x] **P0.10** Repository hygiene: `main` is the default branch and protected by a ruleset (pull request required, `ci-ok` required so matrix changes never need ruleset edits, no force pushes or deletions); the repo is public; `.gitignore` (including `.obsidian/`), the Apache-2.0 licence, `NOTICE`, `CODEOWNERS`, `CONTRIBUTING.md` and `SECURITY.md` exist; secret scanning with push protection, private vulnerability reporting and approval for first-time contributors' workflow runs are switched on in GitHub settings (FO, PL)
- [ ] **P0.10a** Confirm who owns the copyright. _Done: the project owner reports it was built on personal time and equipment, and `NOTICE` names them as the copyright holder._ Still to do: read the employment contract's IP and outside-work clauses once and, if anything could relate to the employer's business, get written confirmation from the employer (FO)
- [ ] **P0.11** Decide whether OpenHands should be evaluated as a runtime or design reference before building our own orchestrator (FO, PL)
- [x] **P0.12** Start the framework decisions log: ADR 0001 (language, storage, layout) and ADR 0002 (policy rules) exist, with a template in `docs/adr/0000-template.md` (FO)
- [ ] **P0.13** Choose the final project name and check it: "Jarvis" is a common name that may clash with existing products or trademarks, the Python package name `jarvis-agentic` must be available if it will be published, and BMAD's trademarks must not be used (FO)
- [ ] **P0.14** Resolve the charter's remaining open decisions: where the event bus and visual layer are hosted (local or shared), which content pack ships first and whether importers are needed on day one, whether the pilot has a 2D timeline only, which channel delivers decision cards (email, chat or GitHub comments), and the stated reason for any second harness or model provider (FO, PL)
- [ ] **P0.15** Decide how identity and signing work, which the identity tasks need: how agents and humans authenticate, where signing keys live, and whether to use GitHub identity, per-agent local keys or an external identity provider (FO, PS)

Exit check: owners named, decisions and pilots recorded, baselines captured.

---

## Phase 1: Schemas and repository skeleton

Goal: stable contracts before any component is built.

- [x] **P1.1** Create the skeleton: `src/jarvis/` (core and adapters stubs), `packs/`, `viz/`, `docs/`; layout decided in ADR 0001 (PL)
- [x] **P1.2** Define schemas with validation: event, task, decision card, policy rule, tool registry entry, wiki frontmatter, trust label, `project.yaml`, `tiers.yaml` (PL, PS)
- [x] **P1.3** Schema tests with fixtures, including invalid cases; example files are validated by the tests (PL)
- [x] **P1.4** Schema versioning policy and changelog: [docs/schema-changelog.md](schema-changelog.md); tests fail if a schema is missing from it or the version table is wrong (PL)
- [x] **P1.5** CI on every change: lint, format check, tests and the schema drift test on Linux and Windows; a `secret-scan` job (gitleaks over the full history, version and SHA-256 pinned); every action pinned to a commit SHA; Dependabot for actions and Python packages; static tests guard all of this (`tests/test_ci_workflow.py`). _The secret-scan job has passed in CI since its first run._ (PL)
- [ ] **P1.5a** Dependency allowlist and existence check, as a defence against invented or typo-squatted packages. Overlaps P4.11 (PS)
- [x] **P1.5b** Verify the first real runs of the P1.5 work. _Done: the `ci` workflow, which includes the `secret-scan` job, succeeded on its runs for pull requests 6, 7 and 8; both Dependabot update runs (`uv` and `github_actions`) succeeded, so both entries are accepted; GitHub secret scanning with push protection and private vulnerability reporting are on. No Dependabot pull request has been opened yet; when one appears it must pass CI like any other._ (FO, PS)
- [ ] **P1.6** ADR template and the first ADRs (storage, schema versioning, trace conventions). _Template and ADR 0001 done; schema versioning and trace conventions ADRs still to do._ (FO)
- [x] **P1.7** Map the OWASP agentic risks (ASI01 to ASI10) to controls and red-team cases as a threat-model document: [docs/threat-model.md](threat-model.md), with 20 red-team cases and a test that checks every cited test exists (PS)

- [ ] **P1.8** Static analysis (SAST) of the framework's own code in CI, for example CodeQL or Bandit, with findings triaged (PS)

Exit check: schemas validate, CI is green, threat model reviewed.

---

## Phase 2: The spine (ledger, policy gate, approvals)

Goal: every action is logged, risky actions are stopped, humans can approve with attribution.

- [x] **P2.1** Hash-chained JSONL event log with `agentctl log`, `verify` and `head`; single-writer lock file; anchors for truncation and rewrite detection (PL)
- [ ] **P2.1a** Record ledger anchors outside the ledger (for example commit `agentctl head` output to Git at milestones or on a schedule) and verify against them in CI (PL, PS)
- [ ] **P2.1b** Ledger retention and archival: rotate or archive without breaking the chain, keep records for at least six months where regulation applies, and control who can read or write them (PL, PS, CO)
- [ ] **P2.2** Token and cost ledger: one row per call with model, tokens in and out, cached, cost, outcome (PL)
- [ ] **P2.3** OpenTelemetry GenAI export: pinned convention version, shared trace ID with the audit log, content capture opt-in and by reference (PL)
- [x] **P2.4** Policy engine and `agentctl check` returning allow, council, human or deny with a reason; risk comes from the registry, never from the action; fail closed; decisions logged to the ledger; thresholds aggregated per task. Rules and exit codes are in ADR 0002 (PS)
- [ ] **P2.4a** Follow-ups: per-day aggregation, an index so checks do not scan the whole ledger, and path-derived risk (PS, PL)
- [ ] **P2.4b** Policy integrity (threat model gap G2): record a SHA-256 of the policy, registry and project files in every decision event, and have the hook load them from a location the agent cannot write. _Hashes are recorded by the hook; loading from a protected location is still to do._ (PS)
- [x] **P2.5** Trust labels, taint tracking and the lethal-trifecta check in the gate. _The gate takes session state as input; the Claude Code hook (P2.6) must supply it truthfully._ (PS)
- [x] **P2.6a** Before building the hook, check Claude Code's current hook interface against its documentation (events, payload fields, whether a hook can block a call) and record what it can and cannot do in an ADR: [ADR 0003](adr/0003-claude-code-hook-interface.md). Key findings: a hook `deny` holds in every permission mode, but a crashed or timed-out hook lets the action through unless `onFailure: block` is set, which needs Claude Code 2.1.295 and this machine has 2.1.276 (PL)
- [x] **P2.6** _Built: `agentctl hook run|snippet|doctor`, see [docs/claude-code-hook.md](claude-code-hook.md). Tested with realistic payloads, not yet in a live session (P2.6c)._ Claude Code hooks that emit events and call `agentctl check` before risky tools, in **log-only mode** first. The hook builds the `Action` from the real tool call and tracks what the session has seen (untrusted content, private data). Design decisions are in [ADR 0003](adr/0003-claude-code-hook-interface.md), including a `ConfigChange` hook that protects the hook configuration (PL)
- [x] **P2.6b** End-to-end test that a lie in the agent's own description of an action cannot change the decision, and that a missing exposure report is caught (threat model T-3, RT-03). _Done at the handler level with realistic payloads (`tests/test_claude_code_hook.py`); the live-session confirmation is part of P2.6c._ (PS)
- [ ] **P2.6c** Run the open-question experiments listed in ADR 0003 on the installed Claude Code and update the ADR: exit-code behaviour on this version, `ask` in a `-p` session, the `MultiEdit`, `NotebookEdit` and MCP payloads, whether `ConfigChange` blocks the agent's own edits to hook settings, whether `deny` holds under `bypassPermissions`, hook start-up latency, and the Windows shell (threat model T-11, RT-18 to RT-20). _Answered in round 1 of the dogfood log: hooks load mid-session, `ConfigChange` fires on the agent's own settings edit, latency is about 1 s per call. Still open: exit-code behaviour on this version, `ask` in `-p` and in this app, `bypassPermissions`, `MultiEdit`, `NotebookEdit` and MCP payloads, the Windows shell form, and a crashing hook._ (PL, PS)
- [x] **P2.6d** Dogfood the hook on this repository in log-only mode. _Round 1 done: installed, 55 decisions from one session reviewed, four false-positive causes fixed (leading `cd`, trailing output filters, trusted app tools, internal bookkeeping tools). See [docs/dogfood-log.md](dogfood-log.md). Further rounds belong to P2.10._ (PS, PL)
- [ ] **P2.6e** Reduce the hook's cost, about one second per call today, mostly import time (round 1 measurements): lazy or lighter imports, and a local service the hook talks to (an `http` hook, linked to P4.1a). Target well under 200 ms per call, so all events can be registered and enforcement is practical (PL)
- [ ] **P2.10a** Decide how strict the taint rule should be, using the dogfood numbers: after one web fetch 62% of later calls were flagged. Options are keep it, apply it only to riskier tools, or clear taint on human approval. Also decide the shell prefixes to allow (PS, FO)
- [ ] **P2.7** Approval flow: decision cards, park and resume, attributed approvals, four-eyes for no-go zones, timeouts that never auto-approve, and human override and stop controls (PL, PS)
- [ ] **P2.7a** Precedents: record every human decision, and when the same decision type is approved repeatedly (start with three) propose a policy change through a normal policy pull request. Agents never edit policy (PS, ME)
- [ ] **P2.7b** Notification channels for decision cards: a twice-daily digest for non-urgent items, push for urgent ones, and an "ask me later" action, all through the same gate API (PL)
- [ ] **P2.7c** Card evidence comes from the gate's verdict (matched rules, taint and trifecta flags) and the real diff, not from text the agent wrote (ASI09, RT-13) (PS)
- [ ] **P2.8** Budgets per task, stage and day; loop breaker; kill switch (PL)
- [ ] **P2.8a** Anomaly rules on the ledger: repeated denied actions, a tool never used before, unusual data volumes, calls to new domains. Alert or pause the task (ASI10, RT-14) (PS)
- [ ] **P2.9** Agent identities and "on behalf of" fields; credential broker stub with short-lived, task-scoped credentials issued only for an approved task. Needs the P0.15 decision (PS)
- [ ] **P2.9a** Authenticate `role` and `agent_id` so a caller cannot claim another role (threat model gap G1, RT-08). Must be done before the gate is switched to enforcing (PS)
- [ ] **P2.9b** Sign ledger events so a forged append is detectable (threat model gap G4, RT-12) (PS)
- [ ] **P2.10** Review the log-only data (how often would the gate have blocked, how many overrides), tune thresholds, then switch to enforcing. Enforcing mode needs Claude Code 2.1.295 or later for `onFailure: block`, and P2.9a must be done first (PS, FO)
- [ ] **P2.11** Start the small red-team suite and the eval skeleton (formalised in phase 6) (PS, ME)

- [ ] **P2.12** Redact secrets and personal data before anything is written to the ledger or a decision card; scan tool input and output (threat model T-5) (PS, PL)

Exit check: a risky action is blocked and approved by a named human, the hash chain verifies, and a budget breach parks a task.

---

## Phase 3: Memory

Goal: knowledge compiled once, retrieved cheaply, validated before it is trusted.

- [ ] **P3.1** Vault template: folders, `AGENTS.md` schema, `index.md`, `log.md`, note templates (ME)
- [ ] **P3.1a** Project-context block in `AGENTS.md`: setup, adopt, refresh and audit operations, each line stamped with the commit it was verified against, kept small. _A hand-written first version exists (`AGENTS.md` and `docs/HANDOFF.md`, checked by tests); the tooling that creates and refreshes it is still to do._ (ME)
- [ ] **P3.2** Inbox write gate, promotion command, trust levels, supersede links (ME, PS)
- [ ] **P3.3** Ingest job: a merged PR or retrospective updates affected pages and appends to `log.md` (ME)
- [ ] **P3.3a** Query operation: read `index.md` and the relevant pages; good answers are filed back as new inbox pages so knowledge compounds (ME)
- [ ] **P3.4** Lint job: stale, contradictory, orphan and unvalidated items (ME)
- [ ] **P3.5** Context assembler: hard token cap, tiered loading, tool-result clearing, recall-first compaction (ME)
- [ ] **P3.5a** Record the retrieved context ids and a memory snapshot reference in decision events, so any decision can be explained and replayed (ME, PL)
- [ ] **P3.6** `record` command: turn a mistake into a CI check, hook, policy rule, playbook or wiki note, in that order of preference (ME, PS)
- [ ] **P3.7** Graph index in SQLite built from wikilinks and frontmatter; 1 to 2 hop retrieval with a cap (ME)
- [ ] **P3.8** Memory tools exposed through the framework's MCP server: `wiki_search`, `wiki_read`, `wiki_propose` (PL)
- [ ] **P3.9** Memory metrics: context size per call, retrieval precision, repeat-mistake rate (ME)
- [ ] **P3.10** Optional: document opening the vault in Obsidian, plugin vetting and licence check (ME)

- [ ] **P3.11** Reflection job (cheap model, batched): after a failure or a human override, write a short post-mortem and propose a lesson. Reward signals are objective: CI and test results, human decisions, rework, incidents, reverts and cost. Lessons are validated before they are trusted (ME)

Exit check: a repeated mistake is captured once, validated by a human, and not repeated; context tokens per task drop against the baseline.

---

## Phase 4: Pipeline end to end

Goal: a small real ticket goes from intake to a reviewed PR with humans only at gates.

- [ ] **P4.1** Task state machine, with "nothing is done without a human" enforced in code; records the baseline revision before any change (PL)
- [ ] **P4.1a** Orchestrator service (daemon) with an event stream and the gate API, so the CLI, web and visual clients attach to one source of truth and agents keep running when a client closes (PL)
- [ ] **P4.2** One git worktree per task; sandbox permissions; edit, test and publish as separate permission levels (PL, PS)
- [ ] **P4.3** Role charters: analyst, builder, reviewer, QA, read-only ops; each with a model tier and a tool allowlist (FO, PL)
- [ ] **P4.4** Tool registry and gateway with 5 to 8 tools (Git, CI, test runner, file search, ticket read, memory tools) and risk classes (PL, PS)
- [ ] **P4.4a** Keep tool token costs low: expose only the role's tools, load schemas on demand when there are many, truncate and paginate results, cache idempotent reads, prefer scripts for bulk work (PL)
- [ ] **P4.4b** Plan-then-execute: the gateway rejects actions outside the approved plan (PS)
- [ ] **P4.5** MCP vetting process: allowlist, pinned versions and checksums, sandboxing, audience validation, no token passthrough, hashed tool definitions (PS)
- [ ] **P4.5a** Quarantine a tool or MCP server whose schema or scope changed after approval, until its owner re-approves (PS)
- [ ] **P4.6** The framework's own MCP server: `policy_check`, `request_approval`, `task_state`, `budget_status`, `wiki_*` (PL)
- [ ] **P4.7** Model gateway: `tiers.yaml`, fallbacks, different models for author and reviewer (PL)
- [ ] **P4.7a** Cost levers: prompt caching with byte-stable prefixes, batch APIs for non-urgent work, reasoning-token caps and prompt trimming, each measured separately; track the cache-hit rate (PL, ME)
- [ ] **P4.7b** Enforce `data_policy.allowed_models` in the gateway, so a repo's data only reaches approved endpoints (PS, PL)
- [ ] **P4.8** Spec template with testable acceptance criteria and non-goals; spec review gate (FO, TL)
- [ ] **P4.8a** Scale-adaptive intake: a small change skips the full spec path, a larger one gets the full path, chosen by rules and not by the agent (FO, PL)
- [ ] **P4.8b** Per-feature threat modelling step in design: a short checklist against the threat model and the OWASP list, with the result stored in the vault (PS)
- [ ] **P4.9** Verify stage: tests, SAST, secret and dependency scans, mutation testing on changed code, human-owned golden tests (PL)
- [ ] **P4.10** Review stage: AI reviewer with severity tuning; track the acted-upon rate (PL)
- [ ] **P4.11** Supply chain: dependency existence and allowlist checks, install scripts off in sandboxes, AI commit trailers, SBOM, agent and model inventory (PS)
- [ ] **P4.11a** SLSA-style provenance that links an agent session to the signed release it produced (PS, PL)
- [ ] **P4.12** Injection basics: egress allowlist, quarantined reader agent, output scanning, untrusted content to the inbox only (PS)
- [ ] **P4.12a** Canary tokens planted in sensitive data, to detect exfiltration (PS)
- [ ] **P4.13** Progressive delivery hooks: feature flags, staged exposure, rollback; production deploy stays human-gated (PL)
- [ ] **P4.14** End-to-end demo on a real ticket, with the audit log replaying every decision (all)

- [ ] **P4.15** Unattended runs: the orchestrator launches harnesses in non-interactive mode, never with confirmations disabled, and enforces limits outside the harness as well (sandbox permissions, scoped credentials, branch protection, CI) (PL, PS)

Exit check: demo ticket reaches a reviewed PR with two human gates, and the audit trail explains each step.

---

## Phase 5: Brownfield

Goal: onboard a real existing repository safely.

- [ ] **P5.0** Triage first: decide whether a system is worth modernising before investing in it, retire systems with high debt and little value, and list what an agent cannot recover (undocumented business rules, architecture decisions) so a human owns it (TL, FO)
- [ ] **P5.1** Deterministic code graph from imports, symbols, build files and dependencies (PL)
- [ ] **P5.2** Profiler: git hotspots, churn, ownership, coverage, CI health, dependency age (PL)
- [ ] **P5.3** Cheap-model module summaries into the inbox as unverified (ME)
- [ ] **P5.4** Human confirmation of the 10 to 20 most critical modules (TL)
- [ ] **P5.5** No-go zones in policy for fragile, undocumented or regulated areas (PS, TL)
- [ ] **P5.6** Characterization test generator, plus a "break it on purpose" check; golden tests human-owned (PL)
- [ ] **P5.7** Archive old greenfield planning docs out of agent reach; project-context block in `AGENTS.md` (ME)
- [ ] **P5.8** Brownfield pack: `init brownfield`, read-only tool set at start (PL)
- [ ] **P5.9** Strangler slice template: one intent per slice, parity tests, staged traffic shifts (FO, PL)
- [ ] **P5.10** Baseline metrics report, then pilot on the chosen repo and a retrospective (TL)

Exit check: one real repo onboarded, a characterized module changed through a slice, and metrics compared with the baseline.

---

## Phase 6: Evals and red team (formalise)

Goal: prompts, policies and agents are treated as code and regression-tested.

- [ ] **P6.1** Clean-room eval harness: temporary workspace, separate grader, 3 or more runs per case (ME)
- [ ] **P6.2** Regression sets per role; human-labelled judge calibration set, rechecked when the judge or rubric changes (ME)
- [ ] **P6.3** CI gates on success, latency and cost for every prompt, model or tool change (PL)
- [ ] **P6.4** Injection suite of 20 to 30 payloads across tickets, READMEs, web pages and tool output; attack success rate tracked; blast radius per role (PS)
- [ ] **P6.5** Production failure becomes a regression test, plus an online check for recurrence (ME)
- [ ] **P6.6** Per-model prompt overrides and per-model evals (ME)
- [ ] **P6.7** Retire any skill or agent that no longer beats the bare-model baseline (ME)
- [ ] **P6.8** Incident playbook and kill-switch drill (PS)
- [ ] **P6.8a** Operator runbook for a stuck lock or a corrupt ledger that restores service without skipping the audit (threat model T-4) (PS, PL)
- [ ] **P6.9** Metrics report: lead time, change-failure rate, time to restore, cycle time, cost per merged PR, rework rate, acted-upon rate of review comments, escalation and override rates, repeat-mistake rate, attack success rate and human review time, compared with the baselines from P0.8 (ME, PL)
- [ ] **P6.10** Approval-fatigue monitoring: decision-card volume, time to decision and override rate, with an alert when thresholds are crossed (PS)

Exit check: evals gate every change, attack success rate is tracked, and a kill-switch drill is completed.

---

## Phase 7: Council, router, second harness, visuals, packaging

Goal: widen the framework once the data shows it is needed.

- [ ] **P7.1** Council for two or three high-risk gates (role pattern first), vote schema, quorum, one round; evaluate it against a single reviewer and retire it where it does not help (FO, ME)
- [ ] **P7.1a** Model council (independent answers, anonymised peer ranking, chair synthesis) and debate patterns, once the role council has proved useful (ME)
- [ ] **P7.2** Learned router from ledger data (bandit-style), with routing reasons logged (ME)
- [ ] **P7.3** Second harness adapter with a conformance test (loads context, hits the gate, emits events) (PL)
- [ ] **P7.4** Second model provider behind the gateway (PL)
- [ ] **P7.5** 2D timeline and list view of events, with the same gate API for approvals (PL)
- [ ] **P7.6** 3D world, if the pilot shows value: scene mapping, human gate as a door, token meters (PL)
- [ ] **P7.7** Durable execution decision: own store, LangGraph-style checkpoints or Temporal; test crash recovery and approval resume separately (PL)
- [ ] **P7.8** Greenfield pack and stack packs (`init greenfield`) (PL)
- [ ] **P7.9** Importers for BMAD, Spec Kit and OpenSpec artifacts; `AGENTS.md` exporter (PL)
- [ ] **P7.10** Ops assistant, read-only first, with machine-readable runbooks (PL)
- [ ] **P7.11** Compliance mapping document: controls to EU AI Act, NIST AI RMF and ISO/IEC 42001 (CO, PS)
- [ ] **P7.12** Packaging, versioning, changelog and user documentation (FO, PL)
- [ ] **P7.12a** Signed releases and an SBOM for the framework itself, with a documented release process (FO, PS)
- [ ] **P7.13** Typed agent-to-agent message schemas and scoped shared memory, only if a multi-agent design is justified by evidence (charter 8.8) (PL)
- [ ] **P7.14** Generate per-harness instruction files (for example a `CLAUDE.md` that points at the canonical `AGENTS.md`) instead of maintaining several copies (PL)

Exit check: pilot metrics reviewed against baselines; keep or retire each optional component on the evidence.

---

## Continuous track (every phase)

- [ ] Security review of each new tool, MCP server and policy change
- [ ] Access review: which roles use which tools; remove unused permissions
- [ ] Dependency and model inventory updates
- [ ] Eval and red-team refresh with new payloads and harder cases
- [ ] Memory pruning and lint reports reviewed
- [ ] Metrics review: lead time, change-failure rate, time to restore, cost per merged PR, rework, acted-upon rate, override rate, repeat-mistake rate, attack success rate
- [ ] Keep the charter, ADRs, the [traceability matrix](traceability.md) and this list in sync
- [ ] Review the threat model when the hook ships (P2.6), when enforcing mode is enabled (P2.10) and before the first pilot; add a red-team case for every incident and near miss
- [ ] Re-verify a charter claim against its primary source whenever it becomes a hard requirement (charter section 16)

## Suggested first two weeks

1. P0.1, P0.3, P0.4, P0.10 (owners, decisions, pilots, repo hygiene)
2. P1.1, P1.2, P1.5 (skeleton, schemas, CI)
3. P2.1 and P2.4 (event log and the policy gate), then P2.6 in log-only mode
4. P0.8 baselines in parallel
