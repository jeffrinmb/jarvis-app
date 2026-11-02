"""Signed approvals (P2.9a, ADR 0010): who really signed, and what the gate counts.

These tests use real ``ssh-keygen`` keys. Hardware security keys cannot be tested here, so the
hardware rule is tested with an injected verifier and the key-type parsing with canned output.
"""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import action, default_registry, policy, rule
from jarvis.adapters.claude_code import HookConfig, HookContext, HookMode, handle
from jarvis.adapters.claude_code.source import DirectorySource
from jarvis.cli import main
from jarvis.core import signing
from jarvis.core.approvals import APPROVAL_AGENT, make_verifier, sign_grant
from jarvis.core.approvals.signing_payload import canonical_payload, new_nonce
from jarvis.core.ledger import Ledger
from jarvis.core.policy import run_check
from jarvis.core.schemas import Decision, Event, EventKind, ProjectConfig
from jarvis.core.schemas.approval import ApprovalGrant, ApprovalScope
from jarvis.core.signing import NS_APPROVAL, Verified

pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="needs the OpenSSH client"
)

REGISTRY = default_registry()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "claude-code"


def make_key(directory: Path, name: str) -> Path:
    path = directory / name
    subprocess.run(
        [signing.ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def allowed_line(principal: str, key: Path, namespaces: str = NS_APPROVAL) -> str:
    kind, blob = key.with_suffix(".pub").read_text(encoding="utf-8").split()[:2]
    return f'{principal} namespaces="{namespaces}" {kind} {blob}\n'


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    directory = tmp_path_factory.mktemp("keys")
    return {name: make_key(directory, name) for name in ("alice", "bob", "mallory")}


@pytest.fixture
def allowed(keys):
    return allowed_line("alice", keys["alice"]) + allowed_line("bob", keys["bob"])


def pol(**approvals):
    return policy(
        human_required=[rule("src-needs-a-person", paths=["src/**"])],
        approvals={"require_signatures": True, **approvals},
    )


def src_edit(**over):
    return action(**{"paths": ["src/x.py"], **over})


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "events.jsonl")


def scope(task="T-1", hours=4, paths=("src/**",), max_diff=100):
    return ApprovalScope(
        task_id=task,
        tools=["fs.write"],
        paths=list(paths),
        max_diff_lines=max_diff,
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )


def add(ledger, grant: ApprovalGrant):
    return ledger.append(
        Event(
            kind=EventKind.APPROVAL_GRANTED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of=grant.approver,
            task_id="T-1",
            data={"grant": grant.model_dump(mode="json")},
        )
    )


def signed(ledger, request_id, key, allowed_text, claimed=None, sc=None):
    """Sign the way the command does, then optionally lie about the name."""
    grant = sign_grant(request_id, sc or scope(), key, allowed_text)
    if claimed is not None:
        grant = grant.model_copy(update={"approver": claimed})
    return add(ledger, grant), grant


def check(ledger, allowed_text, act=None, pol_=None, verifier="default", now=None, project=None):
    pol_ = pol_ or pol()
    if verifier == "default":
        verifier = make_verifier(allowed_text, pol_.approvals.approvers)
    return run_check(
        act or src_edit(), pol_, REGISTRY, project, ledger=ledger, now=now, verifier=verifier
    )


def open_request(ledger, allowed_text, **kw):
    verdict = check(ledger, allowed_text, **kw)
    assert verdict.decision == Decision.HUMAN and verdict.approval_request
    return verdict.approval_request


# ---------- the signing module ----------


def test_a_signature_names_its_signer_from_the_allowed_signers_file(keys, allowed):
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    found = signing.verify(b"data", signature, allowed, NS_APPROVAL)
    assert found is not None and found.principal == "alice" and found.key_type == "ED25519"
    assert not found.hardware


def test_a_signature_does_not_verify_after_the_data_changes(keys, allowed):
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data!", signature, allowed, NS_APPROVAL) is None


def test_a_signature_made_for_another_purpose_is_not_an_approval(keys, allowed):
    signature = signing.sign(b"data", keys["alice"], signing.NS_CHECKPOINT)
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL) is None


def test_a_key_that_is_not_listed_is_nobody(keys, allowed):
    signature = signing.sign(b"data", keys["mallory"], NS_APPROVAL)
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL) is None


