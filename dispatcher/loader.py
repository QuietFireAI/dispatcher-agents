"""Identity side-load loader - Day 3.

Loads a vertical identity package (the side-load) into runtime shape:
  routes.json          REQUIRED - the closed track. Absent = refuse to load.
  priority.json        REQUIRED (fail-closed 2026-07-18) - playbook priority classes. Absent = the
                       SidingScheduler cannot run playbook traffic (it fails
                       closed on unclassified playbooks); absence is NAMED in
                       the returned identity, never silently defaulted.
  NN-*/SKILL.md        agent dirs - each numbered dir must carry a SKILL.md;
                       a numbered dir without one is a violation, not a skip.

Gate principle throughout: absence of an expected artifact is reported by
name. Presence alone is never treated as validity - routes.json must parse
and carry routes; priority classes must be ints in 1..4.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


@dataclass
class Identity:
    root: str
    routes_path: str
    vertical: str
    n_routes: int
    agents: dict[str, str]                 # agent id -> SKILL.md path
    priority_classes: dict[str, int] | None
    priority_status: str
    manners_path: str | None = None
    warnings: list[str] = field(default_factory=list)


def load_identity(root: str) -> Identity:
    violations: list[str] = []
    warnings: list[str] = []

    routes_path = _find(root, "routes.json")
    if not routes_path:
        raise FileNotFoundError(
            f"identity at {root!r}: routes.json absent - no closed track, "
            f"refusing to load (gate principle)")
    try:
        routes_doc = json.load(open(routes_path))
        routes = routes_doc["routes"]
        assert isinstance(routes, list) and routes
    except Exception as e:
        raise ValueError(f"routes.json present but invalid: {e!r}")

    agents: dict[str, str] = {}
    for d in sorted(os.listdir(root)):
        if re.match(r"^\d{2}-", d) and os.path.isdir(os.path.join(root, d)):
            skill = os.path.join(root, d, "SKILL.md")
            if os.path.exists(skill):
                agents[d.split("-")[0]] = skill
            else:
                violations.append(f"agent dir {d}: SKILL.md absent")
    if violations:
        raise ValueError("identity invalid: " + "; ".join(violations))

    prio_path = _find(root, "priority.json")
    priority: dict[str, int] | None = None
    prio_status = "absent - playbook traffic cannot be scheduled until supplied"
    if prio_path:
        doc = json.load(open(prio_path))
        classes = doc.get("classes", {})
        bad = {k: v for k, v in classes.items()
               if not isinstance(v, int) or not 1 <= v <= 4}
        if bad:
            raise ValueError(f"priority.json invalid classes (must be int 1..4): {bad}")
        priority = classes
        prio_status = doc.get("_status", "present, status unstated")
        if "draft" in prio_status.lower() or "pending" in prio_status.lower():
            warnings.append(f"priority table is {prio_status}")
    else:
        # FAIL CLOSED (owner decision C1, 2026-07-18): an identity without a
        # priority table cannot schedule playbook traffic - warning-and-
        # proceeding admitted unclassified traffic by omission, the exact
        # silent-admit class the fail-closed invariant forbids.
        raise ValueError(
            "priority.json absent - identity refuses to load (fail-closed, "
            "owner-ratified 2026-07-18). Supply a ratified priority table.")

    manners = os.path.join(root, "MANNERS.md")
    if not os.path.exists(manners):
        manners = None
        warnings.append("MANNERS.md absent - conduct constants cannot be "
                        "hash-registered at boot attestation")
    return Identity(
        root=root,
        routes_path=routes_path,
        vertical=routes_doc.get("vertical", "unstated"),
        n_routes=len(routes),
        agents=agents,
        priority_classes=priority,
        priority_status=prio_status,
        manners_path=manners,
        warnings=warnings,
    )


def _find(root: str, name: str) -> str | None:
    for cand in (os.path.join(root, name), os.path.join(root, "identity", name)):
        if os.path.exists(cand):
            return cand
    return None
