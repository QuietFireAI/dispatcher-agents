"""End-to-end demo: P11 speed-to-lead - REAL spokes, no stubs.

The driver injects exactly two lead.signal envelopes and runs heartbeats.
Everything else - CRM dedupe round-trip, consent gating, rubric scoring,
nurture enrollment, hot-lead escalation, interaction logging, thought
traces - is the spokes chaining over the closed track. Spoke03 is the
deliberate dark-trace exhibit; the taint gate must catch it.
Steps in the after-action are reconstructed from the audit log's own
persist/ack records - the driver's memory is not evidence.
"""
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.loader import load_identity
from dispatcher.priority import SidingScheduler
from dispatcher.analysis import analyze_reflections, score_spoke_traces
from dispatcher.after_action import generate_report
from dispatcher.runs import PlaybookRun, Watchdog, heartbeat
from dispatcher.spokes import (Spoke01LeadCapture, Spoke02Qualification,
                               Spoke03Nurture, Spoke14CRM)
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.territory import build_transfer, receive_transfer, confirm_release


def stub_selfcheck(prompt: str) -> str:
    return ("FAIL\nLINE: guaranteed\nFIX: verify before asserting"
            if "guaranteed" in prompt else "PASS")


def stub_model_a(prompt: str) -> dict:
    return {"model": "stub-a", "response": f"It might be fine: {prompt[:40]}",
            "thinking": "uncertain"}


def stub_model_b(prompt: str) -> dict:
    return {"model": "stub-b", "response": "Fine.", "thinking": ""}

IDENTITY = os.environ.get(
    "IDENTITY_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "listing-agents"))


def signal(ctx, payload):
    return Envelope(from_agent="20", to_agent="01", intent="lead.signal",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-20", "captured_at": "runtime",
                                "verbatim_available": True})


def run(outdir="after-action"):
    ident = load_identity(IDENTITY)
    print(f"identity: {ident.vertical}, {ident.n_routes} routes, "
          f"{len(ident.agents)} agents; warnings: {ident.warnings}")
    notified = []
    signer = Ed25519Signer()
    # one self-contained audit file PER RUN. AuditLog is append-only by design,
    # so a fixed filename accumulates across reruns and inflates every count
    # (11 -> 22 -> 33). Same discipline as baseline_run.py's run_tag.
    run_tag = uuid.uuid4().hex[:8]
    hub = Hub(Routes(ident.routes_path),
              AuditLog(os.path.join(outdir, f"demo-audit-{run_tag}.jsonl")),
              human_notifier=lambda q, r: notified.append((q, r)),
              selfcheck_model=stub_selfcheck,
              crosspol_models=(stub_model_a, stub_model_b))
    Spoke14CRM(hub); Spoke01LeadCapture(hub)
    Spoke02Qualification(hub); Spoke03Nurture(hub)

    sched = SidingScheduler(hub.audit, ident.priority_classes)
    run_id = f"p11-{uuid.uuid4().hex[:8]}"
    hub.on_turn_start()
    pb = PlaybookRun(hub, "P11", run_id, "lead-w")
    assert sched.request_segment(run_id, "P11", spoke="01")["granted"]

    heartbeat(hub)
    hub.send(signal("lead-w", {"consent": "recorded", "email": "w@x.com",
                               "budget": 550_000, "timeline_days": 90,
                               "channel": "social"}))      # rubric 40 -> WARM
    pb.step(1, note="warm lead signal injected; chain observed on log")
    heartbeat(hub)
    hub.send(signal("lead-h", {"consent": "recorded", "email": "h@x.com",
                               "budget": 900_000, "timeline_days": 14,
                               "channel": "call"}))        # rubric 100 -> HOT
    pb.step(2, note="hot lead signal injected")
    time.sleep(0.01); heartbeat(hub)
    # deliberate overclaim so the exit gate is TEMPTED on this identity too
    held = hub.send(Envelope(from_agent="02", to_agent="14",
                    intent="interaction.log", client_context_id="lead-h",
                    payload={"note": "closing is guaranteed"},
                    provenance={"source": "spoke-02", "captured_at": "runtime",
                                "verbatim_available": True}))
    # drifted reflection so splitvantage's auto second opinion is tempted
    hub._reflect("synthetic-p11",
                 "I am not sure; the tier might be wrong", "Tier confirmed.")
    # crew change: signed territory transfer carries the sleepmark
    hub_b = Hub(Routes(ident.routes_path),
                AuditLog(os.path.join(outdir, f"demo-audit-b-{run_tag}.jsonl")))
    ack = receive_transfer(hub_b, build_transfer(hub, ["lead-w"], signer),
                           Ed25519Verifier(signer.public_key_bytes()))
    confirm_release(hub, ["lead-w"], ack)
    sched.release_segment(run_id, "01")
    pb.complete()
    print(f"selfcheck on identity traffic: {held['status']} "
          f"({held.get('reason')})")

    refl = analyze_reflections(hub)
    traces = score_spoke_traces(hub)
    events = hub.audit.read()
    persisted = {e["envelope_id"]: e for e in events
                 if e["kind"] == "envelope.persisted"}
    steps = [{"step": i + 1,
              "agent": f"{persisted[e['envelope_id']]['from_agent']}->"
                       f"{persisted[e['envelope_id']]['to_agent']}",
              "intent": persisted[e["envelope_id"]]["intent"],
              "envelope_id": e["envelope_id"]}
             for i, e in enumerate(ev for ev in events if ev["kind"] == "ack")]

    print(f"chained envelopes acked: {len(steps)} (driver injected 2)")
    print(f"reflections: {len(refl)}; traces: {len(traces)}, tainted: "
          f"{sum(1 for t in traces if t.get('tainted'))}")
    print(f"human notified: {len(notified)}; heartbeat: "
          f"{Watchdog(0.01).observe(events)}")

    os.makedirs(outdir, exist_ok=True)
    report = generate_report(events, "P11", run_id,
                             client_context_id="lead-w", steps=steps)
    path = os.path.join(outdir, f"P11-{run_id}.md")
    open(path, "w").write(report)
    print(f"after-action: {path}")
    pillar_events = {k: sum(1 for e in events if e["kind"] == k) for k in
                     ("beforeturn.check", "openmind.drift",
                      "agentopenmind.tainted", "selfcheck.verdict",
                      "sleepmark.captured", "splitvantage.review")}
    print("pillar events on LISTING identity (all six nonzero):",
          pillar_events)
    assert all(v > 0 for v in pillar_events.values()), \
        "A PILLAR DID NOT FIRE ON THE IDENTITY - no dispatcher agents"
    return path, steps, notified


if __name__ == "__main__":
    run()
