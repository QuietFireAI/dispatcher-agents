"""KPI module — Day 2.

Spec: DISPATCHER_CORE.md 'Standardized KPIs (identity-independent)'.
Rule from the spec, enforced literally here: computed from the audit log —
the log is the instrumentation; no self-reported metrics; never estimate a
KPI from memory. KPIs whose required events the runtime does not yet emit
are returned as {"computable": False, "missing": <event kinds>} — declared
absent, never estimated. (Gate principle: absence of the expected artifact
is reported, not papered over.)

Computable from Day 1/2 event kinds
(ack, envelope.persisted, reject, dead.letter, dedupe.hit, hold.clarification,
 integrity.violation, hub.reflection, spoke.trace, turn.start,
 openmind.drift, agentopenmind.tainted, agentopenmind.trace):
  - ack integrity rate          (ack backed by prior persist of same envelope)
  - routing latency             (persist ts -> ack ts, same envelope)
  - queue health                (dead-letter rate, hold count, taint count)
  - dedupe hits, signature/integrity rejections, sequence-gap incidents

NOT computable yet — no emitting instrumentation exists:
  - escalation transport time   (no human-notification event)
  - playbook completion / completion-proof coverage (no playbook events)
  - heartbeat uptime            (external watchdog does not exist)
  - loop suspensions            (no suspension event kind)
"""
from __future__ import annotations

from collections import defaultdict


def compute_kpis(events: list[dict]) -> dict:
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_kind[e.get("kind", "?")].append(e)

    persisted = {e["envelope_id"]: e["ts"] for e in by_kind["envelope.persisted"]}
    acks = by_kind["ack"]

    # Ack integrity: every ack must be preceded by a persist of that envelope.
    backed = [a for a in acks
              if a["envelope_id"] in persisted and persisted[a["envelope_id"]] <= a["ts"]]
    ack_integrity = {
        "acks": len(acks),
        "acks_backed_by_persist": len(backed),
        "rate": (len(backed) / len(acks)) if acks else None,
        "integrity_incidents": len(acks) - len(backed),  # target: 0, always
    }

    # Routing latency: persist -> ack per envelope (seconds).
    latencies = sorted(a["ts"] - persisted[a["envelope_id"]] for a in backed)
    routing_latency = {
        "n": len(latencies),
        "max_s": latencies[-1] if latencies else None,
        "p50_s": latencies[len(latencies) // 2] if latencies else None,
    }

    # Sequence-gap incidents: per client context, persisted sequences must be
    # a gapless 1..n run in arrival order.
    seqs: dict[str, list[int]] = defaultdict(list)
    for e in by_kind["envelope.persisted"]:
        if "client_context_id" in e and e.get("sequence") is not None:
            seqs[e["client_context_id"]].append(e["sequence"])
    gap_incidents = sum(
        1 for run in seqs.values()
        for i, s in enumerate(run, start=1) if s != i
    )

    n_persisted = len(by_kind["envelope.persisted"])
    queue_health = {
        "dead_letter": len(by_kind["dead.letter"]),
        "dead_letter_rate": (len(by_kind["dead.letter"]) / n_persisted)
                            if n_persisted else None,
        "holds_clarification": len(by_kind["hold.clarification"]),
        "integrity_violations": len(by_kind["integrity.violation"]),
        "tainted_spoke_traces": len(by_kind["agentopenmind.tainted"]),
        "siding_holds": len(by_kind["siding.hold"]),
        "siding_resumes": len(by_kind["siding.resume"]),
    }

    # escalation transport: raised -> human.notified, matched per queue+ts order
    raised = by_kind["escalation.raised"]
    notified = by_kind["human.notified"]
    if raised and notified:
        n_by_q = defaultdict(list)
        for n in notified:
            n_by_q[n.get("queue")].append(n["ts"])
        transports = []
        for r in raised:
            cands = [t for t in n_by_q.get(r.get("queue"), []) if t >= r["ts"]]
            if cands:
                transports.append(min(cands) - r["ts"])
        escalation_transport = {"computable": True, "n": len(transports),
                                "unnotified": len(raised) - len(transports),
                                "max_s": max(transports) if transports else None}
    else:
        escalation_transport = {"computable": False,
                                "missing": ["escalation.raised/human.notified pairs"]}

    not_computable = {
        "playbook_completion": {"computable": False,
                                "missing": ["playbook.step", "playbook.proof"]},
        "heartbeat_uptime": {"computable": False,
                             "missing": ["external watchdog observations"]},
        "loop_suspensions": {"computable": False,
                             "missing": ["loop.suspended"]},
    }

    return {
        "ack_integrity": ack_integrity,
        "routing_latency": routing_latency,
        "sequence_gap_incidents": gap_incidents,
        "dedupe_hits": len(by_kind["dedupe.hit"]),
        "rejects": len(by_kind["reject"]),
        "queue_health": queue_health,
        "escalation_transport_time": escalation_transport,
        "drift": {
            "hub_reflections_analyzed": len(by_kind["openmind.drift"]),
            "hub_reflections_flagged": sum(1 for e in by_kind["openmind.drift"]
                                           if e.get("flagged")),
            "spoke_traces_scored": len(by_kind["agentopenmind.trace"]),
            "spoke_traces_flagged": sum(1 for e in by_kind["agentopenmind.trace"]
                                        if e.get("flagged")),
        },
        "not_computable": not_computable,
    }
