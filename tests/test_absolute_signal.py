"""ABSOLUTE SIGNAL, disclosure half - executable enforcement.

These are not prose assertions. Each drives the REAL hub.send() path against
a real Routes object and checks the audited outcome. A regression that lets a
counterparty-bound message through, or a paraphrase of the signal text slipped
in downstream, fails here instead of shipping.
"""
import os, sys, json, hashlib, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import (Envelope, Routes, AuditLog, AUDIENCE,
                             AUDIENCE_GATED, AUDIENCE_DEFAULT)
from dispatcher.hub import Hub
from dispatcher.absolute_signal import SIGNAL_TEXT, signal_sha256, rebuttal

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def _routes_with(tmp_path, mutate):
    """Build a Routes from the fixture with per-intent audience overrides so
    each class can be tested against the real loader, not a mock."""
    d = json.load(open(FIX))
    for r in d["routes"]:
        if "external" in r.get("receivers", []):
            mutate(r)
    p = os.path.join(tmp_path, "routes.json")
    json.dump(d, open(p, "w"))
    return Routes(p)


def _hub(routes, tmp_path):
    hub = Hub(routes, AuditLog(os.path.join(tmp_path, "audit.jsonl")))
    for a in ("09", "11", "12", "14"):
        hub.register(a, lambda env: None)
    return hub


def _env(frm, intent, ctx="ctx-1", payload=None):
    return Envelope(from_agent=frm, to_agent="external", intent=intent,
                    client_context_id=ctx, payload=payload or {},
                    provenance={"source": "test"})


# --- fail-closed: unlabeled external route holds --------------------------
def test_unlabeled_external_defaults_to_counterparty_and_holds(tmp_path):
    routes = _routes_with(str(tmp_path), lambda r: None)  # no audience key
    hub = _hub(routes, str(tmp_path))
    r = hub.send(_env("11", "client.message.send"))
    assert r["status"] == "held", r
    assert r["reason"] == "absolute_signal"
    assert r["audience"] == AUDIENCE_DEFAULT == "counterparty"


# --- counterparty holds ----------------------------------------------------
def test_counterparty_holds(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="counterparty"))
    r = _hub(routes, str(tmp_path)).send(_env("09", "vendor.schedule"))
    assert r["status"] == "held" and r["reason"] == "absolute_signal"


# --- public holds ----------------------------------------------------------
def test_public_holds(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="public"))
    r = _hub(routes, str(tmp_path)).send(_env("12", "campaign.publish"))
    assert r["status"] == "held" and r["audience"] == "public"


# --- principal PASSES THE GATE (the one class that is not gated) ----------
# The gate's contract is narrow and must stay narrow: hold gated audiences,
# let principal THROUGH THE GATE. What happens after - external transport - is
# a separate layer the chassis does not implement (an identity wires its own
# notifier/webhook for `external`). So the assertion is "not held by the
# absolute signal", NOT "ack": in a bare chassis with no external transport a
# principal message proceeds past the gate and then dead-letters for lack of a
# registered `external` handler. That dead-letter is a delivery-layer gap, not
# a disclosure hold, and conflating the two would hide a real seam.
def test_principal_passes_the_gate(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="principal"))
    hub = _hub(routes, str(tmp_path))
    r = hub.send(_env("11", "client.message.send"))
    # it was NOT held by the signal ...
    assert r.get("reason") != "absolute_signal", r
    # ... and the audit shows it got PAST the gate to persistence.
    kinds = [e["kind"] for e in hub.audit.read()]
    assert "envelope.persisted" in kinds
    assert "absolute_signal.hold" not in kinds


# --- documents the external-transport gap so it is visible, not silent -----
def test_external_transport_is_unwired_in_bare_chassis(tmp_path):
    """A principal message clears the gate but the chassis has no `external`
    transport, so it dead-letters. This is an identity-layer wiring gap, and
    it is asserted here on purpose: if someone later wires external delivery,
    this test breaks and forces them to confirm the gate still precedes it."""
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="principal"))
    r = _hub(routes, str(tmp_path)).send(_env("11", "client.message.send"))
    assert r["status"] == "dead.letter"  # not held, not acked: unwired transport


# --- internal routes never touched by the gate ----------------------------
def test_internal_route_unaffected(tmp_path):
    routes = Routes(FIX)
    hub = Hub(routes, AuditLog(os.path.join(str(tmp_path), "a.jsonl")))
    hub.register("02", lambda env: None)
    e = Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                 client_context_id="c", payload={},
                 provenance={"source": "test"})
    assert hub.send(e)["status"] == "ack"


