"""JIT run priority + siding - Day 3.

Implements DISPATCHER_CORE.md 'Run priority - JIT doctrine' verbatim:
- Classes 1-4 are CORE and never change (1 escalation, 2 JIT freight,
  3 scheduled service, 4 junk trains).
- Class assignment per playbook is VERTICAL-SPECIFIC and comes from the
  identity module (loader supplies it) - never hardcoded here.
- Contention rule: two runs contending for the same spoke → higher class
  proceeds, lower takes the siding: held LIVE (state intact, never aborted
  by contention), resuming automatically when the segment clears.
- Every siding event is logged (siding.hold / siding.resume) and is
  after-action reportable from the audit log alone.

Ties: equal class does not side anyone - first holder keeps the segment,
the contender waits in FIFO order behind it. Arrival order breaks ties
WITHIN a class only; it never beats class (doctrine: 'never by arrival
order' governs cross-class contention).
"""
from __future__ import annotations

from collections import deque


class SidingScheduler:
    """Per-spoke segment authority. One holder per spoke at a time."""

    def __init__(self, audit, playbook_classes: dict[str, int]):
        self.audit = audit
        self.classes = dict(playbook_classes)      # playbook -> class 1..4
        self.holding: dict[str, tuple[str, int]] = {}   # spoke -> (run, class)
        self.sidings: dict[str, deque] = {}             # spoke -> waiting runs

    def _class_of(self, playbook: str) -> int:
        if playbook not in self.classes:
            raise KeyError(
                f"playbook {playbook!r} has no priority class in the identity "
                f"module - unclassified traffic does not run (gate principle)")
        return self.classes[playbook]

    def request_segment(self, run_id: str, playbook: str, spoke: str) -> dict:
        cls = self._class_of(playbook)
        holder = self.holding.get(spoke)
        if holder is None:
            self.holding[spoke] = (run_id, cls)
            self.audit.append("segment.grant",
                              {"run": run_id, "spoke": spoke, "class": cls})
            return {"granted": True, "run": run_id, "spoke": spoke}
        holder_run, holder_cls = holder
        if cls < holder_cls:
            # contender outranks holder: holder takes the siding, held LIVE
            self.sidings.setdefault(spoke, deque()).appendleft(
                (holder_run, holder_cls))
            self.audit.append("siding.hold",
                              {"run": holder_run, "spoke": spoke,
                               "class": holder_cls, "sided_by": run_id,
                               "sided_by_class": cls, "state": "live"})
            self.holding[spoke] = (run_id, cls)
            self.audit.append("segment.grant",
                              {"run": run_id, "spoke": spoke, "class": cls})
            return {"granted": True, "run": run_id, "spoke": spoke,
                    "sided": holder_run}
        # holder keeps the segment; contender waits (class order, FIFO within)
        q = self.sidings.setdefault(spoke, deque())
        q.append((run_id, cls))
        self.audit.append("siding.hold",
                          {"run": run_id, "spoke": spoke, "class": cls,
                           "sided_by": holder_run, "sided_by_class": holder_cls,
                           "state": "live"})
        return {"granted": False, "run": run_id, "spoke": spoke,
                "held_behind": holder_run}

    def release_segment(self, run_id: str, spoke: str) -> dict | None:
        holder = self.holding.get(spoke)
        if holder is None or holder[0] != run_id:
            raise ValueError(f"{run_id!r} does not hold segment {spoke!r}")
        del self.holding[spoke]
        self.audit.append("segment.release", {"run": run_id, "spoke": spoke})
        q = self.sidings.get(spoke)
        if not q:
            return None
        # best class first; FIFO within class - deque preserves arrival order
        best_i = min(range(len(q)), key=lambda i: q[i][1])
        nxt_run, nxt_cls = q[best_i]
        del q[best_i]
        self.holding[spoke] = (nxt_run, nxt_cls)
        self.audit.append("siding.resume",
                          {"run": nxt_run, "spoke": spoke, "class": nxt_cls})
        self.audit.append("segment.grant",
                          {"run": nxt_run, "spoke": spoke, "class": nxt_cls})
        return {"resumed": nxt_run, "spoke": spoke}
