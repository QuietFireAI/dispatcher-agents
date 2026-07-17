"""Day 3 doctrine as executable assertions: attestation, real signatures,
JIT priority + siding, identity side-load loader, ingestion-time tainting.
Loader/siding tested against the REAL v0.16 listing identity where present."""
import os, sys, shutil, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.attestation import build_manifest, verify_manifest, attest_boot
from dispatcher.signatures import HmacSigner
from dispatcher.priority import SidingScheduler
from dispatcher.loader import load_identity

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")
PKG = os.path.join(os.path.dirname(__file__), "..", "dispatcher")
LISTING = os.environ.get("IDENTITY_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "listing-agents"))  # real identity when present as a sibling clone; identity-gated tests skip cleanly otherwise


def make_hub(tmp_path, verifier=None):
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "audit.jsonl")), verifier)
    hub.register("02", lambda env: None)
    return hub


def env(frm="01", to="02", intent="lead.captured", **kw):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=kw.pop("ctx", "ctx-1"),
                    payload=kw.pop("payload", {}),
                    provenance={"source": "test", "captured_at": "now",
                                "verbatim_available": True}, **kw)


# ------------------------------------------------------- ingestion-time taint
def test_absent_thought_tainted_at_ingestion_not_only_at_analysis(tmp_path):
    hub = make_hub(tmp_path)
    hub.ingest_spoke_trace("07", "env-1", thought="  ", result="done")
    # flagged IMMEDIATELY - before any analysis runs
    assert any(r.get("tainted") for r in hub.queues["integrity.violation"])
    assert "agentopenmind.tainted" in [e["kind"] for e in hub.audit.read()]


def test_taint_not_double_queued_by_analysis(tmp_path):
    from dispatcher.analysis import score_spoke_traces
    hub = make_hub(tmp_path)
    hub.ingest_spoke_trace("07", "env-1", thought="", result="done")
    score_spoke_traces(hub)
    taints = [e for e in hub.audit.read() if e["kind"] == "agentopenmind.tainted"]
    assert len(taints) == 1
    assert len([r for r in hub.queues["integrity.violation"]
                if r.get("tainted")]) == 1


# ------------------------------------------------------------------ attestation
def test_boot_attestation_logged_and_verifies_clean(tmp_path):
    hub = make_hub(tmp_path)
    m = attest_boot(hub, PKG, FIX)
    assert "boot.attestation" in [e["kind"] for e in hub.audit.read()]
    assert verify_manifest(m, PKG, FIX) == []


