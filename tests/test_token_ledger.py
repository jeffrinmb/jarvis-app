"""Token and cost ledger (P2.2)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.cli import main
from jarvis.core.ledger import Ledger
from jarvis.core.schemas import EventKind
from jarvis.core.tokens import (
    PriceTable,
    load_price_table,
    model_call_event,
    record_model_call,
    render_tokens,
    summarize_tokens,
)

EXAMPLE_PRICES = Path(__file__).resolve().parent.parent / "examples" / "prices.yaml"


def prices():
    return PriceTable.model_validate(
        {
            "as_of": "2026-01-01",
            "source": "test",
            "models": {
                "m1": {
                    "input_per_mtok": 2.0,
                    "output_per_mtok": 10.0,
                    "cached_input_per_mtok": 0.2,
                },
                "m2": {"input_per_mtok": 1.0, "output_per_mtok": 4.0},
            },
        }
    )


def call(model="m1", **over):
    base = {
        "agent_id": "agent",
        "on_behalf_of": "alice",
        "model": model,
        "tokens_in": 1000,
        "tokens_out": 500,
        "tokens_cached": 4000,
        "prices": prices(),
    }
    return {**base, **over}


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


# ---------- prices ----------


def test_cost_uses_the_cached_rate_for_cached_input():
    # 1000*2 + 4000*0.2 + 500*10 = 2000 + 800 + 5000 = 7800 per million
    assert prices().cost("m1", 1000, 500, 4000) == pytest.approx(0.0078)


def test_cached_tokens_fall_back_to_the_input_rate_which_overstates():
    # m2 has no cached rate: 1000*1 + 4000*1 + 500*4 = 7000 per million
    assert prices().cost("m2", 1000, 500, 4000) == pytest.approx(0.007)


def test_an_unknown_model_has_no_cost_rather_than_zero():
    assert prices().cost("nope", 1, 1) is None


def test_a_price_table_needs_a_date_a_source_and_models_and_no_negative_rates():
    with pytest.raises(ValidationError):
        PriceTable.model_validate({"source": "x", "models": {"m": {"input_per_mtok": 1}}})
    with pytest.raises(ValidationError):
        PriceTable.model_validate({"as_of": "2026-01-01", "source": "x", "models": {}})
    with pytest.raises(ValidationError):
        PriceTable.model_validate(
            {
                "as_of": "2026-01-01",
                "source": "x",
                "models": {"m": {"input_per_mtok": -1, "output_per_mtok": 1}},
            }
        )
    with pytest.raises(ValidationError):  # unknown fields are rejected like every schema
        PriceTable.model_validate(
            {"as_of": "2026-01-01", "source": "x", "models": {}, "currency": "EUR"}
        )


def test_the_shipped_example_price_table_is_valid_and_says_it_is_not_real():
    table = load_price_table(EXAMPLE_PRICES)
    assert "example-model" in table.models
    assert "not real prices" in EXAMPLE_PRICES.read_text(encoding="utf-8")


# ---------- recording ----------


def test_a_row_records_model_tokens_cost_and_outcome(ledger):
    record_model_call(ledger, **call(task_id="t1", stage="build", outcome="ok"))
    (record,) = ledger.records()
    event = record["event"]
    assert event["kind"] == EventKind.MODEL_CALL.value
    assert event["model"]["name"] == "m1"
    assert event["usage"] == {
        "tokens_in": 1000,
        "tokens_out": 500,
        "tokens_cached": 4000,
        "cost_usd": pytest.approx(0.0078),
    }
    assert (event["task_id"], event["stage"], event["outcome"]) == ("t1", "build", "ok")
    assert event["data"] == {"cost_source": "computed"}
    assert ledger.verify().ok


def test_a_provider_reported_cost_wins_over_the_table():
    event = model_call_event(**call(cost_usd=1.25))
    assert event.usage.cost_usd == 1.25 and event.data["cost_source"] == "reported"


def test_an_unpriced_model_is_marked_unknown_not_free():
    event = model_call_event(**call(model="mystery"))
    assert event.usage.cost_usd == 0.0 and event.data["cost_source"] == "unknown"
    assert model_call_event(**call(prices=None)).data["cost_source"] == "unknown"


def test_a_zero_reported_cost_is_still_reported():
    assert model_call_event(**call(cost_usd=0.0)).data["cost_source"] == "reported"


def test_bad_counts_are_rejected():
    with pytest.raises(ValidationError):
        model_call_event(**call(tokens_in=-1))
    with pytest.raises(ValidationError):
        model_call_event(**call(cost_usd=-0.5))
    with pytest.raises(ValidationError):
        model_call_event(**call(model=""))


# ---------- report ----------


def fill(ledger):
    record_model_call(ledger, **call(task_id="t1"))
    record_model_call(ledger, **call(task_id="t1", model="m2"))
    record_model_call(ledger, **call(task_id="t2", model="mystery", tokens_cached=0))
    record_model_call(ledger, **call(agent_id="other", stage="review"))


def test_report_totals_and_groups_by_model(ledger):
    fill(ledger)
    summary = summarize_tokens(ledger.records(), "model")
    total = summary["total"]
    assert total["calls"] == 4
    assert total["tokens_in"] == 4000 and total["tokens_out"] == 2000
    assert total["tokens_cached"] == 12000
    assert total["cost_usd"] == pytest.approx(0.0078 * 2 + 0.007)
    by_key = {r["key"]: r for r in summary["rows"]}
    assert by_key["m1"]["calls"] == 2 and by_key["m2"]["calls"] == 1
    assert by_key["mystery"]["calls_without_cost"] == 1
    assert total["calls_without_cost"] == 1


def test_the_cache_hit_rate_is_cached_over_all_input(ledger):
    record_model_call(ledger, **call(tokens_in=1000, tokens_cached=3000))
    assert summarize_tokens(ledger.records())["total"]["cache_hit_rate"] == 0.75


def test_other_groupings(ledger):
    fill(ledger)
    assert {r["key"] for r in summarize_tokens(ledger.records(), "task")["rows"]} == {
        "t1",
        "t2",
        "(none)",
    }
    assert {r["key"] for r in summarize_tokens(ledger.records(), "agent")["rows"]} == {
        "agent",
        "other",
    }
    assert {r["key"] for r in summarize_tokens(ledger.records(), "stage")["rows"]} == {
        "(none)",
        "review",
    }
    (day,) = summarize_tokens(ledger.records(), "day")["rows"]
    assert len(day["key"]) == 10


def test_other_event_kinds_and_rows_without_usage_are_ignored(ledger):
    from jarvis.core.schemas import Event

    ledger.append(Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="b"))
    ledger.append(Event(kind=EventKind.MODEL_CALL, agent_id="a", on_behalf_of="b"))
    assert summarize_tokens(ledger.records())["total"]["calls"] == 0


def test_an_unknown_grouping_is_an_error():
    with pytest.raises(ValueError):
        summarize_tokens([], "colour")


def test_the_text_report_warns_when_the_total_is_a_lower_bound(ledger):
    fill(ledger)
    text = render_tokens(summarize_tokens(ledger.records()))
    assert "lower bound" in text and "unpriced" in text
    assert render_tokens(summarize_tokens([])) == "No model calls recorded."


# ---------- command line ----------


def test_record_then_report_from_the_command_line(tmp_path, capsys):
    path = str(tmp_path / "l.jsonl")
    base = [
        "tokens",
        "record",
        "--ledger",
        path,
        "--agent",
        "a",
        "--on-behalf-of",
        "alice",
        "--model",
        "example-model",
        "--tokens-in",
        "1000",
        "--tokens-out",
        "200",
    ]
    assert main([*base, "--prices", str(EXAMPLE_PRICES)]) == 0
    assert "computed" in capsys.readouterr().out
    assert main([*base]) == 0
    assert "unknown" in capsys.readouterr().out
    assert main(["tokens", "report", "--ledger", path, "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total"]["calls"] == 2 and summary["total"]["calls_without_cost"] == 1
    assert main(["tokens", "report", "--ledger", path, "--by", "agent"]) == 0
    assert "WARNING" in capsys.readouterr().out


def test_the_command_line_rejects_bad_input_and_a_missing_or_tampered_ledger(tmp_path, capsys):
    path = tmp_path / "l.jsonl"
    assert main(["tokens", "report", "--ledger", str(path)]) == 2
    args = ["tokens", "record", "--ledger", str(path), "--agent", "a", "--on-behalf-of", "b"]
    assert main([*args, "--model", "m", "--tokens-in", "-5", "--tokens-out", "1"]) == 2
    assert main([*args, "--model", "m", "--tokens-in", "1", "--tokens-out", "1"]) == 0
    path.write_bytes(path.read_bytes().replace(b'"tokens_in":1', b'"tokens_in":9', 1))
    capsys.readouterr()
    assert main(["tokens", "report", "--ledger", str(path)]) == 1
    assert "fails verification" in capsys.readouterr().err


def test_a_bad_price_table_is_an_input_error(tmp_path):
    bad = tmp_path / "prices.yaml"
    bad.write_text("as_of: nope\n", encoding="utf-8")
    argv = [
        "tokens",
        "record",
        "--ledger",
        str(tmp_path / "l.jsonl"),
        "--agent",
        "a",
        "--on-behalf-of",
        "b",
        "--model",
        "m",
        "--tokens-in",
        "1",
        "--tokens-out",
        "1",
        "--prices",
        str(bad),
    ]
    assert main(argv) == 2
