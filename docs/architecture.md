# Architecture diagrams

Companion to the [framework charter](framework-charter.md). The charter is the source of truth; if a diagram and the charter disagree, the charter wins. Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub.

## 1. System architecture

Clients talk to one orchestrator service. Harnesses never talk to models or tools directly: everything passes through the gateways, and everything leaves evidence in the ledger.

```mermaid
flowchart TB
  subgraph CLIENTS["Clients"]
    CLI["CLI"]
    WEB["Web UI and decision cards"]
    TL["2D timeline"]
    W3D["3D world"]
    CHAT["Chat notifications"]
  end

  subgraph CORE["Orchestrator service"]
    SM["Task state machine"]
    POL["Policy engine"]
    COU["Council"]
    GATE["Gates and attributed approvals"]
    BUD["Budgets and loop breakers"]
  end

  subgraph ADAPT["Harness adapters"]
    H1["Claude Code"]
    H2["Other harnesses"]
    EXT["Git, CI, ticketing"]
  end

  subgraph GW["Gateways"]
    MG["Model gateway: tiers, routing, fallbacks"]
    TG["Tool gateway: registry, authz, MCP, credentials, sanitiser, egress"]
  end

  subgraph MEM["Memory"]
    INBOX["Inbox write gate"]
    VAULT["Vault: Markdown wiki"]
    GRAPH["Graph index"]
    CTX["Context assembler with token cap"]
  end

  subgraph EVID["Evidence"]
    LEDGER["Hash-chained audit log"]
    OTEL["OpenTelemetry traces"]
    TOK["Token and cost ledger"]
  end

  CLIENTS --> CORE
  CORE --> ADAPT
  ADAPT --> GW
  CORE --> MEM
  GW --> EVID
  CORE --> EVID
  MEM --> ADAPT
```

## 2. Major decisions only: how a decision is routed

The policy engine decides what is "major". Risk comes from evidence (paths, diff size, tools, taint), never from the agent's own claim. A task waiting on a human is parked; other tasks keep running.

```mermaid
flowchart LR
  A["Agent decision"] --> P{"Policy engine"}
  P -->|"matches auto_approve"| AUTO["Proceed and log"]
  P -->|"matches council_review"| C["Council reviews"]
  P -->|"matches human_required"| H["Park task and send decision card"]
  P -->|"matches never"| D["Deny"]
  C -->|"unanimous approve"| AUTO
  C -->|"split or block"| H
  H --> HU["Human decides"]
  HU -->|"approve"| AUTO
  HU -->|"reject"| R["Reject and record precedent"]
  AUTO --> L["Audit log"]
  R --> L
  D --> L
```

## 3. Delivery pipeline and gates

Gates apply only where policy says so. Low-risk changes flow through without a human; production deploy is human-gated in v1.

```mermaid
flowchart LR
  I["Intake"] --> S["Spec"]
  S --> G1{"Human gate: scope"}
  G1 --> PL["Plan"]
  PL --> B["Build in worktree"]
  B --> V["Verify: tests, SAST, mutation"]
  V --> RV["Review: AI plus human"]
  RV --> G2{"Human gate: merge"}
  G2 --> M["Merge"]
  M --> DP["Progressive deploy"]
  DP --> G3{"Human gate: production"}
  G3 --> O["Operate"]
  O --> LN["Learn"]
  LN -.-> S
```

## 4. Memory: LLM-wiki on Markdown

Agents write only to the inbox. Nothing becomes trusted memory without validation, and memory is data, never instructions.

```mermaid
flowchart LR
  RAW["Raw sources: code, tickets, PRs, logs"] --> ING["Ingest job"]
  ING --> INB["Inbox: unvalidated"]
  INB --> VAL{"Validate"}
  VAL -->|"promote"| WIKI["Wiki: decisions, lessons, components, playbooks"]
  VAL -->|"reject"| DISC["Discard"]
  WIKI --> IDX["Graph and index"]
  IDX --> ASM["Context assembler with token cap"]
  ASM --> AG["Agents"]
  AG -->|"proposals"| INB
  WIKI --> LINT["Lint job: stale, contradictory, orphan"]
  LINT --> INB
```

## 5. Learning loop (feedback around the model, no weight updates)

Prefer a deterministic fix over prose: CI check, then hook or lint rule, then policy rule, then playbook, then wiki note.

```mermaid
flowchart LR
  ACT["Act"] --> OUT["Outcome signals: CI, human decisions, rework, incidents, cost"]
  OUT --> REF["Reflect with a cheap model"]
  REF --> CAND["Candidate lesson"]
  CAND --> VAL{"Validated?"}
  VAL -->|"yes"| FIX["Apply the most deterministic fix available"]
  VAL -->|"no"| HOLD["Hold in inbox"]
  FIX --> ACT
```

## 6. Injection-resistant tool access

Untrusted content is read by a quarantined agent that can only return schema-validated output. The gateway tightens when a session is tainted.

```mermaid
flowchart LR
  U["Untrusted content: tickets, web, third-party code"] --> Q["Quarantined reader agent"]
  Q -->|"schema-validated output only"| PA["Privileged agent"]
  PA --> TG["Tool gateway: authz, taint check, egress allowlist"]
  TG -->|"reversible and within plan"| ACT["Action"]
  TG -->|"irreversible, or outbound while tainted"| HG["Human gate"]
  HG -->|"approved"| ACT
  ACT --> LG["Audit log"]
```

## 7. Roadmap phases

```mermaid
flowchart LR
  P0["P0 Decisions and setup"] --> P1["P1 Schemas and skeleton"]
  P1 --> P2["P2 Spine: ledger, policy, approvals"]
  P2 --> P3["P3 Memory"]
  P3 --> P4["P4 Pipeline end to end"]
  P4 --> P5["P5 Brownfield"]
  P5 --> P6["P6 Evals and red team"]
  P6 --> P7["P7 Council, router, viz, packaging"]
```

Evals and red-team work starts in phase 2 and is formalised in phase 6. See the [TODO list](TODO.md) for tasks.