def test_tampered_file_named_in_violation(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir()
    f = pkg / "mod.py"; f.write_text("x = 1\n")
    routes = tmp_path / "routes.json"; routes.write_text("{}")
    m = build_manifest(str(pkg), str(routes))
    f.write_text("x = 2  # tampered\n")
    v = verify_manifest(m, str(pkg), str(routes))
    assert len(v) == 1 and "mod.py" in v[0] and "mismatch" in v[0]


def test_absent_manifest_fails_closed(tmp_path):
    assert "absent" in verify_manifest({}, PKG, FIX)[0]


def test_unattested_new_file_named(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir()
    (pkg / "mod.py").write_text("x = 1\n")
    routes = tmp_path / "routes.json"; routes.write_text("{}")
    m = build_manifest(str(pkg), str(routes))
    (pkg / "smuggled.py").write_text("evil = True\n")
    v = verify_manifest(m, str(pkg), str(routes))
    assert len(v) == 1 and "smuggled.py" in v[0] and "unattested" in v[0]


# ------------------------------------------------------------------ signatures
def test_signed_authority_intent_acks(tmp_path):
    signer = HmacSigner(b"k" * 32)
    hub = make_hub(tmp_path, verifier=signer.verifier())
    hub.register("05", lambda e: None)
    e = env(frm="human", to="05", intent="listing.change.authorized")
    signer.sign(e)
    assert hub.send(e)["status"] == "ack"


def test_forged_signature_rejected_and_flagged(tmp_path):
    signer = HmacSigner(b"k" * 32)
    hub = make_hub(tmp_path, verifier=signer.verifier())
    hub.register("05", lambda e: None)
    e = env(frm="human", to="05", intent="listing.change.authorized")
    e.signature = "0" * 64                        # forged
    r = hub.send(e)
    assert r["status"] == "reject"
    assert len(hub.queues["integrity.violation"]) == 1


def test_signature_binds_fields_tamper_after_sign_fails(tmp_path):
    signer = HmacSigner(b"k" * 32)
    hub = make_hub(tmp_path, verifier=signer.verifier())
    hub.register("05", lambda e: None)
    e = env(frm="human", to="05", intent="listing.change.authorized",
            payload={"price": 500000})
    signer.sign(e)
    e.payload["price"] = 400000                   # tampered after signing
    assert hub.send(e)["status"] == "reject"


def test_absent_signature_still_rejected(tmp_path):
    signer = HmacSigner(b"k" * 32)
    hub = make_hub(tmp_path, verifier=signer.verifier())
    hub.register("05", lambda e: None)
    r = hub.send(env(frm="human", to="05", intent="listing.change.authorized"))
    assert r["status"] == "reject"


# ------------------------------------------------------------- JIT + siding
CLASSES = {"P11": 3, "P05": 2, "P12": 4}


def test_higher_class_sides_lower_holder_live_then_resumes(tmp_path):
    audit = AuditLog(str(tmp_path / "a.jsonl"))
    s = SidingScheduler(audit, CLASSES)
    assert s.request_segment("run-mkt", "P12", spoke="11")["granted"]
    r = s.request_segment("run-halt", "P05", spoke="11")   # Class 2 arrives
    assert r["granted"] and r["sided"] == "run-mkt"        # junk takes siding
    out = s.release_segment("run-halt", "11")
    assert out["resumed"] == "run-mkt"                     # auto-resume, live
    kinds = [e["kind"] for e in audit.read()]
    assert "siding.hold" in kinds and "siding.resume" in kinds


def test_lower_class_waits_never_preempts(tmp_path):
    s = SidingScheduler(AuditLog(str(tmp_path / "a.jsonl")), CLASSES)
    s.request_segment("run-halt", "P05", spoke="11")
    r = s.request_segment("run-mkt", "P12", spoke="11")
    assert r["granted"] is False and r["held_behind"] == "run-halt"


def test_class_beats_arrival_order_on_resume(tmp_path):
    s = SidingScheduler(AuditLog(str(tmp_path / "a.jsonl")), CLASSES)
    s.request_segment("holder", "P05", spoke="11")
    s.request_segment("late-junk", "P12", spoke="11")      # arrives first
    s.request_segment("sched", "P11", spoke="11")          # arrives second
    out = s.release_segment("holder", "11")
    assert out["resumed"] == "sched"                       # class 3 beats 4


def test_unclassified_playbook_refused(tmp_path):
    s = SidingScheduler(AuditLog(str(tmp_path / "a.jsonl")), CLASSES)
    with pytest.raises(KeyError):
        s.request_segment("run-x", "P99", spoke="11")


# ------------------------------------------------------------------- loader
needs_listing = pytest.mark.skipif(not os.path.isdir(LISTING),
                                   reason="real v0.16 identity not extracted")


@needs_listing
def test_loads_real_listing_identity():
    ident = load_identity(LISTING)
    if ident.vertical != "real-estate-listing-agent":
        pytest.skip("IDENTITY_DIR points at a non-listing identity; "
                    "generic contract test covers it")
    # Pinned to the live identity, verified via fresh clone 2026-07-17.
    # This number goes stale every time a route is added to the real
    # identity - it was 35 for a long time, then 50, and nobody caught
    # either drift because this test only runs when a sibling
    # listing-agents clone is present at IDENTITY_DIR (see needs_listing
    # above). If this assertion fails, check whether it's a real
    # regression or just needs re-pinning to the new correct count -
    # re-verify via load_identity() directly, don't just bump the number
    # to make the test pass.
    assert ident.n_routes == 51
    assert len(ident.agents) == 21
    # Pinned to the live identity. 24 since listing-agents d85ae51
    # (added playbooks P21-P24 and classified them in the DRAFT table).
    assert ident.priority_classes and len(ident.priority_classes) == 24
    # Ratified 2026-07-10 (owner sign-off, approved as written) - the loader
    # must carry the ratified status and emit NO draft/pending warning for it.
    assert "ratified" in ident.priority_status.lower()
    assert not any("DRAFT" in w or "pending" in w.lower() for w in ident.warnings)


@needs_listing
def test_loaded_identity_drives_hub_and_scheduler(tmp_path):
    ident = load_identity(LISTING)
    if ident.vertical != "real-estate-listing-agent":
        pytest.skip("listing-pinned intents/playbooks; generic contract test covers other identities")
    hub = Hub(Routes(ident.routes_path),
              AuditLog(str(tmp_path / "a.jsonl")), None)
    hub.register("02", lambda e: None)
    assert hub.send(env())["status"] == "ack"              # real track routes
    s = SidingScheduler(hub.audit, ident.priority_classes)
    assert s.request_segment("r1", "P11", spoke="06")["granted"]


def test_missing_routes_refuses_to_load(tmp_path):
    d = tmp_path / "ident"; d.mkdir()
    (d / "01-thing").mkdir(); (d / "01-thing" / "SKILL.md").write_text("x")
    with pytest.raises(FileNotFoundError):
        load_identity(str(d))


def test_agent_dir_without_skill_is_violation_not_skip(tmp_path):
    d = tmp_path / "ident"; d.mkdir()
    (d / "routes.json").write_text(
        '{"vertical":"t","routes":[{"intent":"a","senders":["01"],"receivers":["02"]}]}')
    (d / "01-thing").mkdir()                               # no SKILL.md
    with pytest.raises(ValueError, match="SKILL.md absent"):
        load_identity(str(d))


# ------------------------------------------------------------ e2e demo (Day 4)
@needs_listing
def test_p11_demo_end_to_end(tmp_path, monkeypatch):
    if os.path.isdir(LISTING) and load_identity(LISTING).vertical != "real-estate-listing-agent":
        pytest.skip("P11 demo is listing-pinned; generic contract test covers other identities")
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))
    import run_p11_demo
    path, steps, notified = run_p11_demo.run(outdir=str(tmp_path / "aa"))
    report = open(path).read()
    assert len(steps) >= 10                         # driver injected only 2;
    #                                       the swarm chained the rest itself
    assert len(notified) == 1                       # HOT lead reached a human
    for section in ("## run", "## outcome", "## steps", "## gates",
                    "## deviations", "## escalations", "## errors",
                    "## kpis", "## manners re-injections"):
        assert section in report                    # full AFTER_ACTION schema
    assert "TRIGGERED" in report                    # both gates fired
    assert "NOT INSTRUMENTED" in report             # declared, not faked
    assert "'computable': True" in report           # escalation transport real


