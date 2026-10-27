# ADR 0001: Language, storage, tooling and code layout

- Status: Accepted
- Date: 2026-10-27
- Deciders: project owner (choices made when starting `feat/skeleton`)

## Context

Charter section 12 lists the core language, storage and layout as open decisions (tasks P0.3 and P1.1). Phases 1 and 2 need these fixed before schemas and the audit ledger are built.

## Decision

- **Language:** Python 3.12 or newer. Development uses 3.13.
- **Packaging and tooling:** `uv` for environments and the lockfile, `ruff` for lint and format, `pytest` for tests, `hatchling` as the build backend.
- **Schemas:** pydantic v2 models are the source of truth. JSON Schema files in `schemas/` are generated from them and checked for drift by a test.
- **Storage:** an append-only JSONL ledger with a SHA-256 hash chain for audit evidence; SQLite is reserved for indexes and state in later phases.
- **Layout:** Python code lives in `src/jarvis/` (`jarvis.core.*`, `jarvis.adapters`). `packs/` (content) and `viz/` (UI) stay top-level because they are not Python packages. This replaces the literal top-level `core/` and `adapters/` folders shown in the first draft of the charter.
- **CLI:** a single `agentctl` entry point built on the standard library `argparse`, to keep dependencies minimal.

## Consequences

- One tool (`uv`) for contributors and CI, with a committed lockfile for reproducible installs.
- No generic top-level package named `core` that could clash with other installed packages.
- Schema changes are visible in review because generated JSON Schemas are committed.
- The ledger needs a single-writer lock (a lock file) until a stronger mechanism is chosen.
- A compiled daemon can still be added later behind the same schemas and CLI.

## Alternatives considered

- pip with venv: no extra install, but no lockfile without more tooling.
- Top-level `core/` and `adapters/` packages exactly as in the charter: matches the text, but generic names risk import clashes.
- Dataclasses or hand-written validators instead of pydantic: fewer dependencies, but more code to keep in sync with JSON Schema.
