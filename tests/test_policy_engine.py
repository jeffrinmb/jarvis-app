import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from helpers import action, default_registry, policy, rule, variant
from jarvis.core.policy import SEVERITY, combine, evaluate
from jarvis.core.schemas import (
    Action,
    Decision,
    Policy,
    ProjectConfig,
    RiskClass,
    ToolRegistry,
    load_tool_registry,
    load_yaml_model,
)
from jarvis.core.schemas.verdict import RuleMatch

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
REGISTRY = default_registry()


def run(act=None, pol=None, project=None, prior=0, registry=REGISTRY):
    return evaluate(act or action(), pol or policy(), registry, project, prior_diff_lines=prior)


def ids(verdict):
    return {m.rule_id for m in verdict.matches}


# ---------- basic outcomes ----------


def test_auto_rule_allows_matching_action():
    v = run()
    assert v.decision is Decision.ALLOW
    assert ids(v) == {"docs"}
    assert v.risk is RiskClass.LOW
    assert v.policy_version == "test-1"


def test_default_applies_when_nothing_matches():
    v = run(action(paths=["src/app.py"]))
    assert v.decision is Decision.HUMAN
    assert "no rule matched" in v.reason


def test_policy_default_deny_is_respected():
    v = run(action(paths=["src/app.py"]), policy(default="deny"))
    assert v.decision is Decision.DENY


def test_evaluate_is_deterministic():
    assert run() == run()


# ---------- builtin guardrails ----------


def test_unregistered_tool_is_denied():
    v = run(action(tool_id="mystery.tool"))
    assert v.decision is Decision.DENY
    assert ids(v) == {"builtin:unregistered-tool"}
    assert v.risk is None


def test_tool_lookup_is_exact_not_a_glob():
    v = run(action(tool_id="fs.*"))
    assert v.decision is Decision.DENY


def test_role_not_allowed_is_denied():
    v = run(action(role="analyst"))
    assert v.decision is Decision.DENY
    assert "builtin:role-not-allowed" in ids(v)


def test_environment_not_enabled_for_tool_is_denied():
    v = run(action(environment="staging"))
    assert v.decision is Decision.DENY
    assert "builtin:environment-not-enabled" in ids(v)


def test_production_is_always_human():
    act = action(
        role="release-engineer",
        tool_id="deploy.production",
        environment="prod",
        paths=[],
        diff_lines=None,
    )
    v = run(act, policy(auto_approve=[rule("anything", environments=["prod"])]))
    assert v.decision is Decision.HUMAN
    assert "builtin:production" in ids(v)


def _registry_with_fs_write_approval(approval: str) -> ToolRegistry:
    tools = [t for t in REGISTRY.tools if t.id != "fs.write"]
    fs_write = next(t for t in REGISTRY.tools if t.id == "fs.write")
    return ToolRegistry(tools=[*tools, variant(fs_write, approval=approval)])


def test_guardrails_hold_even_for_unvalidated_entries_with_plain_strings():
    """model_copy skips validation and leaves plain strings; the floor must still apply."""
    fs_write = next(t for t in REGISTRY.tools if t.id == "fs.write")
    sloppy = fs_write.model_copy(update={"approval": "human"})
    registry = ToolRegistry.model_construct(tools=[sloppy])
    assert run(registry=registry).decision is Decision.HUMAN


def test_registry_approval_is_a_floor_the_policy_cannot_lower():
    human = run(registry=_registry_with_fs_write_approval("human"))
    assert human.decision is Decision.HUMAN
    assert "registry:approval" in ids(human)
    council = run(registry=_registry_with_fs_write_approval("council"))
    assert council.decision is Decision.COUNCIL


# ---------- rule semantics ----------


def test_auto_rule_requires_all_paths_to_match():
    v = run(action(paths=["docs/a.md", "src/app.py"]))
    assert v.decision is Decision.HUMAN
    assert "docs" not in ids(v)


def test_auto_rule_with_paths_needs_at_least_one_path():
    assert run(action(paths=[])).decision is Decision.HUMAN


def test_auto_rule_without_a_path_condition_does_not_need_paths():
    pol = policy(auto_approve=[rule("dev-low", environments=["dev"], risk_at_most="low")])
    assert run(action(paths=[]), pol).decision is Decision.ALLOW


def test_restrictive_rule_matches_any_path():
    pol = policy(human_required=[rule("auth", paths=["**/auth/**"])])
    v = run(action(paths=["docs/a.md", "src/auth/login.py"]), pol)
    assert v.decision is Decision.HUMAN
    assert "auth" in ids(v)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("never", Decision.DENY),
        ("human_required", Decision.HUMAN),
        ("council_review", Decision.COUNCIL),
    ],
)
def test_restrictive_categories_override_an_auto_rule(category, expected):
    pol = policy(**{category: [rule("r", paths=["docs/**"])]})
    v = run(pol=pol)
    assert v.decision is expected
    assert "docs" in ids(v)  # the auto rule matched too, and lost


