import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from jarvis.core.ledger import (
    GENESIS_HASH,
    Ledger,
    LedgerCorruptError,
    LedgerLockError,
    canonical_json,
    compute_hash,
)
from jarvis.core.schemas import Event, EventKind

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


def make_event(i: int = 0, **over) -> Event:
    base = {
        "kind": EventKind.NOTE,
        "agent_id": "builder-1",
        "on_behalf_of": "alice",
        "ts": T0 + timedelta(seconds=i),
        "outcome": f"ok-{i}",
        "data": {"i": i},
    }
    base.update(over)
    return Event(**base)


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger" / "events.jsonl")


def fill(ledger: Ledger, n: int = 5) -> None:
    for i in range(n):
        ledger.append(make_event(i))


def read_lines(ledger: Ledger) -> list[bytes]:
    return ledger.path.read_bytes().split(b"\n")[:-1]


def write_lines(ledger: Ledger, lines: list[bytes]) -> None:
    ledger.path.write_bytes(b"\n".join(lines) + b"\n")


def rehash(record: dict) -> dict:
    record["hash"] = compute_hash(record["seq"], record["prev_hash"], record["event"])
    return record


# ---------- appending and chaining ----------


def test_first_record_uses_genesis(ledger):
    record = ledger.append(make_event())
    assert record["seq"] == 0
    assert record["prev_hash"] == GENESIS_HASH
    assert record["hash"] == compute_hash(0, GENESIS_HASH, record["event"])


def test_records_chain_together(ledger):
    fill(ledger, 4)
    records = list(ledger.records())
    assert [r["seq"] for r in records] == [0, 1, 2, 3]
    for prev, cur in zip(records, records[1:], strict=False):
        assert cur["prev_hash"] == prev["hash"]


def test_verify_ok_and_head(ledger):
    fill(ledger, 3)
    result = ledger.verify()
    assert result.ok and result.records == 3 and result.head_seq == 2
    assert ledger.head() == (2, result.head_hash)


def test_missing_or_empty_ledger_verifies_with_no_records(ledger):
    assert ledger.verify().ok
    assert ledger.verify().records == 0
    assert ledger.head() is None
    ledger.path.parent.mkdir(parents=True)
    ledger.path.write_bytes(b"")
    assert ledger.verify().ok


def test_unicode_data_round_trips(ledger):
    ledger.append(make_event(data={"note": "résumé ✓ 日本語"}))
    assert ledger.verify().ok
    assert next(ledger.records())["event"]["data"]["note"] == "résumé ✓ 日本語"


def test_canonical_json_is_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_file_has_no_carriage_returns(ledger):
    fill(ledger, 3)
    assert b"\r" not in ledger.path.read_bytes()


# ---------- tamper detection ----------


def test_modified_event_is_detected(ledger):
    fill(ledger, 5)
    lines = read_lines(ledger)
    record = json.loads(lines[2])
    record["event"]["outcome"] = "changed"
    lines[2] = json.dumps(record).encode()
    write_lines(ledger, lines)
    result = ledger.verify()
    assert not result.ok and result.error_seq == 2 and "hash mismatch" in result.error


def test_modified_and_rehashed_record_breaks_the_next_link(ledger):
    fill(ledger, 5)
    lines = read_lines(ledger)
    record = json.loads(lines[2])
    record["event"]["outcome"] = "changed"
    lines[2] = json.dumps(rehash(record)).encode()
    write_lines(ledger, lines)
    result = ledger.verify()
    assert not result.ok and result.error_seq == 3 and "prev_hash" in result.error


def test_deleted_record_is_detected(ledger):
    fill(ledger, 5)
    lines = read_lines(ledger)
    del lines[2]
    write_lines(ledger, lines)
    result = ledger.verify()
    assert not result.ok and "sequence" in result.error


def test_reordered_records_are_detected(ledger):
    fill(ledger, 4)
    lines = read_lines(ledger)
    lines[1], lines[2] = lines[2], lines[1]
    write_lines(ledger, lines)
    assert not ledger.verify().ok


def test_truncation_needs_an_anchor(ledger):
    fill(ledger, 5)
    anchor = ledger.head()
    write_lines(ledger, read_lines(ledger)[:3])
    assert ledger.verify().ok  # the file alone cannot show records are missing
    result = ledger.verify(anchor=anchor)
    assert not result.ok and "anchor not found" in result.error


