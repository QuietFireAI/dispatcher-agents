"""End-to-end demo: P11 speed-to-lead on the runtime — Day 4.

Stub spokes (20, 01, 02, 03, 14) registered on a hub loaded from the REAL
v0.17 listing identity via the side-load loader. Two leads driven through
the playbook's actual tuples:
  WARM lead: 20 -> lead.signal -> 01 -> record.request -> 14,
             01 -> lead.captured -> 02 -> lead.nurture -> 03,
             02 -> interaction.log -> 14
  HOT lead:  same intake, then 02 raises escalation.hot_lead (hub queue,
             not a route) -> human notified (instrumented)
One spoke submits a result with NO thought trace -> tainted at ingestion.
Every step's proof is the audit log; the after-action report is generated
from the log alone. Stub spokes fake WORK, never proof — proof is acks.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.loader import load_identity
from dispatcher.priority import SidingScheduler
from dispatcher.analysis import analyze_reflections, score_spoke_traces
from dispatcher.after_action import generate_report

IDENTITY = os.environ.get("IDENTITY_DIR", "/home/claude/listing")


def env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "demo", "captured_at": "runtime",
                                "verbatim_available": True})


def run(outdir="after-action"):
    ident = load_identity(IDENTITY)
    print(f"identity loaded: {ident.vertical}, {ident.n_routes} routes, "
          f"{len(ident.agents)} agents; warnings: {ident.warnings}")

    notified = []
    hub = Hub(Routes(ident.routes_path), AuditLog("demo-audit.jsonl"),
              human_notifier=lambda q, r: notified.append((q, r)))
    for a in ("01", "02", "03", "14"):
        hub.register(a, lambda e: None)   # stub spokes: accept and return

    sched = SidingScheduler(hub.audit, ident.priority_classes)
    run_id = f"p11-{uuid.uuid4().hex[:8]}"
    hub.on_turn_start()

    steps = []

    def send(frm, to, intent, ctx, payload, step):
        e = env(frm, to, intent, ctx, payload)
        r = hub.send(e)
        steps.append({"step": step, "agent": f"{frm}->{to}", "intent": intent,
                      "envelope_id": e.envelope_id, "status": r["status"]})
        return r

    # ---- WARM lead (ctx lead-w) — P11 continuous sequence
    seg = sched.request_segment(run_id, "P11", spoke="01")
    assert seg["granted"]
    send("20", "01", "lead.signal", "lead-w", {"channel": "social"}, 1)
    send("01", "14", "record.request", "lead-w", {"dedupe": "phone+email"}, 2)
    hub.ingest_spoke_trace("01", steps[-1]["envelope_id"],
                           thought="consent captured verbally; might be a duplicate "
                                   "of last week's inquiry — dedupe before tiering",
                           result="lead object complete, consent=recorded")
    send("01", "02", "lead.captured", "lead-w", {"consent": "recorded"}, 3)
    hub.ingest_spoke_trace("02", steps[-1]["envelope_id"],
                           thought="rubric v3: score 54, WARM tier",
                           result="tier=WARM rubric=v3")
    send("02", "03", "lead.nurture", "lead-w", {"tier": "WARM",
                                                "consent": "on-file"}, 4)
    send("02", "14", "interaction.log", "lead-w", {"tier": "WARM"}, 5)

    # ---- HOT lead (ctx lead-h) — escalation path 4a
    send("20", "01", "lead.signal", "lead-h", {"channel": "call"}, 1)
    send("01", "02", "lead.captured", "lead-h", {"consent": "recorded"}, 3)
    # spoke 02 returns a result WITH NO TRACE -> tainted at ingestion
    hub.ingest_spoke_trace("02", steps[-1]["envelope_id"],
                           thought="", result="tier=HOT rubric=v3")
    hub.escalate("escalation.hot_lead",
                 {"client_context_id": "lead-h", "tier": "HOT", "sla_s": 300})
    send("02", "14", "interaction.log", "lead-h", {"tier": "HOT"}, 5)
    sched.release_segment(run_id, "01")

    # ---- pillar analysis over everything the run produced
    refl = analyze_reflections(hub)
    traces = score_spoke_traces(hub)
    print(f"reflections analyzed: {len(refl)}, flagged: "
          f"{sum(r['flagged'] for r in refl)}")
    print(f"spoke traces: {len(traces)}, tainted: "
          f"{sum(1 for t in traces if t.get('tainted'))}, drift-flagged: "
          f"{sum(1 for t in traces if t.get('flagged'))}")
    print(f"human notified: {len(notified)} (escalation.hot_lead)")

    os.makedirs(outdir, exist_ok=True)
    report = generate_report(hub.audit.read(), "P11", run_id,
                             client_context_id="lead-w", steps=steps)
    path = os.path.join(outdir, f"P11-{run_id}.md")
    open(path, "w").write(report)
    print(f"after-action written: {path}")
    return path, steps, notified


if __name__ == "__main__":
    run()
