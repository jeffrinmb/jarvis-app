# ADR 0014: Rotating the ledger into archived segments

- Status: Accepted and built (segments, then the carried summary and the fast gate path)
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

## The carried summary (built)

Segments alone do not make the gate faster, because it would still read every segment. So the `ledger_rotated` record **carries a summary** of everything before it, and the gate starts from that and folds only the active file. One module (`jarvis.core.ledger.summary`) does the folding for the gate, for rotation and for `verify`, so there is one implementation, and the gate's usage and approval code was rebuilt on top of it.

What the summary holds:

- per task, for ever: the number of counted decisions, total diff lines, and the number of decisions (a few dozen bytes each);
- per task, only while it was active in the last two days and only the last 50: the decisions themselves (fingerprint, verdict, time), which the loop breaker, denial window and burst rule read;
- ledger-wide: the number of counted decisions, the tools used, calls per day, every approval event verbatim (they are rare) and every approval grant id that was used, so a spent single-use approval stays spent.

**It never changes a decision; it only decides how much to read.** For a task whose recent list is incomplete (more than 50 decisions, or idle for days), `exact_for` checks whether the policy's loop, denial and burst windows still fit inside what was kept. When they do not, the gate folds the whole ledger, exactly as before. A link record without a usable summary (for example one written before summaries existed) is treated the same way.

**Tamper-evident.** The summary is inside a chained record, and `verify` recomputes it from the records before each link, replaying the same steps rotation took, and fails on any difference, including a summary edited with the hash chain rebuilt to match.

**Equivalence is tested, not argued.** The old counting code is kept in the tests as a reference and the new fold is compared with it on random histories; a summary taken at a random cut plus the later records is compared with the whole history for many tasks and windows; and the same random sequence of real gate decisions (tools, paths, roles, approvals, budgets, loop and anomaly rules) is run on a ledger that rotates every few records and on one that never rotates, and every verdict, reason text included, must be identical. Eleven mutations of the summary and gate code each make a test fail, one of which (a spent single-use approval coming back to life after a rotation) first survived and now has its own test.

**Measured** (warm, in-process decision, synthetic ledger with decisions 30 s apart, this machine, which varies 2 to 3 times in speed between hours):

| Records | Before | After (rotation at 1 MB) |
|---|---|---|
| 100 | 19 to 41 ms | 35 ms |
| 2,000 | 44 to 83 ms | 40 ms |
| 10,000 | 166 to 274 ms | 41 ms |
| 40,000 | 631 to 1,027 ms | 46 ms |

The hook rotates by default when a write finds the active file at 1,000,000 bytes (`ledger_rotate_bytes` in the adapter config; unset turns it off). Whole-history readers (`verify`, `tokens report`, the OpenTelemetry export, the checkpoint check, `hook report`) still read every segment.

**Found while measuring.** My first benchmark wrote all its events at the same instant, and the gate then correctly fell back to reading everything, because with 60 or more calls inside the burst window it cannot be sure from a short list. A real task needs more than 50 decisions within `burst_seconds` (60 in the example policy) for that to happen, and then the answer is still right, only slower.

**Other details.** Rotation is only an optimisation, so if the rename fails (Windows refuses to rename a file another process has open) the write goes through and the next write tries again. Token capture on `Stop` looks for message ids already recorded by reading only the lines that mention `model_call`, across every segment, so a session that spans a rotation is not recorded twice.

## Consequences

- Readers that want the whole history (`verify`, `tokens report`, OpenTelemetry export, checkpoints, the report) read all segments and are as slow as before; only the hook's own path got faster.
- The summary grows with the number of tasks (a few dozen bytes each, so well under a megabyte a year at today's pace), because per-task totals are kept for ever: dropping one would let a resumed task start from zero. Compacting very old tasks is future work.
- `verify` now also folds every record to recompute the summaries, so it is somewhat slower.
- Retention: archives are never deleted by the framework, so "keep for at least six months" is met by leaving them alone. Access control on the archive folder is the operating system's, and compression, off-machine copies and a deletion policy are not built (P2.1b stays open for them).
- A new event kind, `ledger_rotated`, is added (an additive schema change recorded in the changelog). A consumer that does not know the kind must ignore it, and the framework's own consumers do.

## Alternatives considered

- **A derived summary file beside the ledger.** Simpler to start, but it is a second piece of state an agent could edit without breaking the ledger's chain. Carrying the summary inside a chained record removes that.
- **State kept in memory in the hook server only.** No tamper surface, but only server mode gets faster; the in-process path and `agentctl check` stay slow.
- **Reading only the recent part of the ledger.** Cheap, but a per-task total or a "tool never seen before" check that depends on older history would quietly become more lenient. Safety checks must not change with file size.
