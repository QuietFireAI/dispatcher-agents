"""Tests the 2026-10-13 snapshot found missing (repair plan, Phase 1).

1.4 - three checks whose removal left the whole suite green (targeted
      mutants that survived): the envelope's required fields, the
      required provenance.source, and an outbound route with a misspelled
      audience label failing closed to "counterparty" (held).
1.7 - three additions made in the core for listing-assistants and tested
      only there: the escalation.confidence_underflow queue,
      Hub.arm_signer_registry(), and the WhatsApp notifier.
"""
import io
import json
import os
import sys
import urllib.error
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import AuditLog, Envelope, Routes  # noqa: E402
from dispatcher.hub import Hub  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def hub_on(tmp_path, routes_path=FIXTURE, **kw):
    return Hub(Routes(routes_path), AuditLog(str(tmp_path / "a.jsonl")), **kw)


def good(**kw):
    base = dict(from_agent="01", to_agent="02", intent="lead.captured",
                client_context_id="c1", payload={}, provenance={"source": "test"})
    base.update(kw)
    return Envelope(**base)


# ------------------------------------------------------------ 1.4 schema
@pytest.mark.parametrize("field", ["from_agent", "to_agent", "intent", "client_context_id"])
def test_an_envelope_missing_a_required_field_is_rejected_and_logged(tmp_path, field):
    hub = hub_on(tmp_path)
    env = good(**{field: ""})
    r = hub.send(env)
    assert r["status"] == "reject"
    assert f"missing field: {field}" in r["reason"]
    rejects = [e for e in hub.audit.read() if e["kind"] == "reject"]
    assert rejects and f"missing field: {field}" in rejects[-1]["reason"]
    assert not any(e["kind"] == "envelope.persisted" for e in hub.audit.read())


@pytest.mark.parametrize("prov", [{}, {"captured_at": "x"}, "source", None])
def test_an_envelope_without_provenance_source_is_rejected(tmp_path, prov):
    hub = hub_on(tmp_path)
    r = hub.send(good(provenance=prov))
    assert r["status"] == "reject" and "provenance.source required" in r["reason"]
    assert not any(e["kind"] == "envelope.persisted" for e in hub.audit.read())


def test_the_well_formed_envelope_in_these_tests_does_route(tmp_path):
    # control: the rejections above are the field, not the fixture
    hub = hub_on(tmp_path)
    hub.register("02", lambda env: None)
    assert hub.send(good())["status"] == "ack"


