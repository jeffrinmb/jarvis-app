import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from jarvis.core.schemas import (
    SCHEMAS,
    Decision,
    DecisionCard,
    Event,
    EventKind,
    Policy,
    ProjectConfig,
    RiskClass,
    Task,
    TiersConfig,
    ToolEntry,
    WikiFrontmatter,
    export_json_schemas,
    json_schema,
    load_tool_registry,
    load_yaml_model,
    parse_note,
    split_frontmatter,
)

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


# ---------- examples validate (the charter samples are real, tested files) ----------


def test_example_project_validates():
    cfg = load_yaml_model(EXAMPLES / "project.yaml", ProjectConfig)
    assert cfg.mode.value == "brownfield"


def test_example_policy_validates():
    policy = load_yaml_model(EXAMPLES / "policy.yaml", Policy)
    assert policy.default is Decision.HUMAN
    assert len(policy.human_required) == 4


def test_example_tiers_validate():
    cfg = load_yaml_model(EXAMPLES / "tiers.yaml", TiersConfig)
    assert len(cfg.tiers) == 3


def test_example_tool_registry_validates():
    registry = load_tool_registry(EXAMPLES / "tool-registry.yaml")
    assert {t.id for t in registry.tools} >= {"git.read", "deploy.production"}


def test_example_wiki_note_validates():
    text = (EXAMPLES / "wiki" / "migrations-need-rollback.md").read_text(encoding="utf-8")
    fm, body = parse_note(text)
    assert fm.status.value == "validated"
    assert body.startswith("# Migrations need a rollback script")


# ---------- event ----------


def test_event_minimal_and_defaults():
    e = Event(kind=EventKind.NOTE, agent_id="builder-1", on_behalf_of="alice")
    assert len(e.event_id) == 32
    assert e.ts.tzinfo is not None
    assert e.data == {}


def test_event_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        Event(
            kind=EventKind.NOTE,
            agent_id="a",
            on_behalf_of="h",
            ts=datetime(2026, 1, 1, 12, 0),  # no tzinfo
        )


def test_event_rejects_unknown_field_and_blank_agent():
    with pytest.raises(ValidationError):
        Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", surprise=1)
    with pytest.raises(ValidationError):
        Event(kind=EventKind.NOTE, agent_id="", on_behalf_of="h")


def test_event_rejects_bad_trace_and_negative_usage():
    with pytest.raises(ValidationError):
        Event(kind=EventKind.NOTE, agent_id="a", on_behalf_of="h", trace_id="xyz")
    with pytest.raises(ValidationError):
        Event(
            kind=EventKind.MODEL_CALL,
            agent_id="a",
            on_behalf_of="h",
            usage={"tokens_in": -1},
        )


def test_risk_class_ordering():
    assert RiskClass.READ.rank < RiskClass.MEDIUM.rank < RiskClass.CRITICAL.rank


# ---------- task ----------