# --- the hold emits the canonical rebuttal, byte-identical -----------------
def test_hold_emits_canonical_rebuttal(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="counterparty"))
    hub = _hub(routes, str(tmp_path))
    hub.send(_env("09", "vendor.schedule"))
    holds = [e for e in hub.audit.read() if e["kind"] == "absolute_signal.hold"]
    assert len(holds) == 1
    assert holds[0]["signal_sha256"] == signal_sha256()


# --- provisional label is surfaced, not hidden -----------------------------
def test_provisional_audience_flagged(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="counterparty"))  # unverified
    hub = _hub(routes, str(tmp_path))
    hub.send(_env("09", "vendor.schedule"))
    holds = [e for e in hub.audit.read() if e["kind"] == "absolute_signal.hold"]
    assert holds[0]["audience_verified"] is False


def test_verified_label_recorded(tmp_path):
    routes = _routes_with(str(tmp_path),
                          lambda r: r.update(audience="principal",
                                             audience_verified=True))
    # principal passes, but the verified flag must be readable on the route
    assert routes.audience_is_verified("client.message.send") is True


# --- canonical text integrity ---------------------------------------------
def test_signal_text_carries_both_halves():
    t = SIGNAL_TEXT
    assert "CANNOT execute a financial transaction" in t
    assert "reveal a financial position" in t
    assert "TWO LOCKS" in t
    assert "software update" in t


def test_rebuttal_is_a_designed_state_not_an_error():
    p = rebuttal("vendor.schedule", "counterparty", "ctx-9")
    assert p["not_a_malfunction"] is True
    assert p["state"] == "locked_by_design"
    assert p["capability_doc"] and p["engage_via"]
    assert p["signal_sha256"] == signal_sha256()


# ====================================================================
# THE KEY: signed release of a held message. Same trust bar as
# execution authority - crypto signature + (when armed) signer registry.
# ====================================================================
from dispatcher.signatures import Ed25519Signer
from dispatcher.signer_registry import SignerRegistry



def _gated_routes(tmp_path):
    """A routes file whose external route is gated (counterparty) plus the
    disclosure.authority lane so the human key has a legal track."""
    doc = {"version": "t", "routes": [
        {"intent": "vendor.schedule", "senders": ["09"],
         "receivers": ["external"], "audience": "counterparty",
         "audience_verified": True},
        {"intent": "disclosure.authority", "senders": ["human"],
         "receivers": ["09"]},
    ]}
    p = os.path.join(tmp_path, "routes.json")
    json.dump(doc, open(p, "w"))
    return Routes(p)


def _signed_hub(tmp_path, registry=None):
    signer = Ed25519Signer()
    hub = Hub(_gated_routes(tmp_path),
              AuditLog(os.path.join(tmp_path, "audit.jsonl")),
              signature_verifier=signer.verifier(),
              signer_registry=registry)
    for a in ("09",):
        hub.register(a, lambda env: None)
    return hub, signer


def _held_env():
    return Envelope(from_agent="09", to_agent="external",
                    intent="vendor.schedule", client_context_id="c1",
                    payload={"vendor": "v-1"}, provenance={"source": "t"})


def _auth_env(held_id, signer, stamp=None):
    prov = {"source": "t"}
    if stamp:
        prov["signer"] = stamp
    a = Envelope(from_agent="human", to_agent="09",
                 intent="disclosure.authority", client_context_id="c1",
                 payload={"held_envelope_id": held_id}, provenance=prov)
    a.signature = signer.sign(a)
    return a