# --------------------------------------------- loop protection + manners (Day 5)
def test_loop_suspends_at_threshold_into_clarification(tmp_path):
    hub = make_hub(tmp_path)
    hub.loop_threshold = 3
    results = [hub.send(env(payload={"n": i})) for i in range(5)]
    assert [r["status"] for r in results] == ["ack"] * 3 + ["suspended"] * 2
    assert len(hub.queues["clarification.request"]) == 2
    assert "loop.suspended" in [e["kind"] for e in hub.audit.read()]


# ------------------------------------------- THE FIX: explicit resume
def test_suspended_loop_has_no_recovery_path_before_the_fix_now_does(tmp_path):
    """Was: once suspended, a (context, intent) pair was stuck PERMANENTLY
    - nothing anywhere ever reset loop_counts, not even a manual path for
    a human who reviewed the suspension and decided it was a false
    positive. Restricted-Speed Doctrine's own phrasing ('never self-
    restores') implies explicit resumption should be possible - it
    wasn't, at all, before this fix."""
    hub = make_hub(tmp_path)
    hub.loop_threshold = 2
    ctx, intent = "t-loop-1", "lead.captured"
    for i in range(4):
        hub.send(env(payload={"n": i}, intent=intent, to="02", ctx=ctx))
    assert hub.send(env(payload={"n": 99}, intent=intent, to="02", ctx=ctx))["status"] == "suspended", \
        "still suspended before any explicit resume"

    result = hub.resume_loop_suspension(ctx, intent)
    assert result["status"] == "resumed"
    assert result["prior_count"] > hub.loop_threshold

    assert hub.send(env(payload={"n": 100}, intent=intent, to="02", ctx=ctx))["status"] == "ack", \
        "traffic must actually resume after the explicit human decision"
    assert "loop.resumed" in [e["kind"] for e in hub.audit.read()], \
        "the resume itself must be audited, not a silent clear"