def _task(**over):
    base = {
        "id": "T-1",
        "title": "Add rollback",
        "owner": "alice",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(over)
    return Task(**base)


def test_task_valid():
    assert _task().state.value == "created"


def test_parked_task_needs_reason():
    with pytest.raises(ValidationError):
        _task(state="parked")
    assert _task(state="parked", parked_reason="needs approval").state.value == "parked"


def test_done_task_needs_human():
    with pytest.raises(ValidationError):
        _task(state="done")
    assert _task(state="done", done_by="alice").done_by == "alice"


def test_task_updated_before_created_rejected():
    with pytest.raises(ValidationError):
        _task(updated_at=NOW - timedelta(minutes=1))


# ---------- decision card ----------


def _card(**over):
    base = {
        "id": "D-1",
        "task_id": "T-1",
        "decision": "Add the new dependency?",
        "options": [
            {"id": "yes", "label": "Add it", "tradeoffs": "Faster, new supply-chain risk"},
            {"id": "no", "label": "Write it", "tradeoffs": "Slower, no new dependency"},
        ],
        "recommendation": "no",
        "confidence": 0.7,
        "evidence": [{"kind": "diff", "ref": "pr/12", "summary": "40 lines"}],
        "risk": {
            "reversibility": "easy",
            "blast_radius": "service",
            "risk_class": "medium",
        },
        "created_at": NOW,
    }
    base.update(over)
    return DecisionCard(**base)


def test_decision_card_valid_defaults_to_stay_parked():
    assert _card().default_on_timeout == "stay_parked"


def test_decision_card_never_auto_approves_on_timeout():
    with pytest.raises(ValidationError):
        _card(default_on_timeout="approve")


def test_decision_card_option_rules():
    with pytest.raises(ValidationError):
        _card(options=[{"id": "yes", "label": "x", "tradeoffs": "y"}])  # needs two
    with pytest.raises(ValidationError):
        _card(recommendation="maybe")
    dup = [
        {"id": "a", "label": "x", "tradeoffs": "y"},
        {"id": "a", "label": "z", "tradeoffs": "w"},
    ]
    with pytest.raises(ValidationError):
        _card(options=dup, recommendation="a")


def test_decision_card_needs_evidence_and_valid_expiry():
    with pytest.raises(ValidationError):
        _card(evidence=[])
    with pytest.raises(ValidationError):
        _card(expires_at=NOW - timedelta(hours=1))


def test_council_reasons_capped_at_five():
    vote = {"member": "security", "vote": "block", "confidence": 0.9, "reasons": list("abcdef")}
    with pytest.raises(ValidationError):
        _card(council_votes=[vote])


# ---------- policy ----------


def test_policy_default_cannot_allow():
    with pytest.raises(ValidationError):
        Policy(version="1", default="allow")


def test_policy_rejects_duplicate_rule_ids():
    rule = {"id": "same", "description": "x"}
    with pytest.raises(ValidationError):
        Policy(version="1", auto_approve=[rule], never=[rule])


def test_policy_rejects_bad_rule_id():
    with pytest.raises(ValidationError):
        Policy(version="1", never=[{"id": "Bad Id", "description": "x"}])


# ---------- tool registry ----------


def _tool(**over):
    base = {
        "id": "t.x",
        "source": "native",
        "version": "1",
        "risk": "low",
        "effect": "write-reversible",
        "data_class": "internal",
        "allowed_roles": ["builder"],
        "environments": ["dev"],
        "approval": "auto",
        "owner": "platform",
    }
    base.update(over)
    return ToolEntry(**base)


def test_tool_valid():
    assert _tool().approval.value == "auto"


@pytest.mark.parametrize("effect", ["write-irreversible", "external-comms"])
def test_irreversible_or_external_tools_cannot_auto_approve(effect):
    with pytest.raises(ValidationError):
        _tool(effect=effect)


def test_critical_tools_cannot_auto_approve():
    with pytest.raises(ValidationError):
        _tool(risk="critical")


def test_prod_tools_need_human_approval():
    with pytest.raises(ValidationError):
        _tool(environments=["prod"])
    assert _tool(environments=["prod"], approval="human").approval.value == "human"


def test_registry_rejects_duplicate_ids(tmp_path):
    entry = (
        "- id: dup\n  source: native\n  version: '1'\n  risk: low\n  effect: read-only\n"
        "  data_class: internal\n  allowed_roles: [a]\n  environments: [dev]\n"
        "  approval: auto\n  owner: o\n"
    )
    path = tmp_path / "reg.yaml"
    path.write_text(entry + entry, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_tool_registry(path)


# ---------- wiki ----------


def test_validated_note_needs_trust():
    with pytest.raises(ValidationError):
        WikiFrontmatter(type="lesson", status="validated", confidence=0.5, created="2026-10-27")
    ok = WikiFrontmatter(
        type="lesson",
        status="validated",
        trust="test-verified",
        confidence=0.5,
        created="2026-10-27",
    )
    assert ok.trust.value == "test-verified"


def test_inbox_note_defaults():
    fm = WikiFrontmatter(type="lesson", confidence=0.2, created="2026-10-27")
    assert fm.status.value == "inbox"
    assert fm.trust.value == "agent-inferred"


def test_frontmatter_parsing_errors():
    with pytest.raises(ValueError):
        split_frontmatter("no frontmatter here")
    with pytest.raises(ValueError):
        split_frontmatter("---\ntype: lesson\n")
    data, body = split_frontmatter("---\r\ntype: lesson\r\n---\r\nbody\r\n")
    assert data == {"type": "lesson"}
    assert body == "body\n"


# ---------- project config ----------


def _project(**over):
    base = {
        "project": "p",
        "mode": "greenfield",
        "stack": "python",
        "tiers": {"builder": "standard"},
        "environments": ["dev"],
        "data_policy": {"allowed_models": ["m"]},
    }
    base.update(over)
    return ProjectConfig(**base)


def test_agents_never_get_prod():
    with pytest.raises(ValidationError):
        _project(environments=["dev", "prod"])


def test_brownfield_requires_no_go_paths():
    with pytest.raises(ValidationError):
        _project(mode="brownfield")
    assert _project(mode="brownfield", no_go_paths=["infra/**"]).mode.value == "brownfield"


def test_tiers_config_requires_all_tiers():
    with pytest.raises(ValidationError):
        TiersConfig(tiers={"cheap": ["a"], "standard": ["b"]})
    with pytest.raises(ValidationError):
        TiersConfig(tiers={"cheap": ["a"], "standard": ["b"], "strong": []})


# ---------- JSON Schema export and drift ----------


def test_every_schema_exports(tmp_path):
    written = export_json_schemas(tmp_path)
    assert {p.name for p in written} == {f"{n}.schema.json" for n in SCHEMAS}
    for path in written:
        assert json.loads(path.read_text(encoding="utf-8"))["type"] == "object"


def test_committed_schemas_match_models():
    """If this fails, run: uv run agentctl schema export --out schemas"""
    schema_dir = ROOT / "schemas"
    for name in SCHEMAS:
        committed = json.loads((schema_dir / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert committed == json_schema(name), f"{name}.schema.json is out of date"