def test_full_lock_and_key_cycle(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env()
    r1 = hub.send(env)
    assert r1["status"] == "held" and r1["reason"] == "absolute_signal"
    # the key: signed disclosure.authority naming the held id. The release
    # CLEARS THE GATE - it is no longer held by the signal. (Delivery to the
    # external sink is a separate identity-layer transport, unwired in the
    # bare chassis, so the released message dead-letters at delivery; the
    # point proven here is that the signal no longer holds it.)
    r2 = hub.release_disclosure(_auth_env(env.envelope_id, signer))
    assert r2.get("reason") != "absolute_signal", r2
    kinds = [e["kind"] for e in hub.audit.read()]
    assert "disclosure.authorized" in kinds
    assert "absolute_signal.released" in kinds
    # the hold is gone and the id is consumed - not reusable
    assert env.envelope_id not in hub._absolute_holds
    assert env.envelope_id not in hub._absolute_released


def test_unsigned_release_rejected(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    # forge: an authority envelope with NO valid signature
    bad = Envelope(from_agent="human", to_agent="09",
                   intent="disclosure.authority", client_context_id="c1",
                   payload={"held_envelope_id": env.envelope_id},
                   provenance={"source": "t"})  # signature left None
    r = hub.release_disclosure(bad)
    assert r["status"] == "reject"
    # and the original is STILL held - nothing released
    assert env.envelope_id in hub._absolute_holds


def test_release_unknown_id_rejected(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    r = hub.release_disclosure(_auth_env("no-such-id", signer))
    assert r["status"] == "reject" and "no held envelope" in r["reason"]


def test_wrong_intent_cannot_release(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    a = Envelope(from_agent="human", to_agent="09",
                 intent="config.update",  # not disclosure.authority
                 client_context_id="c1",
                 payload={"held_envelope_id": env.envelope_id},
                 provenance={"source": "t"})
    a.signature = signer.sign(a)
    r = hub.release_disclosure(a)
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds  # still held


def test_release_is_one_shot_no_replay(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    auth = _auth_env(env.envelope_id, signer)
    r1 = hub.release_disclosure(auth)
    assert r1.get("reason") != "absolute_signal"  # cleared the gate
    # replay the SAME authorization: the held id is gone, nothing to release
    r2 = hub.release_disclosure(_auth_env(env.envelope_id, signer))
    assert r2["status"] == "reject"


def test_registry_rejection_blocks_release(tmp_path):
    # armed registry that does NOT authorize this signer for the intent
    reg = SignerRegistry([{"intent": "disclosure.authority",
                           "signer_login": "jeff", "idp": "okta",
                           "mfa_required": True, "effective": "2026-07-11",
                           "revoked": None}])
    hub, signer = _signed_hub(str(tmp_path), registry=reg)
    env = _held_env(); hub.send(env)
    # signed, but provenance carries NO signer stamp -> registry check fails
    r = hub.release_disclosure(_auth_env(env.envelope_id, signer, stamp=None))
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


# --- the other answer: a signed DECLINE (added 2026-10-12) -------------------
def _decline_env(held_id, signer, reason="not this vendor", stamp=None):
    prov = {"source": "t"}
    if stamp:
        prov["signer"] = stamp
    a = Envelope(from_agent="human", to_agent="09",
                 intent="disclosure.authority", client_context_id="c1",
                 payload={"held_envelope_id": held_id, "decision": "decline",
                          "reason": reason}, provenance=prov)
    a.signature = signer.sign(a)
    return a


def test_signed_decline_drops_the_hold_and_sends_nothing(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    r = hub.decline_disclosure(_decline_env(env.envelope_id, signer))
    assert r == {"status": "declined", "envelope_id": env.envelope_id,
                 "intent": "vendor.schedule"}
    assert env.envelope_id not in hub._absolute_holds
    assert env.envelope_id not in hub._absolute_released
    log = hub.audit.read()
    dec = [e for e in log if e["kind"] == "disclosure.declined"]
    assert len(dec) == 1 and dec[0]["held_envelope_id"] == env.envelope_id
    assert dec[0]["reason"] == "not this vendor"
    kinds = [e["kind"] for e in log]
    # never released, never persisted, never delivered
    assert "absolute_signal.released" not in kinds
    assert "disclosure.authorized" not in kinds
    assert not any(e["kind"] == "envelope.persisted"
                   and e.get("envelope_id") == env.envelope_id for e in log)
    assert hub.audit.verify_chain()["ok"]


def test_unsigned_decline_rejected(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    bad = Envelope(from_agent="human", to_agent="09",
                   intent="disclosure.authority", client_context_id="c1",
                   payload={"held_envelope_id": env.envelope_id,
                            "decision": "decline"},
                   provenance={"source": "t"})
    r = hub.decline_disclosure(bad)
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


def test_release_key_cannot_decline_and_decline_key_cannot_release(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    env = _held_env(); hub.send(env)
    r1 = hub.decline_disclosure(_auth_env(env.envelope_id, signer))
    assert r1["status"] == "reject" and "signed for 'release'" in r1["reason"]
    r2 = hub.release_disclosure(_decline_env(env.envelope_id, signer))
    assert r2["status"] == "reject" and "signed for 'decline'" in r2["reason"]
    assert env.envelope_id in hub._absolute_holds   # untouched by both


def test_decline_unknown_or_already_answered_rejected(tmp_path):
    hub, signer = _signed_hub(str(tmp_path))
    r = hub.decline_disclosure(_decline_env("no-such-id", signer))
    assert r["status"] == "reject" and "no held envelope" in r["reason"]
    env = _held_env(); hub.send(env)
    assert hub.decline_disclosure(_decline_env(env.envelope_id, signer))["status"] == "declined"
    again = hub.decline_disclosure(_decline_env(env.envelope_id, signer))
    assert again["status"] == "reject"
    late = hub.release_disclosure(_auth_env(env.envelope_id, signer))
    assert late["status"] == "reject"          # a declined message cannot be released later


def test_registry_rejection_blocks_decline(tmp_path):
    reg = SignerRegistry([{"intent": "disclosure.authority",
                           "signer_login": "jeff", "idp": "okta",
                           "mfa_required": True, "effective": "2026-07-11",
                           "revoked": None}])
    hub, signer = _signed_hub(str(tmp_path), registry=reg)
    env = _held_env(); hub.send(env)
    r = hub.decline_disclosure(_decline_env(env.envelope_id, signer, stamp=None))
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


# --- the rebuttal must not point at a file that does not exist ---------------
def test_capability_doc_pointer_resolves():
    from dispatcher.absolute_signal import CAPABILITY_DOC
    root = os.path.join(os.path.dirname(__file__), "..")
    assert os.path.isfile(os.path.join(root, CAPABILITY_DOC)), \
        f"rebuttal points at {CAPABILITY_DOC} but no such file exists"


# --- release inherits the FULL armed-registry authority stack ----------------
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier


def _ed_hub_armed(tmp_path, intent_authorized="disclosure.authority",
                  login="jeff"):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    reg = SignerRegistry([{"intent": intent_authorized, "signer_login": login,
                           "idp": "okta", "mfa_required": True,
                           "effective": "2026-01-01", "revoked": None}])
    routes = _gated_routes(tmp_path)
    hub = Hub(routes, AuditLog(os.path.join(tmp_path, "a.jsonl")),
              signature_verifier=verifier.verifier(), signer_registry=reg)
    hub.register("09", lambda e: None)
    return hub, signer


def _ed_auth(hub, signer, held_id, login, mfa=True, stamp=True):
    prov = {"source": "t"}
    if stamp:
        prov["signer"] = {"signer_login": login, "idp_session_ref": "s-1",
                          "mfa": mfa}
    a = Envelope(from_agent="human", to_agent="09",
                 intent="disclosure.authority", client_context_id="c1",
                 payload={"held_envelope_id": held_id}, provenance=prov)
    a.signature = signer.sign(a)
    return a


def test_armed_registry_release_requires_authorized_login(tmp_path):
    hub, signer = _ed_hub_armed(str(tmp_path))
    env = _held_env(); hub.send(env)
    # unregistered login, valid signature + mfa -> STILL rejected
    r = hub.release_disclosure(_ed_auth(hub, signer, env.envelope_id, "mallory"))
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


def test_armed_registry_release_requires_mfa(tmp_path):
    hub, signer = _ed_hub_armed(str(tmp_path))
    env = _held_env(); hub.send(env)
    r = hub.release_disclosure(
        _ed_auth(hub, signer, env.envelope_id, "jeff", mfa=False))
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


def test_armed_registry_release_requires_signer_stamp(tmp_path):
    hub, signer = _ed_hub_armed(str(tmp_path))
    env = _held_env(); hub.send(env)
    r = hub.release_disclosure(
        _ed_auth(hub, signer, env.envelope_id, "jeff", stamp=False))
    assert r["status"] == "reject"
    assert env.envelope_id in hub._absolute_holds


def test_armed_registry_authorized_signer_clears(tmp_path):
    hub, signer = _ed_hub_armed(str(tmp_path))
    env = _held_env(); hub.send(env)
    r = hub.release_disclosure(_ed_auth(hub, signer, env.envelope_id, "jeff"))
    assert r.get("reason") != "absolute_signal"  # cleared the gate
    kinds = [e["kind"] for e in hub.audit.read()]
    assert "disclosure.authorized" in kinds