def test_the_allowed_signers_file_can_limit_a_key_to_other_namespaces(keys):
    only_checkpoints = allowed_line("alice", keys["alice"], signing.NS_CHECKPOINT)
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data", signature, only_checkpoints, NS_APPROVAL) is None


@pytest.mark.parametrize("bad", ["", "not a signature", "-----BEGIN SSH SIGNATURE-----\nAAAA\n"])
def test_garbage_signatures_verify_as_nobody(keys, allowed, bad):
    assert signing.verify(b"data", bad, allowed, NS_APPROVAL) is None


def test_an_empty_allowed_signers_file_and_oversized_signatures_verify_nobody(keys, allowed):
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data", signature, "", NS_APPROVAL) is None
    assert signing.verify(b"data", signature + "x" * 9000, allowed, NS_APPROVAL) is None


def test_windows_line_endings_in_a_signature_still_verify(keys, allowed):
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL).replace("\n", "\r\n")
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL) is not None


def test_signing_needs_a_key_file_and_the_tool(keys, monkeypatch):
    with pytest.raises(signing.SigningError, match="not found"):
        signing.sign(b"x", keys["alice"].with_name("nope"), NS_APPROVAL)
    monkeypatch.setattr(signing.shutil, "which", lambda _name: None)
    with pytest.raises(signing.SigningError, match="not found on PATH"):
        signing.sign(b"x", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"x", "-----BEGIN SSH SIGNATURE-----", "a b c", NS_APPROVAL) is None


def test_the_self_test_round_trips_with_a_throwaway_key():
    assert "signs and verifies" in signing.self_test()


def test_a_security_key_type_counts_as_hardware():
    assert Verified("a", "ED25519-SK", "SHA256:x").hardware
    assert Verified("a", "ecdsa-sk", "SHA256:x").hardware
    assert not Verified("a", "ED25519", "SHA256:x").hardware
    assert not Verified("a", "RSA", "SHA256:x").hardware


def test_a_verify_answer_for_a_different_name_than_asked_is_refused(keys, allowed, monkeypatch):
    """ssh-keygen is asked about one principal; if its answer names another, it is not trusted."""
    real_run = signing._run

    def fake(args, data, timeout):
        done = real_run(args, data, timeout)
        if "verify" in args:
            done.stdout = done.stdout.replace(b"for alice", b"for root")
        return done

    monkeypatch.setattr(signing, "_run", fake)
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL) is None


def test_a_verify_answer_for_another_namespace_is_refused(keys, allowed, monkeypatch):
    real_run = signing._run

    def fake(args, data, timeout):
        done = real_run(args, data, timeout)
        if "verify" in args:
            done.stdout = done.stdout.replace(b'"jarvis-approval"', b'"something-else"')
        return done

    monkeypatch.setattr(signing, "_run", fake)
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL) is None


def test_the_key_type_is_read_from_ssh_keygens_answer(keys, allowed, monkeypatch):
    real_run = signing._run

    def fake(args, data, timeout):
        done = real_run(args, data, timeout)
        if "verify" in args:
            done.stdout = done.stdout.replace(b"ED25519 key", b"ED25519-SK key")
        return done

    monkeypatch.setattr(signing, "_run", fake)
    signature = signing.sign(b"data", keys["alice"], NS_APPROVAL)
    assert signing.verify(b"data", signature, allowed, NS_APPROVAL).hardware


# ---------- the gate ----------


def test_a_signed_approval_lets_the_call_through_and_names_the_signer(ledger, keys, allowed):
    rid = open_request(ledger, allowed)
    signed(ledger, rid, keys["alice"], allowed)
    verdict = check(ledger, allowed)
    assert verdict.decision == Decision.ALLOW and "approved by alice" in verdict.reason


def test_an_unsigned_grant_does_not_count_when_signatures_are_required(ledger, allowed):
    rid = open_request(ledger, allowed)
    add(ledger, ApprovalGrant(request_id=rid, approver="alice", via="cli", scope=scope()))
    assert check(ledger, allowed).decision == Decision.HUMAN


