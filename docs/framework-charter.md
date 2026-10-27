# Agentic SDLC Framework: Charter

Status: **Draft v0.2** · Date: 2026-10-27 · Owner: _to be named_ · Reviewers: _security, platform, engineering leads_

Changes in v0.2: added the major-decisions-only flow with a sample policy (4.1), council patterns (4.2), expanded brownfield onboarding (5.8) and the learning loop (5.9), memlog and Obsidian notes (7.1), the visual layer (8.13), the alternatives landscape and BMAD compatibility (13.1-13.2), build operations (14), and configuration samples (15).

This charter defines what the framework is, the rules it is built under, how AI is used in each SDLC phase, and what is built first. It is a reference to build against, not a specification of code.

Evidence notes: most external guidance below comes from vendor and practitioner sources. Each practice is tagged **[P]** primary/standard source, **[S]** secondary or vendor source, or **[O]** our own design decision. Anything tagged **[S]** that becomes a hard requirement must be checked against its primary document first. See [Section 16](#16-evidence-and-verification-status).

---

## 1. Purpose and scope

**Goal.** Automate the software development lifecycle with AI agents, with humans deciding only the major decisions, with a full audit trail, with controlled token spend, and with the ability to learn from mistakes.

**In scope.** Greenfield and brownfield projects; multiple agent harnesses (first: Claude Code) and multiple models behind a gateway; governed tool and MCP access; memory (LLM-wiki on Markdown, graph index); a council for high-risk decisions; observability and a visual (2D/3D) view of agent activity.

**Out of scope for v1.** Fine-tuning or RL on model weights; fully autonomous production deploys; replacing existing ticketing, CI or source control; a custom terminal UI.

**Non-goals.** "Injection-proof" claims; maximising autonomy for its own sake; reproducing any upstream methodology wholesale.

## 2. Principles

1. **Simplest thing that works.** Prefer a single LLM call, then a fixed workflow, then an agent, then multiple agents. Add complexity only when evals show it helps. **[S]** (Anthropic's guidance on workflows vs. agents)
2. **Deterministic first.** Code, AST, linters, tests and CI answer what they can before any model is called. **[O]**
3. **Enforce in code, not in prompts.** Boundaries, gates, budgets and approvals are enforced by services the model cannot bypass. Prompts express intent only. **[O]**
4. **Least agency.** Minimum permissions and minimum autonomy per role. **[S]** (OWASP agentic guidance)
5. **Assume injection will succeed.** Design so a hijacked agent cannot cause serious harm. **[O]**
6. **Humans own accountability.** Every action maps to a named human or team; agents never self-approve. **[O]**
7. **Learn through artifacts.** Prefer turning a mistake into a test, lint rule, hook or CI check over adding prose to memory. **[O]**
8. **Measure before optimising.** Ledger and evals come before routers, councils and visuals. **[O]**
9. **Portability by seams.** Harness, model, tool and memory are adapters behind stable schemas. **[O]**
10. **Adopt concepts, not dependencies.** The adoption test below applies to every upstream idea. **[O]**

### 2.1 Adoption test for upstream ideas (BMAD, nebula, others)

An idea is adopted only if it: (a) solves a requirement we have; (b) is enforceable in code; (c) is cheap in tokens; (d) is stable and portable; (e) still works if the upstream disappears. Where text or code is reused closely, record the source and check its licence first. nebula is MIT; BMAD's licence was not confirmed.

| Adopted concept | Source | How we implement it |
|---|---|---|
| Scale-adaptive entry (idea, plan, small change) | BMAD | Policy tiers and a lighter path for small changes |
| Small, verified, SHA-stamped project context | BMAD | Always-on core block in `AGENTS.md` |
| Mistake becomes hook, lint rule or CI check first | BMAD | `record` command in the learning loop |
| Clean-room evals, 3+ runs, retire what doesn't beat baseline | BMAD | `core/evals` |
| Record baseline revision before changes | BMAD | Task state |
| Nothing marked done without a human | BMAD | Enforced in the orchestrator state machine |
| Persona round-table with cheaper subagent voices | BMAD | Council, plus votes, quorum, escalation |
| Daemon with disposable clients | nebula | Orchestrator service with CLI, web and 3D clients |
| Hook-driven agent status ("waiting on human") | nebula | Harness adapters emit events |
| Harness as a config table with hook dialects | nebula | `adapters/` |
| One git worktree per task | nebula | Sandbox for tier-2 work |
| Orchestrator authority by identity, never by prompt text | nebula | Gateway authorization |

Explicitly not adopted: persona-heavy prompts as the core; prompt-rendered workflows as the engine; upstream ticket formats as our data model; a TUI; permission-skipping modes (`--yolo`, `--force`) for unattended runs.

## 3. Architecture

```
Clients: CLI · web · 2D timeline · 3D world · chat notifications
                  │
            Orchestrator service (daemon)
   state machine · policy engine · council · decision cards · budgets
        │                 │                   │
  Model gateway     Tool gateway        Memory services
  tiers, routing,   registry, authz,    vault (wiki) · graph index ·
  fallbacks         MCP, credentials,   context assembler · write gate
        │           sanitiser, egress          │
        └────────────── Event ledger ──────────┘
          hash-chained audit log + OpenTelemetry traces + token ledger
                  │
        Harness adapters (Claude Code first) · CI · Git · ticketing
```

Per-project files live in `.agents/` (`project.yaml`, `policy.yaml`, `vault/`). Nothing project-specific lives in `core/`.

Repository layout (target):

```
core/{orchestrator,policy,council,memory,ledger,gates,tools,security,evals,agents,skills}
adapters/   packs/{greenfield,brownfield,stacks}   viz/   docs/
```

## 4. Autonomy tiers and human-in-the-loop

| Tier | AI may | Human role |
|---|---|---|
| T0 Assist | Suggest only | Does the work |
| T1 Draft | Produce an artifact | Approves before use |
| T2 Act in sandbox | Change code, run tests, open a PR in a worktree/branch | Gates the merge |
| T3 Act with guardrails | Run automatically | Samples and audits afterwards |
| No-go | Never act alone | Human only: production deploy, secrets/IAM, destructive prod data changes, security exceptions, compliance sign-off |

**Decision scoring.** Escalate when **any** axis crosses its threshold (never an average): reversibility, blast radius, cost, risk class (security, auth, payments, PII, compliance, licensing), uncertainty (ambiguity, low confidence), novelty/conflict (contradicts an ADR). **[O]**

**Rules.**
- A task that needs a human is **parked**; independent tasks continue.
- Decision cards contain: the decision in one sentence, 2-3 options with trade-offs, the agent's recommendation and confidence, evidence (diff summary, tests, council votes, cost).
- Timeouts never auto-approve. Non-urgent decisions are batched into a digest.
- The author of a change cannot approve it. No-go zones require four-eyes.
- The human can pause, redirect or halt any agent at any time (kill switch).
- Escalation triggers: ambiguity after one clarification, three failed attempts, budget exceeded, deviation from plan.
- Review is **risk-weighted**: low-risk changes get light review, high-risk get deep review. Reviewing everything equally causes rubber-stamping.
- Approval fatigue is tracked as a metric (volume of cards, time-to-decision, override rate).

### 4.1 Major decisions only: how routing to a human works

The agent runs continuously. A **policy engine outside the model** decides what counts as major. The human is paged only when a decision crosses a line.

```
Agent decision
   ├─ matches auto_approve ────────────────► proceed, log it
   ├─ matches council_review ─► council ───► unanimous: proceed, log
   │                                         split or block: escalate
   └─ matches human_required ──────────────► park the task, send decision card
```

The policy is a versioned file enforced by a service, so the agent cannot talk its way past it. Starter example (thresholds are placeholders to tune from data):

```yaml
# .agents/policy.yaml
auto_approve:
  - tests, docs, lint fixes, refactors under 200 lines
  - dependency patch bumps that pass CI and the allowlist
  - any change confined to a task branch or worktree
council_review:            # advisory; human only if the council splits
  - new dependency, public API shape change, dev schema change
human_required:            # the "major decisions"
  - production deploy or rollback
  - destructive data migration
  - auth, secrets, IAM, payment logic
  - architecture change that contradicts an ADR
  - spend above budget
  - ambiguity remaining after one clarification attempt
  - three failed attempts on the same task
never:
  - disable tests or CI to get green
  - touch .env, secrets paths or production credentials
```

**Rules and failure modes**
- **Risk is computed from evidence** (touched paths, diff size, file types, tools used, taint state), never self-reported by the agent. **[O]**
- **Aggregate, so changes cannot be split to slip under a threshold:** thresholds apply per task and per day, not per commit. **[O]**
- **Precedents:** every human decision is stored. After the same decision type is approved repeatedly (start with three), the framework proposes a policy change; a human approves it through a normal policy PR. The agent never edits policy. **[O]**
- **Start conservative:** in the first weeks escalate almost everything, log what the human decided, then move always-approved types into `auto_approve`. **[O]**
- **Health targets:** humans see a small share of decisions and overturn few of them. Many overrides mean thresholds are too loose; near-universal approvals mean too tight.
- **Cheap human step:** decision cards, a twice-daily digest for non-urgent items, push notification for urgent ones, an "ask me later" action, and a stated safe default (stay parked, never auto-approve).
- **Autonomy beyond the gate:** once a task is approved, the agent proceeds without further prompts inside the approved scope; a deviation from the approved plan re-triggers the gate.

### 4.2 Council

A council is several agents or models deliberating on one decision. It costs several model calls, so it is used only at high-risk gates.

| Pattern | How it works | Best for |
|---|---|---|
| **Role council** | Architect, Security, QA, Performance/Cost and Product each review the artifact from their own angle | Design and high-risk PR review |
| **Model council** | Several models answer independently, then rank each other's answers anonymously, then a chair model synthesises. This is the pattern from Karpathy's `llm-council` project **[S]** | Hard design questions where model diversity helps |
| **Debate** | One agent argues for and one against; a judge decides | Trade-off decisions |

**Rules**
- **The council advises; the human decides.** A council vote never replaces a merge or deploy approval.
- Members return a structured vote: `approve | concerns | block`, confidence, at most 5 reasons. Quorum is configured per gate; a blocking Security vote escalates to a human.
- **One round**, cheaper models for most members, a strong model only for the chair, one shared cached prefix for the artifact.
- Anonymise answers before peer review so models do not favour their own family. Use different providers where possible for diversity, while remembering that models share biases: the council does not replace deterministic checks.
- Every council run is logged (members, models, prompt versions, votes, outcome) in the audit log.
- **The council must earn its place.** Evaluate whether council outcomes differ from, and beat, a single reviewer on the eval set; if not, retire it for that gate. **[O]**

---

## 5. Practices by SDLC phase

Each phase lists the AI role, default tier, controls, and metrics.

### 5.1 Intake and requirements
- **AI role:** clarify, find gaps and ambiguity, draft stories and acceptance criteria, trace requirements to tests. **Tier T1.** Humans own intent and priority.
- **Spec structure.** Goal, scope, testable acceptance criteria, constraints, pointers to relevant code, explicit non-goals, edge cases, and data contracts (signatures, OpenAPI, schemas) where relevant. **[S]**
- Acceptance criteria are written as checkable conditions ("when X, then Y") and map one-to-one to tests. **[S]**
- **Review the spec before building:** ambiguity is the main bottleneck, and a spec error is cheapest to fix here. Agents follow specs literally, so errors propagate. **[S]**
- State critical rules once, in a canonical place, and keep a "common failure modes" list fed by the learning loop. **[S]**/**[O]**
- Scale to size: small changes skip the full spec path (BMAD-style entry points). **[O]**
- **Metrics:** clarification count, spec-change rate after build starts, requirement-to-test coverage.

### 5.2 Design and architecture
- **AI role:** options with trade-offs, ADR drafts, threat model per feature. **Tier T0-T1.** Council reviews high-risk designs (new dependency, API or schema change, auth, data flow). Human decides.
- Every decision is an ADR in the vault with links to affected components; contradicting an ADR is an escalation trigger.
- **Threat modelling** per feature against the OWASP agentic and LLM lists (Section 8.5).
- Prefer the simplest architecture. Multi-agent designs need justification (Section 8.8).
- **Metrics:** ADR reversal rate, design-review findings caught later in build.

### 5.3 Build
- **AI role:** implement against an approved spec in a worktree/branch. **Tier T2.** Small slices: one story or one intent per PR (never "port and fix" together).
- Record the **baseline revision** before changes. Tag AI-assisted commits with agent, model, task ID and approver (trailers). **[S]**/**[O]**
- Dependencies: existence check, allowlist, provenance check, install scripts disabled in the sandbox, human approval for any dependency change (Section 8.6). **[S]**
- Permission levels are separate: edit files, run tests, publish artifacts. **[S]**
- Context is assembled under a hard token cap (Sections 7.3 and 8.2).
- **Metrics:** first-pass CI success, tokens and cost per merged PR, PR size, rework rate.

### 5.4 Test and verification
- **Deterministic gates agents cannot bypass:** unit/integration tests, linters, SAST, secret scanning, dependency scanning, coverage thresholds, type checks. **[O]**
- **Do not trust AI-written tests by coverage alone.** Use mutation testing on changed code, flag tests that don't kill mutations, and treat a suite that stays green when behaviour is deliberately broken as not ready. **[S]** (an 80% kill-rate target was suggested by one guide; treat it as a starting point, not a standard)
- **Protect the oracle.** Golden and characterization tests are human-owned. Agents have no edit access, and CI rejects migration/refactor PRs that touch them. **[S]**
- Property-based and edge-case generation is allowed (T1), reviewed for real assertions.
- Never disable tests or CI to get green (policy `never` rule).
- **Metrics:** mutation kill rate on changed code, defect escape rate, flaky-test rate.

### 5.5 Code review
- **AI role:** first-pass review for bugs, security and style; **a different model/prompt from the author** to reduce shared blind spots. **[O]** Humans review design, logic and risk.
- **Noise control.** AI reviewers produce false positives (benchmarks quoted at roughly 5-15%) and teams learn to ignore them. Tune severity thresholds, suppress low-value classes, and track the **comment-acted-upon rate** as the core metric. **[S]**
- The diff is untrusted input (injection). Deterministic scanners remain the gate.
- **Council** is used only for high-risk changes, one round, structured votes (`approve | concerns | block`, confidence, up to 5 reasons), quorum rules, and a blocking Security vote escalates to a human. **[O]**
- **Metrics:** acted-upon rate, review latency, post-merge defects by review type.

### 5.6 Integrate, release and deploy
- **Progressive delivery by default for agent-authored changes:** feature flags that represent real release decisions with named owners, staged exposure (internal, canary, partial, full), automated gates on SLOs, cost burn and anomalous tool access, rollback kept live at full release. **[S]**
- Early rings use tight thresholds and human review; later rings may rely on automated gates. **[S]**
- **Production deploy is human-gated in v1** (no-go zone for agents). Revisit only with data.
- **Tool and schema drift:** an MCP tool whose schema or scope changes is quarantined until its owner re-approves. **[S]**
- Provenance: SBOM and SLSA-style attestations linking agent sessions to signed releases. **[S]**
- **Metrics:** change-failure rate, rollback rate, time to restore, lead time (DORA-style).

### 5.7 Operate and respond
- **AI role:** investigation, not remediation, at first: summarise incidents, enrich alerts, correlate logs, retrieve runbooks, assess change impact, draft post-incident reports. **Tier T0-T1.** **[S]**
- Remediation is limited to bounded, reversible, pre-approved playbooks, with explicit human confirmation for production mutations. Rollback autonomy only where a clear deployment-to-failure correlation exists. **[S]**
- Runbooks are machine-readable (preconditions, decision points, action boundaries) and retrieved as tool-style operations, not free-form commands. **[S]**
- Prerequisites before enabling: telemetry, structured runbooks, a service catalogue. One source suggests governance overhead is justified around 3-5 significant incidents a month. **[S]**
- **Metrics:** MTTR for assisted incidents, escalation accuracy, wrong-action rate.

### 5.8 Maintain, modernise and brownfield
- **Onboarding sequence for an existing repo** **[O]**, informed by the practices below:
  1. **Map (deterministic, no model tokens):** code graph from imports, symbols, build files and dependencies.
  2. **Profile:** git history for hotspots, churn and ownership; test coverage, CI health, dependency age.
  3. **Infer:** cheap-model summaries per module go to the inbox as `agent-inferred`.
  4. **Confirm:** humans review the 10-20 most critical modules; confirmed ones become trusted memory.
  5. **Mark no-go zones:** fragile, undocumented or regulated areas get stricter policy (human required).
  6. **Safety net:** characterization tests (below) around an area before any agent changes it.
  7. **Baseline:** record cycle time, defect rate, build time and cost before agents start, so improvement can be shown.
  8. **Start read-only:** brownfield tool sets begin read-only and widen as trust builds.
- **Characterization tests before any change;** verify them by deliberately breaking the code (modules that stay green are not ready). **[S]**
- **Strangler pattern** with small slices, one intent per slice, one PR per job, traffic shifts in stages, and legacy retired only after a stable cycle at full traffic. **[S]**
- **Triage first.** Retire systems that carry debt but little value. AI cannot recover undocumented business rules or make architecture decisions: escalate those. **[S]**
- Agents amplify existing process quality: without tests, search and observability, adding agents makes output worse. Fix prerequisites first. **[S]**
- No-go zones (fragile, undocumented, regulated) are marked in policy and require a human.
- **Metrics:** parity-test pass rate per slice, defects found in shadow traffic, slices completed per cycle.

### 5.9 Learn and improve
- **"Reinforcement learning" here means a feedback loop around the model, not weight updates.** Hosted models cannot be retrained by us; learning happens through memory, policy, tests and routing. It is cheaper, auditable and reversible. Fine-tuning or true RL is a later option only if outcome data volume justifies it. **[O]**
- **Reward signals come from objective sources, not the agent's opinion:** CI and test results, human decisions at gates (approved, edited, rejected, overridden), review comments and post-merge rework, defects, incidents and reverts traced to a change, cost and token overrun, and council votes later proven right or wrong.
- Outcome signals feed a reflection job (cheap model, batched): after a failure or override it writes a short post-mortem (what happened, root cause, what would have prevented it); after a hard success, what worked.
- Lessons are **validated before storing**: specific and checkable, confirmed by a second occurrence, a human, or a replay test, and consistent with higher-trust rules.
- **Order of preference for fixing a repeated mistake:** CI check or test, then hook or lint rule, then policy rule, then playbook, then wiki note, then prompt text. **[O]**
- Production failures become regression tests, with an online check to catch recurrence. **[S]**
- A bandit-style router learns model choice per task type from ledger data.

## 6. Greenfield vs brownfield packs

| | Greenfield | Brownfield |
|---|---|---|
| Start | Idea, PRD | Existing code, unknown behaviour |
| First phase | Requirements, architecture, scaffold | Discovery, mapping, baselines |
| Memory at start | Empty, grows with work | Seeded by analysis, human-confirmed |
| Posture | Moderate autonomy | Conservative, read-only tools first |
| Tests | Test-first | Characterization tests first |
| Change style | Build forward | Small slices, flags, strangler |

Both use the same core. Packs hold defaults and bootstrap flows. Stack packs hold conventions and test/build commands per language.

## 7. Memory and context

### 7.1 LLM-wiki on Markdown (Obsidian-compatible)
Three layers: **raw sources** (immutable: code, tickets, PRDs, logs), **wiki** (LLM-maintained Markdown pages with links and frontmatter), **schema** (the rulebook in `AGENTS.md` and `_policy/`). Operations: **ingest** (a source updates several pages and appends to `log.md`), **query** (read `index.md`, then relevant pages; good answers are filed back), **lint** (contradictions, stale claims, orphans, unvalidated items).

Memory types: working (context window), episodic (event log, summaries), semantic (components, ADRs, glossary), procedural (playbooks, rules, CI checks), preference (team/user).

Vault layout: `00-inbox/` (agent writes only here), `10-decisions/`, `20-lessons/`, `30-components/`, `40-playbooks/`, `50-incidents/`, `60-prefs/`, `90-index/` (generated), `_policy/`.

Rules: agents write only to the inbox; promotion needs validation; every note carries `status`, `trust` (agent-inferred < test-verified < human-confirmed), `confidence`, `supersedes`, provenance; supersede, don't silently delete; prune and deduplicate on a schedule; no secrets or PII; memory is data, never instructions. Obsidian is an optional viewer; `.obsidian/` is git-ignored.

**Obsidian practicalities.** Nothing in the framework depends on Obsidian: agents work on the Markdown files directly, so it can be adopted later by opening the vault folder. Use `[[wikilinks]]`, YAML frontmatter, readable filenames, one idea per note with a one-line summary at the top, and keep generated files in `90-index/` so humans and agents never edit the same file. Install only core plugins plus Dataview or Bases if queries are needed; community plugins run with the user's file access, so review them. Keep secrets and PII out, turn cloud sync off (Git is the history), and check Obsidian's licence terms for company use before rollout. Agents write only to per-agent inbox files; one process merges them, which avoids edit clashes with a human who has the same vault open.

**Episodic logs versus the wiki.** A per-epic run log (such as BMAD's `.memlog.md`, whose exact format has not been reviewed) is episodic memory: what happened, in order. It is a raw source for the ingest job, not a replacement for the wiki. Epic retrospectives are a natural ingest trigger: when one lands, the affected wiki pages are updated and the run log is left unchanged as the record. A small always-on project-context block (in `AGENTS.md`, stamped with the verified commit) stays as the cheap core.

### 7.2 Graph index
Nodes: requirement, story, ADR, component, file/symbol, test, PR, incident, lesson, agent, approver. Edges: `implements`, `depends_on`, `decided_by`, `supersedes`, `caused_by`, `fixed_by`, `violates`, `tested_by`, `approved_by`, `learned_from`. Built deterministically from code and wikilinks where possible; derived index is rebuildable from the vault (vault is the source of truth). Retrieval is hybrid (vector candidates, then 1-2 graph hops, capped).

### 7.3 Context engineering rules **[S]**
- **Just-in-time retrieval:** keep identifiers (paths, links, note names) and load content on demand; small always-on core.
- **Tool-result clearing:** drop raw tool output once processed.
- **Compaction:** tune for recall first, then precision; over-aggressive summaries drop details needed later.
- **Structured note-taking** outside the context window (task notes, todo lists).
- **Subagents for noisy work** return conclusions, not dumps.
- **Fresh context per stage;** pass artifacts, not conversations.
- **Memory maintenance:** prune, deduplicate, remove outdated facts or retrieval quality degrades.

## 8. Cross-cutting practices

### 8.1 Model gateway and routing **[O]**/**[S]**
- Roles request a **tier** (`cheap | standard | strong`); `tiers.yaml` maps tiers to models. Use an existing gateway (LiteLLM, OpenRouter, Portkey, cloud gateways) rather than writing one.
- Route on evidence: a static table first, then ledger data (success, rework, cost). Routing decisions are logged with reasons. Escalate tier on failure, not by default.
- Author and reviewer use different models. Sensitive repos use only approved or local models.
- Per-model evals and prompt overrides, since prompts do not transfer cleanly.
- Cost levers, stacked and measured separately: prompt caching (input tokens only; keep the prefix byte-stable), batch APIs for non-urgent work, model routing, reasoning-token caps, prompt trimming. Published savings figures are vendor claims; measure on our own workload.

### 8.2 Token budgets and cost control **[O]**
- Budgets per task, stage and day; soft limit (cheaper model, compaction), hard limit (park and escalate).
- Loop breakers on repeated calls or errors.
- One ledger row per call: agent, on-behalf-of, task, model, tokens in/out, cached, cost, outcome.
- Metrics: cost per merged PR, cache hit rate, context size per call, retrieval precision, repeat-mistake rate.

### 8.3 Tool and MCP governance
- **Registry only.** Every tool (native or MCP) has: id, source, pinned version, risk class, effect, data class, allowed roles, environments, approval mode, owner. Unregistered tools cannot be called.
- Risk class drives gates: read-only auto; reversible writes auto and logged; irreversible or external actions need a human; critical (IAM, secrets, security settings) human only.
- **MCP server vetting:** allowlist, source and maintainer review, pinned version and checksum, least-privilege token, container/sandbox with egress limits, separate trust zones (web-reading vs. production-writing). Hash tool definitions and alert on change (tool poisoning). **[S]**
- **MCP spec rules** **[P]**: no token passthrough; validate token audience on every request; keep per-user, per-client consent records for proxy/third-party flows; consent screens name the client and scopes.
- **Token cost of tools:** expose only the role's tools, keep descriptions short, load schemas on demand when there are many, truncate and paginate results, cache idempotent reads, prefer scripts for bulk work.
- The framework also **exposes its own MCP server** (`policy_check`, `request_approval`, `wiki_*`, `graph_query`, `budget_status`, `task_state`, `council_review`).

### 8.4 Identity and credentials **[S]**
- Each component (orchestrator, each agent, each tool connector) has its own verifiable identity (SPIFFE-style workload identity as a reference). Agents act "on behalf of" a named human or team.
- **Short-lived, task-scoped credentials** minted by a broker only after the task is approved; no shared static keys; no production credentials in agent reach by default; instant revocation.
- Runtime authorization (policy decides what an identity may do now) on top of identity. mTLS between components is a candidate. Multi-agent agent-to-agent identity has no settled standard; keep it simple and revisit.
- Secret scanning on tool input and output; redact before logging.

### 8.5 Security and injection resistance
- **Threat model:** map each risk in the OWASP Top 10 for Agentic Applications (2026) and the LLM Top 10 to a control and a red-team case. Confirm exact risk numbering against OWASP's own document. **[S]**
- **Trust labels** on every context source (`trusted | internal | untrusted`); untrusted sources include tickets, external PRs, third-party code and READMEs, web pages, emails, logs, tool descriptions and outputs, and poisoned memory.
- **Taint tracking:** once untrusted data enters a session it is marked tainted and the policy tightens.
- **Lethal-trifecta rule:** never combine private-data access, exposure to untrusted content and an external communication path in one session. If unavoidable, gate the outbound step.
- **Quarantine pattern:** a restricted agent reads untrusted content and returns only schema-validated output to a privileged agent. **Plan-then-execute:** deviations from the approved plan are rejected by the gateway.
- Egress allowlist; block exfiltration channels (markdown image URLs, link unfurling, arbitrary HTTP/DNS); output scanning for secrets; canary tokens in sensitive data.
- Memory poisoning defence: untrusted-derived content goes to the inbox only; behaviour-changing lessons (rules, permissions) need human approval.
- Detection (classifiers, anomaly rules) is a signal, not a wall.
- **Red-team suite** (20-30 payloads at start) runs on every model, prompt or tool change; track attack success rate and per-role blast radius. Incident playbook and kill switch exist before pilot.

### 8.6 Software supply chain **[S]**
- Treat agents, models and integrations as supply-chain nodes: maintain an inventory with versions; review changes.
- **Slopsquatting defence:** verify every dependency exists and is allowlisted before install; agent-driven package resolution and CI execution are privileged actions.
- SBOM, SLSA-style provenance, AI-contribution commit trailers; disable install scripts in agent sandboxes; isolate build credentials from agent sessions.

### 8.7 Observability and audit
- **Two layers, one trace ID.** (1) Operational: OpenTelemetry GenAI traces (model-call spans, agent spans with nested tool calls, MCP spans), W3C `traceparent` propagation, prompt/completion content opt-in and by reference. (2) Evidential: append-only, hash-chained audit log anchored in Git or write-once storage. **[S]** The OTel GenAI conventions were partly experimental at last check; verify status and attribute names at the official spec. Choose OTel GenAI or OpenInference deliberately.
- Each audit record: agent, on-behalf-of human, task, tool and version, redacted args, result hash, risk class, policy decision and version, approver, tokens, duration, outcome, model ID, prompt version, memory snapshot, retrieved context IDs (so a decision can be explained and replayed).
- Denied and failed calls are logged. Access reviews and anomaly alerts run periodically. Retention and access controls are defined; secrets and PII are redacted.

### 8.8 Multi-agent discipline **[S]**
- Start with one agent or a fixed workflow. Add an agent only if it parallelises something a single context cannot handle. "It seemed like a clean separation of concerns" is a signal to consolidate.
- Published failure studies attribute a large share of failures to inter-agent misalignment and inconsistent shared state; coordination cost grows with team size.
- Use explicit, role-aware message schemas (intent, inputs, expected outputs); pass structured briefings with pointers to source documents, not raw transcripts; scope shared memory (read-only vs. write).
- Patterns in use: orchestrator-workers (subtasks decided by the orchestrator), evaluator-optimizer (clear evaluation criteria), routing, parallelisation, prompt chaining.

### 8.9 Durable execution **[S]**
- Park-and-resume requires persisted state. Start with checkpointed state in our own store or LangGraph-style checkpointing; move to a durable-execution engine (e.g. Temporal) when workflows span services, run for days, or take irreversible actions.
- Side effects live in idempotent steps; keep workflow logic deterministic. Resuming after a human approval and recovering from a crash are different operations; test both. Prune old checkpoints to control history size.

### 8.10 Evals and quality of the framework itself **[S]**
- Treat prompts, policies, skills and routing as code: versioned, evaluated, reviewed.
- Run evals in CI on every prompt, model or tool change, with gates on **success, latency and cost**.
- Prefer deterministic checks; use LLM judges only for subjective criteria, with narrow pass/fail questions and a **human-labelled calibration set** (one vendor suggests 50-200 examples) rechecked when the judge or rubric changes.
- Clean-room runs, 3+ runs per case before calling a result settled; rotate in new and harder cases to avoid overfitting; production failures feed the suite; production traces catch drift between releases.
- A skill or agent that no longer beats the bare-model baseline is retired, not patched.

### 8.11 Governance, compliance and measurement
- **Accountability:** RACI per stage and gate; approvals are attributed and signed (SSO identity; signed commits for high-risk gates); `policy.yaml` changes go through PR with CODEOWNERS; agents may propose policy changes but never apply them.
- **Oversight capabilities (EU AI Act Art. 14 as a reference):** understand, monitor, intervene, halt. Logging and retention (Arts. 12 and 26 as references). NIST AI RMF (Govern, Map, Measure, Manage) and ISO/IEC 42001 are voluntary mappings. Whether this framework or the systems built with it fall under "high-risk" depends on use; **compliance owners must decide**, and the applicable dates must be verified in the official text (sources disagree). **[S]**
- **Measure honestly.** A randomized trial in 2025 (16 experienced developers, 246 tasks) found developers took longer with AI while believing they were faster; it used early-2025 tools and a small sample, so do not extrapolate, but do **measure rather than trust perception**. DORA's AI capabilities work argues gains depend on foundational systems more than on tools. **[S]**
- Baseline before the pilot. Track: lead time, change-failure rate, time to restore, cycle time, tokens and cost per merged PR, rework rate, acted-upon rate of AI review comments, escalation rate and override rate, repeat-mistake rate, defect escape rate, attack success rate, time spent in human review.

### 8.12 Harness and model portability **[O]**
- Canonical project instructions in `AGENTS.md`; other harness files are generated or thin pointers.
- One MCP server and one policy CLI (`agentctl check`) shared by all harnesses; one event schema; per-harness adapters do three jobs only: inject context, call the gate, emit events.
- Enforcement must not rely on a harness's hooks: also enforce through sandbox permissions, scoped credentials, branch protection and CI, since some harnesses cannot block actions.
- Harnesses run in non-interactive mode under the orchestrator for unattended work. Never run unattended with confirmations disabled.

### 8.13 Visual layer: 2D timeline and 3D world **[O]**

**Principle:** the visual layer is **observability, not control**, and it is driven by real events from the ledger and traces, never simulated. Boundaries are enforced by permissions, policy and CI, not by anything in the scene.

- **Order of build:** 2D timeline and list view first (it is what audits and screen readers need), 3D world later. Both read the same event stream; the 3D client is one more client of the orchestrator.
- **Scene mapping:** districts per SDLC stage; one avatar per agent with animation per state (reading, editing, testing, thinking, blocked, waiting on human) taken from events; a council chamber where votes appear and a blocking vote turns red; the **human gate as a door** where queued tasks visibly wait; artifacts (spec, PR, test report) moving between districts; a token-budget ring per agent and stage that changes colour near the limit; alerts for loops, budget overruns and anomalous tool use.
- **Actions from the scene** (approve, reject, pause) call the **same gate API with the same authorization** as the CLI. There is no separate path.
- **Privacy:** show identifiers and states, not prompts or code; redaction applies as in the audit log.
- **Cost:** the visual layer adds no model tokens; send small events, not transcripts.
- Prior art (for example, simulation research such as Stanford's "Generative Agents" and open-source agent dashboards) is inspiration only; review current projects before building.

---

## 9. Roles and accountability (starting RACI)

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Policy and thresholds | Platform/security engineer | Framework owner | Security, engineering leads | Teams |
| Spec approval | Product owner | Product owner | Council (if high-risk) | Team |
| Architecture decision | Architect agent drafts | Human architect | Council | Team |
| Merge approval | Reviewer (human) | Task owner | AI reviewer, council | Team |
| Production deploy | Release engineer (human) | Release owner | Council advice | Stakeholders |
| Tool/MCP onboarding | Tool owner | Security lead | Platform | Teams |
| Memory promotion | Reviewer | Project owner | Lint reports | Team |

## 10. Minimum viable slice and roadmap

Build the smallest slice that proves the hard parts. Agents come after policy and audit.

| Phase | Deliverable | Exit check |
|---|---|---|
| 0 | Decisions locked, charter approved, baseline metrics captured, pilot repos chosen | Owners named |
| 1 | Schemas (event, task, decision card, policy, tool entry, wiki frontmatter, trust label) with tests | Schema tests pass |
| 2 | Hash-chained event log + token ledger + OTel export; `agentctl check` (policy gate, taint and trifecta checks); Claude Code hook in **log-only mode**, then enforcing; approval flow with attributed decisions; budgets and loop breaker | Risky action blocked and approved; chain verifies |
| 3 | Vault template, inbox write gate, ingest and lint jobs, context assembler with token cap, `record` command | Repeated mistake captured once and not repeated; measurable context reduction |
| 4 | One pipeline end to end (intake, spec, build, verify, review, human gate, merge), worktrees, role charters, tool registry and gateway (5-8 tools), model gateway with tiers, injection basics | Small real ticket to reviewed PR with humans only at gates; audit replay works |
| 5 | Brownfield discovery: code graph, profiler, characterization tests, no-go zones, baseline metrics | One real repo onboarded |
| 6 | Evals and red-team in CI (started in week 2, formalised here) | Gates on success, latency, cost, attack success rate |
| 7 | Council on 2-3 high-risk gates; router from ledger data; second harness or provider; 2D timeline, later 3D world; packaging (`init greenfield` and `init brownfield`) | Pilot metrics reviewed |

Do not start with the council, router or 3D world: they depend on the ledger and gates existing.

**Pilot:** one real brownfield repo and one small greenfield project. Keep human gates strict at first; loosen only from ledger data (escalation rate, override rate, defects).

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Policy thresholds wrong | Log-only mode first; tune from data; review overrides |
| Approval fatigue or rubber-stamping | Risk-weighted review; cap card volume; track time-to-decision |
| Agents amplify weak process | Prerequisites check (tests, search, observability) before enabling |
| AI tests that don't test | Mutation testing; human-owned golden tests |
| Review noise | Severity tuning; acted-upon rate; drop low-value rule classes |
| Memory poisoning or rot | Inbox gate, trust levels, lint, pruning, human approval for behaviour-changing lessons |
| Supply chain via agents | Allowlist, existence checks, no install scripts, provenance |
| Upstream churn (BMAD v7 alpha, nebula WIP, MCP/OTel specs) | Adapters, pinned versions, own schemas |
| Scope creep | Hold to the minimum slice |
| Over-claiming | Say "injection-resilient", not "injection-proof"; measure outcomes |

## 12. Open decisions

1. Core language (default: Python) and whether a compiled daemon is needed later.
2. First pilot repos, stacks and ticketing/CI systems (Jira or Azure DevOps; GitHub or GitLab).
3. Second harness and second model provider, and the reason for each.
4. Where the vault lives (per repo by default) and who owns it.
5. Hosting for the event bus and viz (local vs. shared).
6. Observability convention: OTel GenAI vs. OpenInference.
7. Durable execution engine timing (own store vs. LangGraph-style vs. Temporal).
8. Compliance scope and applicable regulatory dates (compliance owner).
9. Licence check for any reused upstream text or code (BMAD licence unconfirmed).
10. Data policy: which models may see which repos.
11. Which content pack(s) to ship first (BMAD-style, Spec Kit, OpenSpec) and whether importers are needed on day one.
12. Whether the visual layer is a 2D timeline only for the pilot, with 3D deferred.

## 13. Reference designs reviewed

- **BMAD-METHOD** (methodology; skills and a Python state layer). Read: README, `AGENTS.md`, selected skills (`bmad-project-context`, `bmad-eval`, `bmad-party-mode`, `bmad-build-auto`, `bmad-code-review` entry), `tools/ticket-tree-rules.md`. Not read: persona definitions, code-review reviewer layers, build workflow steps, `docs/existing-codebases/`. v7 is alpha.
- **nebula** (Rust TUI/daemon supervising agent CLIs; MIT; work in progress). Read: README, `ARCHITECTURE.md`, `docs/how-it-works.md`, `docs/orchestrator.md`, `docs/configuration.md`. No memory, routing, policy engine, MCP, or injection defences in what was read.

### 13.1 Landscape of alternatives and how we relate to them

We build a governance and memory layer, and we compose with, not replace, spec and agent tooling. Status notes come from secondary sources found in October 2026; verify each project before depending on it.

| Tool | What it is | Relation to this framework |
|---|---|---|
| **GitHub Spec Kit** | Open-source (MIT) spec-first toolkit: constitution, specify, plan, tasks, implement; works across many coding agents. Official history reports v1.0.0 on 2026-08-21 **[S]** | Candidate **content pack** for the spec phase; light, harness-neutral. No policy, memory or audit layer |
| **Kiro (AWS)** | Spec-driven IDE: requirements (EARS-style) → design → tasks, steering files in `.kiro/steering/`, event-driven hooks **[S]** | A product, not a portable method; a source of ideas (steering, hooks). Adopt concepts only |
| **OpenSpec** | Specs in `openspec/specs/`, per-change folders in `openspec/changes/` with proposal, requirement deltas and tasks, archived on completion; brownfield focus; MIT **[S]** | Strong fit for **change-by-change brownfield** work; its "change proposal and archive" idea maps onto our task and ADR records |
| **Tessl** | Spec-as-source-of-truth approach (reported as more experimental) **[S]** | Watch only |
| **MetaGPT / ChatDev** | Simulated software-company multi-agent frameworks | Useful for prototypes; weak on human gates, audit and brownfield; heavier on tokens |
| **CrewAI, AutoGen, LangGraph, OpenAI Agents SDK** | General agent orchestration libraries | Possible runtime for our orchestrator; LangGraph-style interrupt and checkpoint fits park-and-resume (8.9) |
| **Claude Code subagents, skills, hooks, plugins** | Native roles, commands, hooks and headless mode | First harness and adapter target |
| **Autonomous agents (Devin, OpenHands, Copilot coding agent, Jules)** | Ticket in, PR out | Tier-2 workers behind our gate; control comes from our PR and CI gates **(from general knowledge; not re-verified)** |

**Choosing a content pack per project:** full lifecycle with personas (BMAD-style); lightest spec loop (Spec Kit); existing codebase, change by change (OpenSpec); an IDE-integrated workflow (Kiro). The framework's core stays the same; the pack is replaceable.

### 13.2 Compatibility without dependence

- **Importers, not imports:** a one-time importer converts existing BMAD (or Spec Kit, OpenSpec) artifacts, such as briefs, PRDs and architecture documents, into our spec and vault formats.
- **Exporter:** a thin export keeps an `AGENTS.md` project-context block current so any harness still gets always-on context.
- **Own versioned schemas** for task, decision, lesson, event and policy, documented and tested. Upstream format changes (BMAD v7 is alpha) cannot break us.
- **Attribution and licence:** note the source of any closely reused idea; confirm licences before copying text or code.

---

## 14. Running the build

- **Team:** two to three engineers can build the spine. One owns policy and security; one owns memory and evals; a named owner approves policy changes.
- **Milestones are demos, not feature lists:** "a risky action is blocked and approved", "a mistake is learned once and not repeated", "a ticket goes from intake to a reviewed PR with two human gates", "the audit log replays why a decision was made".
- **Pilot early** on one real brownfield repo (it exposes gaps faster than a greenfield demo) and one small greenfield project; compare against baselines.
- **Keep a decisions log** (ADRs) for the framework itself, reviewed by a human.
- **Version the core** and the packs; keep a changelog; run conformance tests per harness adapter (does it load context, hit the gate, emit events) with real processes, in the spirit of nebula's end-to-end tests.
- **Governance overhead scales with use:** do not enable heavy controls (council, durable engine, visual layer) before the data shows they are needed.

## 15. Configuration samples

`project.yaml` (illustrative; schema to be defined in phase 1):

```yaml
# .agents/project.yaml
project: orders-service
mode: brownfield                 # greenfield | brownfield
stack: dotnet                    # selects a stack pack
commands: { build: "dotnet build", test: "dotnet test", lint: "dotnet format --verify-no-changes" }
tiers:                           # role -> model tier (tiers.yaml maps tiers to models)
  analyst: standard
  builder: standard
  reviewer: standard
  council_chair: strong
  summariser: cheap
budgets: { per_task_tokens: 400000, per_day_cost: 50 }
no_go_paths: ["infra/prod/**", "**/.env", "db/migrations/legacy/**"]
tools_enabled: [git.read, git.branch, ci.run_tests, files.search, tickets.read, wiki.search]
environments: [dev, staging]     # never prod for agents
data_policy: { allowed_models: [approved-endpoint-a], retention: zero }
```

Tool registry entry:

```yaml
- id: github.create_pr
  source: mcp:github
  version: 1.4.2
  risk: medium
  effect: write-reversible
  data_class: internal
  allowed_roles: [builder, reviewer]
  environments: [dev, staging]
  approval: auto
  owner: platform-team
```

Vault note frontmatter:

```yaml
---
type: lesson
status: inbox        # inbox | validated | deprecated
trust: agent-inferred  # agent-inferred | test-verified | human-confirmed
confidence: 0.6
created: 2026-10-27
supersedes: []
---
```

## 16. Evidence and verification status

Sources are mostly vendor or practitioner material. Items to verify against primary documents before they become hard requirements:

| Item | Verify at |
|---|---|
| OWASP agentic risk list and numbering | OWASP GenAI Security Project (primary document) |
| OTel GenAI convention status (stable vs. experimental) and attribute names | OpenTelemetry semantic conventions spec |
| Anthropic context-engineering and agent guidance (read only via summaries) | Anthropic Engineering primary posts |
| EU AI Act applicability, articles and dates; NIST AI RMF mapping | EUR-Lex; NIST; internal compliance |
| Specific numbers (false-positive rates, kill-rate targets, calibration set size, MTTR, cost savings, failure-mode percentages, the 18% MCP scoping statistic) | Treat as indicative only |
| DORA seven capabilities (only the landing page was read) | DORA report |
| METR study details (secondary reports) | METR primary report |
| Spec Kit, Kiro, OpenSpec, Tessl status and features (secondary blogs; Spec Kit v1.0.0 date from a search summary of the official history) | Each project's own repository or docs (kiro.dev, github.com/Fission-AI/OpenSpec, GitHub spec-kit) |
| Council pattern (anonymised peer ranking and chair synthesis) | karpathy/llm-council repository README |
| Autonomous agent products (Devin, OpenHands, Copilot coding agent, Jules) | Vendor docs; not re-checked in this review |

### Sources

- MCP security best practices (primary): https://modelcontextprotocol.io/specification/latest/basic/security_best_practices
- OWASP Top 10 for Agentic Applications (secondary summaries): https://securew2.com/blog/owasp-top-10-agentic-ai · https://www.aigl.blog/owasp-top-10-for-agentic-applications-2026/
- OpenTelemetry on AI agent observability: https://opentelemetry.io/blog/2025/ai-agent-observability/index.md
- Anthropic, Building effective agents: https://www.anthropic.com/research/building-effective-agents
- Context engineering summaries: https://agentic-ai.readthedocs.io/en/latest/ContextEngineering/anthropic/ · https://www.developersdigest.tech/blog/context-engineering-for-agents-explained
- Agent identity: https://cloudsecurityalliance.org/articles/the-emerging-identity-imperatives-of-agentic-ai · https://www.paloaltonetworks.com/blog/?p=355877 · https://www.arthur.ai/column/non-human-identity-ai-agents
- Supply chain: https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-agents-software-supply-chain-risk-20260/ · https://jfrog.com/blog/governing-the-agentic-sdlc/ · https://safedep.io/ai-native-sdlc-supply-chain-threat-model/ · https://alpeshnakrani.com/books/ai-native-sdlc/agent-permissions-sandboxes-and-supply-chain-trust/
- Evals: https://www.langchain.com/resources/llm-evals · https://rubinlake.com/fr/technology-radar/developer-ai-and-delivery/llm-evals-as-ci · https://mlflow.org/articles/tags/agent-evaluation
- Regulatory mapping: https://openrouter.ai/blog/human-oversight-eu-ai-act-compliance-agent-sdk · https://gravitee.io/corpus/gen-1925/regulatory-compliance/mapping-ai-agent-controls-to-nist-ai-rmf-and-eu-ai-act-requirements.html
- DORA AI capabilities report: https://cloud.google.com/resources/content/2025-dora-ai-capabilities-model-report
- Multi-agent failure modes and coordination: https://www.splunk.com/en_us/blog/artificial-intelligence/multi-agent-system-failures.html · https://www.splunk.com/en_us/blog/artificial-intelligence/multi-agent-coordination-strategies.html · https://tianpan.co/blog/2025-10-14-why-multi-agent-llm-systems-fail
- Spec-driven development: https://www.allstacks.com/blog/spec-driven-development · https://noqta.tn/en/blog/spec-driven-development-ai-agents-software-engineering-2026
- AI code review noise and mutation testing: https://www.codeant.ai/blogs/ai-code-review-false-positives · https://tianpan.co/blog/2026-04-17-ai-code-review-at-scale-false-positive-trap · https://www.augmentcode.com/guides/mutation-testing-ai-generated-code · https://www.thoughtworks.com/radar/techniques/mutation-testing
- Progressive delivery for agent changes: https://www.getunleash.io/blog/what-are-autonomous-feature-flags · https://home.featbit.co/blogs/Advanced-Progressive-Delivery-with-Feature-Flags · https://arxiv.org/pdf/2508.11867v1
- AI SRE and incident response: https://rootly.com/blog/ai-driven-incident-response-for-sres-best-practices-use-cases-risks-and-mttr-reduction.md · https://www.softwareseni.com/what-ai-sre-agents-actually-do-in-an-incident-and-when-you-should-not-deploy-one
- Legacy modernisation with agents: https://codex.danielvaughan.com/2026/06/14/codex-cli-legacy-code-modernisation-strangler-fig-execplan-parity-testing-migration-workflows/ · https://exadel.com/news/ai-assisted-legacy-systems-modernization-with-cursor · https://developertoolkit.ai/en/workflows/build/legacy-modernization/
- Productivity measurement (METR trial, secondary): https://www.infoq.com/news/2025/07/ai-productivity · https://getdx.com/blog/metr-study-on-how-ai-affects-developer-productivity/
- Cost optimisation (vendor, indicative): https://blog.premai.io/llm-cost-optimization-8-strategies-that-cut-api-spend-by-80-2026-guide · https://www.gmicloud.ai/en/blog/llm-inference-cost-optimization-caching-batching-routing
- Durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution · https://temporal.io/blog/manetu-the-thread-is-the-workflow · https://rubinlake.com/en/technology-radar/ai-and-data-engineering/ignoring-durability-agent-workflows
- Spec-driven alternatives: https://ssojet.com/blog/best-spec-driven-development-tools · https://github.github.com/spec-kit/history.html · https://kiro.dev/docs/getting-started/first-project/ · https://github.com/Fission-AI/OpenSpec · https://developertoolkit.ai/en/ecosystem/frameworks/openspec/
- LLM council pattern: https://www.analyticsvidhya.com/blog/2025/12/llm-council-by-andrej-karpathy/ · https://virtuslab.com/blog/ai/llm-llm-council/
- Reference repos: https://github.com/agentSystemLabs/nebula · https://github.com/bmad-code-org/BMAD-METHOD
