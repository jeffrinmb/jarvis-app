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
- [ ] **P0.3** Lock the core decisions: language (default Python), first harness (Claude Code), storage (SQLite plus JSONL), vault location (per repo), observability convention (OTel GenAI or OpenInference) (FO, PL)
- [ ] **P0.4** Choose the pilot repos: one brownfield and one small greenfield, with their stacks, ticketing system and CI (FO, TL)
- [ ] **P0.5** Write the data policy: which models and endpoints may see which repos, retention terms (PS, CO)
- [ ] **P0.6** Scope compliance with the compliance owner: EU AI Act applicability, log retention (at least six months where it applies), confirm the Digital Omnibus dates on EUR-Lex (CO)
- [ ] **P0.7** Close the charter's unverified items: read the full OWASP agentic document, confirm OTel opt-in and `execute_tool` details, check Tessl, MetaGPT, CrewAI, AutoGen, LangGraph, the OpenAI Agents SDK and ISO/IEC 42001 (PS, ME)
- [ ] **P0.8** Capture baselines for both pilots: cycle time, defect rate, build time, cost, review time (TL)
- [ ] **P0.9** Run the DORA readiness checklist for each pilot team (clear AI stance, version control, small batches, internal data access, platform quality) (TL)
- [ ] **P0.10** Repository hygiene: make `main` the default branch (done on GitHub if completed), branch protection on `main`, CODEOWNERS, `.gitignore` (including `.obsidian/`), licence decision, contributing guide (FO, PL)
- [ ] **P0.11** Decide whether OpenHands should be evaluated as a runtime or design reference before building our own orchestrator (FO, PL)
- [ ] **P0.12** Start the framework decisions log (ADR 0001: language and storage) (FO)

Exit check: owners named, decisions and pilots recorded, baselines captured.

---

## Phase 1: Schemas and repository skeleton

Goal: stable contracts before any component is built.

- [x] **P1.1** Create the skeleton: `src/jarvis/` (core and adapters stubs), `packs/`, `viz/`, `docs/`; layout decided in ADR 0001 (PL)
- [x] **P1.2** Define schemas with validation: event, task, decision card, policy rule, tool registry entry, wiki frontmatter, trust label, `project.yaml`, `tiers.yaml` (PL, PS)
- [x] **P1.3** Schema tests with fixtures, including invalid cases; example files are validated by the tests (PL)
- [ ] **P1.4** Schema versioning policy and changelog (additive changes only; breaking changes bump the version). _Policy is written in `schemas/common.py`; the changelog is still to do._ (PL)
- [ ] **P1.5** CI on every change: lint, unit tests, schema checks, secret scan, dependency allowlist check. _Lint, format check, tests and the schema drift test run on Linux and Windows; secret scan, dependency allowlist check and action SHA pinning are still to do._ (PL)
- [ ] **P1.6** ADR template and the first ADRs (storage, schema versioning, trace conventions). _Template and ADR 0001 done; schema versioning and trace conventions ADRs still to do._ (FO)
- [ ] **P1.7** Map the OWASP agentic risks (ASI01 to ASI10) to controls and red-team cases as a threat-model document (PS)

Exit check: schemas validate, CI is green, threat model reviewed.

---

## Phase 2: The spine (ledger, policy gate, approvals)

Goal: every action is logged, risky actions are stopped, humans can approve with attribution.

- [x] **P2.1** Hash-chained JSONL event log with `agentctl log`, `verify` and `head`; single-writer lock file; anchors for truncation and rewrite detection (PL)
- [ ] **P2.1a** Record ledger anchors outside the ledger (for example commit `agentctl head` output to Git at milestones or on a schedule) and verify against them in CI (PL, PS)
- [ ] **P2.2** Token and cost ledger: one row per call with model, tokens in and out, cached, cost, outcome (PL)
- [ ] **P2.3** OpenTelemetry GenAI export: pinned convention version, shared trace ID with the audit log, content capture opt-in and by reference (PL)
- [ ] **P2.4** Policy engine and `agentctl check` returning allow, council, human or deny with a reason; risk computed from evidence; thresholds aggregated per task and per day (PS)
- [ ] **P2.5** Trust labels, taint tracking and the lethal-trifecta check in the gate (PS)
- [ ] **P2.6** Claude Code hooks that emit events and call `agentctl check` before risky tools, in **log-only mode** first (PL)
- [ ] **P2.7** Approval flow: decision cards, park and resume, attributed approvals, four-eyes for no-go zones, timeouts that never auto-approve (PL, PS)
- [ ] **P2.8** Budgets per task, stage and day; loop breaker; kill switch (PL)
- [ ] **P2.9** Agent identities and "on behalf of" fields; credential broker stub with short-lived, task-scoped credentials (PS)
- [ ] **P2.10** Review the log-only data (how often would the gate have blocked, how many overrides), tune thresholds, then switch to enforcing (PS, FO)
- [ ] **P2.11** Start the small red-team suite and the eval skeleton (formalised in phase 6) (PS, ME)

