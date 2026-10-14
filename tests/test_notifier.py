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
    # the trigger text can carry a client's words; it never leaves in a text (2026-10-14)
    assert "client asked for a pricing opinion" not in text


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


def test_placeholder_credentials_send_nothing_and_say_so(monkeypatch):
    """Until 2026-10-14 this test posted to the real Twilio API with the
    placeholder credentials and expected a 401 - the suite depended on
    Twilio, and every escalation did the same in use. Now nothing is posted
    while the credentials are placeholders; the request Twilio receives once
    they are set is checked with a captured request in
    tests/test_sweep_modules_2026_10_13.py."""
    import dispatcher.notifier as n
    calls = []
    # the shipped defaults, whatever TWILIO_* this machine has set
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "PLACEHOLDER_ACCOUNT_SID")
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "PLACEHOLDER_AUTH_TOKEN")
    monkeypatch.setattr(n.urllib.request, "urlopen", lambda *a, **k: calls.append(a))
    assert n.twilio_configured() is False
    for result in (send_sms_via_twilio("wiring check"), n.send_whatsapp_via_twilio("x")):
        assert result["status"] == "failed" and result["is_fallback"] is True
        assert result["error"].startswith("Twilio not configured")
    assert calls == []


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