def test_a_grant_that_claims_another_name_than_its_key_does_not_count(ledger, keys, allowed):
    """Alice signs, but the grant says bob approved. The name must be the key's own."""
    rid = open_request(ledger, allowed)
    signed(ledger, rid, keys["alice"], allowed, claimed="bob")
    assert check(ledger, allowed).decision == Decision.HUMAN


def test_a_signature_from_an_unlisted_key_does_not_count_whatever_name_it_claims(
    ledger, keys, allowed
):
    rid = open_request(ledger, allowed)
    nonce, sc = new_nonce(), scope()
    signature = signing.sign(canonical_payload(rid, nonce, sc), keys["mallory"], NS_APPROVAL)
    add(
        ledger,
        ApprovalGrant(
            request_id=rid, approver="alice", via="cli", scope=sc, nonce=nonce, signature=signature
        ),
    )
    assert check(ledger, allowed).decision == Decision.HUMAN


def test_changing_a_signed_scope_afterwards_breaks_the_signature(ledger, keys, allowed):
    rid = open_request(ledger, allowed)
    _, grant = signed(ledger, rid, keys["alice"], allowed, sc=scope(paths=("src/x.py",)))
    widened = grant.scope.model_copy(update={"paths": ["**/*.py", "src/**"]})
    add(ledger, grant.model_copy(update={"scope": widened}))
    assert check(ledger, allowed, act=src_edit(paths=["lib/y.py"])).decision == Decision.HUMAN


def test_without_a_verifier_or_an_allowed_signers_file_nothing_counts(ledger, keys, allowed):
    rid = open_request(ledger, allowed)
    signed(ledger, rid, keys["alice"], allowed)
    assert check(ledger, allowed, verifier=None).decision == Decision.HUMAN
    assert check(ledger, None).decision == Decision.HUMAN  # no file


def test_only_listed_approvers_count_by_the_name_of_their_key(ledger, keys, allowed):
    listed = pol(approvers=["bob"])
    rid = open_request(ledger, allowed, pol_=listed)
    signed(ledger, rid, keys["alice"], allowed)
    assert check(ledger, allowed, pol_=listed).decision == Decision.HUMAN
    signed(ledger, rid, keys["bob"], allowed)
    assert check(ledger, allowed, pol_=listed).decision == Decision.ALLOW


def test_a_key_listed_under_an_agents_name_never_counts(ledger, keys):
    sneaky = allowed_line("builder-1", keys["alice"])
    rid = open_request(ledger, sneaky)
    signed(ledger, rid, keys["alice"], sneaky)
    assert check(ledger, sneaky).decision == Decision.HUMAN


def test_the_gate_refuses_an_agent_even_if_a_custom_verifier_names_one(ledger, keys, allowed):
    """Belt and braces: whatever a verifier returns, an agent's name is never an approver."""
    rid = open_request(ledger, allowed)
    signed(ledger, rid, keys["alice"], allowed)

    def agent_verifier(_grant):
        return Verified("builder-1", "ED25519", "SHA256:x")

    assert check(ledger, allowed, verifier=agent_verifier).decision == Decision.HUMAN


def test_expiry_and_the_lifetime_cap_still_apply_to_signed_grants(ledger, keys, allowed):
    rid = open_request(ledger, allowed)
    signed(ledger, rid, keys["alice"], allowed, sc=scope(hours=1))
    later = datetime.now(UTC) + timedelta(hours=2)
    assert check(ledger, allowed, now=later).decision == Decision.HUMAN
    other = src_edit(task_id="T-2", paths=["src/y.py"])  # another task: the grant above is not its
    rid2 = open_request(ledger, allowed, act=other)
    signed(ledger, rid2, keys["alice"], allowed, sc=scope("T-2", hours=100, paths=("src/y.py",)))
    assert check(ledger, allowed, act=other).decision == Decision.HUMAN  # past the 24 h cap


def test_a_rejected_signed_grant_cannot_be_brought_back_by_copying_it(ledger, keys, allowed):
    rid = open_request(ledger, allowed)
    _, grant = signed(ledger, rid, keys["alice"], allowed)
    assert check(ledger, allowed).decision == Decision.ALLOW
    ledger.append(
        Event(
            kind=EventKind.APPROVAL_DENIED,
            agent_id=APPROVAL_AGENT,
            on_behalf_of="alice",
            data={"request_id": rid, "approver": "alice"},
        )
    )
    assert check(ledger, allowed).decision == Decision.HUMAN
    add(ledger, grant)  # an identical copy of the earlier signed grant
    assert check(ledger, allowed).decision == Decision.HUMAN
    signed(ledger, rid, keys["alice"], allowed)  # a fresh signature is a fresh decision
    assert check(ledger, allowed).decision == Decision.ALLOW


