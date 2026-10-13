"""The record says a person was notified only when the notifier confirms it
(repair plan 1.1, 2026-10-13).

Found in the 2026-10-13 snapshot: Hub.escalate and Hub.queue_and_notify
wrote human.notified after every notifier call and ignored its result, so a
Twilio 401 with the placeholder account went on the hash chain as a person
notified, and the escalation transport KPI was computed from it.
"""
import io
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.after_action import generate_report  # noqa: E402
from dispatcher.core import AuditLog, Envelope, Routes  # noqa: E402
from dispatcher.hub import Hub, NOTIFY_DELIVERED  # noqa: E402
from dispatcher.kpi import compute_kpis  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def hub_with(tmp_path, notifier):
    return Hub(Routes(FIXTURE), AuditLog(str(tmp_path / "a.jsonl")), human_notifier=notifier)


def kinds(hub):
    return [e["kind"] for e in hub.audit.read()]


def last(hub, kind):
    return [e for e in hub.audit.read() if e["kind"] == kind][-1]


def test_a_failed_send_is_recorded_as_failed_not_notified(tmp_path):
    hub = hub_with(tmp_path, lambda q, r: {"status": "failed", "http_status": 401,
                                           "body": "secret-ish twilio body"})
    out = hub.escalate("escalation.hot_lead", {"client_context_id": "C1", "reason": "call now"})
    assert out == {"status": "escalated", "queue": "escalation.hot_lead"}
    assert "human.notified" not in kinds(hub)
    f = last(hub, "human.notify_failed")
    assert f["queue"] == "escalation.hot_lead" and f["notifier_status"] == "failed"
    assert f["notifier_http_status"] == 401 and f["client_context_id"] == "C1"
    assert "body" not in f and "secret-ish" not in str(f)
    k = compute_kpis(hub.audit.read())["escalation_transport_time"]
    assert k["n"] == 0 and k["unnotified"] == 1 and k["notify_failed"] == 1 and k["confirmed"] == 0


@pytest.mark.parametrize("status", sorted(NOTIFY_DELIVERED))
def test_a_confirmed_send_is_recorded_as_notified(tmp_path, status):
    hub = hub_with(tmp_path, lambda q, r: {"status": status})
    hub.escalate("escalation.complaint", {"client_context_id": "C2"})
    n = last(hub, "human.notified")
    assert n["transport_status"] == status and n["queue"] == "escalation.complaint"
    assert "human.notify_failed" not in kinds(hub)
    k = compute_kpis(hub.audit.read())["escalation_transport_time"]
    assert k["n"] == 1 and k["confirmed"] == 1 and k["not_reported"] == 0 and k["notify_failed"] == 0


@pytest.mark.parametrize("status", ["queued", "failed", "", None, "SENT", "ok"])
def test_any_other_status_is_a_failure(tmp_path, status):
    hub = hub_with(tmp_path, lambda q, r: {"status": status})
    hub.escalate("escalation.legal_line", {"client_context_id": "C3"})
    assert "human.notified" not in kinds(hub)
    assert last(hub, "human.notify_failed")["notifier_status"] == status


def test_a_notifier_that_reports_nothing_is_kept_apart(tmp_path):
    hub = hub_with(tmp_path, lambda q, r: None)
    hub.escalate("escalation.hot_lead", {"client_context_id": "C4"})
    n = last(hub, "human.notified")
    assert n["transport"] == "not reported" and "transport_status" not in n
    k = compute_kpis(hub.audit.read())["escalation_transport_time"]
    assert k["n"] == 1 and k["not_reported"] == 1 and k["confirmed"] == 0


def test_a_notifier_that_raises_is_recorded_and_the_hub_carries_on(tmp_path):
    def boom(q, r):
        raise ConnectionError("network down")
    hub = hub_with(tmp_path, boom)
    out = hub.escalate("escalation.hot_lead", {"client_context_id": "C5"})
    assert out["status"] == "escalated"
    assert hub.queues["escalation.hot_lead"][-1]["client_context_id"] == "C5"
    f = last(hub, "human.notify_failed")
    assert "network down" in f["error"] and "human.notified" not in kinds(hub)


def test_a_raising_notifier_does_not_abort_a_send(tmp_path):
    # unknown intent -> clarification hold -> queue_and_notify; and a crashing
    # handler -> escalation.system_error. Both used to raise out of send().
    def boom(q, r):
        raise RuntimeError("pager crashed")
    hub = hub_with(tmp_path, boom)
    held = hub.send(Envelope(from_agent="01", to_agent="02", intent="no.such.intent",
                             client_context_id="C6", payload={}, provenance={"source": "t"}))
    assert held["status"] == "held"

    def crash(env):
        raise ValueError("handler bug")
    hub.register("02", crash)
    dead = hub.send(Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                             client_context_id="C6", payload={}, provenance={"source": "t"}))
    assert dead["status"] == "dead.letter"
    assert kinds(hub).count("human.notify_failed") == 2


def test_the_record_cannot_overwrite_the_framing(tmp_path):
    hub = hub_with(tmp_path, lambda q, r: {"status": "failed"})
    hub.escalate("escalation.hot_lead", {"client_context_id": "C7", "queue": "fake",
                                         "notifier_status": "sent"})
    f = last(hub, "human.notify_failed")
    assert f["queue"] == "escalation.hot_lead" and f["notifier_status"] == "failed"
    hub2 = Hub(Routes(FIXTURE), AuditLog(str(tmp_path / "b.jsonl")),
               human_notifier=lambda q, r: {"status": "sent"})
    hub2.escalate("escalation.hot_lead", {"client_context_id": "C7", "transport_status": "x"})
    assert last(hub2, "human.notified")["transport_status"] == "sent"


def test_the_shipped_sms_notifier_with_a_401_is_a_failure(tmp_path, monkeypatch):
    import dispatcher.notifier as n

    def reject(req, timeout=5.0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"code":20003,"message":"Authenticate"}'))
    monkeypatch.setattr(n.urllib.request, "urlopen", reject)
    hub = hub_with(tmp_path, n.sms_human_notifier)
    hub.escalate("escalation.hot_lead", {"client_context_id": "C8", "reason": "hot"})
    f = last(hub, "human.notify_failed")
    assert f["notifier_status"] == "failed" and f["notifier_http_status"] == 401
    assert "human.notified" not in kinds(hub)
    assert "20003" not in str(f)          # the provider's response body stays off the chain


def test_the_after_action_report_says_the_notification_failed(tmp_path):
    hub = hub_with(tmp_path, lambda q, r: {"status": "failed", "http_status": 401})
    hub.escalate("escalation.hot_lead", {"client_context_id": "C9"})
    report = generate_report(hub.audit.read(), "P11", "r1", "C9", steps=[])
    assert "NOTIFICATION FAILED (failed) - no person was reached" in report
    assert "transport" not in report.split("## escalations")[1].split("##")[0].replace(
        "NOTIFICATION FAILED", "")