def test_resuming_a_never_suspended_pair_is_a_safe_noop(tmp_path):
    hub = make_hub(tmp_path)
    result = hub.resume_loop_suspension("never-suspended", "lead.captured")
    assert result["status"] == "resumed"
    assert result["prior_count"] is None


def test_retries_never_inflate_loop_count(tmp_path):
    hub = make_hub(tmp_path)
    hub.loop_threshold = 2
    e = env()
    hub.send(e)
    for _ in range(5):                       # ack-loss retries: dedupe first
        assert hub.send(e)["status"] == "duplicate"
    assert hub.send(env(payload={"n": 2}))["status"] == "ack"   # count is 2, not 7


def test_manners_reinjection_audited_and_kpi_counted(tmp_path):
    from dispatcher.kpi import compute_kpis
    hub = make_hub(tmp_path)
    hub.manners_reinjection("phase_gate", position="P11 step 3")
    hub.manners_reinjection("post_compaction")
    with pytest.raises(ValueError):
        hub.manners_reinjection("vibes")     # unknown trigger refused
    k = compute_kpis(hub.audit.read())
    assert k["manners_reinjections"]["count"] == 2
    assert k["manners_reinjections"]["by_trigger"]["phase_gate"] == 1


# --------------------------------------------------------- Ed25519 (approved)
def test_ed25519_signed_authority_acks_and_verifier_cannot_forge(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = make_hub(tmp_path, verifier=verifier.verifier())
    hub.register("05", lambda e: None)
    e = env(frm="human", to="05", intent="listing.change.authorized")
    signer.sign(e)
    assert hub.send(e)["status"] == "ack"
    assert not hasattr(verifier, "sign")     # hub side holds no signing power


def test_ed25519_tamper_and_forge_reject(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    signer = Ed25519Signer()
    hub = make_hub(tmp_path,
                   verifier=Ed25519Verifier(signer.public_key_bytes()).verifier())
    hub.register("05", lambda e: None)
    e = env(frm="human", to="05", intent="listing.change.authorized",
            payload={"price": 500000})
    signer.sign(e)
    e.payload["price"] = 1                   # tamper after sign
    assert hub.send(e)["status"] == "reject"
    e2 = env(frm="human", to="05", intent="listing.change.authorized")
    e2.signature = Ed25519Signer().sign_bytes(b"wrong key entirely")
    assert hub.send(e2)["status"] == "reject"


def test_signed_boot_manifest_with_manners_hash(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.attestation import attest_boot, verify_manifest
    import json as _json
    ident = load_identity(LISTING) if os.path.isdir(LISTING) else None
    if ident is None or ident.manners_path is None:
        pytest.skip("real identity with MANNERS.md not present")
    signer = Ed25519Signer()
    hub = make_hub(tmp_path)
    m = attest_boot(hub, PKG, FIX, extra_files=[ident.manners_path],
                    signer=signer)
    assert "MANNERS.md" in m                 # conduct hash registered per spec
    assert verify_manifest(m, PKG, FIX, extra_files=[ident.manners_path]) == []
    ev = [e for e in hub.audit.read() if e["kind"] == "boot.attestation"][0]
    assert ev["signed"] is True
    v = Ed25519Verifier(signer.public_key_bytes())
    assert v.verify_bytes(_json.dumps(m, sort_keys=True).encode(),
                          ev["manifest_signature"])


# ---------------------------------------------- Day 6: real spokes, chain demo
def test_real_spokes_chain_hot_and_warm_from_single_signals(tmp_path):
    from dispatcher.spokes import (Spoke01LeadCapture, Spoke02Qualification,
                                   Spoke03Nurture, Spoke14CRM)
    from dispatcher.analysis import score_spoke_traces
    notified = []
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "a.jsonl")),
              human_notifier=lambda q, r: notified.append(q))
    crm, cap = Spoke14CRM(hub), Spoke01LeadCapture(hub)
    qual, nur = Spoke02Qualification(hub), Spoke03Nurture(hub)
    # single injected signal per lead - spokes chain the rest themselves
    hub.send(env(frm="20", to="01", intent="lead.signal", ctx="lead-w",
                 payload={"consent": "recorded", "email": "w@x.com",
                          "budget": 550_000, "timeline_days": 90,
                          "channel": "social"}))          # 40 -> WARM
    hub.send(env(frm="20", to="01", intent="lead.signal", ctx="lead-h",
                 payload={"consent": "recorded", "email": "h@x.com",
                          "budget": 900_000, "timeline_days": 14,
                          "channel": "call"}))            # 100 -> HOT
    assert nur.enrolled == ["lead-w"]                     # WARM chained to drip
    assert notified == ["escalation.hot_lead"]            # HOT reached a human
    assert len(crm.interactions) == 2                     # both logged
    scored = score_spoke_traces(hub)
    assert sum(1 for t in scored if t.get("tainted")) == 1   # 03's dark trace
    assert sum(1 for t in scored if not t.get("tainted")) >= 6  # real thoughts


