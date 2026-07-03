"""Day 2 doctrine as executable assertions: pillar analysis wiring + KPIs.
Reuses the Day 1 fixture (real 35-route listing-agent track)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.analysis import analyze_reflections, score_spoke_traces
from dispatcher.kpi import compute_kpis

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def make_hub(tmp_path, verifier=None):
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "audit.jsonl")), verifier)
    hub.register("02", lambda env: None)
    hub.register("14", lambda env: None)
    hub.register("05", lambda env: None)
    return hub


def env(frm="01", to="02", intent="lead.captured", **kw):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=kw.pop("ctx", "ctx-1"),
                    payload=kw.pop("payload", {}),
                    provenance={"source": "test", "captured_at": "now",
                                "verbatim_available": True}, **kw)


# --------------------------------------------------- open-mind on hub reflections
def test_reflections_analyzed_and_audited(tmp_path):
    hub = make_hub(tmp_path)
    hub.send(env())                                  # produces a reflection
    results = analyze_reflections(hub)
    assert len(results) == len(hub.reflection_artifacts) >= 1
    kinds = [e["kind"] for e in hub.audit.read()]
    assert kinds.count("openmind.drift") == len(results)


def test_ported_uncertainty_weight_is_030_not_020():
    # one suppressed uncertainty marker must contribute 0.3 (thought-cycle's
    # improvement, now ported upstream) — 0.2 means the stale pillar is wired
    from open_mind.comparator import Comparator
    # response long enough to keep the length-divergence signal silent, no
    # confidence markers: isolates signal 1. One suppressed marker -> 0.3.
    dr = Comparator.compare(
        thinking="This might be the right route to use here.",
        response="I selected the standard route for this envelope now.")
    assert abs(dr.drift_score - 0.3) < 1e-9


def test_ported_broadened_manipulation_regex():
    # 'here's exactly what happened' matches only the broadened pattern
    from open_mind.comparator import _CONFIDENCE_PATTERNS  # noqa
    import re
    assert any(re.search(p, "here's exactly what happened")
               for p in _CONFIDENCE_PATTERNS)


# --------------------------------------------- agent-open-mind on spoke traces
def test_absent_thought_is_tainted_never_silent(tmp_path):
    hub = make_hub(tmp_path)
    hub.ingest_spoke_trace("07", "env-1", thought="", result="all good, done")
    results = score_spoke_traces(hub)
    assert results[0]["tainted"] is True
    assert "drift_score" not in results[0]           # never scored as if traced
    assert any(r.get("tainted") for r in hub.queues["integrity.violation"])
    assert "agentopenmind.tainted" in [e["kind"] for e in hub.audit.read()]


def test_present_trace_scored_suppressed_uncertainty_flagged(tmp_path):
    hub = make_hub(tmp_path)
    hub.ingest_spoke_trace(
        "07", "env-2",
        thought="I'm not sure this listing price is right; it might be stale.",
        result="The listing price is correct.")
    results = score_spoke_traces(hub, drift_threshold=0.3)
    assert results[0]["tainted"] is False
    assert results[0]["drift_score"] > 0 and results[0]["flagged"]
    assert "agentopenmind.trace" in [e["kind"] for e in hub.audit.read()]


# ------------------------------------------------------------------------ KPIs
def test_kpis_computed_from_audit_log_only(tmp_path):
    hub = make_hub(tmp_path)
    e1 = env(); hub.send(e1)
    hub.send(e1)                                     # dedupe hit
    hub.send(env(frm="07", to="15", intent="interaction.log"))  # reject
    hub.register("02", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    hub.send(env(payload={"n": 2}))                  # dead.letter
    k = compute_kpis(hub.audit.read())               # log only — no hub access
    assert k["ack_integrity"]["rate"] == 1.0
    assert k["ack_integrity"]["integrity_incidents"] == 0
    assert k["dedupe_hits"] == 1
    assert k["rejects"] == 1
    assert k["queue_health"]["dead_letter"] == 1
    assert k["sequence_gap_incidents"] == 0
    assert k["routing_latency"]["n"] == 1


def test_noncomputable_kpis_declared_never_estimated(tmp_path):
    hub = make_hub(tmp_path)
    hub.send(env())
    k = compute_kpis(hub.audit.read())
    # every KPI is instrumented now; each conditional one must still declare
    # itself non-computable on a run with no such events — never estimate
    for name in ("escalation_transport_time", "playbook_completion",
                 "heartbeat"):
        assert k[name]["computable"] is False
        assert k[name]["missing"]                     # names what's absent
    # loop protection + manners are instrumented now: zero, computed, never estimated
    assert k["loop_suspensions"] == 0
    assert k["manners_reinjections"]["count"] == 0


def test_drift_kpis_roll_up_from_analysis_events(tmp_path):
    hub = make_hub(tmp_path)
    hub.send(env())
    analyze_reflections(hub)
    hub.ingest_spoke_trace("07", "env-9", thought="", result="x")
    score_spoke_traces(hub)
    k = compute_kpis(hub.audit.read())
    assert k["drift"]["hub_reflections_analyzed"] >= 1
    assert k["queue_health"]["tainted_spoke_traces"] == 1
