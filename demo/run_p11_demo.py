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

IDENTITY = os.environ.get("IDENTITY_DIR", "/home/claude/listing")


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
    hub = Hub(Routes(ident.routes_path), AuditLog("demo-audit.jsonl"),
              human_notifier=lambda q, r: notified.append((q, r)))
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
    sched.release_segment(run_id, "01")
    pb.complete()

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
    return path, steps, notified


if __name__ == "__main__":
    run()
