"""Doctrine as executable assertions, against the REAL 35-route listing-agent
track (routes_fixture.json exported from v0.16 ROUTES) — not toy data."""
import os, sys, json, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def make_hub(tmp_path, verifier=None):
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "audit.jsonl")), verifier)
    hub.register("02", lambda env: None)
    hub.register("14", lambda env: None)
    hub.register("05", lambda env: None)
    return hub


def env(frm="01", to="02", intent="lead.captured", **kw):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=kw.pop("ctx", "ctx-1"),
                    payload=kw.pop("payload", {}),
                    provenance={"source": "test", "captured_at": "now",
                                "verbatim_available": True}, **kw)


def test_legal_tuple_acks(tmp_path):
    r = make_hub(tmp_path).send(env())          # 01 -> lead.captured -> 02
    assert r["status"] == "ack" and r["sequence"] == 1


def test_illegal_tuple_rejected_agent15_defect_class(tmp_path):
    # the exact defect class that survived four spec passes: interaction.log
    # routes only to 14; 07 -> 15 must die at the hub
    r = make_hub(tmp_path).send(env(frm="07", to="15", intent="interaction.log"))
    assert r["status"] == "reject" and "tuple illegal" in r["reason"]


def test_ack_only_after_persist_and_delivery(tmp_path):
    hub = make_hub(tmp_path)
    hub.send(env())
    kinds = [e["kind"] for e in hub.audit.read()]
    assert kinds.index("envelope.persisted") < kinds.index("ack")


def test_failed_delivery_never_acks(tmp_path):
    hub = make_hub(tmp_path)
    hub.register("02", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    r = hub.send(env())
    assert r["status"] == "dead.letter" and "boom" in r["reason"]
    assert "ack" not in [e["kind"] for e in hub.audit.read()]


def test_duplicate_envelope_processes_once(tmp_path):
    hub = make_hub(tmp_path)
    e = env()
    assert hub.send(e)["status"] == "ack"
    assert hub.send(e)["status"] == "duplicate"


def test_sequence_monotonic_per_context(tmp_path):
    hub = make_hub(tmp_path)
    s1 = hub.send(env())["sequence"]
    s2 = hub.send(env(frm="16"))["sequence"]           # same ctx-1
    s3 = hub.send(env(ctx="ctx-2"))["sequence"]        # new context
    assert (s1, s2, s3) == (1, 2, 1)


def test_sender_set_sequence_is_schema_violation(tmp_path):
    e = env(); e.sequence = 7
    assert make_hub(tmp_path).send(e)["status"] == "reject"


def test_unsigned_authority_intent_rejected_and_flagged(tmp_path):
    hub = make_hub(tmp_path)
    r = hub.send(env(frm="human", to="05", intent="listing.change.authorized"))
    assert r["status"] == "reject"
    assert hub.queues["integrity.violation"]


def test_signed_authority_intent_passes(tmp_path):
    hub = make_hub(tmp_path, verifier=lambda e: e.signature == "VALID")
    e = env(frm="human", to="05", intent="listing.change.authorized",
            signature="VALID")
    assert hub.send(e)["status"] == "ack"


def test_unknown_intent_holds_live_never_drops(tmp_path):
    hub = make_hub(tmp_path)
    r = hub.send(env(intent="totally.new.intent"))
    assert r["status"] == "held"
    assert hub.queues["clarification.request"]          # held, not dropped
    assert "hold.clarification" in [e["kind"] for e in hub.audit.read()]


def test_illegal_confidence_rejected(tmp_path):
    r = make_hub(tmp_path).send(env(confidence="inferred"))
    assert r["status"] == "reject" and "confidence" in r["reason"]


def test_pillar_seams_fire_and_are_auditable(tmp_path):
    hub = make_hub(tmp_path)
    state = hub.on_turn_start()                         # before-turn seam
    assert "open_holds" in state
    hub.send(env())                                     # produces reflection
    assert hub.reflection_artifacts                     # open-mind input exists
    assert {"thought", "response"} <= set(hub.reflection_artifacts[-1])
    hub.ingest_spoke_trace("02", "e-1", "scored on rubric v3", "tier B")
    assert hub.spoke_traces                             # agent-open-mind input
    kinds = [e["kind"] for e in hub.audit.read()]
    assert {"turn.start", "hub.reflection", "spoke.trace"} <= set(kinds)