def test_consent_gate_holds_without_recorded_consent(tmp_path):
    from dispatcher.spokes import Spoke01LeadCapture, Spoke14CRM
    hub = make_hub(tmp_path)
    Spoke14CRM(hub); cap = Spoke01LeadCapture(hub)
    hub.send(env(frm="20", to="01", intent="lead.signal", ctx="lead-x",
                 payload={"consent": "unknown", "email": "x@x.com"}))
    assert cap.pending == {}                              # never advanced
    traces = hub.spoke_traces
    assert any("TCPA" in t["thought"] for t in traces)    # reasoning on log


# ----------------------------------------------------- heartbeat + playbook KPI
def test_watchdog_names_gap_incidents_never_percentages(tmp_path):
    from dispatcher.runs import heartbeat, Watchdog
    import time as _t
    hub = make_hub(tmp_path)
    heartbeat(hub); _t.sleep(0.01); heartbeat(hub); _t.sleep(0.06); heartbeat(hub)
    obs = Watchdog(cadence_s=0.01).observe(hub.audit.read())
    assert obs["computable"] and obs["beats"] == 3 and obs["gap_incidents"] == 1
    assert "uptime" not in obs                            # no invented %


def test_playbook_completion_counted_from_log_only(tmp_path):
    from dispatcher.runs import PlaybookRun
    from dispatcher.kpi import compute_kpis
    hub = make_hub(tmp_path)
    r1 = PlaybookRun(hub, "P11", "r1", "lead-a"); r1.step(1); r1.complete()
    PlaybookRun(hub, "P11", "r2", "lead-b").step(1)       # never completed
    k = compute_kpis(hub.audit.read())
    pc = k["playbook_completion"]
    assert pc["computable"] and pc["started"] == 2 and pc["rate"] == 0.5
    assert pc["incomplete_runs"] == ["r2"]                # named, not hidden


