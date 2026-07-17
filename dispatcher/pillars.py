"""Six-pillar wiring - the detection tier running IN the runtime.

Every pillar is imported from its own package (never vendored, never
re-implemented: repackaging drift is defect class P4). Each binds to a
named seam:

  before-turn              -> Hub.on_turn_start (turn entry)
  open-mind                -> analysis.analyze_reflections (hub thought-vs-action)
  agent-open-mind          -> Hub.ingest_spoke_trace taint gate (spoke entry)
  pre-response-selfcheck   -> exit gate on outbound deliveries
  sleep-marks              -> territory transfer (crew change)
  splitvantage             -> second opinion on drift-flagged reflections

Two pillars (pre-response-selfcheck, splitvantage) need model callables to
run their full check; that is deployment configuration. UNARMED IS AUDITED,
never silent: a gate that is off says so on the log.
"""
from __future__ import annotations

from before_turn import BEFORE_TURN_QUESTIONS
from splitvantage import analyze_diff
from sleep_marks.marker import SleepMark
from pre_response_selfcheck.reader_shift import ReaderShift


# ------------------------------------------------------------- before-turn
def before_turn_check(hub, last_n: int = 3) -> dict:
    """Pillar contract at turn entry: re-read your own recent thinking, then
    answer the check questions before acting. The hub's 'recent thinking' is
    its reflection artifacts; the questions come from the pillar package."""
    recent = [a["thought"] for a in hub.reflection_artifacts[-last_n:]]
    rec = {"thoughts_reviewed": len(recent),
           "questions": list(BEFORE_TURN_QUESTIONS),
           "recent_thoughts": recent}
    hub.audit.append("beforeturn.check", rec)
    return rec


# --------------------------------------------------- pre-response-selfcheck
def exit_gate(hub, env, model=None, audience: str = "cold_reader"):
    """Reader-shift exit gate on an outbound envelope's human-facing text.
    With a model: run the pillar's check; FAIL verdict -> envelope held in
    clarification (never delivered), verdict audited with the flagged line.
    Without a model: the gate is UNARMED and says so on the log."""
    text = " ".join(str(v) for v in env.payload.values() if isinstance(v, str))
    if model is None:
        hub.audit.append("selfcheck.unarmed",
                         {"envelope_id": env.envelope_id,
                          "reason": "no reviewer model configured - gate off, "
                                    "declared not silent"})
        return {"armed": False, "passed": None}
    verdict = ReaderShift.check(text or "(no text payload)",
                                audience=audience, model=model)
    rec = {"envelope_id": env.envelope_id, "passed": verdict.passed,
           "line": verdict.line, "fix": verdict.suggested_fix,
           "audience": audience}
    hub.audit.append("selfcheck.verdict", rec)
    if not verdict.passed:
        hub.queue_and_notify(
            "clarification.request",
            {**env.to_record(), "held_by": "pre-response-selfcheck",
             "flagged_line": verdict.line})
    return {"armed": True, **rec}


# -------------------------------------------------------------- sleep-marks
def capture_sleepmark(hub, context_summary: str) -> dict:
    """Crew change: capture the hub's reasoning state as a SleepMark. Open
    questions = what is actually unresolved on the queues; reasoning traces
    = the hub's own recent reflections. Constructed from log-backed state,
    never from memory of prior sessions."""
    open_q = [f"{q}: {len(items)} open item(s)"
              for q, items in hub.queues.items() if items]
    traces = [{"step": i, "thinking": a["thought"]}
              for i, a in enumerate(hub.reflection_artifacts[-5:])]
    mark = SleepMark(conversation_id="hub",
                     context_summary=context_summary,
                     reasoning_traces=traces,
                     open_questions=open_q,
                     reflection_text=context_summary)
    rec = {"context_summary": context_summary,
           "open_questions": open_q, "traces": len(traces)}
    hub.audit.append("sleepmark.captured", rec)
    return {"mark": mark.__dict__, **rec}


def restore_sleepmark(hub, mark: dict) -> dict:
    hub.audit.append("sleepmark.restored",
                     {"context_summary": mark.get("context_summary"),
                      "open_questions": mark.get("open_questions", [])})
    return mark


# ------------------------------------------------------------- splitvantage
def second_opinion(hub, prompt: str, out_a: dict, out_b: dict,
                   envelope_id: str | None = None) -> dict:
    """CrossPol on a drift-flagged decision: two models' outputs on the same
    prompt, structurally diffed by the pillar. Caller supplies the outputs
    ({model, response, thinking}); the runtime never calls model APIs itself."""
    diff = analyze_diff(out_a, out_b, prompt)
    rec = {"envelope_id": envelope_id,
           "models": [out_a.get("model"), out_b.get("model")],
           "uncertainty_delta": diff.get("uncertainty_delta"),
           "notes": diff.get("notes")}
    hub.audit.append("splitvantage.review", rec)
    return diff
