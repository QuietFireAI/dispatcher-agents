"""Real spokes for the listing vertical - no no-op lambdas.

Each spoke does deterministic real work on the payload, submits its OWN
thought trace (agent-open-mind's ingest is part of the spoke contract, not
the script's), and reacts by sending follow-on envelopes over the hub - 
the swarm chains itself; the driver only injects the initial signal.

Exception, deliberate and labeled: Spoke03's nurture handler reports a
result WITHOUT a thought trace. It is the negative-path exhibit for the
taint gate - a spoke whose reasoning went dark. Keep it dark; the runtime
is supposed to catch it, and the test asserts that it does.
"""
from __future__ import annotations

from .core import Envelope


def _env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}",
                                "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke14CRM:
    """Record system: answers record.request with record.response;
    keeps interaction log entries."""

    def __init__(self, hub):
        self.hub = hub
        self.records: dict[str, dict] = {}
        self.interactions: list[dict] = []
        hub.register("14", self.handle)

    def handle(self, env):
        if env.intent == "record.request":
            key = env.payload.get("dedupe_key", env.client_context_id)
            known = key in self.records
            self.records.setdefault(key, {"ctx": env.client_context_id})
            verdict = ("HIT - prior record exists, caller must merge not "
                       "duplicate" if known else "MISS - new record created")
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought=f"lookup key={key!r}: {verdict}",
                result=f"known={known}")
            self.hub.send(_env("14", env.from_agent, "record.response",
                               env.client_context_id, {"known": known}))
        elif env.intent == "interaction.log":
            self.interactions.append(env.to_record())
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought="append-only log entry; no interpretation applied",
                result="logged")


class Spoke01LeadCapture:
    """Validates consent, requests dedupe from 14, then hands to 02.
    Holds per-context state between record.request and record.response."""

    def __init__(self, hub):
        self.hub = hub
        self.pending: dict[str, dict] = {}
        hub.register("01", self.handle)

    def handle(self, env):
        if env.intent == "lead.signal":
            consent = env.payload.get("consent", "unknown")
            if consent != "recorded":
                self.hub.ingest_spoke_trace(
                    "01", env.envelope_id,
                    thought=f"consent={consent!r} - TCPA gate: cannot proceed "
                            f"to outreach tiering without recorded consent; "
                            f"holding, not guessing",
                    result="held: consent not recorded")
                return
            self.pending[env.client_context_id] = dict(env.payload)
            self.hub.ingest_spoke_trace(
                "01", env.envelope_id,
                thought="consent recorded; dedupe against CRM before tiering "
                        " -  duplicate leads double-count pipeline",
                result="record.request issued")
            self.hub.send(_env("01", "14", "record.request",
                               env.client_context_id,
                               {"dedupe_key": env.payload.get("email", "?")}))
        elif env.intent == "record.response":
            lead = self.pending.pop(env.client_context_id, {})
            lead["duplicate"] = env.payload["known"]
            self.hub.ingest_spoke_trace(
                "01", env.envelope_id,
                thought=f"dedupe answer known={env.payload['known']}; "
                        f"forwarding complete lead object to qualification",
                result="lead.captured issued")
            self.hub.send(_env("01", "02", "lead.captured",
                               env.client_context_id, lead))


class Spoke02Qualification:
    """Deterministic rubric v3: budget>=500k -> +40; timeline<=30d -> +40;
    channel=call -> +20. Score >=70 = HOT (escalate), >=40 WARM (nurture),
    else COLD (log only)."""

    def __init__(self, hub):
        self.hub = hub
        hub.register("02", self.handle)

    def handle(self, env):
        if env.intent != "lead.captured":
            return
        p = env.payload
        score = ((40 if p.get("budget", 0) >= 500_000 else 0)
                 + (40 if p.get("timeline_days", 999) <= 30 else 0)
                 + (20 if p.get("channel") == "call" else 0))
        tier = "HOT" if score >= 70 else "WARM" if score >= 40 else "COLD"
        self.hub.ingest_spoke_trace(
            "02", env.envelope_id,
            thought=f"rubric v3: budget={p.get('budget')}, "
                    f"timeline={p.get('timeline_days')}d, "
                    f"channel={p.get('channel')} -> score={score}; "
                    f"duplicate={p.get('duplicate')} - if duplicate, tier "
                    f"stands but pipeline count must not increment",
            result=f"tier={tier} score={score}")
        if tier == "HOT":
            self.hub.escalate("escalation.hot_lead",
                              {"client_context_id": env.client_context_id,
                               "score": score, "sla_s": 300})
        elif tier == "WARM":
            self.hub.send(_env("02", "03", "lead.nurture",
                               env.client_context_id,
                               {"tier": tier, "consent": "on-file"}))
        self.hub.send(_env("02", "14", "interaction.log",
                           env.client_context_id,
                           {"tier": tier, "score": score}))


class Spoke03Nurture:
    """DELIBERATE NEGATIVE-PATH EXHIBIT: enrolls the lead but reports its
    result with NO thought trace. The taint gate must catch this."""

    def __init__(self, hub):
        self.hub = hub
        self.enrolled: list[str] = []
        hub.register("03", self.handle)

    def handle(self, env):
        if env.intent == "lead.nurture":
            self.enrolled.append(env.client_context_id)
            self.hub.ingest_spoke_trace(          # thought deliberately absent
                "03", env.envelope_id, thought="",
                result="enrolled in 14-day drip")