def test_most_restrictive_category_wins():
    pol = policy(
        council_review=[rule("c", paths=["docs/**"])],
        human_required=[rule("h", paths=["docs/**"])],
        never=[rule("n", paths=["docs/**"])],
    )
    assert run(pol=pol).decision is Decision.DENY


def test_risk_range_conditions():
    pol = policy(auto_approve=[rule("mid", risk_at_least="medium", risk_at_most="high")])
    assert run(action(tool_id="fs.write"), pol).decision is Decision.HUMAN  # low risk
    web = action(tool_id="web.post")
    assert run(web, pol).decision is not Decision.ALLOW  # council floor, still not allowed


def test_tool_glob_condition():
    pol = policy(human_required=[rule("any-fs", tools=["fs.*"])])
    assert run(pol=pol).decision is Decision.HUMAN


# ---------- diff size ----------


def test_cumulative_size_counts_prior_changes():
    within = run(action(diff_lines=10), prior=190)  # 200 is exactly the limit
    assert within.total_diff_lines == 200
    assert within.decision is Decision.ALLOW
    over = run(action(diff_lines=11), prior=190)  # 201 exceeds it, so the auto rule must not match
    assert over.total_diff_lines == 201
    assert over.decision is Decision.HUMAN


def test_unknown_size_never_satisfies_a_max_limit():
    v = run(action(diff_lines=None))
    assert v.diff_size_known is False
    assert v.decision is Decision.HUMAN


def test_unknown_size_is_fine_for_read_only_tools():
    pol = policy(auto_approve=[rule("reads", tools=["git.read"], max_diff_lines=10)])
    v = run(action(tool_id="git.read", diff_lines=None, paths=[]), pol)
    assert v.diff_size_known is True
    assert v.decision is Decision.ALLOW


def test_min_diff_lines_matches_when_size_is_unknown():
    pol = policy(council_review=[rule("big", min_diff_lines=100)])
    assert run(action(diff_lines=None), pol).decision is Decision.COUNCIL
    assert run(action(diff_lines=50), pol).decision is Decision.ALLOW
    assert run(action(diff_lines=150), pol).decision is Decision.COUNCIL


# ---------- paths ----------


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "C:\\Windows\\x", "docs/../../x", ""])
def test_suspicious_paths_go_to_a_human_even_under_a_catch_all_rule(bad):
    pol = policy(auto_approve=[rule("all", paths=["**"])])
    v = run(action(paths=[bad]), pol)
    assert v.decision is Decision.HUMAN
    assert "builtin:suspicious-path" in ids(v)


def test_paths_are_normalised_before_matching():
    for raw in ["./docs/a.md", "docs\\a.md", "DOCS/A.MD", "docs//a.md"]:
        assert run(action(paths=[raw])).decision is Decision.ALLOW, raw


# ---------- project config ----------


def _project(**over):
    base = {
        "project": "p",
        "mode": "greenfield",
        "stack": "python",
        "tiers": {"builder": "standard"},
        "environments": ["dev"],
        "tools_enabled": ["fs.*", "git.read"],
        "no_go_paths": ["infra/prod/**", "**/.env"],
        "data_policy": {"allowed_models": ["m"]},
    }
    return ProjectConfig(**{**base, **over})


def test_project_enabled_tools_are_enforced():
    v = run(project=_project(tools_enabled=["git.read"]))
    assert v.decision is Decision.DENY
    assert "builtin:tool-not-enabled" in ids(v)


def test_project_with_no_enabled_tools_denies_everything():
    assert run(project=_project(tools_enabled=[])).decision is Decision.DENY


def test_environment_must_be_enabled_for_the_project():
    fs_write = next(t for t in REGISTRY.tools if t.id == "fs.write")
    registry = ToolRegistry(tools=[variant(fs_write, environments=["dev", "staging"])])
    v = run(action(environment="staging"), project=_project(), registry=registry)
    assert v.decision is Decision.DENY
    assert "builtin:environment-not-in-project" in ids(v)


def test_no_go_path_requires_a_human():
    v = run(action(paths=["infra/prod/main.tf"]), project=_project())
    assert v.decision is Decision.HUMAN
    assert "builtin:no-go-path" in ids(v)


def test_no_go_matching_is_case_insensitive():
    v = run(action(paths=["config/.ENV"]), project=_project())
    assert v.decision is Decision.HUMAN


# ---------- taint and the lethal trifecta ----------


def test_untrusted_content_in_session_or_input_taints():
    session = {"trust_labels_seen": ["untrusted"]}
    assert run(action(session=session)).tainted is True
    assert run(action(input_trust=["untrusted"])).tainted is True
    assert run(action(input_trust=["trusted", "internal"])).tainted is False


