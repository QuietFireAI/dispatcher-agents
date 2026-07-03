"""Heartbeat + playbook-run instrumentation - the last two KPI gaps.

Heartbeat: hub emits `heartbeat` events on cadence; the Watchdog OBSERVES
(spec: 'external watchdog observations' - it reads the log, it never
self-reports for the hub) and names gap incidents where the interval
exceeded 2x cadence. No uptime PERCENTAGE is invented: without an external
wall-clock baseline a percentage would be an estimate, and estimates are
banned. Gaps, spans, and incident counts are facts; report those.

Playbook runs: started / step / completed events so completion becomes a
counted KPI. A run with no `playbook.completed` on the log is incomplete - 
there is no other evidence class for done.
"""
from __future__ import annotations


def heartbeat(hub, source: str = "hub") -> None:
    hub.audit.append("heartbeat", {"source": source})


class Watchdog:
    """External observer of heartbeat events on an audit log."""

    def __init__(self, cadence_s: float):
        if cadence_s <= 0:
            raise ValueError("cadence must be positive")
        self.cadence_s = cadence_s

    def observe(self, events: list[dict]) -> dict:
        beats = sorted(e["ts"] for e in events if e.get("kind") == "heartbeat")
        if len(beats) < 2:
            return {"computable": False,
                    "missing": ["at least 2 heartbeat events"]}
        gaps = [b - a for a, b in zip(beats, beats[1:])]
        incidents = [g for g in gaps if g > 2 * self.cadence_s]
        return {"computable": True, "beats": len(beats),
                "span_s": beats[-1] - beats[0],
                "max_gap_s": max(gaps),
                "gap_incidents": len(incidents),
                "cadence_s": self.cadence_s}


class PlaybookRun:
    """Audit-backed run lifecycle. Completion exists only on the log."""

    def __init__(self, hub, playbook: str, run_id: str, client_context_id: str):
        self.hub, self.playbook, self.run_id = hub, playbook, run_id
        self.ctx = client_context_id
        hub.audit.append("playbook.started",
                         {"playbook": playbook, "run_id": run_id,
                          "client_context_id": client_context_id})

    def step(self, n: int, envelope_id: str | None = None, note: str = ""):
        self.hub.audit.append("playbook.step",
                              {"playbook": self.playbook, "run_id": self.run_id,
                               "step": n, "envelope_id": envelope_id,
                               "note": note})

    def complete(self, outcome: str = "completed"):
        self.hub.audit.append("playbook.completed",
                              {"playbook": self.playbook, "run_id": self.run_id,
                               "outcome": outcome})
