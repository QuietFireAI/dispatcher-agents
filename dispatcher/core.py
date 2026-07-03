"""dispatcher.core — the TelsonBase dispatcher message core (Day 1).

Doctrine encoded as executable behavior:
- An ack is a factual claim: issued only after persist (audit) AND delivery.
- The (from -> intent -> to) tuple is enforced at runtime — the track is closed.
- envelope_id is the idempotency key; duplicates process once.
- sequence is hub-assigned per client_context_id; hub is the single writer.
- Authority intents require a verified signature; sender fields are forgeable.
- Unroutable / ambiguous traffic HOLDS live (restricted-speed), never drops.
Spec sources: DISPATCHER_CORE.md, 00-dispatcher/SKILL.md, SWARM.md v0.16.
"""
from __future__ import annotations
import json, os, time, uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

CONFIDENCE = {"source_verified", "stated_by_party", "unknown"}
SPECIAL = {"human", "external", "queue", "any"}


@dataclass
class Envelope:
    from_agent: str
    to_agent: str
    intent: str
    client_context_id: str
    payload: dict
    provenance: dict
    confidence: str = "unknown"
    escalation_flag: bool = False
    in_reply_to: Optional[str] = None
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: Optional[int] = None  # hub-assigned; senders submit None
    signature: Optional[str] = None

    def validate_schema(self) -> list[str]:
        errs = []
        for f in ("from_agent", "to_agent", "intent", "client_context_id"):
            if not getattr(self, f):
                errs.append(f"missing field: {f}")
        if self.confidence not in CONFIDENCE:
            errs.append(f"illegal confidence: {self.confidence!r}")
        if self.sequence is not None:
            errs.append("sequence is hub-assigned; senders must submit None")
        if not isinstance(self.provenance, dict) or "source" not in self.provenance:
            errs.append("provenance.source required")
        return errs

    def to_record(self) -> dict:
        return {k: getattr(self, k) for k in (
            "envelope_id", "from_agent", "to_agent", "intent", "in_reply_to",
            "sequence", "client_context_id", "payload", "provenance",
            "confidence", "escalation_flag")}


class Routes:
    """The closed track. Loaded from an identity side-load (routes.json)."""

    def __init__(self, path: str):
        data = json.load(open(path))
        self.version = data.get("version", "?")
        self.entries = [(r["intent"], set(r["senders"]), set(r["receivers"]))
                        for r in data["routes"]]

    def matches(self, intent: str):
        for i, s, r in self.entries:
            if i == intent or (i.endswith(".*") and intent.startswith(i[:-1])):
                yield s, r

    def tuple_legal(self, frm: str, intent: str, to: str) -> bool:
        for s, r in self.matches(intent):
            frm_ok = frm in s or "any" in s or (frm == "human" and "human" in s)
            to_ok = (to in r or "any" in r
                     or (to in SPECIAL and to in r))
            if frm_ok and to_ok:
                return True
        return False


class AuditLog:
    """Append-only JSONL. Persist happens BEFORE delivery; the log is the
    single source of truth for KPIs — no self-reported metrics exist."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, kind: str, record: dict) -> None:
        line = json.dumps({"ts": time.time(), "kind": kind, **record})
        with open(self.path, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        return [json.loads(l) for l in open(self.path) if l.strip()]
