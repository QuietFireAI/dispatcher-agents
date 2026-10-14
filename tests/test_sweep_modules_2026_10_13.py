"""Tests written from the 2026-10-13 full mutation sweep (repair plan 1.4):
the loader, the scheduler, territories, attestation, signatures, the signer
registry, the pillar seams, analysis, the shipped notifier and runs.

Each test holds a promise the module makes that the sweep showed no test
held: the record a scheduler or a transfer leaves, what a loader refuses,
what a transfer carries, the exact request the notifier sends.
"""
import base64
import hashlib
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import AuditLog, Envelope, Routes  # noqa: E402
from dispatcher.hub import Hub  # noqa: E402
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


class Rec:
    """An audit stand-in that keeps (kind, record) pairs in order."""

    def __init__(self):
        self.events = []

    def append(self, kind, record):
        self.events.append((kind, record))

    def kinds(self):
        return [k for k, _ in self.events]


def hub_at(tmp_path, name="audit.jsonl", **kw):
    return Hub(Routes(FIX), AuditLog(str(tmp_path / name)), **kw)


def kinds(hub):
    return [e["kind"] for e in hub.audit.read()]


def entries(hub, kind):
    return [e for e in hub.audit.read() if e["kind"] == kind]


# ===================================================================== loader
def identity(tmp_path, routes=None, classes=None, status="RATIFIED 2026-10-13", manners=True):
    root = tmp_path / "ident"
    root.mkdir()
    (root / "routes.json").write_text(json.dumps(
        {"routes": routes if routes is not None else
         [{"intent": "a", "senders": ["01"], "receivers": ["02"]}]}), encoding="utf-8")
    (root / "01-agent").mkdir()
    (root / "01-agent" / "SKILL.md").write_text("skill", encoding="utf-8")
    (root / "priority.json").write_text(json.dumps(
        {"_status": status, "classes": classes if classes is not None else {"P01": 2}}),
        encoding="utf-8")
    if manners:
        (root / "MANNERS.md").write_text("manners", encoding="utf-8")
    return str(root)


@pytest.mark.parametrize("routes", [[], {"intent": "a"}, "routes"])
def test_a_track_that_is_not_a_non_empty_list_refuses_to_load(tmp_path, routes):
    from dispatcher.loader import load_identity
    with pytest.raises(ValueError, match="routes.json present but invalid"):
        load_identity(identity(tmp_path, routes=routes))


@pytest.mark.parametrize("classes", [{"P01": 0}, {"P01": 5}, {"P01": "2"}, {"P01": 2.0}])
def test_priority_classes_must_be_whole_numbers_one_to_four(tmp_path, classes):
    from dispatcher.loader import load_identity
    with pytest.raises(ValueError, match="must be int 1..4"):
        load_identity(identity(tmp_path, classes=classes))


def test_priority_classes_one_and_four_are_accepted(tmp_path):
    from dispatcher.loader import load_identity
    ident = load_identity(identity(tmp_path, classes={"P01": 1, "P02": 4}))
    assert ident.priority_classes == {"P01": 1, "P02": 4} and ident.warnings == []


@pytest.mark.parametrize("status,warned", [("DRAFT - not ratified", True), ("pending owner", True),
                                           ("RATIFIED 2026-10-13", False)])
def test_a_draft_or_pending_priority_table_loads_with_a_warning(tmp_path, status, warned):
    from dispatcher.loader import load_identity
    ident = load_identity(identity(tmp_path, status=status))
    assert ident.priority_status == status
    assert (f"priority table is {status}" in ident.warnings) is warned


def test_an_identity_without_manners_says_so(tmp_path):
    from dispatcher.loader import load_identity
    ident = load_identity(identity(tmp_path, manners=False))
    assert ident.manners_path is None
    assert any(w.startswith("MANNERS.md absent") for w in ident.warnings)


# ================================================================== scheduler
def test_the_scheduler_records_every_grant_siding_and_release():
    from dispatcher.priority import SidingScheduler
    a = Rec()
    s = SidingScheduler(a, {"low": 3, "high": 1})
    assert s.request_segment("r-low", "low", "07")["granted"] is True
    assert s.request_segment("r-high", "high", "07")["sided"] == "r-low"
    assert s.request_segment("r-low2", "low", "07")["granted"] is False
    assert s.release_segment("r-high", "07") == {"resumed": "r-low", "spoke": "07"}
    assert a.kinds() == ["segment.grant", "siding.hold", "segment.grant", "siding.hold",
                         "segment.release", "siding.resume", "segment.grant"]
    assert a.events[0][1] == {"run": "r-low", "spoke": "07", "class": 3}
    assert a.events[2][1] == {"run": "r-high", "spoke": "07", "class": 1}
    assert a.events[4][1] == {"run": "r-high", "spoke": "07"}
    assert a.events[6][1] == {"run": "r-low", "spoke": "07", "class": 3}


