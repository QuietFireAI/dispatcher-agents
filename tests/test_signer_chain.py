"""Signer-registry enforcement + hash-chained audit log (2026-07-11 decision).

Every test here is a doctrine line as executable behavior:
- the audit chain breaks loudly on edit/delete/reorder
- authority classification covers identity `.authority` lanes (the hardcoded
  set alone was a real defect - these tests pin the fix)
- the signer registry arms fail-closed and rejects fail-closed
- unarmed is audited, never silent
"""
import json
import pytest

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub, is_authority
from dispatcher.signer_registry import SignerRegistry
from dispatcher.signatures import HmacSigner
from dispatcher.priority import SidingScheduler

KEY = b"k" * 32


def _routes(tmp_path):
    doc = {"version": "0.1", "vertical": "t", "routes": [
        {"intent": "pay.authority", "senders": ["human"], "receivers": ["10"]},
        {"intent": "note.record", "senders": ["01"], "receivers": ["13"]},
    ]}
    p = tmp_path / "routes.json"
    p.write_text(json.dumps(doc))
    return Routes(str(p))


def _env(intent="pay.authority", frm="human", to="10", signer=None):
    prov = {"source": "t", "captured_at": "now", "verbatim_available": True}
    if signer is not None:
        prov["signer"] = signer
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id="c1", payload={"amt": "1"},
                    provenance=prov)


def _registry():
    return SignerRegistry([{"intent": "pay.authority", "signer_login": "jeff",
                            "idp": "okta", "mfa_required": True,
                            "effective": "2026-07-11", "revoked": None}])


STAMP_OK = {"signer_login": "jeff", "idp_session_ref": "sess-1", "mfa": True}


# ---------------------------------------------------------------- chain
def test_chain_verifies_and_resumes(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    log.append("x", {"n": 1}); log.append("x", {"n": 2})
    assert log.verify_chain() == {"ok": True, "entries": 2, "break_at": None}
    # a NEW AuditLog on the same file resumes the tip - chain survives reopen
    log2 = AuditLog(p)
    log2.append("x", {"n": 3})
    assert log2.verify_chain()["ok"] and log2.verify_chain()["entries"] == 3


@pytest.mark.parametrize("attack", ["edit", "delete", "reorder"])
def test_chain_breaks_loudly_on_tamper(tmp_path, attack):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    for i in range(3):
        log.append("x", {"n": i})
    lines = open(p).read().splitlines()
    if attack == "edit":
        e = json.loads(lines[1]); e["n"] = 99
        lines[1] = json.dumps(e)
    elif attack == "delete":
        lines = [lines[0], lines[2]]
    else:
        lines = [lines[1], lines[0], lines[2]]
    open(p, "w").write("\n".join(lines) + "\n")
    v = AuditLog(p).verify_chain()
    assert v["ok"] is False and v["break_at"] is not None


# ---------------------------------------------------- authority classification
def test_identity_authority_suffix_is_enforced():
    assert is_authority("pay.authority")
    assert is_authority("ratecon.authority")
    assert is_authority("config.update")
    assert not is_authority("note.record")


def test_unsigned_identity_authority_intent_rejected(tmp_path):
    hub = Hub(_routes(tmp_path), AuditLog(str(tmp_path / "a.jsonl")))
    hub.register("10", lambda e: None)
    r = hub.send(_env())  # no signature at all
    assert r["status"] == "reject" and "signature" in r["reason"]


# ------------------------------------------------------------- registry arming
def test_registry_refuses_unratified_template(tmp_path):
    root = tmp_path / "ident"; (root / "config").mkdir(parents=True)
    (root / "config" / "authority_signers.json").write_text(json.dumps(
        {"_status": "UNRATIFIED TEMPLATE - fail closed", "entries": [
            {"intent": "pay.authority", "signer_login": "jeff",
             "mfa_required": True}]}))
    with pytest.raises(ValueError, match="UNRATIFIED"):
        SignerRegistry.load(str(root))


def test_registry_refuses_zero_entries_and_no_mfa():
    with pytest.raises(ValueError, match="zero usable"):
        SignerRegistry([])
    with pytest.raises(ValueError, match="mfa_required"):
        SignerRegistry([{"intent": "pay.authority", "signer_login": "jeff",
                         "mfa_required": False}])


# ------------------------------------------------------------ hub enforcement
def _armed_hub(tmp_path):
    signer = HmacSigner(KEY)
    hub = Hub(_routes(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
              signature_verifier=signer.verifier(),
              signer_registry=_registry())
    hub.register("10", lambda e: None)
    return hub, signer


def test_signed_and_authorized_login_acks(tmp_path):
    hub, signer = _armed_hub(tmp_path)
    env = _env(signer=STAMP_OK)
    signer.sign(env)
    r = hub.send(env)
    assert r["status"] == "ack"
    kinds = [e["kind"] for e in hub.audit.read()]
    assert "signer.verified" in kinds
    assert hub.audit.verify_chain()["ok"]


@pytest.mark.parametrize("stamp,frag", [
    (None, "no signer stamp"),
    ({"signer_login": "mallory", "idp_session_ref": "s", "mfa": True}, "not authorized"),
    ({"signer_login": "jeff", "idp_session_ref": "s", "mfa": False}, "MFA"),
    ({"signer_login": "jeff", "mfa": True}, "idp_session_ref"),
])
def test_bad_stamps_reject_with_reason(tmp_path, stamp, frag):
    hub, signer = _armed_hub(tmp_path)
    env = _env(signer=stamp)
    signer.sign(env)
    r = hub.send(env)
    assert r["status"] == "reject" and frag in r["reason"]
    assert any(e["kind"] == "integrity.violation" for e in hub.audit.read())


def test_unarmed_is_audited_not_silent(tmp_path):
    signer = HmacSigner(KEY)
    hub = Hub(_routes(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
              signature_verifier=signer.verifier())  # no registry
    hub.register("10", lambda e: None)
    env = _env(); signer.sign(env)
    assert hub.send(env)["status"] == "ack"
    assert any(e["kind"] == "signer.unarmed" for e in hub.audit.read())


def test_revoked_login_rejected(tmp_path):
    reg = SignerRegistry([{"intent": "pay.authority", "signer_login": "jeff",
                           "idp": "okta", "mfa_required": True,
                           "effective": "2026-07-11", "revoked": "2026-07-12"}])
    signer = HmacSigner(KEY)
    hub = Hub(_routes(tmp_path), AuditLog(str(tmp_path / "a.jsonl")),
              signature_verifier=signer.verifier(), signer_registry=reg)
    hub.register("10", lambda e: None)
    env = _env(signer=STAMP_OK); signer.sign(env)
    r = hub.send(env)
    assert r["status"] == "reject" and "revoked" in r["reason"]


# --------------------------------------------------- scheduler gate (doctrine)
def test_unclassified_playbook_fails_closed(tmp_path):
    s = SidingScheduler(AuditLog(str(tmp_path / "a.jsonl")), {"P01": 1})
    with pytest.raises(KeyError, match="unclassified"):
        s.request_segment("run1", "P99", "02")