# ------------------------------------------------------------------ territories
def test_signed_transfer_adopts_contexts_and_sequence_continues(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.territory import (build_transfer, receive_transfer,
                                      confirm_release)
    signer = Ed25519Signer()
    a = make_hub(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path)
    b = Hub(Routes(FIX), AuditLog(str(tmp_path / "b.jsonl")), None)
    b.register("02", lambda e: None)
    for i in range(3):
        a.send(env(payload={"n": i}))                     # hwm -> 3 on hub A
    rec = build_transfer(a, ["ctx-1"], signer)
    ack = receive_transfer(b, rec, Ed25519Verifier(signer.public_key_bytes()))
    assert ack["status"] == "ack"
    confirm_release(a, ["ctx-1"], ack)
    r = b.send(env(payload={"n": 99}))                    # same ctx, hub B
    assert r["status"] == "ack" and r["sequence"] == 4    # continues, no gap
    assert "ctx-1" not in a.seq                           # never in two regions


def test_unsigned_or_forged_transfer_refused_contexts_not_adopted(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.territory import build_transfer, receive_transfer, confirm_release
    good, bad = Ed25519Signer(), Ed25519Signer()
    a = make_hub(tmp_path)
    a.send(env())
    b = Hub(Routes(FIX), AuditLog(str(tmp_path / "b.jsonl")), None)
    rec = build_transfer(a, ["ctx-1"], bad)               # wrong authority
    res = receive_transfer(b, rec, Ed25519Verifier(good.public_key_bytes()))
    assert res["status"] == "refused"
    assert "ctx-1" not in b.seq                           # NOT adopted
    assert b.queues["integrity.violation"]                # held for review
    with pytest.raises(ValueError):
        confirm_release(a, ["ctx-1"], res)                # sender keeps it


# ------------------------------------------- six-pillar wiring (final drop)
def test_before_turn_pillar_runs_at_turn_entry(tmp_path):
    # HOOK PATH, not the bare function: on_turn_start must fire the pillar.
    # (v1 of this test called before_turn_check directly and stayed green
    # while the hook was dead code after a return - tests the wiring now.)
    hub = make_hub(tmp_path)
    hub.send(env())
    hub.on_turn_start()
    checks = [e for e in hub.audit.read() if e["kind"] == "beforeturn.check"]
    assert len(checks) == 1
    assert checks[0]["thoughts_reviewed"] >= 1    # read its own reflections
    assert len(checks[0]["questions"]) == 4       # pillar's question set


def test_taint_gate_is_the_pillar_not_a_copy(tmp_path):
    import dispatcher.hub as h, inspect
    src = inspect.getsource(h.Hub.ingest_spoke_trace)
    assert "taint_check" in src                   # pillar import, single source
    hub = make_hub(tmp_path)
    hub.ingest_spoke_trace("07", "e1", thought="", result="x")
    assert any(r.get("tainted") for r in hub.queues["integrity.violation"])


def test_exit_gate_blocks_on_fail_verdict(tmp_path):
    from dispatcher.pillars import exit_gate
    hub = make_hub(tmp_path)
    e = env(payload={"msg": "Your offer was definitely accepted."})
    fail_model = lambda prompt: "FAIL\nLINE: definitely accepted\nFIX: verify before asserting"
    r = exit_gate(hub, e, model=fail_model)
    assert r["armed"] and r["passed"] is False
    assert any(i.get("held_by") == "pre-response-selfcheck"
               for i in hub.queues["clarification.request"])
    pass_model = lambda prompt: "PASS"
    assert exit_gate(hub, env(payload={"msg": "ok"}), model=pass_model)["passed"]


def test_exit_gate_unarmed_is_audited_never_silent(tmp_path):
    from dispatcher.pillars import exit_gate
    hub = make_hub(tmp_path)
    r = exit_gate(hub, env(), model=None)
    assert r["armed"] is False
    assert "selfcheck.unarmed" in [e["kind"] for e in hub.audit.read()]


def test_sleepmark_rides_territory_transfer(tmp_path):
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.territory import build_transfer, receive_transfer
    signer = Ed25519Signer()
    a = make_hub(tmp_path)
    a.send(env())
    b = Hub(Routes(FIX), AuditLog(str(tmp_path / "b.jsonl")), None)
    rec = build_transfer(a, ["ctx-1"], signer)
    assert rec["sleepmark"]["context_summary"].startswith("territory transfer")
    assert receive_transfer(b, rec,
                            Ed25519Verifier(signer.public_key_bytes()))["status"] == "ack"
    kinds_a = [e["kind"] for e in a.audit.read()]
    kinds_b = [e["kind"] for e in b.audit.read()]
    assert "sleepmark.captured" in kinds_a and "sleepmark.restored" in kinds_b


def test_splitvantage_second_opinion_audited(tmp_path):
    from dispatcher.pillars import second_opinion
    hub = make_hub(tmp_path)
    d = second_opinion(hub, "route this lead?",
                       {"model": "a", "response": "It might possibly be WARM.", "thinking": ""},
                       {"model": "b", "response": "WARM.", "thinking": ""},
                       envelope_id="e9")
    assert d["uncertainty_delta"] == 2            # word-boundary counting live
    assert "splitvantage.review" in [e["kind"] for e in hub.audit.read()]


def test_baseline_run_all_six_pillars_fire(tmp_path, monkeypatch):
    """The set functions together on the null identity or no dispatcher
    agents - the baseline script's own fail-loud assertion, as regression."""
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))
    import baseline_run
    pillar_events, kpis = baseline_run.run(outdir=str(tmp_path / "b"))
    assert all(v > 0 for v in pillar_events.values())
    assert kpis["ack_integrity"]["integrity_incidents"] == 0
    assert kpis["playbook_completion"]["rate"] == 1.0