def test_only_the_holder_can_release_a_segment():
    from dispatcher.priority import SidingScheduler
    s = SidingScheduler(Rec(), {"p": 2})
    with pytest.raises(ValueError, match="does not hold segment"):
        s.release_segment("r1", "07")                      # nobody holds it
    s.request_segment("r1", "p", "07")
    with pytest.raises(ValueError, match="does not hold segment"):
        s.release_segment("r2", "07")                      # somebody else holds it
    assert s.holding["07"] == ("r1", 2)


# ================================================================ territories
def test_a_transfer_carries_only_its_contexts_and_the_receiver_adopts_their_open_items(tmp_path):
    from dispatcher.territory import build_transfer, confirm_release, receive_transfer
    signer = Ed25519Signer()
    a = hub_at(tmp_path, "a.jsonl")
    a.queues["clarification.request"] += [{"client_context_id": "c1", "x": 1},
                                           {"client_context_id": "c2", "x": 2}, "not-a-dict"]
    rec = build_transfer(a, ["c1", "c-new"], signer, manifest={"core.py": "abc"})
    assert rec["contexts"] == {"c1": {"sequence_hwm": 0}, "c-new": {"sequence_hwm": 0}}
    assert rec["open_items"] == {"clarification.request": [{"client_context_id": "c1", "x": 1}]}
    assert rec["attested_manifest"] == {"core.py": "abc"}
    sent = entries(a, "territory.transfer.sent")[-1]
    assert sent["contexts"] == ["c1", "c-new"] and sent["signed"] is True
    # a transport may re-serialise the record with its keys in another order
    moved = json.loads(json.dumps(rec, sort_keys=True))
    b = hub_at(tmp_path, "b.jsonl")
    ack = receive_transfer(b, moved, Ed25519Verifier(signer.public_key_bytes()))
    assert ack == {"status": "ack", "contexts": ["c-new", "c1"]}
    assert b.queues["clarification.request"] == [{"client_context_id": "c1", "x": 1}]
    got = entries(b, "territory.transfer.received")[-1]
    assert got["open_item_queues"] == ["clarification.request"]
    assert entries(b, "territory.transfer.ack")[-1]["contexts"] == ["c-new", "c1"]
    confirm_release(a, ["c1", "c-new"], ack)
    assert entries(a, "territory.transfer.released")[-1]["contexts"] == ["c1", "c-new"]


def test_a_refused_transfer_is_on_the_record(tmp_path):
    from dispatcher.territory import build_transfer, receive_transfer
    rec = build_transfer(hub_at(tmp_path, "a.jsonl"), ["c1"], Ed25519Signer())
    b = hub_at(tmp_path, "b.jsonl")
    res = receive_transfer(b, rec, Ed25519Verifier(Ed25519Signer().public_key_bytes()))
    assert res["status"] == "refused"
    r = entries(b, "territory.transfer.refused")[-1]
    assert r["contexts"] == ["c1"] and r["reason"].startswith("territory transfer signature")


# ================================================================ attestation
def test_the_manifest_covers_the_track_and_names_what_is_missing(tmp_path):
    from dispatcher.attestation import build_manifest, verify_manifest
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    routes = tmp_path / "routes.json"
    routes.write_text("{}", encoding="utf-8")
    m = build_manifest(str(pkg), str(routes))
    assert set(m) == {"a.py", "routes.json"}
    assert m["routes.json"] == hashlib.sha256(b"{}").hexdigest()
    gone = dict(m, **{"b.py": "0" * 64})
    assert verify_manifest(gone, str(pkg), str(routes)) == ["b.py: in manifest, absent on disk"]
    changed = dict(m, **{"a.py": "f" * 64})
    assert verify_manifest(changed, str(pkg), str(routes)) == [
        f"a.py: hash mismatch (attested {'f' * 12}…, running {m['a.py'][:12]}…)"]


def test_a_signer_without_sign_bytes_gives_an_unsigned_attestation(tmp_path):
    from dispatcher.attestation import attest_boot
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    routes = tmp_path / "routes.json"
    routes.write_text("{}", encoding="utf-8")
    hub = hub_at(tmp_path)
    attest_boot(hub, str(pkg), str(routes), signer=object())
    rec = entries(hub, "boot.attestation")[-1]
    assert rec["signed"] is False and "manifest_signature" not in rec


# ================================================================= signatures
def test_a_signature_survives_the_payload_keys_arriving_in_another_order():
    s = Ed25519Signer()
    e = Envelope(from_agent="human", to_agent="05", intent="config.update", client_context_id="c",
                 payload={"b": 1, "a": {"y": 2, "x": 3}}, provenance={"source": "t"})
    s.sign(e)
    e.payload = {"a": {"x": 3, "y": 2}, "b": 1}
    assert Ed25519Verifier(s.public_key_bytes()).verify(e) is True