# ---------- four-eyes and hardware keys ----------


NO_GO = ProjectConfig(
    project="demo",
    mode="brownfield",
    stack="python",
    tiers={"builder": "standard"},
    tools_enabled=["*"],
    environments=["dev"],
    no_go_paths=["billing/**"],
    data_policy={"allowed_models": ["any"]},
)


def billing():
    return action(paths=["billing/x.py"])


def test_a_no_go_zone_needs_two_different_signers(ledger, keys, allowed):
    rid = open_request(ledger, allowed, act=billing(), project=NO_GO)
    sc = scope(paths=("billing/**",))
    signed(ledger, rid, keys["alice"], allowed, sc=sc)
    signed(ledger, rid, keys["alice"], allowed, sc=sc)  # the same person again
    assert check(ledger, allowed, act=billing(), project=NO_GO).decision == Decision.HUMAN
    signed(ledger, rid, keys["bob"], allowed, sc=sc)
    assert check(ledger, allowed, act=billing(), project=NO_GO).decision == Decision.ALLOW


def test_the_policy_can_require_hardware_keys_for_a_no_go_zone(ledger, keys, allowed):
    strict = pol(hardware_key_for_four_eyes=True)
    rid = open_request(ledger, allowed, act=billing(), project=NO_GO, pol_=strict)
    sc = scope(paths=("billing/**",))
    signed(ledger, rid, keys["alice"], allowed, sc=sc)
    signed(ledger, rid, keys["bob"], allowed, sc=sc)

    def verifier(hardware_for):
        real = make_verifier(allowed, [])

        def verify(grant):
            found = real(grant)
            if found is None:
                return None
            kind = "ED25519-SK" if found.principal in hardware_for else "ED25519"
            return Verified(found.principal, kind, found.fingerprint)

        return verify

    ask = {"act": billing(), "project": NO_GO, "pol_": strict}
    assert check(ledger, allowed, verifier=verifier(set()), **ask).decision == Decision.HUMAN
    assert check(ledger, allowed, verifier=verifier({"alice"}), **ask).decision == Decision.HUMAN
    assert (
        check(ledger, allowed, verifier=verifier({"alice", "bob"}), **ask).decision
        == Decision.ALLOW
    )


def test_hardware_keys_are_not_required_unless_the_policy_says_so(ledger, keys, allowed):
    rid = open_request(ledger, allowed, act=billing(), project=NO_GO)
    sc = scope(paths=("billing/**",))
    signed(ledger, rid, keys["alice"], allowed, sc=sc)
    signed(ledger, rid, keys["bob"], allowed, sc=sc)
    assert check(ledger, allowed, act=billing(), project=NO_GO).decision == Decision.ALLOW


# ---------- the command line ----------


def write_files(tmp_path, allowed_text, require=True):
    allowed_file = tmp_path / "allowed-signers"
    allowed_file.write_text(allowed_text, encoding="utf-8")
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "version: v\nhuman_required:\n  - id: src\n    description: d\n    when:\n"
        "      paths: ['src/**']\n"
        f"approvals:\n  require_signatures: {str(require).lower()}\n",
        encoding="utf-8",
    )
    return allowed_file, policy_file


def cli(ledger, allowed_file, policy_file, *args):
    return (
        main(
            [
                "approvals",
                args[0],
                "--ledger",
                str(ledger.path),
                "--policy",
                str(policy_file),
                "--allowed-signers",
                str(allowed_file),
                *args[1:],
            ]
        )
        if args[0] == "approve"
        else main(
            [
                "approvals",
                args[0],
                "--ledger",
                str(ledger.path),
                "--policy",
                str(policy_file),
                *args[1:],
            ]
        )
    )


