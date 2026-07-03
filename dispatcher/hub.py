"""dispatcher.hub - Agent 00 as running code (Day 1).

Pipeline per envelope, in doctrine order:
  schema validate -> signature check (authority intents) -> tuple legality
  (closed track) -> idempotency dedupe -> PERSIST to audit -> sequence
  assignment -> deliver to registered handler -> ACK (only now).
Failures never vanish: rejects carry raw reasons; unroutable-but-well-formed
traffic holds live in the clarification queue (restricted-speed: held is
acked-received at transport level, never dropped, never advanced).

Pillar hook points (Day 2 wiring targets, real seams today):
  on_turn_start -> before-turn (hub reads its own prior state first)
  on_decision -> emits a reflection artifact per routing decision, in the
                     format open-mind's Comparator consumes (thought vs action)
  ingest_spoke_trace -> agent-open-mind: hub-central monitoring of what spokes
                     THOUGHT, not just what they sent
"""
from __future__ import annotations
from typing import Callable, Optional
from .core import Envelope, Routes, AuditLog

AUTHORITY_INTENTS = {"listing.change.authorized", "config.update"}


class Reject(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Hub:
    def __init__(self, routes: Routes, audit: AuditLog,
                 signature_verifier: Optional[Callable[[Envelope], bool]] = None,
                 human_notifier: Optional[Callable[[str, dict], None]] = None,
                 loop_threshold: int = 20):
        # loop_threshold: max envelopes per (client_context_id, intent) before
        # the loop suspends into clarification. 20 is PROVISIONAL AND
        # ARBITRARY (no spec number, no empirical basis) - after-action data
        # sets the real value, same discipline as MANNERS N=10.
        self.routes = routes
        self.audit = audit
        self.verify_sig = signature_verifier or (lambda env: False)
        self.human_notifier = human_notifier
        self.loop_threshold = loop_threshold
        self.loop_counts: dict[tuple, int] = {}
        self.handlers: dict[str, Callable[[Envelope], None]] = {}
        self.seen_ids: set[str] = set()
        self.seq: dict[str, int] = {}
        self.queues: dict[str, list] = {
            "clarification.request": [], "integrity.violation": [],
            "escalation.legal_line": [], "escalation.hot_lead": [],
            "escalation.complaint": [], "dead.letter": []}
        # pillar seams
        self.reflection_artifacts: list[dict] = []   # open-mind input
        self.spoke_traces: list[dict] = []           # agent-open-mind input

    # ---------------------------------------------------------- pillar seams
    def on_turn_start(self) -> dict:
        """before-turn seam: the hub reads its own prior state before acting.
        Returns the state summary it read - callers can assert it happened."""
        state = {"last_events": self.audit.read()[-5:],
                 "open_holds": {q: len(v) for q, v in self.queues.items() if v}}
        self.audit.append("turn.start", {"read_prior_state": True,
                                         "open_holds": state["open_holds"]})
        return state

    def _reflect(self, envelope_id: str, thought: str, action: str) -> None:
        """open-mind seam: one artifact per decision - what the hub reasoned
        vs what it did. Comparator-consumable shape: {thought, response}."""
        art = {"envelope_id": envelope_id, "thought": thought, "response": action}
        self.reflection_artifacts.append(art)
        self.audit.append("hub.reflection", art)

    def ingest_spoke_trace(self, agent_id: str, envelope_id: str,
                           thought: str, result: str) -> None:
        """agent-open-mind seam: spokes submit reasoning traces alongside
        results; hub centrality makes this the monitoring point."""
        rec = {"agent": agent_id, "envelope_id": envelope_id,
               "thought": thought, "result": result}
        if not (thought or "").strip():
            # Absent Thoughts = Tainted Result - structural, at the moment of
            # ingestion, not deferred to whenever analysis happens to run.
            rec["tainted"] = True
            flag = {"agent": agent_id, "envelope_id": envelope_id,
                    "tainted": True,
                    "reason": "absent thought trace at ingestion - tainted, held for review"}
            self.queues["integrity.violation"].append(flag)
            self.audit.append("agentopenmind.tainted", flag)
        self.spoke_traces.append(rec)
        self.audit.append("spoke.trace", rec)

    MANNERS_TRIGGERS = ("phase_gate", "post_compaction", "turn_backstop")

    def manners_reinjection(self, trigger: str, position: str = "") -> None:
        """MANNERS.md anti-fade mechanism, instrumented: phase_gate and
        post_compaction are CONSTANTS; turn_backstop is the N=10 PROVISIONAL
        backstop. Counts and positions feed after-action fade-tracking."""
        if trigger not in self.MANNERS_TRIGGERS:
            raise ValueError(f"unknown manners trigger {trigger!r}; "
                             f"constants are {self.MANNERS_TRIGGERS}")
        self.audit.append("manners.reinjection",
                          {"trigger": trigger, "position": position})

    # ------------------------------------------------------------- transport
    def escalate(self, queue: str, record: dict) -> dict:
        """Spokes raise escalations into hub queues (they are not routes - 
        no spoke-to-spoke tuple exists for them). Audit first, then notify
        the human channel if one is registered; notification is itself an
        audited event (human.notified) so escalation transport time is a
        computed KPI, never self-reported."""
        if queue not in self.queues or not queue.startswith("escalation."):
            raise KeyError(f"unknown escalation queue {queue!r}")
        self.queues[queue].append(record)
        self.audit.append("escalation.raised", {"queue": queue, **record})
        if self.human_notifier is not None:
            self.human_notifier(queue, record)
            self.audit.append("human.notified", {"queue": queue, **record})
        return {"status": "escalated", "queue": queue}

    def register(self, agent_id: str, handler: Callable[[Envelope], None]):
        self.handlers[agent_id] = handler

    def send(self, env: Envelope) -> dict:
        # 0. idempotency FIRST - a retry of an acked envelope (same
        # envelope_id, hub-stamped sequence riding along) is the normal
        # ack-loss case and must dedupe before any other check can reject it
        if env.envelope_id in self.seen_ids:
            self.audit.append("dedupe.hit", {"envelope_id": env.envelope_id})
            return {"status": "duplicate", "processed": False,
                    "envelope_id": env.envelope_id}
        # 0.5 loop protection - per (context, intent) threshold, suspend +
        # clarification (core protocol mechanics). Counts real attempts only:
        # rides after dedupe so ack-loss retries never inflate the count.
        key = (env.client_context_id, env.intent)
        self.loop_counts[key] = self.loop_counts.get(key, 0) + 1
        if self.loop_counts[key] > self.loop_threshold:
            self.queues["clarification.request"].append(env.to_record())
            self.audit.append("loop.suspended",
                              {"client_context_id": env.client_context_id,
                               "intent": env.intent,
                               "count": self.loop_counts[key],
                               "threshold": self.loop_threshold,
                               "envelope_id": env.envelope_id})
            return {"status": "suspended", "queue": "clarification.request",
                    "reason": f"loop threshold {self.loop_threshold} exceeded "
                              f"for {key}", "envelope_id": env.envelope_id}
        # 1. schema
        errs = env.validate_schema()
        if errs:
            return self._reject(env, f"schema: {errs}")
        # 2. authority signature - the signature, not the sender field, is trust
        if env.intent in AUTHORITY_INTENTS:
            if not self.verify_sig(env):
                self.queues["integrity.violation"].append(env.to_record())
                self.audit.append("integrity.violation",
                                  {"envelope_id": env.envelope_id,
                                   "reason": "authority intent without verified signature"})
                return self._reject(env, "unverified signature on authority intent")
        # 3. closed track
        if not self.routes.tuple_legal(env.from_agent, env.intent, env.to_agent):
            known_intent = any(True for _ in self.routes.matches(env.intent))
            if known_intent:
                return self._reject(
                    env, f"tuple illegal: {env.from_agent} -> {env.intent} -> {env.to_agent}")
            # well-formed but unknown route: restricted-speed HOLD, never drop
            self.queues["clarification.request"].append(env.to_record())
            self.audit.append("hold.clarification", env.to_record())
            self._reflect(env.envelope_id,
                          f"intent {env.intent!r} not on any track; doctrine says hold live",
                          "held in clarification.request")
            return {"status": "held", "queue": "clarification.request",
                    "envelope_id": env.envelope_id}
        # 4. PERSIST - before delivery, always
        self.seen_ids.add(env.envelope_id)
        env.sequence = self.seq[env.client_context_id] = \
            self.seq.get(env.client_context_id, 0) + 1
        self.audit.append("envelope.persisted", env.to_record())
        # 6. deliver
        handler = self.handlers.get(env.to_agent)
        if handler is None:
            self.queues["dead.letter"].append(env.to_record())
            self.audit.append("dead.letter", {"envelope_id": env.envelope_id,
                                              "reason": f"no handler for {env.to_agent}"})
            return {"status": "dead.letter", "envelope_id": env.envelope_id}
        try:
            handler(env)
        except Exception as e:  # raw reason, never softened
            self.queues["dead.letter"].append(env.to_record())
            self.audit.append("dead.letter", {"envelope_id": env.envelope_id,
                                              "reason": repr(e)})
            return {"status": "dead.letter", "envelope_id": env.envelope_id,
                    "reason": repr(e)}
        # 7. ACK - only now is it a fact
        self.audit.append("ack", {"envelope_id": env.envelope_id})
        self._reflect(env.envelope_id,
                      f"tuple legal, persisted seq={env.sequence}, delivered to {env.to_agent}",
                      "ack issued")
        return {"status": "ack", "envelope_id": env.envelope_id,
                "sequence": env.sequence}

    def _reject(self, env: Envelope, reason: str) -> dict:
        self.audit.append("reject", {"envelope_id": env.envelope_id,
                                     "reason": reason})
        return {"status": "reject", "reason": reason,
                "envelope_id": env.envelope_id}
