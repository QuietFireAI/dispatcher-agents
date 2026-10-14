"""Tests written from the 2026-10-13 full mutation sweep (repair plan 1.4):
the after-action report, the KPIs, the watchdog and the playbook record.

Before these, the after-action report had one test in the core (the failed
notification line); 32 of its 52 mutants survived. The report is built only
from the audit log, so each test feeds a fixed log and checks the exact
lines a reader gets.
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.after_action import generate_report  # noqa: E402
from dispatcher.kpi import compute_kpis  # noqa: E402


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


C = "ctx-A"
LOG = [
    {"kind": "envelope.persisted", "ts": 100.0, "envelope_id": "e1", "client_context_id": C},
    {"kind": "ack", "ts": 100.25, "envelope_id": "e1"},                 # no context on acks
    {"kind": "envelope.persisted", "ts": 101.0, "envelope_id": "e2", "client_context_id": C},
    {"kind": "reject", "ts": 102.0, "envelope_id": "e3", "reason": "tuple illegal: x"},   # step's id, no ctx
    {"kind": "reject", "ts": 102.5, "envelope_id": "zz", "client_context_id": "ctx-OTHER",
     "reason": "someone else's run"},
    {"kind": "human.notify_failed", "ts": 102.8, "queue": "escalation.complaint",
     "notifier_status": "failed"},                                      # stale: before its escalation
    {"kind": "escalation.raised", "ts": 103.0, "queue": "escalation.hot_lead", "client_context_id": C},
    {"kind": "human.notified", "ts": 103.5, "queue": "escalation.hot_lead"},
    {"kind": "escalation.raised", "ts": 104.0, "queue": "escalation.legal_line", "client_context_id": C},
    {"kind": "human.notify_failed", "ts": 104.1, "queue": "escalation.legal_line", "error": "RuntimeError('x')"},
    {"kind": "escalation.raised", "ts": 105.0, "queue": "escalation.complaint", "client_context_id": C},
    {"kind": "agentopenmind.tainted", "ts": 105.5, "agent": "03", "client_context_id": C},
    {"kind": "dead.letter", "ts": 106.0, "envelope_id": "e2", "reason": "no handler for 09"},
    {"kind": "manners.reinjection", "ts": 107.0, "trigger": "long_thread", "position": 12,
     "client_context_id": C},
    {"kind": "manners.reinjection", "ts": 108.0, "trigger": "new_party", "client_context_id": C},
]
STEPS = [{"step": 1, "agent": "01", "intent": "lead.inbound", "envelope_id": "e1"},
         {"step": 2, "agent": "02", "intent": "lead.score", "envelope_id": "e2"},
         {"step": 3, "agent": "05", "intent": "listing.x", "envelope_id": "e3"}]


def report(**kw):
    return generate_report(LOG, "P11", "run-1", C, STEPS, **kw).splitlines()


def section(lines, name):
    i = lines.index(f"## {name}")
    out = []
    for l in lines[i + 1:]:
        if l.startswith("## "):
            break
        if l:
            out.append(l)
    return out


def test_the_run_window_is_the_first_and_last_event_of_this_run():
    r = report()
    assert f"- started: {iso(100.0)}" in r and f"- ended: {iso(108.0)}" in r


def test_steps_are_proven_by_their_ack_with_the_latency_from_the_log():
    assert section(report(), "steps") == [
        "- step 1 [01] lead.inbound: executed=yes, proof=ack on e1 (audit), latency=0.2500s",
        "- step 2 [02] lead.score: executed=NO ACK ON LOG (envelope e2) - unproven, not counted done",
        "- step 3 [05] listing.x: executed=NO ACK ON LOG (envelope e3) - unproven, not counted done"]


def test_the_outcome_carries_an_abort_reason_verbatim_only_when_there_is_one():
    assert section(report(), "outcome") == ["- completed"]
    assert section(report(outcome="aborted", abort_reason="seller withdrew"), "outcome") == \
        ["- aborted - verbatim reason: seller withdrew"]


def test_the_gates_count_escalations_and_taints():
    assert section(report(), "gates") == [
        "- hot-lead escalation gate: TRIGGERED x3 (evidence: escalation.raised on log)",
        "- absent-thought taint gate: TRIGGERED x1 (evidence: agentopenmind.tainted)"]


def test_deviations_are_this_runs_and_only_this_runs():
    devs = section(report(), "deviations")
    assert [d.split(":")[0] for d in devs] == ["- reject", "- agentopenmind.tainted", "- dead.letter"]
    assert not any("someone else's run" in d for d in devs)


def test_each_escalation_says_whether_a_person_was_reached():
    assert section(report(), "escalations") == [
        "- escalation.hot_lead: transport 0.5000s; human response time: NOT INSTRUMENTED",
        "- escalation.legal_line: raised, NOTIFICATION FAILED (RuntimeError('x')) - no person was reached",
        # the failure on record is from BEFORE this escalation - it proves nothing about it
        "- escalation.complaint: raised, NO human.notified on log - transport unproven"]


def test_errors_list_rejects_and_dead_letters_with_their_reasons():
    assert section(report(), "errors") == ["- reject: tuple illegal: x", "- dead.letter: no handler for 09"]


def test_manners_reinjections_are_counted_and_listed():
    assert section(report(), "manners re-injections") == [
        "- count: 2", "- long_thread @ 12", "- new_party @ unstated"]


def test_a_quiet_run_says_none_in_each_section():
    log = [{"kind": "envelope.persisted", "ts": 1.0, "envelope_id": "q1", "client_context_id": "Q"},
           {"kind": "ack", "ts": 1.5, "envelope_id": "q1"}]
    r = generate_report(log, "P1", "r", "Q", [{"step": 1, "agent": "01", "intent": "i",
                                                "envelope_id": "q1"}]).splitlines()
    assert section(r, "gates") == ["- hot-lead escalation gate: not triggered this run",
                                   "- absent-thought taint gate: cleared - all traces present"]
    assert section(r, "deviations") == ["- none on log"]
    assert section(r, "escalations") == ["- none"]
    assert section(r, "errors") == ["- none on log"]
    assert section(r, "manners re-injections") == ["- none on log this run (instrumented; zero fired)"]


def test_a_run_with_no_events_on_the_log_is_refused():
    with pytest.raises(ValueError, match="without log evidence would be fabricated"):
        generate_report([{"kind": "x", "ts": 1.0, "client_context_id": "other"}],
                        "P1", "r", "Q", [])


# ======================================================================= KPIs
def test_an_ack_counts_only_when_its_envelope_was_persisted_first():
    k = compute_kpis([
        {"kind": "envelope.persisted", "ts": 10.0, "envelope_id": "a"},
        {"kind": "ack", "ts": 10.5, "envelope_id": "a"},
        {"kind": "ack", "ts": 11.0, "envelope_id": "b"},                 # never persisted
        {"kind": "ack", "ts": 11.5, "envelope_id": "c"},                 # acked before persisted
        {"kind": "envelope.persisted", "ts": 12.0, "envelope_id": "c"}])["ack_integrity"]
    assert k == {"acks": 3, "acks_backed_by_persist": 1, "rate": 1 / 3, "integrity_incidents": 2}


def test_routing_latency_p50_is_the_middle_of_the_sorted_latencies():
    ev = []
    for i, lat in enumerate([0.4, 0.1, 0.3, 0.2]):
        ev += [{"kind": "envelope.persisted", "ts": 100.0 + i, "envelope_id": f"e{i}"},
               {"kind": "ack", "ts": 100.0 + i + lat, "envelope_id": f"e{i}"}]
    r = compute_kpis(ev)["routing_latency"]
    assert r["n"] == 4 and r["p50_s"] == pytest.approx(0.3) and r["max_s"] == pytest.approx(0.4)


def test_sequence_gaps_are_counted_per_context_from_one():
    ev = [{"kind": "envelope.persisted", "ts": 1, "envelope_id": f"a{s}", "client_context_id": "A",
           "sequence": s} for s in (1, 2, 4)]                              # 4 is out of place
    ev += [{"kind": "envelope.persisted", "ts": 1, "envelope_id": f"b{s}", "client_context_id": "B",
            "sequence": s} for s in (1, 2)]
    ev += [{"kind": "envelope.persisted", "ts": 1, "envelope_id": "n1", "client_context_id": "A",
            "sequence": None},                                             # not stamped: ignored
           {"kind": "envelope.persisted", "ts": 1, "envelope_id": "n2", "sequence": 9}]  # no context
    assert compute_kpis(ev)["sequence_gap_incidents"] == 1


def test_escalation_transport_needs_an_escalation_to_measure():
    k = compute_kpis([{"kind": "human.notified", "ts": 1.0, "queue": "escalation.hot_lead"}])
    assert k["escalation_transport_time"]["computable"] is False


def test_heartbeat_from_two_beats_with_exact_span_and_gap():
    k = compute_kpis([{"kind": "heartbeat", "ts": t} for t in (10.0, 11.0, 14.0)])["heartbeat"]
    assert k == {"computable": True, "beats": 3, "span_s": 4.0, "max_gap_s": 3.0}
    two = compute_kpis([{"kind": "heartbeat", "ts": t} for t in (10.0, 12.5)])["heartbeat"]
    assert two == {"computable": True, "beats": 2, "span_s": 2.5, "max_gap_s": 2.5}


def test_manners_and_drift_counts_are_exact():
    ev = [{"kind": "manners.reinjection", "ts": 1, "trigger": t}
          for t in ("phase_gate", "phase_gate", "turn_backstop", "other")]
    ev += [{"kind": "openmind.drift", "ts": 1, "flagged": f} for f in (True, False, True)]
    ev += [{"kind": "agentopenmind.trace", "ts": 1, "flagged": f} for f in (False, True)]
    k = compute_kpis(ev)
    assert k["manners_reinjections"] == {"count": 4, "by_trigger": {
        "phase_gate": 2, "post_compaction": 0, "turn_backstop": 1}}
    assert k["drift"] == {"hub_reflections_analyzed": 3, "hub_reflections_flagged": 2,
                          "spoke_traces_scored": 2, "spoke_traces_flagged": 1}


def test_another_runs_escalation_is_not_in_this_report():
    log = LOG + [{"kind": "escalation.raised", "ts": 109.0, "queue": "escalation.hot_lead",
                  "client_context_id": "ctx-OTHER"},
                 {"kind": "human.notified", "ts": 109.5, "queue": "escalation.hot_lead",
                  "client_context_id": "ctx-OTHER"}]
    r = generate_report(log, "P11", "run-1", C, STEPS).splitlines()
    assert len(section(r, "escalations")) == 3                    # this run's three only
    assert section(r, "gates")[0] == "- hot-lead escalation gate: TRIGGERED x3 (evidence: escalation.raised on log)"


def _persisted(ctx, *seqs):
    return [{"kind": "envelope.persisted", "ts": 1, "envelope_id": f"{ctx}{i}",
             "client_context_id": ctx, "sequence": s} for i, s in enumerate(seqs)]


def test_a_bare_restart_is_a_restart_and_a_transferred_context_continues(tmp_path):
    # repair plan 2.4 (2026-10-14): both used to be counted as gaps
    k = compute_kpis(_persisted("A", 1, 2, 3, 1, 2)                   # hub restarted, no state
                     + [{"kind": "territory.transfer.received", "ts": 0,
                         "contexts": {"T": {"sequence_hwm": 7}}}]
                     + _persisted("T", 8, 9)                        # continues after the mark
                     + _persisted("G", 1, 3)                        # a real gap
                     + _persisted("U", 5))                          # starts mid-way, no transfer
    assert k["sequence_gap_incidents"] == 2 and k["sequence_restarts"] == 1
