"""Pillar analysis wiring - Day 2.

Consumes the seams Day 1 left deliberately empty:
  hub.reflection_artifacts -> open-mind Comparator (PILLAR source, imported - 
                               not a vendored copy; repackaging drift is a
                               named defect class, P4).
  hub.spoke_traces -> agent-open-mind scoring. Semantics from the
                               pillar's SKILL.md protocol verbatim:
                               (1) read the trace, not just the result;
                               (2) Absent Thoughts = Tainted Result - flag,
                                   never silent-admit;
                               (3) feed suppressed uncertainty back to the
                                   dispatcher's decision layer.
                               The thought-vs-result gap is measured with the
                               same open-mind Comparator: one drift engine
                               across hub and spokes, two granularities.

Every analysis result is audit-logged. The audit log stays the single source
of truth; these modules add events, they never self-report outside it.
"""
from __future__ import annotations

from open_mind.comparator import Comparator, DriftResult   # pillar source


# ------------------------------------------------------------- open-mind tier
def analyze_reflections(hub, drift_threshold: float = 0.4) -> list[dict]:
    """Run the pillar Comparator over every hub reflection artifact.

    Returns one record per artifact: envelope_id, drift_score, signals,
    flagged (score >= threshold). Each record is appended to the audit log
    as kind='openmind.drift' before it is returned - persist before report,
    same rule as delivery.
    """
    results = []
    for art in hub.reflection_artifacts:
        dr: DriftResult = Comparator.compare(art["thought"], art["response"])
        rec = {
            "envelope_id": art["envelope_id"],
            "drift_score": dr.drift_score,
            "signals": list(dr.signals),
            "flagged": dr.drift_score >= drift_threshold,
        }
        hub.audit.append("openmind.drift", rec)
        if rec["flagged"] and getattr(hub, "crosspol_models", None):
            from .pillars import second_opinion
            ma, mb = hub.crosspol_models
            prompt = art["thought"]
            second_opinion(hub, prompt, ma(prompt), mb(prompt),
                           envelope_id=art["envelope_id"])
        results.append(rec)
    return results


# ------------------------------------------------------- agent-open-mind tier
def score_spoke_traces(hub, drift_threshold: float = 0.4) -> list[dict]:
    """agent-open-mind protocol over hub-collected spoke traces.

    Gate first: a trace with no thought content is TAINTED - flagged for
    review via the integrity queue and audit-logged, never silently admitted
    and never scored as if a trace existed. Presence is then analyzed:
    thought-vs-result drift, so the dispatcher decides on what the spoke
    actually reasoned, not its shaped output alone.
    """
    results = []
    for trace in hub.spoke_traces:
        thought = (trace.get("thought") or "").strip()
        base = {"agent": trace["agent"], "envelope_id": trace["envelope_id"]}
        from agent_open_mind import taint_check
        if taint_check(trace)["tainted"]:
            rec = {**base, "tainted": True,
                   "reason": "absent thought trace - tainted result, held for review"}
            if not trace.get("tainted"):
                # not caught at ingestion (e.g. trace loaded from elsewhere):
                # the analysis layer is the backstop - flag here
                hub.queue_and_notify("integrity.violation", rec)
                hub.audit.append("agentopenmind.tainted", rec)
            results.append(rec)
            continue
        dr: DriftResult = Comparator.compare(thought, trace["result"])
        rec = {**base, "tainted": False,
               "drift_score": dr.drift_score,
               "signals": list(dr.signals),
               "flagged": dr.drift_score >= drift_threshold}
        hub.audit.append("agentopenmind.trace", rec)
        results.append(rec)
    return results