def test_a_key_saved_and_loaded_is_the_same_key():
    s = Ed25519Signer()
    again = Ed25519Signer(private_key_bytes=s.private_key_bytes())
    assert len(s.private_key_bytes()) == 32
    assert again.public_key_bytes() == s.public_key_bytes()


def test_verification_answers_false_never_none():
    s = Ed25519Signer()
    v = Ed25519Verifier(s.public_key_bytes())
    e = Envelope(from_agent="human", to_agent="05", intent="x", client_context_id="c",
                 payload={}, provenance={})
    assert v.verify(e) is False                                 # no signature
    assert v.verify_bytes(b"data", "zz") is False               # not hex
    assert v.verify_bytes(b"data", s.sign_bytes(b"other")) is False


# ============================================================ signer registry
@pytest.mark.parametrize("entry", [{"intent": "x"}, {"signer_login": "jeff"}])
def test_a_signer_entry_without_intent_or_login_refuses_to_arm(entry):
    from dispatcher.signer_registry import SignerRegistry
    with pytest.raises(ValueError, match="missing intent or signer_login"):
        SignerRegistry([{"mfa_required": True, **entry}])


def test_a_stamp_without_a_login_is_refused_by_name():
    from dispatcher.signer_registry import SignerRegistry
    reg = SignerRegistry([{"intent": "config.update", "signer_login": "jeff", "idp": "okta",
                           "mfa_required": True, "effective": "2026-01-01", "revoked": None}])
    e = Envelope(from_agent="human", to_agent="05", intent="config.update", client_context_id="c",
                 payload={}, provenance={"signer": {"mfa": True}})
    v = reg.check(e)
    assert v.ok is False and v.reason == "signer stamp missing signer_login"


# ================================================================ pillar seams
def test_before_turn_reviews_the_last_three_thoughts_and_returns_its_record(tmp_path):
    from dispatcher.pillars import before_turn_check
    hub = hub_at(tmp_path)
    for n in range(5):
        hub._reflect(f"e{n}", f"thought {n}", "done")
    rec = before_turn_check(hub)
    assert rec["thoughts_reviewed"] == 3
    assert rec["recent_thoughts"] == ["thought 2", "thought 3", "thought 4"]
    assert entries(hub, "beforeturn.check")[-1]["thoughts_reviewed"] == 3


def test_the_exit_gate_without_a_reviewer_says_it_is_off(tmp_path):
    from dispatcher.pillars import exit_gate
    hub = hub_at(tmp_path)
    e = Envelope(from_agent="11", to_agent="external", intent="x", client_context_id="c",
                 payload={"text": "hi"}, provenance={"source": "t"})
    assert exit_gate(hub, e) == {"armed": False, "passed": None}
    off = entries(hub, "selfcheck.unarmed")[-1]
    assert off["envelope_id"] == e.envelope_id


def test_a_sleepmark_carries_the_last_five_thoughts_and_restore_returns_it(tmp_path):
    from dispatcher.pillars import capture_sleepmark, restore_sleepmark
    hub = hub_at(tmp_path)
    for n in range(7):
        hub._reflect(f"e{n}", f"t{n}", "done")
    out = capture_sleepmark(hub, "handover")
    assert out["traces"] == 5
    assert [t["thinking"] for t in out["mark"]["reasoning_traces"]] == ["t2", "t3", "t4", "t5", "t6"]
    assert restore_sleepmark(hub, out["mark"]) is out["mark"]


# =================================================================== analysis
def test_drift_is_flagged_at_or_above_the_threshold(tmp_path):
    from dispatcher.analysis import analyze_reflections
    hub = hub_at(tmp_path)                       # no reviewer pair: no second opinion
    hub._reflect("e1", "route the lead to 02", "routed the lead to 02")
    assert [r["flagged"] for r in analyze_reflections(hub, drift_threshold=0.0)] == [True]
    assert [r["flagged"] for r in analyze_reflections(hub, drift_threshold=1.01)] == [False]
    assert "splitvantage.review" not in kinds(hub)


def test_a_trace_without_thought_found_only_at_analysis_is_flagged_there(tmp_path):
    from dispatcher.analysis import score_spoke_traces
    hub = hub_at(tmp_path)
    hub.spoke_traces.append({"agent": "03", "envelope_id": "e7", "thought": "", "result": "sent"})
    out = score_spoke_traces(hub)
    assert out[0]["tainted"] is True
    t = entries(hub, "agentopenmind.tainted")[-1]
    assert t["envelope_id"] == "e7" and t["agent"] == "03"
    assert [i["envelope_id"] for i in hub.queues["integrity.violation"]] == ["e7"]