def test_taint_rule_can_be_written_in_a_policy():
    pol = policy(human_required=[rule("tainted-writes", tainted=True)])
    tainted = action(input_trust=["untrusted"])
    assert run(tainted, pol).decision is Decision.HUMAN
    assert run(action(), pol).decision is Decision.ALLOW


def test_tainted_session_using_an_irreversible_tool_goes_to_a_human_even_if_policy_allows():
    pol = policy(auto_approve=[rule("dev", environments=["dev"])])
    act = action(
        tool_id="danger.irreversible",
        paths=[],
        diff_lines=None,
        session={"trust_labels_seen": ["untrusted"]},
    )
    v = run(act, pol)
    assert v.decision is Decision.HUMAN
    assert "builtin:tainted-irreversible-or-outbound" in ids(v)


def test_lethal_trifecta_goes_to_a_human_not_just_the_council():
    pol = policy(auto_approve=[rule("dev", environments=["dev"])])
    act = action(
        tool_id="web.post",
        paths=[],
        diff_lines=None,
        session={"trust_labels_seen": ["untrusted"], "data_classes_seen": ["restricted"]},
    )
    v = run(act, pol)
    assert v.trifecta is True
    assert v.decision is Decision.HUMAN  # the registry floor alone would only say council
    assert "builtin:lethal-trifecta" in ids(v)


@pytest.mark.parametrize(
    ("tool_id", "session", "input_trust"),
    [
        # untrusted + private data, but no outbound communication
        ("fs.write", {"trust_labels_seen": ["untrusted"], "data_classes_seen": ["restricted"]}, []),
        # private data + outbound, but nothing untrusted
        ("web.post", {"data_classes_seen": ["restricted"]}, []),
        # untrusted + outbound, but only internal data (mail.send handles internal data)
        ("mail.send", {"trust_labels_seen": ["untrusted"], "data_classes_seen": ["internal"]}, []),
    ],
)
def test_two_of_three_is_not_the_trifecta(tool_id, session, input_trust):
    act = action(tool_id=tool_id, paths=[], diff_lines=0, session=session, input_trust=input_trust)
    assert run(act).trifecta is False


def test_the_tool_own_data_class_counts_as_private_data():
    act = action(tool_id="web.post", paths=[], diff_lines=0, input_trust=["untrusted"])
    assert run(act).trifecta is True  # web.post is registered as handling confidential data


# ---------- combine ----------


def _match(effect):
    return RuleMatch(rule_id=f"r-{effect.value}", category="builtin", effect=effect, reason="x")


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_combine_never_allows_unless_an_allow_matched_and_nothing_restrictive_did(size):
    effects = list(Decision)
    for combo in itertools.chain.from_iterable(
        itertools.combinations_with_replacement(effects, n) for n in range(size + 1)
    ):
        matches = [_match(e) for e in combo]
        decision, _ = combine(matches, Decision.HUMAN)
        restrictive = [e for e in combo if e is not Decision.ALLOW]
        if restrictive:
            assert decision is max(restrictive, key=SEVERITY.__getitem__)
        elif Decision.ALLOW in combo:
            assert decision is Decision.ALLOW
        else:
            assert decision is Decision.HUMAN  # default


# ---------- schemas that feed the engine ----------


def test_action_has_no_way_to_declare_its_own_risk():
    with pytest.raises(ValidationError):
        Action(
            agent_id="a", on_behalf_of="h", role="r", tool_id="t", environment="dev", risk="read"
        )


# ---------- the shipped examples behave as documented ----------


def _examples():
    pol = load_yaml_model(EXAMPLES / "policy.yaml", Policy)
    reg = load_tool_registry(EXAMPLES / "tool-registry.yaml")
    act = Action.model_validate(json.loads((EXAMPLES / "action.json").read_text("utf-8")))
    return pol, reg, act


def test_example_action_is_allowed_by_the_example_policy():
    pol, reg, act = _examples()
    v = evaluate(act, pol, reg)
    assert v.decision is Decision.ALLOW
    assert "docs-and-tests" in {m.rule_id for m in v.matches}


def test_example_policy_sends_ci_changes_to_a_human():
    pol, reg, act = _examples()
    v = evaluate(variant(act, paths=[".github/workflows/ci.yml"]), pol, reg)
    assert v.decision is Decision.HUMAN
    assert "ci-config-changes" in {m.rule_id for m in v.matches}


def test_example_policy_never_lets_agents_touch_secrets():
    pol, reg, act = _examples()
    v = evaluate(variant(act, paths=["config/.env"]), pol, reg)
    assert v.decision is Decision.DENY


def test_example_dev_rule_does_not_approve_high_risk_tools():
    pol, reg, act = _examples()
    outbound = variant(act, tool_id="web.post", paths=[], diff_lines=0)
    v = evaluate(outbound, pol, reg)
    assert v.decision is not Decision.ALLOW