def test_consistent_rewrite_needs_an_anchor(ledger):
    fill(ledger, 3)
    anchor = ledger.head()
    lines = read_lines(ledger)
    last = json.loads(lines[-1])
    last["event"]["outcome"] = "rewritten"
    lines[-1] = json.dumps(rehash(last)).encode()
    write_lines(ledger, lines)
    assert ledger.verify().ok  # self-consistent, so only the anchor reveals it
    result = ledger.verify(anchor=anchor)
    assert not result.ok and "anchor mismatch" in result.error


def test_anchor_still_valid_after_growth(ledger):
    fill(ledger, 3)
    anchor = ledger.head()
    ledger.append(make_event(9))
    assert ledger.verify(anchor=anchor).ok


def test_partial_final_line_is_detected(ledger):
    fill(ledger, 2)
    with open(ledger.path, "ab") as fh:
        fh.write(b'{"seq": 2, "pre')
    result = ledger.verify()
    assert not result.ok and "partial" in result.error


def test_invalid_json_and_blank_lines_are_detected(ledger):
    fill(ledger, 3)
    lines = read_lines(ledger)
    lines[1] = b"not json"
    write_lines(ledger, lines)
    assert "not valid JSON" in ledger.verify().error
    lines[1] = b""
    write_lines(ledger, lines)
    assert not ledger.verify().ok


def test_unexpected_fields_are_detected(ledger):
    fill(ledger, 2)
    lines = read_lines(ledger)
    record = json.loads(lines[1])
    record["extra"] = 1
    lines[1] = json.dumps(record).encode()
    write_lines(ledger, lines)
    assert "unexpected fields" in ledger.verify().error


def test_event_that_fails_the_schema_is_detected_even_with_a_valid_hash(ledger):
    fill(ledger, 1)
    prev = json.loads(read_lines(ledger)[0])["hash"]
    bad_event = {"kind": "not-a-kind", "agent_id": "a", "on_behalf_of": "h"}
    record = rehash({"seq": 1, "prev_hash": prev, "event": bad_event, "hash": ""})
    with open(ledger.path, "ab") as fh:
        fh.write(canonical_json(record) + b"\n")
    result = ledger.verify()
    assert not result.ok and result.error_seq == 1 and "schema" in result.error


def test_head_refuses_a_broken_chain(ledger):
    fill(ledger, 3)
    lines = read_lines(ledger)
    del lines[1]
    write_lines(ledger, lines)
    with pytest.raises(LedgerCorruptError):
        ledger.head()


# ---------- append safety ----------


def test_append_refuses_a_modified_tail(ledger):
    fill(ledger, 3)
    lines = read_lines(ledger)
    record = json.loads(lines[-1])
    record["event"]["outcome"] = "changed"
    lines[-1] = json.dumps(record).encode()
    write_lines(ledger, lines)
    with pytest.raises(LedgerCorruptError):
        ledger.append(make_event(9))


def test_append_refuses_a_partial_tail(ledger):
    fill(ledger, 2)
    with open(ledger.path, "ab") as fh:
        fh.write(b"half")
    with pytest.raises(LedgerCorruptError):
        ledger.append(make_event(9))


def test_append_handles_records_larger_than_a_read_block(ledger):
    ledger.append(make_event(0, data={"blob": "x" * 20000}))
    ledger.append(make_event(1))
    assert ledger.verify().records == 2


def test_stale_lock_times_out_with_a_helpful_error(tmp_path):
    ledger = Ledger(tmp_path / "events.jsonl", lock_timeout=0.1)
    (tmp_path / "events.jsonl.lock").write_text("999999")
    with pytest.raises(LedgerLockError, match="stale"):
        ledger.append(make_event())


def test_lock_file_is_removed_after_append(ledger):
    ledger.append(make_event())
    assert not ledger.path.with_name(ledger.path.name + ".lock").exists()


def test_concurrent_appends_keep_the_chain_intact(tmp_path):
    path = tmp_path / "events.jsonl"

    def work(worker: int) -> None:
        local = Ledger(path, lock_timeout=30)
        for i in range(10):
            local.append(make_event(i, agent_id=f"agent-{worker}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, range(4)))

    result = Ledger(path).verify()
    assert result.ok and result.records == 40
    assert [r["seq"] for r in Ledger(path).records()] == list(range(40))
