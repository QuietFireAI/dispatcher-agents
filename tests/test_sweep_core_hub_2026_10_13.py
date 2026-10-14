"""Tests written from the 2026-10-13 full mutation sweep (repair plan 1.4):
core.py and hub.py.

The sweep put 807 mutants into the 16 core files, one at a time, and ran the
whole core suite against each. These tests are for mutants that survived and
changed behavior the core promises - who may send on a route, wildcard
routes, the unverified-audience marker, what an unsigned disclosure leaves
on the record, dead letters, the anchor checks, the hash format, disk
flushes. Each test names the promise it holds, not the mutant.
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher import core  # noqa: E402
from dispatcher.core import AuditLog, AuditLogDamaged, Envelope, Routes  # noqa: E402
from dispatcher.hub import Hub, Reject  # noqa: E402
from dispatcher.signatures import Ed25519Signer  # noqa: E402
from dispatcher.signer_registry import SignerRegistry  # noqa: E402


def routes_file(tmp_path, routes):
    p = str(tmp_path / "routes.json")
    json.dump({"version": "t", "routes": routes}, open(p, "w", encoding="utf-8"))
    return Routes(p)


def env(frm, to, intent, ctx="c1", payload=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent, client_context_id=ctx,
                    payload=payload or {}, provenance={"source": "test"})


def kinds(hub):
    return [e["kind"] for e in hub.audit.read()]


def entries(hub, kind):
    return [e for e in hub.audit.read() if e["kind"] == kind]


# =============================================================== the envelope
def test_an_envelope_is_not_escalated_unless_the_sender_says_so():
    e = env("01", "02", "x")
    assert e.escalation_flag is False and e.in_reply_to is None and e.confidence == "unknown"


# ================================================================== the routes
WILD = [
    {"intent": "lead.*", "senders": ["01"], "receivers": ["02"]},
    {"intent": "vendor.*", "senders": ["09"], "receivers": ["external"],
     "audience": "public", "audience_verified": True},
    {"intent": "client.*", "senders": ["11"], "receivers": ["external"],
     "audience": "counterparty"},
    {"intent": "vendor.book", "senders": ["09"], "receivers": ["external"],
     "audience": "counterparty", "audience_verified": False},
]


def test_a_wildcard_route_covers_its_own_family_and_nothing_else(tmp_path):
    r = routes_file(tmp_path, WILD)
    assert r.tuple_legal("01", "lead.new", "02") is True
    assert r.tuple_legal("01", "lead.", "02") is True
    # "lead.*" is the family "lead." - not every intent that starts with "lead"
    assert r.tuple_legal("01", "leadership.x", "02") is False
    assert r.tuple_legal("01", "other.intent", "02") is False
    assert list(r.matches("leadx")) == []


def test_audience_lookups_follow_the_same_wildcard_rule(tmp_path):
    r = routes_file(tmp_path, WILD)
    assert r.audience_for("vendor.schedule") == "public"
    assert r.audience_for("vendorx") is None
    assert r.audience_for("lead.new") is None                 # not an external route
    assert r.audience_is_verified("vendor.schedule") is True
    assert r.audience_is_verified("client.message") is False  # label never confirmed
    assert r.audience_is_verified("vendorx") is False
    assert r.audience_is_verified("unknown.intent") is False  # no route: never verified


def test_an_exact_route_is_found_by_its_own_name(tmp_path):
    r = routes_file(tmp_path, [
        {"intent": "vendor.book", "senders": ["09"], "receivers": ["external"],
         "audience": "public", "audience_verified": True},
        {"intent": "vendor.cancel", "senders": ["09"], "receivers": ["external"],
         "audience": "counterparty", "audience_verified": False}])
    assert r.audience_is_verified("vendor.cancel") is False
    assert r.audience_is_verified("vendor.book") is True
    assert r.audience_for("vendor.cancel") == "counterparty"


def test_only_the_named_senders_may_use_a_route(tmp_path):
    r = routes_file(tmp_path, [
        {"intent": "config.update", "senders": ["human"], "receivers": ["05"]},
        {"intent": "listing.draft", "senders": ["04"], "receivers": ["05"]}])
    assert r.tuple_legal("human", "config.update", "05") is True
    assert r.tuple_legal("04", "config.update", "05") is False   # a human-only lane
    assert r.tuple_legal("human", "listing.draft", "05") is False  # not a human lane


def test_only_the_named_receivers_may_get_a_route(tmp_path):
    r = routes_file(tmp_path, [
        {"intent": "listing.draft", "senders": ["04"], "receivers": ["05"]},
        {"intent": "vendor.schedule", "senders": ["09"], "receivers": ["external"]}])
    for special in ("external", "human", "queue"):
        assert r.tuple_legal("04", "listing.draft", special) is False, special
    assert r.tuple_legal("09", "vendor.schedule", "external") is True
    assert r.tuple_legal("09", "vendor.schedule", "human") is False


# =============================================================== the audit log
def test_the_entry_hash_is_sha256_over_the_canonical_json(tmp_path):
    # The hash format is a promise to anyone verifying the log with their own
    # tools: sha256(previous hash + JSON of the entry without its two hash
    # fields, keys sorted, no spaces).
    log = AuditLog(str(tmp_path / "a.jsonl"))
    log.append("x", {"zeta": 1, "alpha": {"b": 2, "a": 1}})
    e = log.read()[0]
    body = {k: v for k, v in e.items() if k not in ("prev_hash", "entry_hash")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
    assert e["entry_hash"] == hashlib.sha256((AuditLog.GENESIS + canon).encode()).hexdigest()


def test_a_caller_cannot_supply_the_chain_fields(tmp_path):
    log = AuditLog(str(tmp_path / "a.jsonl"))
    log.append("x", {"n": 1})
    log.append("y", {"n": 2, "prev_hash": "forged", "entry_hash": "forged"})
    e = log.read()
    assert e[1]["prev_hash"] == e[0]["entry_hash"] and e[1]["entry_hash"] != "forged"
    assert log.verify_chain() == {"ok": True, "entries": 2, "break_at": None}


def test_every_append_reaches_the_disk_before_it_returns(tmp_path, monkeypatch):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    sizes = []
    real = os.fsync

    def fsync(fd):
        sizes.append(os.fstat(fd).st_size)      # what is in the file at that moment
        return real(fd)
    monkeypatch.setattr(core.os, "fsync", fsync)
    log.append("x", {"n": 1})
    assert sizes == [os.path.getsize(p)]         # flushed, then synced, the whole line


def test_a_repair_syncs_the_side_file_and_the_repaired_log_before_swapping(tmp_path, monkeypatch):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    for n in range(2):
        log.append("x", {"n": n})
    complete = os.path.getsize(p)
    open(p, "ab").write(b'{"half')
    seen = []
    real = os.fsync

    def fsync(fd):
        seen.append(os.fstat(fd).st_size)
        return real(fd)
    monkeypatch.setattr(core.os, "fsync", fsync)
    AuditLog.repair_torn_tail(p)
    # the side file (6 bytes, flushed), then the whole repaired log before it
    # is swapped in, then (not on Windows) the folder, so the swap is durable
    assert seen[0] == 6 and seen[1] == os.path.getsize(p) > complete
    assert len(seen) == (2 if os.name == "nt" else 3)


def test_repair_of_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no such audit log"):
        AuditLog.repair_torn_tail(str(tmp_path / "missing.jsonl"))


def test_repair_names_earlier_damage_as_damage_not_a_cut_off(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    for n in range(3):
        log.append("x", {"n": n})
    lines = open(p, "rb").read().split(b"\n")
    lines[1] = b"{nope"
    open(p, "wb").write(b"\n".join(lines) + b'{"half')
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog.repair_torn_tail(p)
    assert ei.value.line == 2 and ei.value.torn_tail is False


def test_damage_mid_file_is_not_a_cut_off_even_when_the_file_lacks_a_final_newline(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    for n in range(3):
        log.append("x", {"n": n})
    lines = open(p, "rb").read().rstrip(b"\n").split(b"\n")
    lines[1] = b"{nope"
    open(p, "wb").write(b"\n".join(lines))          # last entry complete, no newline
    c = AuditLog.check_file(p)
    assert c["break_at"] == 2 and c["torn_tail"] is False
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog(p)
    assert ei.value.torn_tail is False
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog.reader(p).read()
    assert ei.value.line == 2 and ei.value.torn_tail is False


def test_an_absent_or_empty_log_reads_as_empty_and_intact(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    assert log.read() == []
    assert log.verify_chain() == {"ok": True, "entries": 0, "break_at": None}
    assert log.anchor() == {"entries": 0, "head_hash": AuditLog.GENESIS}
    open(p, "w").close()                                    # exists, empty
    assert log.verify_chain() == {"ok": True, "entries": 0, "break_at": None}
    assert AuditLog.check_file(p)["entries"] == 0


# ------------------------------------------------------------------- anchors
def test_an_empty_log_anchor_verifies_only_with_genesis(tmp_path):
    log = AuditLog(str(tmp_path / "a.jsonl"))
    assert log.verify_anchor({"entries": 0, "head_hash": AuditLog.GENESIS}) == \
        {"ok": True, "reason": None}
    assert log.verify_anchor({"entries": 0, "head_hash": "f" * 64}) == \
        {"ok": False, "reason": "empty-log anchor must carry GENESIS"}


def test_an_anchor_fails_when_the_chain_breaks_before_it(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    for n in range(3):
        log.append("x", {"n": n})
    a = log.anchor()
    lines = open(p, "rb").read().split(b"\n")
    lines[0] = lines[0].replace(b'"n": 0', b'"n": 7')
    open(p, "wb").write(b"\n".join(lines))
    r = log.verify_anchor(a)
    assert r["ok"] is False and r["reason"] == "chain breaks at line 1, before the anchored position 3"


def test_an_anchor_still_holds_when_entries_were_added_after_it(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    log.append("x", {"n": 0})
    a = log.anchor()
    log.append("x", {"n": 1})
    assert log.verify_anchor(a) == {"ok": True, "reason": None}


def test_an_anchor_names_a_deleted_log(tmp_path):
    p = str(tmp_path / "a.jsonl")
    log = AuditLog(p)
    log.append("x", {"n": 0})
    a = log.anchor()
    os.remove(p)
    r = log.verify_anchor(a)
    assert r["ok"] is False and r["reason"].endswith("log file absent - wholesale deletion")


# ======================================================================= hub
def plain_hub(tmp_path, routes=None, **kw):
    r = routes_file(tmp_path, routes or [
        {"intent": "work.do", "senders": ["01"], "receivers": ["02"]},
        {"intent": "work.park", "senders": ["01"], "receivers": ["queue"]}])
    return Hub(r, AuditLog(str(tmp_path / "audit.jsonl")), **kw)


def test_a_reject_carries_its_reason_as_its_message():
    e = Reject("schema: bad")
    assert e.reason == "schema: bad" and str(e) == "schema: bad" and e.args == ("schema: bad",)


def test_the_default_loop_threshold_is_twenty(tmp_path):
    hub = plain_hub(tmp_path)
    hub.register("02", lambda e: None)
    out = [hub.send(env("01", "02", "work.do"))["status"] for _ in range(21)]
    assert out[:20] == ["ack"] * 20 and out[20] == "suspended"


def test_boot_declares_each_reviewer_that_is_off_and_only_those(tmp_path):
    hub = plain_hub(tmp_path)
    assert {"selfcheck.unarmed", "splitvantage.unarmed"} <= set(kinds(hub))
    armed = Hub(routes_file(tmp_path, [{"intent": "a", "senders": ["01"], "receivers": ["02"]}]),
                AuditLog(str(tmp_path / "armed.jsonl")),
                selfcheck_model=lambda *a, **k: "PASS", crosspol_models=(lambda *a: "x", lambda *a: "y"))
    k = kinds(armed)
    assert "selfcheck.unarmed" not in k and "splitvantage.unarmed" not in k


def test_turn_start_reads_the_last_five_events_and_says_so(tmp_path):
    hub = plain_hub(tmp_path)
    for n in range(8):
        hub.audit.append("x", {"n": n})
    state = hub.on_turn_start()
    assert [e.get("n") for e in state["last_events"]] == [3, 4, 5, 6, 7]
    t = entries(hub, "turn.start")[-1]
    assert t["read_prior_state"] is True


def test_without_the_taint_pillar_traces_are_kept_and_its_absence_declared_once(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent_open_mind", None)   # import now fails
    hub = plain_hub(tmp_path)
    hub.ingest_spoke_trace("02", "e1", "thinking", "done")
    hub.ingest_spoke_trace("02", "e2", "thinking more", "done")
    assert kinds(hub).count("agentopenmind.unarmed") == 1
    traces = entries(hub, "spoke.trace")
    assert [t["envelope_id"] for t in traces] == ["e1", "e2"]
    assert [t["envelope_id"] for t in hub.spoke_traces] == ["e1", "e2"]


def test_escalate_takes_only_escalation_queues(tmp_path):
    hub = plain_hub(tmp_path)
    before = list(hub.queues["integrity.violation"])
    with pytest.raises(KeyError):
        hub.escalate("integrity.violation", {"client_context_id": "c1"})
    with pytest.raises(KeyError):
        hub.escalate("escalation.no_such_queue", {"client_context_id": "c1"})
    assert hub.queues["integrity.violation"] == before
    assert "escalation.raised" not in kinds(hub)


def test_a_duplicate_is_reported_as_not_processed(tmp_path):
    hub = plain_hub(tmp_path)
    hub.register("02", lambda e: None)
    e = env("01", "02", "work.do")
    hub.send(e)
    assert hub.send(e) == {"status": "duplicate", "processed": False, "envelope_id": e.envelope_id}


def test_an_unknown_route_is_held_with_its_reason_on_the_record(tmp_path):
    hub = plain_hub(tmp_path)
    r = hub.send(env("01", "02", "never.heard.of"))
    assert r["status"] == "held"
    refl = entries(hub, "hub.reflection")[-1]
    assert "not on any track" in refl["thought"] and refl["response"] == "held in clarification.request"


def test_a_message_to_the_queue_is_recorded_as_held(tmp_path):
    hub = plain_hub(tmp_path)
    e = env("01", "queue", "work.park")
    assert hub.send(e)["status"] == "held"
    h = entries(hub, "hold.queued")[-1]
    assert h["envelope_id"] == e.envelope_id and h["intent"] == "work.park"


def test_no_handler_is_a_dead_letter_on_the_queue_and_the_record(tmp_path):
    hub = plain_hub(tmp_path)
    e = env("01", "02", "work.do")
    assert hub.send(e)["status"] == "dead.letter"
    assert [d["envelope_id"] for d in hub.queues["dead.letter"]] == [e.envelope_id]
    d = entries(hub, "dead.letter")[-1]
    assert d["envelope_id"] == e.envelope_id and d["reason"] == "no handler for 02"


def test_a_crashing_handler_is_a_dead_letter_on_the_queue_too(tmp_path):
    hub = plain_hub(tmp_path)

    def boom(e):
        raise RuntimeError("handler bug")
    hub.register("02", boom)
    e = env("01", "02", "work.do")
    hub.send(e)
    assert [d["envelope_id"] for d in hub.queues["dead.letter"]] == [e.envelope_id]
    assert "handler bug" in entries(hub, "dead.letter")[-1]["reason"]


def test_an_unsigned_authority_message_is_named_on_the_record(tmp_path):
    hub = plain_hub(tmp_path, routes=[{"intent": "config.update", "senders": ["human"],
                                       "receivers": ["05"]}])
    e = env("human", "05", "config.update")
    assert hub.send(e)["status"] == "reject"
    v = entries(hub, "integrity.violation")[-1]
    assert v["envelope_id"] == e.envelope_id
    assert v["reason"] == "authority intent without verified signature"


# --------------------------------------------- the outbound gate and disclosure
GATED = [
    {"intent": "vendor.schedule", "senders": ["09"], "receivers": ["external"],
     "audience": "counterparty", "audience_verified": True},
    {"intent": "client.update", "senders": ["11"], "receivers": ["external"],
     "audience": "counterparty"},
    {"intent": "disclosure.authority", "senders": ["human"], "receivers": ["09"]},
]


def signed_hub(tmp_path, registry=None):
    s = Ed25519Signer()
    hub = Hub(routes_file(tmp_path, GATED), AuditLog(str(tmp_path / "audit.jsonl")),
              signature_verifier=s.verifier(), signer_registry=registry)
    hub.register("09", lambda e: None)
    return hub, s


def test_a_hold_on_an_unconfirmed_audience_label_says_the_label_is_provisional(tmp_path):
    hub, _ = signed_hub(tmp_path)
    hub.send(env("11", "external", "client.update"))
    hub.send(env("09", "external", "vendor.schedule"))
    q = hub.queues["clarification.request"]
    assert [r["intent"] for r in q] == ["client.update", "vendor.schedule"]
    assert q[0]["absolute_signal"]["audience_provisional"] is True
    assert "audience_provisional" not in q[1]["absolute_signal"]
    assert q[1]["absolute_signal"]["signal"] == "ABSOLUTE_SIGNAL"
    refl = [r for r in entries(hub, "hub.reflection") if r["response"] == "held: absolute_signal"]
    assert len(refl) == 2 and "carries position risk" in refl[0]["thought"]


def auth(held_id, signer, decision=None, reason=None, sign=True):
    payload = {"held_envelope_id": held_id}
    if decision:
        payload["decision"] = decision
    if reason is not None:
        payload["reason"] = reason
    a = env("human", "09", "disclosure.authority", payload=payload)
    if sign:
        a.signature = signer.sign(a)
    return a


@pytest.mark.parametrize("verb", ["release", "decline"])
def test_an_unsigned_disclosure_answer_is_an_integrity_violation(tmp_path, verb):
    hub, s = signed_hub(tmp_path)
    held = env("09", "external", "vendor.schedule")
    hub.send(held)
    a = auth(held.envelope_id, s, decision=None if verb == "release" else "decline", sign=False)
    r = (hub.release_disclosure if verb == "release" else hub.decline_disclosure)(a)
    assert r["status"] == "reject" and r["reason"] == f"unverified signature on disclosure {verb}"
    assert [v["envelope_id"] for v in hub.queues["integrity.violation"]] == [a.envelope_id]
    v = entries(hub, "integrity.violation")[-1]
    assert v["reason"] == "disclosure.authority without verified signature"


def test_a_disclosure_answer_the_registry_refuses_is_an_integrity_violation(tmp_path):
    reg = SignerRegistry([{"intent": "disclosure.authority", "signer_login": "jeff",
                           "idp": "okta", "mfa_required": True, "effective": "2026-07-11",
                           "revoked": None}])
    hub, s = signed_hub(tmp_path, registry=reg)
    held = env("09", "external", "vendor.schedule")
    hub.send(held)
    a = auth(held.envelope_id, s)                  # signed, but no signer stamp
    r = hub.release_disclosure(a)
    assert r["status"] == "reject" and r["reason"].startswith("signer registry: ")
    assert [v["envelope_id"] for v in hub.queues["integrity.violation"]] == [a.envelope_id]
    assert entries(hub, "integrity.violation")[-1]["reason"].startswith("signer registry: ")


def test_a_decline_reason_is_kept_to_500_characters(tmp_path):
    hub, s = signed_hub(tmp_path)
    held = env("09", "external", "vendor.schedule")
    hub.send(held)
    r = hub.decline_disclosure(auth(held.envelope_id, s, decision="decline", reason="r" * 900))
    assert r["status"] == "declined"
    assert entries(hub, "disclosure.declined")[-1]["reason"] == "r" * 500


# -------------------------------------------------------------- notification
def test_notifier_errors_are_kept_to_300_characters(tmp_path):
    def raises(q, r):
        raise RuntimeError("x" * 900)
    hub = plain_hub(tmp_path, human_notifier=raises)
    hub.escalate("escalation.hot_lead", {"client_context_id": "c1"})
    assert len(entries(hub, "human.notify_failed")[-1]["error"]) == 300
    hub2 = Hub(routes_file(tmp_path, [{"intent": "a", "senders": ["01"], "receivers": ["02"]}]),
               AuditLog(str(tmp_path / "b.jsonl")),
               human_notifier=lambda q, r: {"status": "failed", "error": "y" * 900})
    hub2.escalate("escalation.hot_lead", {"client_context_id": "c1"})
    assert entries(hub2, "human.notify_failed")[-1]["notifier_error"] == "y" * 300
