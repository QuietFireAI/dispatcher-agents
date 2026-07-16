import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.notifier import (format_notification_text, send_sms_via_twilio,
                                  sms_human_notifier, DEFAULT_TO_NUMBER)


def test_placeholder_number_is_nanp_fictional_range():
    """555-01xx is NANP's reserved-for-fiction block - guaranteed to never
    route to a real phone, and trivially greppable to replace."""
    assert DEFAULT_TO_NUMBER == "+15555550100"


def test_format_pulls_real_fields_never_invents_them():
    text = format_notification_text("escalation.legal_line", {
        "client_context_id": "ctx-123", "agent": "11",
        "trigger": "client asked for a pricing opinion"})
    assert "escalation.legal_line" in text
    assert "agent=11" in text
    assert "ctx-123" in text
    assert "client asked for a pricing opinion" in text


def test_format_handles_missing_fields_gracefully():
    text = format_notification_text("clarification.request", {})
    assert "clarification.request" in text
    assert "ctx=unknown" in text
    assert "agent=?" in text


def test_format_caps_length_for_sms():
    huge_reason = "x" * 1000
    text = format_notification_text("escalation.complaint",
                                    {"reason": huge_reason})
    assert len(text) <= 320


def test_real_network_call_reaches_actual_twilio_endpoint():
    """Proves this is real wiring, not a stub - with placeholder
    credentials this genuinely fails with Twilio's own 401, not a local
    mock or a DNS/connection error. That specific failure mode IS the
    proof the request is correctly formed and actually reaching Twilio."""
    result = send_sms_via_twilio("wiring check")
    assert result["status"] == "failed"
    assert result["http_status"] == 401
    assert "Authentication Error" in result["body"] or "20003" in result["body"]


def test_human_notifier_signature_matches_hub_expectation(monkeypatch):
    """Confirms sms_human_notifier(queue, record) is call-compatible with
    how Hub.escalate()/the queue-delivery fix actually invoke it, using an
    injected transport so this test doesn't depend on live network."""
    calls = []

    def fake_send(body, to_number=None, timeout=5.0):
        calls.append(body)
        return {"status": "sent", "http_status": 201}

    import dispatcher.notifier as notifier_module
    monkeypatch.setattr(notifier_module, "send_sms_via_twilio", fake_send)

    result = notifier_module.sms_human_notifier(
        "escalation.hot_lead", {"client_context_id": "c-1", "agent": "02"})
    assert result["status"] == "sent"
    assert len(calls) == 1
    assert "escalation.hot_lead" in calls[0]
