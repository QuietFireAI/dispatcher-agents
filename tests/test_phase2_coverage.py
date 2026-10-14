"""Tests for the four mutants the 2026-10-14 full sweep left alive in the
Phase 2 code (kpi transfer guard, notifier placeholder check, the 320
character cap, the not-configured channel field)."""
import dispatcher.notifier as n
from dispatcher.kpi import compute_kpis


def persisted(ctx, seq, i):
    return {"kind": "envelope.persisted", "envelope_id": f"e{i}", "ts": float(i),
            "client_context_id": ctx, "sequence": seq}


def test_a_malformed_transfer_record_is_ignored_not_raised():
    events = [{"kind": "territory.transfer.received", "ts": 0.0,
               "contexts": {"c1": {"sequence_hwm": "7"}, "c2": 5}},
              persisted("c1", 1, 1), persisted("c2", 1, 2), persisted("c2", 2, 3)]
    k = compute_kpis(events)
    # neither context had a usable high-water mark, so both start at 1
    assert k["sequence_gap_incidents"] == 0 and k["sequence_restarts"] == 0


def test_one_placeholder_credential_is_enough_to_send_nothing(monkeypatch):
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACreal")
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "PLACEHOLDER_AUTH_TOKEN")
    assert n.twilio_configured() is False
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "PLACEHOLDER_ACCOUNT_SID")
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "real-token")
    assert n.twilio_configured() is False
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACreal")
    assert n.twilio_configured() is True


def test_the_text_is_cut_at_exactly_320_characters():
    assert len(n.format_notification_text("q", {"client_context_id": "x" * 400})) == 320


def test_not_configured_names_the_channel_only_when_there_is_one():
    assert n._not_configured("whatsapp")["channel"] == "whatsapp"
    assert "channel" not in n._not_configured()
