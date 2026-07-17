"""dispatcher.hub - Agent 00 as running code (Day 1).

Pipeline per envelope, in actual code order (fixed 2026-07-17 - this
docstring previously described a different order than the real code ran):
  idempotency dedupe -> loop protection (per context+intent threshold) ->
  schema validate -> signature check (authority intents) -> tuple legality
  (closed track) -> PERSIST to audit -> sequence assignment -> pre-response-
  selfcheck exit gate -> deliver to registered handler -> ACK (only now).
Idempotency runs first deliberately: a retry of an already-acked envelope
(same envelope_id) is the normal ack-loss case and must dedupe before any
other check can reject it on grounds that no longer apply to a duplicate.
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


def is_authority(intent: str) -> bool:
    """Authority classification for the enforcement gate. Identity modules
    across the stack name signed-human lanes with the `.authority` suffix
    (payment.authority, ratecon.authority, comp.authority, ...); the static
    set keeps config.update and the original listing intent. A hardcoded
    set alone was the defect: identity authority intents sailed past the
    signature gate unchecked."""
    return intent.endswith(".authority") or intent in AUTHORITY_INTENTS


class Reject(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Hub:
    def __init__(self, routes: Routes, audit: AuditLog,
                 signature_verifier: Optional[Callable[[Envelope], bool]] = None,
                 human_notifier: Optional[Callable[[str, dict], None]] = None,
                 loop_threshold: int = 20,
                 selfcheck_model=None,
                 crosspol_models: tuple | None = None,
                 signer_registry=None):
        # selfcheck_model: reviewer callable for the pre-response-selfcheck
        # exit gate on every outbound delivery. crosspol_models: pair of
        # callables (prompt -> {model,response,thinking}) for splitvantage
        # second opinions on drift-flagged reflections. Both are deployment
        # config; UNARMED IS AUDITED AT BOOT, once, never silently off.
        # loop_threshold: max envelopes per (client_context_id, intent) before
        # the loop suspends into clarification. RATIFIED (owner, 2026-07-17):
        # 20 stands as a deliberate placeholder - no empirical basis existed
        # at ratification (no real production traffic yet to measure a
        # normal repeat-count against), and none was invented to justify it.
        # Revisit once real after-action data exists; until then this is a
        # decided value, not an open question. Same discipline as MANNERS
        # N=10, ratified the same day for the same reason.
        self.routes = routes
        self.audit = audit
        self.verify_sig = signature_verifier or (lambda env: False)
        self.human_notifier = human_notifier
        self.loop_threshold = loop_threshold
        self.loop_counts: dict[tuple, int] = {}
        self.selfcheck_model = selfcheck_model
        self.crosspol_models = crosspol_models
        # signer_registry: SignerRegistry armed from the identity's ratified
        # config/authority_signers.json (login-based signer decision,
        # 2026-07-11). None = UNARMED IS AUDITED per authority envelope -
        # crypto signature still required, WHO-signed binding is off and
        # says so. It never silently defaults.
        self.signer_registry = signer_registry
        if selfcheck_model is None:
            self.audit.append("selfcheck.unarmed",
                              {"scope": "boot", "reason": "no reviewer model "
                               "configured - exit gate off, declared not silent"})
        if crosspol_models is None:
            self.audit.append("splitvantage.unarmed",
                              {"scope": "boot", "reason": "no reviewer pair "
                               "configured - second opinion off, declared not silent"})
        self.handlers: dict[str, Callable[[Envelope], None]] = {}
        self.seen_ids: set[str] = set()
        self.seq: dict[str, int] = {}
        self.queues: dict[str, list] = {
            "clarification.request": [], "integrity.violation": [],
            "escalation.legal_line": [], "escalation.hot_lead": [],
            "escalation.complaint": [], "escalation.system_error": [],
            "dead.letter": []}
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
        try:
            from .pillars import before_turn_check
        except ImportError:
            # Core-only install (no [pillars] extra). UNARMED IS AUDITED,
            # never a crash and never silent - the zero-dependency core
            # routes; the detection tier declares itself absent.
            self.audit.append("beforeturn.unarmed",
                              {"reason": "pillar packages not installed - "
                                         "turn-entry check off, declared "
                                         "not silent"})
        else:
            before_turn_check(self)
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
        try:
            from agent_open_mind import taint_check   # pillar = single source
        except ImportError:
            # Same unarmed discipline as before-turn: pillar absent is
            # DECLARED on the audit log, once per hub, never silent - and
            # never a per-message crash. Before this fix, a missing
            # agent_open_mind package raised ModuleNotFoundError inside
            # every handler delivery, dead-lettering 100% of swarm traffic
            # with a generic crash reason (fresh-clone verified 2026-07-17:
            # 98 test failures, all this one line). Fail-closed must mean
            # "held and named", not "everything dies undiagnosed".
            if not getattr(self, "_aom_unarmed_declared", False):
                self._aom_unarmed_declared = True
                self.audit.append("agentopenmind.unarmed",
                                  {"reason": "agent_open_mind package not "
                                             "installed - taint gate off, "
                                             "declared not silent"})
            self.spoke_traces.append(rec)
            self.audit.append("spoke.trace", rec)
            return
        gate = taint_check(rec)
        if gate["tainted"]:
            # structural, at the moment of ingestion, not deferred to
            # whenever analysis happens to run.
            rec["tainted"] = True
            flag = {"agent": agent_id, "envelope_id": envelope_id,
                    "tainted": True,
                    "reason": "absent thought trace at ingestion - tainted, held for review"}
            self.queue_and_notify("integrity.violation", flag)
            self.audit.append("agentopenmind.tainted", flag)
        self.spoke_traces.append(rec)
        self.audit.append("spoke.trace", rec)

    MANNERS_TRIGGERS = ("phase_gate", "post_compaction", "turn_backstop")

    def manners_reinjection(self, trigger: str, position: str = "") -> None:
        """MANNERS.md anti-fade mechanism, instrumented: phase_gate and
        post_compaction are CONSTANTS; turn_backstop is the N=10 backstop,
        RATIFIED (owner, 2026-07-17) as a deliberate placeholder pending
        real fade-rate data. Counts and positions feed after-action
        fade-tracking."""
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
        # queue is dispatcher-assigned routing, not caller-writable: splat the
        # untrusted record FIRST so the framing queue wins (a spoke must not be
        # able to redirect its escalation's audited queue).
        self.audit.append("escalation.raised", {**record, "queue": queue})
        if self.human_notifier is not None:
            self.human_notifier(queue, record)
            self.audit.append("human.notified", {**record, "queue": queue})
        return {"status": "escalated", "queue": queue}

    def register(self, agent_id: str, handler: Callable[[Envelope], None]):
        self.handlers[agent_id] = handler

    def queue_and_notify(self, queue_name: str, record: dict) -> None:
        """Real gap, found 2026-07-17: the 'append to a queue, then notify
        the human channel' pattern was hand-reimplemented at 5 separate
        call sites across this codebase (hub.py's own unknown-route hold,
        pillars.py's exit_gate FAIL, territory.py's transfer-refusal,
        analysis.py's tainted-trace backstop, plus the one that WAS fixed
        - hub.py's 'queue' virtual-destination delivery, the original bug
        this whole mechanism exists to prevent). Only that one call site
        ever actually called human_notifier - the other four recreated
        the exact same silent-queue bug in different code, because the
        append+notify pairing lived nowhere as a single, reusable unit.
        Every one of those callers is now updated to call this instead of
        reimplementing the pattern by hand."""
        self.queues.setdefault(queue_name, []).append(record)
        if self.human_notifier is not None:
            self.human_notifier(queue_name, record)
            self.audit.append("human.notified", {**record, "queue": queue_name})

    def resume_loop_suspension(self, client_context_id: str, intent: str) -> dict:
        """Real gap, found 2026-07-17: once a (context, intent) pair
        crossed loop_threshold, it was suspended PERMANENTLY - nothing
        anywhere ever reset loop_counts, not even a manual path for a
        human who reviewed the suspension and decided it wasn't actually
        a runaway (e.g. a genuinely high-volume, healthy long-running
        interaction that happened to cross an arbitrary placeholder
        threshold). Restricted-Speed Doctrine says resumption requires
        explicit human/dispatcher direction and never self-restores -
        that phrasing implies resumption IS possible, just never
        automatic. This is that explicit path: a human decision, not a
        silent auto-clear, and it's audited like every other override in
        this hub."""
        key = (client_context_id, intent)
        had_count = self.loop_counts.pop(key, None)
        self.audit.append("loop.resumed",
                          {"client_context_id": client_context_id,
                           "intent": intent, "prior_count": had_count})
        return {"status": "resumed", "client_context_id": client_context_id,
                "intent": intent, "prior_count": had_count}

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
            self.queue_and_notify("clarification.request", env.to_record())
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
        if is_authority(env.intent):
            if not self.verify_sig(env):
                self.queue_and_notify("integrity.violation", env.to_record())
                self.audit.append("integrity.violation",
                                  {"envelope_id": env.envelope_id,
                                   "reason": "authority intent without verified signature"})
                return self._reject(env, "unverified signature on authority intent")
            # 2b. signer identity - the crypto proves the envelope is sealed;
            # the registry proves the sealed stamp names an authorized human
            # login (IdP+MFA doctrine). Registry verdicts ride the hash chain.
            if self.signer_registry is not None:
                v = self.signer_registry.check(env)
                if not v.ok:
                    self.queue_and_notify("integrity.violation", env.to_record())
                    self.audit.append("integrity.violation",
                                      {"envelope_id": env.envelope_id,
                                       "reason": f"signer registry: {v.reason}"})
                    return self._reject(env, f"signer registry: {v.reason}")
                stamp = env.provenance["signer"]
                self.audit.append("signer.verified",
                                  {"envelope_id": env.envelope_id,
                                   "intent": env.intent,
                                   "signer_login": stamp["signer_login"],
                                   "idp_session_ref": stamp["idp_session_ref"]})
            else:
                self.audit.append("signer.unarmed",
                                  {"envelope_id": env.envelope_id,
                                   "reason": "no signer registry armed - "
                                             "WHO-signed binding off, declared "
                                             "not silent (crypto check only)"})
        # 3. closed track
        if not self.routes.tuple_legal(env.from_agent, env.intent, env.to_agent):
            known_intent = any(True for _ in self.routes.matches(env.intent))
            if known_intent:
                return self._reject(
                    env, f"tuple illegal: {env.from_agent} -> {env.intent} -> {env.to_agent}")
            # well-formed but unknown route: restricted-speed HOLD, never drop
            self.queue_and_notify("clarification.request", env.to_record())
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
        # 5. pre-response-selfcheck exit gate (auto when armed) - a FAIL
        # verdict holds the envelope live in clarification: persisted,
        # never delivered, never acked, flagged line on the log.
        if self.selfcheck_model is not None:
            from .pillars import exit_gate
            g = exit_gate(self, env, model=self.selfcheck_model)
            if not g["passed"]:
                return {"status": "held", "queue": "clarification.request",
                        "envelope_id": env.envelope_id,
                        "reason": f"selfcheck FAIL: {g['line']}"}
        # 6. deliver
        if env.to_agent == "queue":
            # "queue" is a virtual destination (clarification.request,
            # integrity.violation), not a real registered agent - real bug
            # found mid-session: every clarification.request sent via the
            # normal send() path was silently dead-lettering here, because
            # nothing registers a handler for the literal string "queue".
            # The dedicated tracking queue was never actually populated by
            # any agent's real traffic - tests only ever checked
            # envelope.persisted (which happens before this step), so it
            # went undetected. Fixed: route to the queue by intent name,
            # and notify immediately - unexpected/unrecognized values
            # deserve the same active-push urgency as an escalation, not a
            # passive list nobody is watching.
            self.queue_and_notify(env.intent, env.to_record())
            self.audit.append("hold.queued", {"envelope_id": env.envelope_id,
                                              "intent": env.intent})
            return {"status": "held", "queue": env.intent,
                    "envelope_id": env.envelope_id}
        handler = self.handlers.get(env.to_agent)
        if handler is None:
            self.queues["dead.letter"].append(env.to_record())
            self.audit.append("dead.letter", {"envelope_id": env.envelope_id,
                                              "reason": f"no handler for {env.to_agent}"})
            return {"status": "dead.letter", "envelope_id": env.envelope_id}
        try:
            handler(env)
        except Exception as e:  # raw reason, never softened
            # Distinct from "no handler yet" above - that's benign,
            # expected during incremental build-out. This is a REGISTERED
            # agent crashing on real input: a genuine defect. A crashed
            # handler cannot self-report its own failure, so the hub - the
            # only thing that ever sees this - has to raise the alarm
            # immediately. Routed through the real escalate() method (not
            # a manual queue append) so it actually reaches human_notifier
            # like every other escalation does, rather than sitting in a
            # passive list nobody is actively watching.
            self.queues["dead.letter"].append(env.to_record())
            self.audit.append("dead.letter", {"envelope_id": env.envelope_id,
                                              "reason": repr(e)})
            self.escalate("escalation.system_error",
                         {"envelope_id": env.envelope_id, "agent": env.to_agent,
                          "intent": env.intent,
                          "client_context_id": env.client_context_id,
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