def test_approve_signs_with_the_key_and_the_gate_counts_it(ledger, keys, allowed, tmp_path, capsys):
    allowed_file, policy_file = write_files(tmp_path, allowed)
    pol_ = pol()
    rid = run_check(src_edit(), pol_, REGISTRY, ledger=ledger).approval_request
    capsys.readouterr()
    code = cli(ledger, allowed_file, policy_file, "approve", rid, "--key", str(keys["alice"]))
    assert code == 0 and "approved" in capsys.readouterr().out
    grant = [r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_granted"][-1]
    assert grant["on_behalf_of"] == "alice" and grant["data"]["grant"]["signature"]
    assert check(ledger, allowed).decision == Decision.ALLOW


def test_a_signed_approval_is_named_by_its_key_not_by_by(ledger, keys, allowed, tmp_path):
    allowed_file, policy_file = write_files(tmp_path, allowed)
    rid = run_check(src_edit(), pol(), REGISTRY, ledger=ledger).approval_request
    assert (
        cli(
            ledger,
            allowed_file,
            policy_file,
            "approve",
            rid,
            "--key",
            str(keys["bob"]),
            "--by",
            "carol",
        )
        == 0
    )
    grant = [r["event"] for r in ledger.records() if r["event"]["kind"] == "approval_granted"][-1]
    assert grant["data"]["grant"]["approver"] == "bob"


def test_the_command_refuses_to_approve_without_a_listed_key(
    ledger, keys, allowed, tmp_path, capsys
):
    allowed_file, policy_file = write_files(tmp_path, allowed)
    rid = run_check(src_edit(), pol(), REGISTRY, ledger=ledger).approval_request
    assert cli(ledger, allowed_file, policy_file, "approve", rid, "--by", "alice") == 2
    assert "requires signed approvals" in capsys.readouterr().err
    assert (
        cli(ledger, allowed_file, policy_file, "approve", rid, "--key", str(keys["mallory"])) == 2
    )
    assert "not listed" in capsys.readouterr().err
    assert (
        cli(ledger, tmp_path / "missing", policy_file, "approve", rid, "--key", str(keys["alice"]))
        == 2
    )
    assert "allowed-signers file not found" in capsys.readouterr().err
    assert (
        cli(ledger, allowed_file, policy_file, "approve", rid, "--key", str(tmp_path / "nokey"))
        == 2
    )


def test_unsigned_approvals_still_work_when_the_policy_does_not_require_signatures(
    ledger, allowed, tmp_path
):
    allowed_file, policy_file = write_files(tmp_path, allowed, require=False)
    rid = run_check(
        src_edit(), policy(human_required=[rule("s", paths=["src/**"])]), REGISTRY, ledger=ledger
    ).approval_request
    assert cli(ledger, allowed_file, policy_file, "approve", rid, "--by", "alice") == 0


def test_agentctl_check_verifies_with_the_allowed_signers_file(
    ledger, keys, allowed, tmp_path, monkeypatch, capsys
):
    allowed_file, policy_file = write_files(tmp_path, allowed)
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(REGISTRY.model_dump_json(), encoding="utf-8")
    act = src_edit().model_dump_json()
    argv = [
        "check",
        "--action",
        "-",
        "--policy",
        str(policy_file),
        "--registry",
        str(registry_file),
        "--ledger",
        str(ledger.path),
        "--allowed-signers",
        str(allowed_file),
        "--state-dir",
        str(tmp_path / "state"),
    ]
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(act))
    assert main(argv) == 11  # human
    rid = json.loads(capsys.readouterr().out)["approval_request"]
    assert cli(ledger, allowed_file, policy_file, "approve", rid, "--key", str(keys["alice"])) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(act))
    assert main(argv) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(act))
    allowed_file.write_text("", encoding="utf-8")  # take the key away
    assert main(argv) == 11


# ---------- the hook ----------


@pytest.fixture
def project(tmp_path, allowed):
    root = tmp_path / "proj"
    (root / ".agents").mkdir(parents=True)
    for name in ("policy.yaml", "tool-registry.yaml"):
        shutil.copy(EXAMPLES / name, root / ".agents" / name)
    (root / ".agents" / "allowed-signers").write_text(allowed, encoding="utf-8")
    return root


def hook_ctx(project, tmp_path, mode=HookMode.ENFORCE, **cfg):
    source = DirectorySource(project) if mode is HookMode.ENFORCE else None
    return HookContext(
        HookConfig(on_behalf_of="alice", **cfg), mode, project, tmp_path / "state", source
    )


