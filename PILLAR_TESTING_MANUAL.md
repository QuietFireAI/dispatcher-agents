# PILLAR_TESTING_MANUAL - dispatcher-agents, null identity

The gate this manual enforces: each pillar correct on its own, AND the six
functioning together on a bare dispatcher - or no dispatcher agents. The
numbers captured here are the BASELINE KPIs every identity (listing-agents
first) is measured against.

Flow: install -> per-pillar validation -> integrated baseline run (KPIs) ->
identity load -> owner binding -> watch the back end produce data.

Every step names its expected evidence BEFORE you run it. Evidence is audit
events and test exits, never the runtime's word for itself.

## PHASE 0 - Install (fresh machine, ~5 min)

    for R in dispatcher-agents open-mind before-turn pre-response-selfcheck \
             agent-open-mind sleep-marks splitvantage; do
      git clone https://github.com/QuietFireAI/$R.git; done
    cd dispatcher-agents && pip install -e .
    for P in ../open-mind ../before-turn ../pre-response-selfcheck \
             ../agent-open-mind ../sleep-marks ../splitvantage; do
      pip install -e $P; done

EXPECTED: seven installs, no errors. (PEP 668 systems: add
--break-system-packages or use a venv.)

## PHASE 1 - Each pillar correct on its own

Run every pillar's OWN suite from its own clone: `python3 -m pytest -q`
EXPECTED, exactly: open-mind 9 | before-turn 6 | pre-response-selfcheck 11 |
agent-open-mind 27 | sleep-marks 20 | splitvantage 12. Any other number =
that pillar regressed; stop, report the raw failure. Do not proceed to
integration on a broken pillar - the set cannot be validated on a bad part.

Then the dispatcher's suite from its clone: `python3 -m pytest tests/ -q`
EXPECTED: 57 passed. (Identity-dependent tests skip unless IDENTITY_DIR
points at a listing-agents clone; with it set, still 57.)

## PHASE 2 - Six pillars together on the bare dispatcher

    python3 demo/baseline_run.py

One command, deterministic (stub reviewers), fails loudly if ANY pillar
does not fire. EXPECTED output, exactly this shape:

    pillar event counts (all six must be nonzero): {'beforeturn.check': 1,
      'openmind.drift': 4, 'agentopenmind.tainted': 1,
      'selfcheck.verdict': 4, 'sleepmark.captured': 1,
      'splitvantage.review': 1}
    selfcheck held envelope: held (selfcheck FAIL: guaranteed)

What each count proves:
- beforeturn.check 1: hub re-read its own reflections at turn entry and
  answered the pillar's 4 questions (auto-hook, not manual call).
- openmind.drift 4: every hub reasoning-vs-action artifact scored by the
  pillar Comparator, including one deliberately drifted reflection.
- agentopenmind.tainted 1: probe p2 returned a result with NO thought;
  the pillar gate flagged it at ingestion. The dark trace is planted -
  a baseline with zero taints would mean the gate was never tempted.
- selfcheck.verdict 4: exit gate reviewed every outbound delivery; the
  envelope whose payload said "guaranteed" is HELD in clarification -
  persisted, never delivered, never acked, flagged line on the log.
- sleepmark.captured 1: the crew change (signed territory transfer to a
  second hub) carried the reasoning state; audit-b.jsonl on the receiving
  hub shows sleepmark.restored.
- splitvantage.review 1: the drifted reflection triggered an automatic
  two-model second opinion (word-boundary counting, the fixed defect).

Also verify the unarmed contract: boot a hub with NO reviewer models
(python3 -c or any test) and confirm selfcheck.unarmed and
splitvantage.unarmed appear ONCE at boot. Off is declared, never silent.

## PHASE 3 - Capture the baseline KPIs

baseline-out/baseline-kpi.json is the artifact. Record these fields as the
reference row (machine, date, model config = stubs):
ack_integrity.rate (expect 1.0, integrity_incidents 0), routing_latency
p50/max, sequence_gap_incidents 0, loop_suspensions 0, dedupe_hits,
queue_health (dead_letter 0, tainted_spoke_traces 1), drift roll-ups,
escalation_transport_time, playbook_completion.rate 1.0,
heartbeat_watchdog. baseline-out/BASELINE-<run>.md is the after-action;
its steps section must show step 4 as NO ACK ON LOG - that is the
selfcheck hold proving unproven work is not counted done.

Rerun 3x. Latency varies; counts must not. A count that moves across
identical runs is nondeterminism - report it as a defect.

## PHASE 4 - Load the identity, bind the owner

    git clone https://github.com/QuietFireAI/listing-agents ../listing-agents
    IDENTITY_DIR=../listing-agents python3 -m pytest tests/ -q   # still 57
    IDENTITY_DIR=../listing-agents python3 demo/run_p11_demo.py

EXPECTED: identity loads 35 routes v0.17 / 21 agents / 15 classes with the
DRAFT-priority warning surfaced; demo chains 11 envelopes from 2 injected,
1 taint, 1 human notification.

Owner binding (configure spec to the human owner): generate the authority
keypair and keep the private key OFF the machine that runs spokes -

    python3 -c "from dispatcher.signatures import Ed25519Signer; s=Ed25519Signer(); \
      open('owner.key','wb').write(s.private_key_bytes()); \
      open('owner.pub','wb').write(s.public_key_bytes())"

Hub boots with the verifier only (public key). Every config.update and
listing.change.authorized must be signed with owner.key or it is rejected
and flagged - the baseline run already proved that path with a signed
config.update carrying {"spec_owner": "human"}. Sign the boot manifest with
the same key (attest_boot(..., signer=...)) so WHAT is running is bound to
WHO approved it.

## PHASE 5 - Watch the back end function

Live evidence while the system runs:

    tail -f <outdir>/audit.jsonl | grep -E \
      "beforeturn|openmind|tainted|selfcheck|sleepmark|splitvantage"

That stream IS the back of house. Reflection data arriving = openmind.drift
per decision. Conduct data = taint and selfcheck events. Crew-change data =
sleepmark pairs. After each run: generate the after-action (the demo and
baseline scripts both do) and diff its KPI block against the Phase 3
baseline row. Identity-on-baseline deltas are the first real measurement of
what a vertical costs and catches - the numbers your A/B needs.

## Known limits, stated

- Stub reviewers make Phases 2-3 deterministic; real-model baselines (an
  OpenRouter callable in selfcheck_model / crosspol_models) will move the
  selfcheck and splitvantage numbers and belong in a second baseline row,
  never mixed with the stub row.
- Selfcheck holds appear as selfcheck.verdict + clarification queue entries,
  not as hold.clarification KPI counts - two different counters, both on
  the log.
- Single-process runtime: these baselines measure the governance layer, not
  network transport or concurrency.