# ============================================================ absolute signal
def test_the_signal_hash_is_sha256_of_its_text():
    from dispatcher.absolute_signal import SIGNAL_TEXT, signal_sha256
    assert signal_sha256() == hashlib.sha256(SIGNAL_TEXT.encode("utf-8")).hexdigest()


# ===================================================================== runs
def test_a_watchdog_needs_a_positive_cadence():
    from dispatcher.runs import Watchdog
    for bad in (0, -1):
        with pytest.raises(ValueError, match="cadence must be positive"):
            Watchdog(cadence_s=bad)


def test_every_playbook_step_is_on_the_record(tmp_path):
    from dispatcher.runs import PlaybookRun
    hub = hub_at(tmp_path)
    run = PlaybookRun(hub, "P11", "r1", "c1")
    run.step(1, "env-1", "lead in")
    run.complete()
    s = entries(hub, "playbook.step")
    assert [(x["step"], x["envelope_id"], x["note"]) for x in s] == [(1, "env-1", "lead in")]


# =================================================================== notifier
class Captured:
    def __init__(self, status=201):
        self.req = None
        self.status = status

    def __call__(self, req, timeout=5.0):
        self.req = req
        status = self.status

        class R:
            def __enter__(s):
                s.status = status
                return s

            def __exit__(s, *a):
                return False
        return R()


def test_the_sms_request_is_the_one_twilio_expects(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")
    cap = Captured()
    monkeypatch.setattr(n.urllib.request, "urlopen", cap)
    out = n.send_sms_via_twilio("hello")
    assert out == {"status": "sent", "http_status": 201, "is_fallback": False}
    auth = base64.b64encode(f"{n.TWILIO_ACCOUNT_SID}:{n.TWILIO_AUTH_TOKEN}".encode()).decode()
    assert cap.req.get_header("Authorization") == f"Basic {auth}"
    assert cap.req.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert f"To={n.TWILIO_TO_NUMBER.replace('+', '%2B')}" in cap.req.data.decode()
    n.send_sms_via_twilio("hello", to_number="+15550001111")
    assert "To=%2B15550001111" in cap.req.data.decode()


def test_the_whatsapp_request_is_the_one_twilio_expects(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")
    cap = Captured()
    monkeypatch.setattr(n.urllib.request, "urlopen", cap)
    assert n.send_whatsapp_via_twilio("hi", to_number="+15550001111") == \
        {"status": "sent", "http_status": 201, "channel": "whatsapp"}
    assert cap.req.get_header("Authorization").startswith("Basic ")
    assert cap.req.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert "To=whatsapp%3A%2B15550001111" in cap.req.data.decode()


def test_a_refused_page_keeps_at_most_300_characters_of_the_answer(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")

    def refuse(req, timeout=5.0):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"z" * 900))
    monkeypatch.setattr(n.urllib.request, "urlopen", refuse)
    sms = n.send_sms_via_twilio("x")
    assert sms["status"] == "failed" and sms["is_fallback"] is True and sms["body"] == "z" * 300
    wa = n.send_whatsapp_via_twilio("x")
    assert wa["status"] == "failed" and wa["body"] == "z" * 300


def test_a_wait_id_is_named_in_the_page_only_when_there_is_one():
    from dispatcher.notifier import format_notification_text
    assert "wait_id=w-9" in format_notification_text("q", {"wait_id": "w-9"})
    assert "wait_id" not in format_notification_text("q", {})


def test_a_page_that_never_reaches_twilio_is_a_failure_with_the_error(monkeypatch):
    import dispatcher.notifier as n
    monkeypatch.setattr(n, "TWILIO_ACCOUNT_SID", "ACtest")      # set, not the placeholder
    monkeypatch.setattr(n, "TWILIO_AUTH_TOKEN", "test-token")

    def offline(req, timeout=5.0):
        raise OSError("network unreachable")
    monkeypatch.setattr(n.urllib.request, "urlopen", offline)
    out = n.send_sms_via_twilio("x")
    assert out["status"] == "failed" and out["is_fallback"] is True
    assert out["error"] == "OSError('network unreachable')"
    assert "not delivered" in out["fallback_reason"]


def test_two_beats_are_enough_for_the_watchdog():
    from dispatcher.runs import Watchdog
    obs = Watchdog(cadence_s=1.0).observe([{"kind": "heartbeat", "ts": 5.0},
                                           {"kind": "heartbeat", "ts": 8.0}])
    assert obs == {"computable": True, "beats": 2, "span_s": 3.0, "max_gap_s": 3.0,
                   "gap_incidents": 1, "cadence_s": 1.0}