Exit check: a risky action is blocked and approved by a named human, the hash chain verifies, and a budget breach parks a task.

---

## Phase 3: Memory

Goal: knowledge compiled once, retrieved cheaply, validated before it is trusted.

- [ ] **P3.1** Vault template: folders, `AGENTS.md` schema, `index.md`, `log.md`, note templates (ME)
- [ ] **P3.2** Inbox write gate, promotion command, trust levels, supersede links (ME, PS)
- [ ] **P3.3** Ingest job: a merged PR or retrospective updates affected pages and appends to `log.md` (ME)
- [ ] **P3.4** Lint job: stale, contradictory, orphan and unvalidated items (ME)
- [ ] **P3.5** Context assembler: hard token cap, tiered loading, tool-result clearing, recall-first compaction (ME)
- [ ] **P3.6** `record` command: turn a mistake into a CI check, hook, policy rule, playbook or wiki note, in that order of preference (ME, PS)
- [ ] **P3.7** Graph index in SQLite built from wikilinks and frontmatter; 1 to 2 hop retrieval with a cap (ME)
- [ ] **P3.8** Memory tools exposed through the framework's MCP server: `wiki_search`, `wiki_read`, `wiki_propose` (PL)
- [ ] **P3.9** Memory metrics: context size per call, retrieval precision, repeat-mistake rate (ME)
- [ ] **P3.10** Optional: document opening the vault in Obsidian, plugin vetting and licence check (ME)

Exit check: a repeated mistake is captured once, validated by a human, and not repeated; context tokens per task drop against the baseline.

---

## Phase 4: Pipeline end to end

Goal: a small real ticket goes from intake to a reviewed PR with humans only at gates.

- [ ] **P4.1** Task state machine, with "nothing is done without a human" enforced in code (PL)
- [ ] **P4.2** One git worktree per task; sandbox permissions; edit, test and publish as separate permission levels (PL, PS)
- [ ] **P4.3** Role charters: analyst, builder, reviewer, QA, read-only ops; each with a model tier and a tool allowlist (FO, PL)
- [ ] **P4.4** Tool registry and gateway with 5 to 8 tools (Git, CI, test runner, file search, ticket read, memory tools) and risk classes (PL, PS)
- [ ] **P4.5** MCP vetting process: allowlist, pinned versions and checksums, sandboxing, audience validation, no token passthrough, hashed tool definitions (PS)
- [ ] **P4.6** The framework's own MCP server: `policy_check`, `request_approval`, `task_state`, `budget_status`, `wiki_*` (PL)
- [ ] **P4.7** Model gateway: `tiers.yaml`, fallbacks, different models for author and reviewer (PL)
- [ ] **P4.8** Spec template with testable acceptance criteria and non-goals; spec review gate (FO, TL)
- [ ] **P4.9** Verify stage: tests, SAST, secret and dependency scans, mutation testing on changed code, human-owned golden tests (PL)
- [ ] **P4.10** Review stage: AI reviewer with severity tuning; track the acted-upon rate (PL)
- [ ] **P4.11** Supply chain: dependency existence and allowlist checks, install scripts off in sandboxes, AI commit trailers, SBOM, agent and model inventory (PS)
- [ ] **P4.12** Injection basics: egress allowlist, quarantined reader agent, output scanning, untrusted content to the inbox only (PS)
- [ ] **P4.13** Progressive delivery hooks: feature flags, staged exposure, rollback; production deploy stays human-gated (PL)
- [ ] **P4.14** End-to-end demo on a real ticket, with the audit log replaying every decision (all)

Exit check: demo ticket reaches a reviewed PR with two human gates, and the audit trail explains each step.

---

## Phase 5: Brownfield

Goal: onboard a real existing repository safely.

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

Exit check: evals gate every change, attack success rate is tracked, and a kill-switch drill is completed.

---

## Phase 7: Council, router, second harness, visuals, packaging

Goal: widen the framework once the data shows it is needed.

- [ ] **P7.1** Council for two or three high-risk gates (role pattern first), vote schema, quorum, one round; evaluate it against a single reviewer and retire it where it does not help (FO, ME)
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

Exit check: pilot metrics reviewed against baselines; keep or retire each optional component on the evidence.

---

## Continuous track (every phase)

- [ ] Security review of each new tool, MCP server and policy change
- [ ] Access review: which roles use which tools; remove unused permissions
- [ ] Dependency and model inventory updates
- [ ] Eval and red-team refresh with new payloads and harder cases
- [ ] Memory pruning and lint reports reviewed
- [ ] Metrics review: lead time, change-failure rate, time to restore, cost per merged PR, rework, acted-upon rate, override rate, repeat-mistake rate, attack success rate
- [ ] Keep the charter, ADRs and this list in sync

## Suggested first two weeks

1. P0.1, P0.3, P0.4, P0.10 (owners, decisions, pilots, repo hygiene)
2. P1.1, P1.2, P1.5 (skeleton, schemas, CI)
3. P2.1 and P2.4 (event log and the policy gate), then P2.6 in log-only mode
4. P0.8 baselines in parallel