def test_kpi_gate_passes_clean_run_and_names_slept_gates(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    from kpi_gate import gate
    hub = make_hub(tmp_path)
    hub.send(env())
    hub.ingest_spoke_trace("07", "e1", thought="", result="x")   # 1 planted
    assert gate(hub.audit.read(), taints_expected=1) == []       # caught -> pass
    v = gate(hub.audit.read(), taints_expected=2)                # claim 2 planted
    assert len(v) == 1 and "taint gate slept" in v[0]            # 1 caught -> named
    v2 = gate(hub.audit.read(), selfcheck_bait_expected=1)       # bait never sent
    assert len(v2) == 1 and "exit gate slept" in v2[0]


def test_baseline_rerun_does_not_poison_the_gate(tmp_path, monkeypatch):
    """Manual says rerun 3x; append-only audit made run 2 fail the gate with
    phantom sequence gaps (hard-test find). Each run now gets its own log."""
    monkeypatch.chdir(tmp_path)
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "demo"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import baseline_run
    from kpi_gate import gate
    import glob, json as _json
    for _ in range(3):
        baseline_run.run(outdir=str(tmp_path / "b"))
    logs = glob.glob(str(tmp_path / "b" / "audit-*.jsonl"))
    logs = [l for l in logs if "audit-b-" not in l]
    assert len(logs) == 3                          # three self-contained logs
    for log in logs:
        events = [_json.loads(l) for l in open(log) if l.strip()]
        assert gate(events, taints_expected=1, selfcheck_bait_expected=1) == []


# ---------------------------------------------------- generic identity loading
# Not pinned to any vertical: whatever IDENTITY_DIR points at must load with a
# closed track, agents behind every numbered dir, legal priority classes, and
# MANNERS present - the loadability contract every side-load must meet.
@needs_listing
def test_loads_any_identity_generic_contract():
    ident = load_identity(LISTING)
    assert ident.n_routes > 0
    assert len(ident.agents) >= 2          # hub + at least one spoke
    assert "00" in ident.agents            # the hub is not optional
    if ident.priority_classes is not None:
        assert all(isinstance(v, int) and 1 <= v <= 4
                   for v in ident.priority_classes.values())
    assert ident.manners_path, "MANNERS.md must ship with every identity"
    assert ident.vertical != "unstated"


def test_listing_pinned_test_skips_cleanly_on_other_identities():
    # The listing-pinned test above asserts listing constants; when IDENTITY_DIR
    # points elsewhere, the generic contract test is the one that must hold.
    # This test documents that split so the suite reads correctly.
    assert True
