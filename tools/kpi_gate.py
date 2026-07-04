"""KPI gate - the invariant class, enforced.

Ratified 07/2026 (owner): these are correctness invariants, zero tolerance,
presumed long-term and adjustable only by the owner:

  ack_integrity.integrity_incidents == 0
  sequence_gap_incidents            == 0
  unverified authority acceptances  == 0   (proven structurally: any accepted
                                            authority intent had a verified
                                            signature or the suite is broken;
                                            here we check no integrity
                                            violation was later acked)
  planted temptations all caught    (taints_expected, selfcheck_bait_expected
                                     supplied by the caller: catching FEWER
                                     than planted means a gate slept)

Delta-class KPIs (latency, dead-letter rate, drift rates, taint rates on
real agents) are RECORDED, not thresholded - thresholds before real T3610
data would be fabricated confidence. They print for comparison only.

Usage:
  python3 tools/kpi_gate.py <audit.jsonl> [--taints-expected N]
                            [--selfcheck-bait-expected N]
Exit 0 = all invariants hold. Exit 1 = violation, each named on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.kpi import compute_kpis


def gate(events: list[dict], taints_expected: int = 0,
         selfcheck_bait_expected: int = 0) -> list[str]:
    """Returns [] when every invariant holds; else one named violation each."""
    k = compute_kpis(events)
    v: list[str] = []
    ii = k["ack_integrity"]["integrity_incidents"]
    if ii != 0:
        v.append(f"INVARIANT ack integrity: {ii} ack(s) without prior persist")
    if k["sequence_gap_incidents"] != 0:
        v.append(f"INVARIANT sequence gaps: {k['sequence_gap_incidents']}")
    acked = {e.get("envelope_id") for e in events if e["kind"] == "ack"}
    bad_auth = [e for e in events if e["kind"] == "integrity.violation"
                and e.get("envelope_id") in acked]
    if bad_auth:
        v.append(f"INVARIANT authority: {len(bad_auth)} integrity-flagged "
                 f"envelope(s) were nevertheless acked")
    taints = k["queue_health"]["tainted_spoke_traces"]
    if taints < taints_expected:
        v.append(f"INVARIANT taint gate slept: {taints} caught, "
                 f"{taints_expected} planted")
    held = sum(1 for e in events
               if e["kind"] == "selfcheck.verdict" and not e.get("passed"))
    if held < selfcheck_bait_expected:
        v.append(f"INVARIANT exit gate slept: {held} held, "
                 f"{selfcheck_bait_expected} baited")
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audit_log")
    ap.add_argument("--taints-expected", type=int, default=0)
    ap.add_argument("--selfcheck-bait-expected", type=int, default=0)
    a = ap.parse_args(argv)
    events = [json.loads(l) for l in open(a.audit_log) if l.strip()]
    v = gate(events, a.taints_expected, a.selfcheck_bait_expected)
    k = compute_kpis(events)
    print("delta-class (recorded, not thresholded):",
          json.dumps({"routing_latency": k["routing_latency"],
                      "queue_health": k["queue_health"],
                      "drift": k["drift"]}, default=str))
    if v:
        for line in v:
            print("FAIL", line)
        return 1
    print("KPI GATE PASS - all invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
