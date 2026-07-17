"""Regression tests for hub-core notification bugs found across two
sessions:

1. clarification.request (and any 'queue'-directed intent) was silently
   dead-lettering - "queue" has no registered handler, and nothing
   special-cased it before normal delivery. Every agent's
   clarification.request calls throughout this entire build were
   affected. Went undetected because existing tests only checked
   envelope.persisted (which happens before delivery), never final
   delivery status or the actual queue contents.

2. A handler exception (a genuine crashed agent, not the benign
   "not built yet" case) had no active notification path at all - it
   silently appended to a passive dead.letter list. A crashed handler
   cannot self-report its own failure, so the hub itself has to raise
   the alarm immediately.

3. (2026-07-17, granular pillar review) The append+notify pairing that
   fixed (1) was never actually centralized - it was hand-reimplemented
   at the one call site that got fixed, while FIVE OTHER call sites
   across hub.py, pillars.py, territory.py, and analysis.py recreated
   the exact same silent-queue bug independently: unknown-route holds,
   loop suspension, unverified/unauthorized authority signatures,
   ingestion-time taint detection, the pre-response-selfcheck FAIL path,
   territory-transfer signature refusal, and the agent-open-mind
   analysis-layer taint backstop. All now route through a single shared
   Hub.queue_and_notify() helper, so this bug class can't recur silently
   at a sixth call site.

All now route through human_notifier, the same active-push mechanism
escalate() already used - matching the standard "unexpected value or
error needs up-to-the-minute feedback" requirement.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def make_hub(tmp_path, notifier=None, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
              human_notifier=notifier, **kw)


def test_REGRESSION_clarification_request_actually_reaches_the_queue(tmp_path):
    hub = make_hub(str(tmp_path))
    hub.on_turn_start()
    env = Envelope(from_agent="08", to_agent="queue", intent="clarification.request",
                  client_context_id="c-1", payload={"reason": "test"},
                  provenance={"source": "spoke-08", "captured_at": "runtime",
                              "verbatim_available": True})
    result = hub.send(env)
    assert result["status"] == "held"
    assert len(hub.queues["clarification.request"]) == 1
    assert hub.queues["clarification.request"][0]["payload"]["reason"] == "test"


def test_REGRESSION_clarification_request_triggers_active_notification(tmp_path):
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append((q, r)))
    hub.on_turn_start()
    env = Envelope(from_agent="08", to_agent="queue", intent="clarification.request",
                  client_context_id="c-1", payload={"reason": "unexpected value"},
                  provenance={"source": "spoke-08", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert len(notified) == 1
    assert notified[0][0] == "clarification.request"


def test_REGRESSION_crashed_handler_escalates_immediately_not_just_logged(tmp_path):
    """A registered agent that crashes on real input is a genuine defect -
    distinct from 'not built yet'. Must surface with the same urgency as
    a legal-line escalation, not sit in a passive dead-letter list."""
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append((q, r)))
    hub.on_turn_start()

    def broken_handler(env):
        raise ValueError("simulated crash on unexpected payload shape")

    hub.handlers["14"] = broken_handler
    env = Envelope(from_agent="01", to_agent="14", intent="record.request",
                  client_context_id="c-2", payload={"dedupe_key": "c-2"},
                  provenance={"source": "spoke-01", "captured_at": "runtime",
                              "verbatim_available": True})
    result = hub.send(env)

    assert result["status"] == "dead.letter"
    assert "simulated crash" in result["reason"]
    assert len(hub.queues["escalation.system_error"]) == 1
    err = hub.queues["escalation.system_error"][0]
    assert err["agent"] == "14"
    assert "simulated crash" in err["reason"]
    assert any(q == "escalation.system_error" for q, r in notified), \
        "a crashed handler must trigger the same active notification as any other escalation"


def test_REGRESSION_missing_handler_does_not_false_alarm_as_system_error(tmp_path):
    """The benign 'agent not built yet' case must stay distinct from a
    genuine crash - it should not flood escalation.system_error during
    normal incremental build-out."""
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append((q, r)))
    hub.on_turn_start()
    env = Envelope(from_agent="04", to_agent="17", intent="content.review",
                  client_context_id="c-3", payload={},
                  provenance={"source": "spoke-04", "captured_at": "runtime",
                              "verbatim_available": True})
    # "17" has no handler registered yet (not built) - this is the normal,
    # expected state during incremental build-out, not a crash
    hub.send(env)
    assert hub.queues["escalation.system_error"] == []
    assert not notified


# --------------------------------- 2026-07-17 granular pillar review fixes
def test_REGRESSION_unknown_route_hold_now_notifies(tmp_path):
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    env = Envelope(from_agent="01", to_agent="02", intent="totally.unrouted.intent",
                  client_context_id="c-4", payload={},
                  provenance={"source": "spoke-01", "captured_at": "runtime",
                              "verbatim_available": True})
    r = hub.send(env)
    assert r["status"] == "held"
    assert notified == ["clarification.request"], \
        "an unrouted-but-well-formed intent must actively notify, not just queue silently"


def test_REGRESSION_loop_suspension_now_notifies(tmp_path):
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    hub.loop_threshold = 2
    for i in range(4):
        hub.send(Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                          client_context_id="c-5", payload={"n": i},
                          provenance={"source": "spoke-01", "captured_at": "runtime",
                                      "verbatim_available": True}))
    assert "clarification.request" in notified, \
        "a suspended loop is a potential runaway and must actively notify"


def test_REGRESSION_unverified_authority_signature_now_notifies(tmp_path):
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    env = Envelope(from_agent="human", to_agent="any", intent="config.update",
                  client_context_id="c-6", payload={},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    r = hub.send(env)
    assert r["status"] == "reject"
    assert notified == ["integrity.violation"], \
        "an authority intent without a verified signature is the single most " \
        "security-critical event this hub can see - must notify, not sit passively"


def test_REGRESSION_signer_registry_rejection_now_notifies(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())

    class DenyAllRegistry:
        def check(self, env):
            class V:
                ok = False
                reason = "test: no login authorized"
            return V()

    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q),
                   signature_verifier=verifier.verifier(),
                   signer_registry=DenyAllRegistry())
    env = Envelope(from_agent="human", to_agent="any", intent="config.update",
                  client_context_id="c-7", payload={},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    r = hub.send(env)
    assert r["status"] == "reject"
    assert notified == ["integrity.violation"], \
        "a cryptographically valid but unauthorized signer must notify too"


def test_REGRESSION_ingestion_time_taint_now_notifies(tmp_path):
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    hub.on_turn_start()
    hub.ingest_spoke_trace("03", "e-8", thought="", result="did something")
    assert notified == ["integrity.violation"], \
        "an absent thought trace at ingestion is a genuine integrity concern"


def test_REGRESSION_exit_gate_fail_now_notifies(tmp_path):
    from dispatcher.pillars import exit_gate
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    e = Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                client_context_id="c-9", payload={"msg": "definitely accepted"},
                provenance={"source": "spoke-01", "captured_at": "runtime",
                            "verbatim_available": True})
    fail_model = lambda prompt: "FAIL\nLINE: definitely accepted\nFIX: verify first"
    exit_gate(hub, e, model=fail_model)
    assert notified == ["clarification.request"], \
        "a FAILED selfcheck verdict holds an envelope live - must notify, not sit passively"


def test_REGRESSION_territory_transfer_refusal_now_notifies(tmp_path):
    from dispatcher.territory import receive_transfer

    class AlwaysRejectVerifier:
        def verify_bytes(self, data, sig):
            return False

    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    r = receive_transfer(hub, {"contexts": {"c-10": {}}, "signature": None},
                         AlwaysRejectVerifier())
    assert r["status"] == "refused"
    assert notified == ["integrity.violation"], \
        "a territory transfer with an invalid/missing signature must notify immediately"


def test_REGRESSION_analysis_backstop_taint_now_notifies(tmp_path):
    """The analysis-layer backstop (score_spoke_traces) catches a tainted
    trace that wasn't already flagged at ingestion - e.g. one loaded from
    elsewhere rather than submitted through ingest_spoke_trace directly."""
    from dispatcher.analysis import score_spoke_traces
    notified = []
    hub = make_hub(str(tmp_path), notifier=lambda q, r: notified.append(q))
    hub.spoke_traces.append({"agent": "05", "envelope_id": "e-11",
                             "thought": "", "result": "did something",
                             "tainted": False})  # loaded from elsewhere, not ingested normally
    score_spoke_traces(hub)
    assert notified == ["integrity.violation"], \
        "the analysis-layer backstop must notify too, not just the ingestion-time gate"
