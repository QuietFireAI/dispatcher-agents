"""dispatcher.core - the TelsonBase dispatcher message core (Day 1).

Doctrine encoded as executable behavior:
- An ack is a factual claim: issued only after persist (audit) AND delivery.
- The (from -> intent -> to) tuple is enforced at runtime - the track is closed.
- envelope_id is the idempotency key; duplicates process once.
- sequence is hub-assigned per client_context_id; hub is the single writer.
- Authority intents require a verified signature; sender fields are forgeable.
- Unroutable / ambiguous traffic HOLDS live (restricted-speed), never drops.
Spec sources: DISPATCHER_CORE.md, 00-dispatcher/SKILL.md, SWARM.md v0.16.
"""
from __future__ import annotations
import hashlib
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
    """Append-only, HASH-CHAINED JSONL. Persist happens BEFORE delivery; the
    log is the single source of truth for KPIs - no self-reported metrics
    exist.

    Chain doctrine (login-based signer decision, 2026-07-11): every entry
    carries prev_hash (the entry_hash of the line before it, or GENESIS) and
    entry_hash (sha256 of this entry's canonical content + prev_hash).
    Editing, deleting, or reordering any line breaks every hash after it -
    integrity and non-repudiation of the record come from this chain, not
    from trust in the file."""

    GENESIS = "GENESIS"

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._prev = self._recover_tip()

    def _recover_tip(self) -> str:
        """Resume the chain across process restarts: tip = last entry_hash."""
        if not os.path.exists(self.path):
            return self.GENESIS
        tip = self.GENESIS
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    tip = json.loads(line).get("entry_hash", tip)
        return tip

    RESERVED = ("ts", "kind", "prev_hash", "entry_hash")  # framing fields the log owns, never the caller

    @staticmethod
    def _entry_hash(body: dict, prev: str) -> str:
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((prev + canon).encode()).hexdigest()

    def append(self, kind: str, record: dict) -> None:
        # Framing wins: a caller-supplied record can never shadow the log's own
        # ts/kind/chain fields. Without this, any splatted untrusted dict (see
        # Hub.escalate) could forge or erase an event kind - the audit log is
        # the single source of truth, so its framing is not caller-writable.
        body = {**record, "ts": time.time(), "kind": kind}
        for k in ("prev_hash", "entry_hash"):
            body.pop(k, None)
        eh = self._entry_hash(body, self._prev)
        line = json.dumps({**body, "prev_hash": self._prev, "entry_hash": eh})
        with open(self.path, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._prev = eh

    def verify_chain(self) -> dict:
        """Walk the file and recompute every link. Returns
        {ok, entries, break_at} - break_at names the first bad line (1-based)."""
        prev, n = self.GENESIS, 0
        if not os.path.exists(self.path):
            return {"ok": True, "entries": 0, "break_at": None}
        with open(self.path) as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                e = json.loads(line)
                body = {k: v for k, v in e.items()
                        if k not in ("prev_hash", "entry_hash")}
                if e.get("prev_hash") != prev or \
                   e.get("entry_hash") != self._entry_hash(body, prev):
                    return {"ok": False, "entries": i, "break_at": i}
                prev = e["entry_hash"]; n = i
        return {"ok": True, "entries": n, "break_at": None}

    def read(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        return [json.loads(l) for l in open(self.path) if l.strip()]