def edit_ci(project):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": str(project),
        "tool_name": "Edit",
        "tool_use_id": "tu_1",
        "tool_input": {
            "file_path": str(project / ".github" / "workflows" / "ci.yml"),
            "old_string": "a",
            "new_string": "b",
        },
    }


def test_enforce_mode_refuses_a_policy_that_does_not_require_signatures(project, tmp_path):
    policy_file = project / ".agents" / "policy.yaml"
    policy_file.write_text(
        policy_file.read_text(encoding="utf-8").replace(
            "require_signatures: true", "require_signatures: false"
        ),
        encoding="utf-8",
    )
    result = handle(edit_ci(project), hook_ctx(project, tmp_path))
    assert result.exit_code == 2 and "require_signatures" in (result.stderr or "")


def test_the_hook_lets_a_signed_approval_through_end_to_end(project, keys, tmp_path, capsys):
    context = hook_ctx(project, tmp_path, non_interactive=True)
    denied = handle(edit_ci(project), context)
    reason = json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    request = next(word for word in reason.split() if word.startswith("req-")).rstrip(".,]`:")
    ledger_path = project / ".agents" / "ledger" / "events.jsonl"
    code = main(
        [
            "approvals",
            "approve",
            request,
            "--ledger",
            str(ledger_path),
            "--policy",
            str(project / ".agents" / "policy.yaml"),
            "--allowed-signers",
            str(project / ".agents" / "allowed-signers"),
            "--key",
            str(keys["alice"]),
        ]
    )
    assert code == 0
    allowed_now = handle(edit_ci(project), context)
    assert (allowed_now.exit_code, allowed_now.stdout) == (0, None)


def test_the_hook_does_not_count_a_signature_from_a_key_the_protected_file_does_not_list(
    project, keys, tmp_path
):
    context = hook_ctx(project, tmp_path, non_interactive=True)
    handle(edit_ci(project), context)
    ledger = Ledger(project / ".agents" / "ledger" / "events.jsonl")
    request = next(
        r["event"]["data"]["request"]["request_id"]
        for r in ledger.records()
        if r["event"]["kind"] == "approval_requested"
    )
    # Mallory signs a grant that names alice; only alice's and bob's keys are in the file.
    only_mallory = allowed_line("alice", keys["mallory"])
    rogue = sign_grant(request, scope_for_request(ledger, request), keys["mallory"], only_mallory)
    add(ledger, rogue.model_copy(update={"approver": "alice"}))
    result = handle(edit_ci(project), context)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def scope_for_request(ledger, request_id):
    for record in ledger.records():
        data = (record["event"].get("data") or {}).get("request")
        if data and data["request_id"] == request_id:
            proposed = data["proposed_scope"]
            return ApprovalScope(
                task_id=data["task_id"],
                tools=proposed["tools"],
                paths=proposed["paths"],
                max_diff_lines=proposed["max_diff_lines"],
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
    raise AssertionError("request not found")


# ---------- doctor ----------


def test_doctor_checks_signatures_in_enforce_mode(project, monkeypatch, capsys):
    import jarvis.hook_cli as hook_cli

    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    args = ["hook", "doctor", "--mode", "enforce", "--policy-source", "directory"]
    assert main([*args, "--policy-dir", str(project)]) == 0
    out = capsys.readouterr().out
    assert "signs and verifies" in out and "allowed signers" in out

    policy_file = project / ".agents" / "policy.yaml"
    policy_file.write_text(
        policy_file.read_text(encoding="utf-8").replace(
            "require_signatures: true", "require_signatures: false"
        ),
        encoding="utf-8",
    )
    assert main([*args, "--policy-dir", str(project)]) == 1
    assert "require_signatures" in capsys.readouterr().out


def test_doctor_notes_a_missing_allowed_signers_file(project, monkeypatch, capsys):
    import jarvis.hook_cli as hook_cli

    (project / ".agents" / "allowed-signers").unlink()
    monkeypatch.setattr(hook_cli, "_claude_version_text", lambda: "2.1.300")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    args = ["hook", "doctor", "--mode", "enforce", "--policy-source", "directory"]
    assert main([*args, "--policy-dir", str(project)]) == 0
    assert "no approval can be counted" in capsys.readouterr().out
