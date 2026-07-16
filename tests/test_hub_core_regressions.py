"""Regression tests for two real hub-core bugs found mid-session:

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

Both now route through human_notifier, the same active-push mechanism
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
