"""After-action report generator - Day 4.

Implements AFTER_ACTION.md's template from the audit log ONLY - never from
agent memory, never self-reported. One markdown report per run.

Schema fields the runtime cannot yet populate are DECLARED, not faked:
- human response time: notification is instrumented, response is not
(Corrected 2026-10-13: this list also named manners re-injections, which the
report below does read from the log.)
An unreported deviation is a fabricated report (schema's own words), so
deviations are computed mechanically: any audited reject, hold, dead-letter,
taint, or siding event inside the run window is a deviation, verbatim.
"""
from __future__ import annotations

import datetime

from .kpi import compute_kpis, pair_escalations


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone.utc).isoformat()


_DEVIATION_KINDS = ("reject", "hold.clarification", "dead.letter",
                    "integrity.violation", "agentopenmind.tainted",
                    "siding.hold")


def generate_report(events: list[dict], playbook: str, run_id: str,
                    client_context_id: str, steps: list[dict],
                    outcome: str = "completed",
                    abort_reason: str | None = None) -> str:
    """events: full audit log. steps: the playbook's step list as executed,
    each {step, agent, intent, envelope_id} - proof and latency are looked up
    in the log, never taken from the caller's word."""
    # this run's events only: its client context, or an envelope of its
    # steps. (Until 2026-10-13 every escalation.raised and notification on
    # the log was added regardless of context, so a report listed other
    # runs' escalations as its own.) Notifications are still paired with
    # escalations over the whole log (kpi.pair_escalations), so an outcome
    # recorded without this context still answers this run's escalation.
    run_events = [e for e in events
                  if e.get("client_context_id") == client_context_id
                  or e.get("envelope_id") in {s.get("envelope_id") for s in steps}]
    if not run_events:
        raise ValueError(f"no audited events for run {run_id!r} - a report "
                         f"without log evidence would be fabricated")
    t0, t1 = run_events[0]["ts"], run_events[-1]["ts"]

    persisted = {e.get("envelope_id"): e["ts"] for e in events
                 if e["kind"] == "envelope.persisted"}
    acked = {e.get("envelope_id"): e["ts"] for e in events if e["kind"] == "ack"}

    lines = [f"# After-Action - {playbook} run {run_id}", "",
             "## run",
             f"- playbook: {playbook}",
             f"- run_id: {run_id}",
             f"- client_context_id: {client_context_id}",
             f"- started: {_iso(t0)}",
             f"- ended: {_iso(t1)}", "",
             "## outcome",
             f"- {outcome}" + (f" - verbatim reason: {abort_reason}"
                               if abort_reason else ""), "",
             "## steps"]
    for s in steps:
        eid = s.get("envelope_id")
        if eid in acked:
            lat = acked[eid] - persisted.get(eid, acked[eid])
            proof = f"ack on {eid} (audit)"
            lines.append(f"- step {s['step']} [{s['agent']}] {s['intent']}: "
                         f"executed=yes, proof={proof}, latency={lat:.4f}s")
        else:
            lines.append(f"- step {s['step']} [{s['agent']}] {s['intent']}: "
                         f"executed=NO ACK ON LOG (envelope {eid}) - "
                         f"unproven, not counted done")

    lines += ["", "## gates"]
    esc = [e for e in run_events if e["kind"] == "escalation.raised"]
    taints = [e for e in run_events if e["kind"] == "agentopenmind.tainted"]
    lines.append(f"- hot-lead escalation gate: "
                 + (f"TRIGGERED x{len(esc)} (evidence: escalation.raised on log)"
                    if esc else "not triggered this run"))
    lines.append(f"- absent-thought taint gate: "
                 + (f"TRIGGERED x{len(taints)} (evidence: agentopenmind.tainted)"
                    if taints else "cleared - all traces present"))

    lines += ["", "## deviations"]
    devs = [e for e in run_events if e["kind"] in _DEVIATION_KINDS]
    if devs:
        for d in devs:
            lines.append(f"- {d['kind']}: {d}")
    else:
        lines.append("- none on log")

    lines += ["", "## escalations"]
    # each escalation's own outcome (kpi.pair_escalations), paired over the
    # whole log so another escalation's notification never stands in
    outcome = {id(r): o for r, o in pair_escalations(events)}
    if esc:
        for e in esc:
            q = e.get("queue")
            o = outcome.get(id(e))
            if o is None:
                lines.append(f"- {q}: raised, NO human.notified on log - "
                             f"transport unproven")
            elif o["kind"] == "human.notify_failed":
                why = o.get("notifier_status") or o.get("error") or "unknown"
                lines.append(f"- {q}: raised, NOTIFICATION FAILED ({why}) - "
                             f"no person was reached")
            elif o.get("transport") == "not reported":
                lines.append(f"- {q}: raised, the notifier reported nothing - "
                             f"delivery unconfirmed, no transport time")
            else:
                lines.append(f"- {q}: transport {o['ts'] - e['ts']:.4f}s; "
                             f"human response time: NOT INSTRUMENTED")
    else:
        lines.append("- none")

    errors = [e for e in run_events
              if e["kind"] in ("reject", "dead.letter")]
    lines += ["", "## errors"]
    lines += ([f"- {e['kind']}: {e.get('reason', e)}" for e in errors]
              or ["- none on log"])

    k = compute_kpis(events)
    lines += ["", "## kpis (full-log DISPATCHER_CORE set)",
              f"- ack integrity rate: {k['ack_integrity']['rate']}",
              f"- routing latency p50/max: {k['routing_latency']['p50_s']}"
              f" / {k['routing_latency']['max_s']} s",
              f"- escalation transport: {k['escalation_transport_time']}",
              f"- sequence gaps: {k['sequence_gap_incidents']}, "
              f"dedupe hits: {k['dedupe_hits']}, rejects: {k['rejects']}",
              f"- queue health: {k['queue_health']}",
              f"- drift: {k['drift']}",
              "", "## manners re-injections"]
    manners = [e for e in run_events if e["kind"] == "manners.reinjection"]
    if manners:
        lines.append(f"- count: {len(manners)}")
        for m in manners:
            lines.append(f"- {m.get('trigger')} @ {m.get('position') or 'unstated'}")
    else:
        lines.append("- none on log this run (instrumented; zero fired)")
    return "\n".join(lines) + "\n"
