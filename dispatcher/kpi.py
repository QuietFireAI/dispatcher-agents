"""KPI module - Day 2.

Spec: DISPATCHER_CORE.md 'Standardized KPIs (identity-independent)'.
Rule from the spec, enforced literally here: computed from the audit log - 
the log is the instrumentation; no self-reported metrics; never estimate a
KPI from memory. KPIs whose required events the runtime does not yet emit
are returned as {"computable": False, "missing": <event kinds>} - declared
absent, never estimated. (Gate principle: absence of the expected artifact
is reported, not papered over.)

What it computes (docstring corrected 2026-10-13: an earlier list said the
last four were not computable; the code below computes all of them when
their events are on the log):
 - ack integrity rate          (ack backed by prior persist of same envelope)
 - routing latency             (persist ts -> ack ts, same envelope)
 - queue health                (dead-letter rate, hold count, taint count)
 - dedupe hits, rejections, sequence-gap incidents, loop suspensions
 - escalation transport time   (escalation.raised -> human.notified); since
                               2026-10-13 a failed notification is
                               human.notify_failed and never counts as a
                               transport, and a notifier that reported nothing
                               is counted apart as "not_reported"
 - playbook completion         (playbook.started / playbook.completed)
 - heartbeat observations      (needs at least two heartbeat events; gap
                               incidents need the Watchdog's cadence)
"""
from __future__ import annotations

from collections import defaultdict


NOTIFY_OUTCOMES = ("human.notified", "human.notify_failed")


def pair_escalations(events: list[dict]) -> list[tuple[dict, dict | None]]:
    """Each escalation.raised with the notification outcome written for it,
    in log order. Since the second 2026-10-13 review every outcome carries
    "raised_entry": the entry_hash of the escalation.raised it answers (or
    None), and pairing is exact. Logs written before that are paired by
    order: the first outcome on the escalation's queue after it and before
    the next escalation on that queue. An outcome is never shared: one
    escalation's delivery cannot stand in for another's failure (first
    review: matching by queue and time alone let a later page cover an
    earlier failed one; second review: two processes raising on one queue
    could still interleave)."""
    pairs: list[list] = []
    waiting: dict = {}
    by_entry: dict = {}
    for e in events:
        k = e.get("kind")
        if k == "escalation.raised":
            if e.get("entry_hash"):
                by_entry[e["entry_hash"]] = len(pairs)
            waiting[e.get("queue")] = len(pairs)
            pairs.append([e, None])
        elif k in NOTIFY_OUTCOMES and "raised_entry" in e:
            # written since the second review: the outcome names its
            # escalation's entry_hash, or None when it answers no escalation
            # (a queue landing, Hub.notify). Exact; log order does not matter.
            i = by_entry.get(e["raised_entry"]) if e["raised_entry"] else None
            if i is not None and pairs[i][1] is None:
                pairs[i][1] = e
                if waiting.get(e.get("queue")) == i:
                    waiting.pop(e.get("queue"))
        elif k in NOTIFY_OUTCOMES and e.get("queue") in waiting:
            pairs[waiting.pop(e.get("queue"))][1] = e     # older logs: queue order
    return [(r, o) for r, o in pairs]


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

    # Sequence-gap incidents: per client context, each persisted sequence must
    # follow the one before it. A context starts at 1, or - when this hub
    # received it by territory transfer - right after the high-water mark
    # the transfer carried. A sequence that starts over at 1 is a restart of
    # a hub that kept no state (a bare core), counted as a restart, not a gap
    # (2026-10-14, repair plan 2.4: both used to count as gaps).
    seqs: dict[str, list[int]] = defaultdict(list)
    for e in by_kind["envelope.persisted"]:
        if "client_context_id" in e and e.get("sequence") is not None:
            seqs[e["client_context_id"]].append(e["sequence"])
    first_expected: dict[str, int] = {}
    for e in by_kind["territory.transfer.received"]:
        for ctx, meta in (e.get("contexts") or {}).items():
            if isinstance(meta, dict) and isinstance(meta.get("sequence_hwm"), int):
                first_expected[ctx] = meta["sequence_hwm"] + 1
    gap_incidents = sequence_restarts = 0
    for ctx, run in seqs.items():
        expected = first_expected.get(ctx, 1)
        for s in run:
            if s == 1 and expected != 1:
                sequence_restarts += 1
            elif s != expected:
                gap_incidents += 1
            expected = s + 1

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

    # escalation transport: each escalation paired with its own outcome
    # (pair_escalations). Only human.notified is a transport;
    # human.notify_failed is counted as failed, and a notified entry whose
    # notifier reported nothing ("transport": "not reported") is counted
    # apart from confirmed ones.
    raised = by_kind["escalation.raised"]
    pairs = pair_escalations(events)
    if raised and any(o for _, o in pairs):
        notified = [(r, o) for r, o in pairs if o and o["kind"] == "human.notified"]
        transports = [o["ts"] - r["ts"] for r, o in notified]
        escalation_transport = {"computable": True, "n": len(transports),
                                "unnotified": len(raised) - len(transports),
                                "max_s": max(transports) if transports else None,
                                "confirmed": sum(1 for _, o in notified
                                                 if o.get("transport") != "not reported"),
                                "not_reported": sum(1 for _, o in notified
                                                    if o.get("transport") == "not reported"),
                                "notify_failed": sum(1 for _, o in pairs
                                                     if o and o["kind"] == "human.notify_failed")}
    else:
        escalation_transport = {"computable": False,
                                "missing": ["escalation.raised/human.notified pairs"]}

    started = by_kind["playbook.started"]
    completed = by_kind["playbook.completed"]
    if started:
        done_ids = {e.get("run_id") for e in completed}
        playbook_completion = {
            "computable": True, "started": len(started),
            "completed": len(completed),
            "rate": len([e for e in started if e.get("run_id") in done_ids])
                    / len(started),
            "incomplete_runs": [e.get("run_id") for e in started
                                if e.get("run_id") not in done_ids]}
    else:
        playbook_completion = {"computable": False,
                               "missing": ["playbook.started/completed"]}

    beats = sorted(e["ts"] for e in by_kind["heartbeat"])
    if len(beats) >= 2:
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        heartbeat_obs = {"computable": True, "beats": len(beats),
                         "span_s": beats[-1] - beats[0],
                         "max_gap_s": max(gaps)}
        # NOTE: gap INCIDENTS need an expected cadence - that lives with the
        # external Watchdog (dispatcher.runs), which owns the threshold.
    else:
        heartbeat_obs = {"computable": False,
                         "missing": ["at least 2 heartbeat events"]}

    not_computable = {}

    return {
        "ack_integrity": ack_integrity,
        "routing_latency": routing_latency,
        "sequence_gap_incidents": gap_incidents,
        "sequence_restarts": sequence_restarts,
        "loop_suspensions": len(by_kind["loop.suspended"]),
        "manners_reinjections": {
            "count": len(by_kind["manners.reinjection"]),
            "by_trigger": {t: sum(1 for e in by_kind["manners.reinjection"]
                                  if e.get("trigger") == t)
                           for t in ("phase_gate", "post_compaction",
                                     "turn_backstop")}},
        "dedupe_hits": len(by_kind["dedupe.hit"]),
        "rejects": len(by_kind["reject"]),
        "queue_health": queue_health,
        "escalation_transport_time": escalation_transport,
        "playbook_completion": playbook_completion,
        "heartbeat": heartbeat_obs,
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
