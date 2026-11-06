# ADR 0014: Rotating the ledger into archived segments

- Status: Accepted (segments built; the carried summary and the fast gate path are the next step)
- Date: 2026-11-06
- Deciders: project owner (chose rotation over a summary file or an in-memory index), Claude Code (implementation)

## Context

Tasks P2.4a (an index so checks do not scan the whole ledger) and P2.1b (retention and archival without breaking the chain). Every hook call reads and folds the whole ledger: it parses each record and counts usage, diff totals and approvals from them. Measured on this machine with a warm in-process decision and a synthetic ledger of realistic decisions:

| Records | File | Time per decision |
|---|---|---|
| 100 | 0.1 MB | 19 ms |
| 2,000 | 1.7 MB | 44 ms |
| 10,000 | 8.7 MB | 166 ms |
| 40,000 | 34.6 MB | 631 ms |

The maintainer's real ledger gained about 1,000 records in a day of use, so the hook would pass 150 ms in about ten days and approach its 15 second timeout in a year or two. Roughly half the time is parsing JSON and half is the Python loops over the parsed records.

## Decision

1. **Rotate the ledger into segments.** The active file is moved whole into `<name>.archive/`, named for the sequence numbers it holds (`0000000000-0000001999.jsonl`). Nothing is deleted, edited or compressed. A new active file starts with a `ledger_rotated` record.
2. **One chain across segments.** The `ledger_rotated` record takes the next sequence number and the last record's hash as its `prev_hash`, so the hash chain is unbroken. Its data pins the archived file: its name, its SHA-256, its first and last sequence numbers and the last record's hash.
3. **Readers see one ledger.** `records()`, `verify()` and `head()` read every segment in order; `active_records()` reads only the file being written (for the fast path). Verification walks all segments and also fails on: an archive missing, renamed or swapped (the name must match what it holds), an unexpected file in the archive folder, a segment that does not start with a valid link record, and a changed archive (the pinned SHA-256 catches even edits the record hashes do not cover, such as whitespace).
4. **Crash safe.** Rotation is an atomic rename followed by writing the link record. A crash between them leaves archives and no active file, which is a valid chain; the next write notices and writes the link first. Rotation refuses to replace an existing archive name.
5. **Signed checkpoints keep working.** A checkpoint's ledger id is the hash of the very first record, which is now in the first archive; `ledger_id()` reads through the archive, so ids and old checkpoints are unchanged, and a truncation after a rotation is still caught.
6. **When it happens.** `agentctl ledger rotate` (refuses a ledger that fails verification) and `agentctl ledger status` are the manual path. A `Ledger` built with `rotate_bytes` rotates by itself when a write finds the active file at least that big; the hook will set it in the next step.

## What this does not do yet

Segments alone do not make the gate faster, because the gate still reads every segment. The next step (still P2.4a) makes the `ledger_rotated` record **carry a summary** of what the gate needs from older history, so a call reads only the active file:

- per task: number of counted decisions, total diff lines, and the last few decisions (fingerprint, verdict, time) for the loop breaker, denial window and burst rule;
- ledger-wide: the number of counted decisions, the set of tools seen, and calls per day;
- every approval event and every grant id used, verbatim (they are rare), so approval state folds the same way.

The summary sits inside a chained record, so it is tamper-evident, and `verify` will recompute it from the archives and fail on a difference. The gate will use it only when the policy's windows fit inside what the summary keeps; otherwise it reads every segment, as today. The test that matters is equivalence: for randomised ledgers, a decision from the summary plus the active file must equal the decision from the full history, reason text included.

## Consequences

- Readers that want the whole history (`verify`, `tokens report`, OpenTelemetry export, checkpoints, the report) read all segments and are as slow as before; only the hook's own path will get faster.
- The token-capture step on `Stop` looks for message ids it already recorded across the whole ledger; it will need a cheap scan of the archives (a substring filter before parsing) so a session that spans a rotation does not record its messages twice. This is part of the next step.
- Retention: archives are never deleted by the framework, so "keep for at least six months" is met by leaving them alone. Access control on the archive folder is the operating system's, and compression, off-machine copies and a deletion policy are not built (P2.1b stays open for them).
- A new event kind, `ledger_rotated`, is added (an additive schema change recorded in the changelog). A consumer that does not know the kind must ignore it, and the framework's own consumers do.

## Alternatives considered

- **A derived summary file beside the ledger.** Simpler to start, but it is a second piece of state an agent could edit without breaking the ledger's chain. Carrying the summary inside a chained record removes that.
- **State kept in memory in the hook server only.** No tamper surface, but only server mode gets faster; the in-process path and `agentctl check` stay slow.
- **Reading only the recent part of the ledger.** Cheap, but a per-task total or a "tool never seen before" check that depends on older history would quietly become more lenient. Safety checks must not change with file size.
