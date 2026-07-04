"""Baseline run - all six pillars exercised on the NULL identity, one command.

    python3 demo/baseline_run.py

Output: baseline/after-action report + baseline-kpi.json. These numbers are
the reference KPIs for every future identity. Stub reviewer models are used
for the two model-gated pillars so the baseline is deterministic; swap in
real callables (OpenRouter, Hermes) for deployment baselining - the script
takes them via env or edit, and the after-action will say which was used.
"""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.attestation import attest_boot, verify_manifest
from dispatcher.priority import SidingScheduler
from dispatcher.analysis import analyze_reflections, score_spoke_traces
from dispatcher.after_action import generate_report
from dispatcher.runs import PlaybookRun, Watchdog, heartbeat
from dispatcher.territory import build_transfer, receive_transfer, confirm_release
from dispatcher.kpi import compute_kpis

BASE = os.path.join(os.path.dirname(__file__), "..", "baseline")
PKG = os.path.join(os.path.dirname(__file__), "..", "dispatcher")


def env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "baseline", "captured_at": "runtime",
                                "verbatim_available": True})


# deterministic stub reviewers - deployment swaps real models in
def stub_selfcheck(prompt: str) -> str:
    return ("FAIL\nLINE: guaranteed\nFIX: verify before asserting"
            if "guaranteed" in prompt else "PASS")


def stub_model_a(prompt: str) -> dict:
    return {"model": "stub-a", "response": f"It might be fine: {prompt[:40]}",
            "thinking": "uncertain about scope"}


def stub_model_b(prompt: str) -> dict:
    return {"model": "stub-b", "response": "Fine.", "thinking": ""}


def run(outdir="baseline-out"):
    os.makedirs(outdir, exist_ok=True)
    signer = Ed25519Signer()                       # human-owner authority
    hub = Hub(Routes(os.path.join(BASE, "routes.json")),
              AuditLog(os.path.join(outdir, "audit.jsonl")),
              signature_verifier=Ed25519Verifier(
                  signer.public_key_bytes()).verifier(),
              selfcheck_model=stub_selfcheck,
              crosspol_models=(stub_model_a, stub_model_b))
    p1_seen, p2_seen = [], []
    hub.register("p1", lambda e: p1_seen.append(e.intent))
    hub.register("p2", lambda e: p2_seen.append(e.intent))

    # PILLAR: before-turn (auto inside on_turn_start)
    hub.on_turn_start()
    # attestation, signed by the owner key
    manifest = attest_boot(hub, PKG, os.path.join(BASE, "routes.json"),
                           signer=signer)
    assert verify_manifest(manifest, PKG,
                           os.path.join(BASE, "routes.json")) == []

    run_id = f"baseline-{uuid.uuid4().hex[:8]}"
    pb = PlaybookRun(hub, "B01", run_id, "diag-1")
    sched = SidingScheduler(hub.audit,
                            json.load(open(os.path.join(
                                BASE, "priority.json")))["classes"])
    assert sched.request_segment(run_id, "B01", "p1")["granted"]
    heartbeat(hub)

    # authority path: signed config.update from the human owner
    cfg = env("human", "p1", "config.update", "diag-1",
              {"spec_owner": "human", "note": "baseline owner binding"})
    signer.sign(cfg)
    steps = [{"step": 1, "agent": "human->p1", "intent": "config.update",
              "envelope_id": cfg.envelope_id}]
    assert hub.send(cfg)["status"] == "ack"
    pb.step(1, cfg.envelope_id, "signed owner config accepted")

    # clean traffic + spoke traces (agent-open-mind pillar: one dark trace)
    e1 = env("human", "p1", "diag.echo", "diag-1", {"msg": "hello"})
    hub.send(e1)
    hub.ingest_spoke_trace("p1", e1.envelope_id,
                           thought="echo received; relaying per track",
                           result="relayed")
    e2 = env("p1", "p2", "diag.relay", "diag-1", {"msg": "hello"})
    hub.send(e2)
    hub.ingest_spoke_trace("p2", e2.envelope_id, thought="",
                           result="report drafted")      # tainted, on purpose
    e3 = env("p2", "p1", "diag.report", "diag-1",
             {"msg": "This outcome is guaranteed."})     # selfcheck bait
    r3 = hub.send(e3)                                    # exit gate holds it
    for i, e in ((2, e1), (3, e2), (4, e3)):
        steps.append({"step": i, "agent": f"{e.from_agent}->{e.to_agent}",
                      "intent": e.intent, "envelope_id": e.envelope_id})
    heartbeat(hub); time.sleep(0.01); heartbeat(hub)

    # open-mind + splitvantage: force one flaggable reflection then analyze
    hub._reflect("synthetic-1",
                 "I am not sure this route is right; it might be wrong",
                 "Routed with certainty.")
    refl = analyze_reflections(hub, drift_threshold=0.3)
    traces = score_spoke_traces(hub)

    # sleep-marks: crew change via signed territory transfer to hub B
    hub_b = Hub(Routes(os.path.join(BASE, "routes.json")),
                AuditLog(os.path.join(outdir, "audit-b.jsonl")))
    rec = build_transfer(hub, ["diag-1"], signer)
    ack = receive_transfer(hub_b, rec,
                           Ed25519Verifier(signer.public_key_bytes()))
    confirm_release(hub, ["diag-1"], ack)
    sched.release_segment(run_id, "p1")
    pb.complete()

    events = hub.audit.read()
    kpis = compute_kpis(events)
    kpis["heartbeat_watchdog"] = Watchdog(0.01).observe(events)
    report = generate_report(events, "B01", run_id, "diag-1", steps)
    rp = os.path.join(outdir, f"BASELINE-{run_id}.md")
    open(rp, "w").write(report)
    kp = os.path.join(outdir, "baseline-kpi.json")
    json.dump(kpis, open(kp, "w"), indent=1, default=str)

    pillar_events = {k: sum(1 for e in events if e["kind"] == k) for k in
                     ("beforeturn.check", "openmind.drift",
                      "agentopenmind.tainted", "selfcheck.verdict",
                      "sleepmark.captured", "splitvantage.review")}
    print("pillar event counts (all six must be nonzero):", pillar_events)
    print(f"selfcheck held envelope: {r3['status']} ({r3.get('reason')})")
    print(f"after-action: {rp}\nkpis: {kp}")
    assert all(v > 0 for v in pillar_events.values()), \
        "A PILLAR DID NOT FIRE - no dispatcher agents"
    return pillar_events, kpis


if __name__ == "__main__":
    run()