# ------------------------------------------------- 1.4 audience fail-closed
def routes_file(tmp_path, audience):
    p = tmp_path / "routes.json"
    p.write_text(json.dumps({"version": "t", "routes": [
        {"intent": "offer.share", "senders": ["11"], "receivers": ["external"],
         "audience": audience}]}), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("label", ["publik", "Principal", "", "client", 7])
def test_a_misspelled_audience_label_holds_like_counterparty(tmp_path, label):
    routes = Routes(routes_file(tmp_path, label))
    assert routes.audience_for("offer.share") == "counterparty"
    hub = Hub(routes, AuditLog(str(tmp_path / "a.jsonl")))
    r = hub.send(good(from_agent="11", to_agent="external", intent="offer.share"))
    assert r["status"] == "held" and r["reason"] == "absolute_signal"
    assert r["audience"] == "counterparty"
    kinds = [e["kind"] for e in hub.audit.read()]
    assert "absolute_signal.hold" in kinds and "envelope.persisted" not in kinds


def test_a_correct_principal_label_passes_the_gate(tmp_path):
    # control: the label is what decides, so "principal" spelled right passes
    hub = Hub(Routes(routes_file(tmp_path, "principal")), AuditLog(str(tmp_path / "a.jsonl")))
    r = hub.send(good(from_agent="11", to_agent="external", intent="offer.share"))
    assert r["status"] != "held"


# ------------------------------------------- 1.7 confidence_underflow queue
def test_confidence_underflow_is_an_escalation_queue(tmp_path):
    seen = []
    hub = hub_on(tmp_path, human_notifier=lambda q, r: seen.append(q) or {"status": "sent"})
    out = hub.escalate("escalation.confidence_underflow",
                       {"client_context_id": "c9", "agent": "02", "reason": "under floor"})
    assert out == {"status": "escalated", "queue": "escalation.confidence_underflow"}
    assert hub.queues["escalation.confidence_underflow"][-1]["client_context_id"] == "c9"
    assert seen == ["escalation.confidence_underflow"]
    raised = [e for e in hub.audit.read() if e["kind"] == "escalation.raised"]
    assert raised[-1]["queue"] == "escalation.confidence_underflow"
    with pytest.raises(KeyError):
        hub.escalate("escalation.no_such_queue", {})


# ----------------------------------------------- 1.7 arm_signer_registry
def identity_root(tmp_path, status="RATIFIED 2026-10-13 test owner"):
    root = tmp_path / "ident"
    (root / "config").mkdir(parents=True)
    (root / "config" / "authority_signers.json").write_text(json.dumps({
        "_status": status,
        "entries": [{"intent": "config.update", "signer_login": "owner@example.test",
                     "idp": "test", "mfa_required": True,
                     "effective": "2026-01-01", "revoked": None}]}), encoding="utf-8")
    return str(root)


def test_arm_signer_registry_arms_from_an_identity_and_logs_it(tmp_path):
    hub = hub_on(tmp_path)
    reg = hub.arm_signer_registry(identity_root(tmp_path))
    assert hub.signer_registry is reg
    armed = [e for e in hub.audit.read() if e["kind"] == "signer.armed"]
    assert armed and armed[-1]["intents"] == ["config.update"]
    assert armed[-1]["identity_root"].endswith("ident")


def test_arm_signer_registry_refuses_an_unratified_registry(tmp_path):
    hub = hub_on(tmp_path)
    with pytest.raises(ValueError, match="UNRATIFIED"):
        hub.arm_signer_registry(identity_root(tmp_path, status="UNRATIFIED TEMPLATE"))
    assert hub.signer_registry is None
    assert not any(e["kind"] == "signer.armed" for e in hub.audit.read())


def test_arm_signer_registry_default_root_names_the_missing_file_in_the_core(tmp_path):
    # The default root is the repo holding dispatcher/ - an identity repo in
    # practice. The core repo itself carries no config/, so it must refuse
    # by name rather than arm on nothing.
    hub = hub_on(tmp_path)
    with pytest.raises(FileNotFoundError, match="authority_signers.json absent"):
        hub.arm_signer_registry()
    assert hub.signer_registry is None


# -------------------------------------------------- 1.7 WhatsApp notifier
class _Resp:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_whatsapp_sends_with_the_channel_prefix(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")
    sent = []

    def fake_urlopen(req, timeout=5.0):
        sent.append(urllib.parse.parse_qs(req.data.decode()))
        return _Resp()
    monkeypatch.setattr(n.urllib.request, "urlopen", fake_urlopen)
    out = n.send_whatsapp_via_twilio("hello", to_number="+15550000002", from_number="whatsapp:+15550000001")
    assert out == {"status": "sent", "http_status": 201, "channel": "whatsapp"}
    assert sent[0]["To"] == ["whatsapp:+15550000002"]
    assert sent[0]["From"] == ["whatsapp:+15550000001"]
    assert sent[0]["Body"] == ["hello"]


def test_whatsapp_failures_are_reported_not_raised(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")

    def http_error(req, timeout=5.0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad creds"))
    monkeypatch.setattr(n.urllib.request, "urlopen", http_error)
    out = n.send_whatsapp_via_twilio("x")
    assert out["status"] == "failed" and out["http_status"] == 401 and out["channel"] == "whatsapp"

    def down(req, timeout=5.0):
        raise OSError("network down")
    monkeypatch.setattr(n.urllib.request, "urlopen", down)
    out = n.send_whatsapp_via_twilio("x")
    assert out["status"] == "failed" and "network down" in out["error"]


def test_whatsapp_human_notifier_formats_and_sends(monkeypatch):
    import dispatcher.notifier as n
    bodies = []
    monkeypatch.setattr(n, "send_whatsapp_via_twilio",
                        lambda body, **kw: bodies.append(body) or {"status": "sent", "channel": "whatsapp"})
    out = n.whatsapp_human_notifier("escalation.hot_lead", {"client_context_id": "c1", "agent": "02"})
    assert out["status"] == "sent"
    assert bodies == [n.format_notification_text("escalation.hot_lead",
                                                 {"client_context_id": "c1", "agent": "02"})]
