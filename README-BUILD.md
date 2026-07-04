# dispatcher-agents runtime - Day 1 build (DRAFT)

The message core of the TelsonBase dispatcher, doctrine as executable behavior.
Every claim below is a passing test in tests/test_hub.py, run against the REAL
35-route listing-agent track (routes_fixture.json), not toy data:

- ack issued only after persist AND delivery (and never on failed delivery)
- (from -> intent -> to) tuple enforced at runtime - closed track
- envelope_id idempotency, dedupe-first (ack-loss retry semantics)
- hub-assigned monotonic sequence per client_context_id
- authority intents require verified signature; unsigned = reject + integrity flag
- unknown-intent traffic HOLDS live in clarification (restricted-speed), never drops
- confidence vocabulary enforced ("inferred" does not exist)
- pillar seams fire and are auditable: on_turn_start (before-turn),
  reflection artifacts per decision (open-mind shape: thought/response),
  ingest_spoke_trace (agent-open-mind hub-central monitoring)

Day 2 (dispatcher/analysis.py, dispatcher/kpi.py) - every claim a passing
test in tests/test_analysis_kpi.py:

- hub reflection artifacts analyzed by the PILLAR open-mind Comparator
  (imported, not vendored - repackaging drift is defect class P4);
  every result audit-logged as openmind.drift before it is returned
- the two improvements stranded in thought-cycle are ported upstream and
  PINNED by test: suppressed-uncertainty weight 0.3 (not 0.2), broadened
  "here's (exactly) what happened" confidence regex
- agent-open-mind gate on spoke traces: absent thought = TAINTED - flagged
  to integrity.violation + audited, never scored, never silently admitted;
  present traces scored thought-vs-result with the same Comparator
- KPIs computed from the audit log ONLY (ack integrity rate, routing
  latency, sequence-gap incidents, dedupe hits, queue health, drift
  roll-ups); KPIs the runtime cannot yet instrument (escalation transport
  time, playbook completion, heartbeat uptime, loop suspensions) are
  returned as computable:false with the missing event kinds named - 
  declared absent, never estimated

Install: pip install -e .  plus  pip install -e <clone of
QuietFireAI/open-mind> (carries the required comparator since 857f0dc).
Test: python -m pytest tests/
Day 3 (attestation.py, signatures.py, priority.py, loader.py, hub taint
gate) - every claim a passing test in tests/test_day3.py:

- absent spoke thought tainted AT INGESTION (structural), analysis layer
  is backstop only, no double-flag
- boot attestation: SHA-256 manifest of running package + routes, logged
  as boot.attestation; verification fail-closed - tamper, absence, and
  unattested new files each NAMED. Integrity, not authenticity: manifest
  signing rides the signature layer at deployment (limitation stated in
  module docstring)
- real signature verification: HMAC-SHA256 over canonical envelope fields
  (sequence excluded - hub stamps it post-sign), constant-time compare;
  signed authority intent acks, forged/tampered/absent all reject +
  integrity flag. Symmetric-key limitation stated; Ed25519 = upgrade path
- JIT priority + siding per core doctrine verbatim: classes 1-4 core,
  per-playbook assignment from the identity module (never hardcoded),
  higher class sides lower (held LIVE, auto-resume), class beats arrival
  order, unclassified playbooks refused (gate principle), every siding
  event audit-logged and KPI-counted
- identity side-load loader: validated against the REAL v0.16 listing
  identity (35 routes, 21 agents, 15 DRAFT priority classes surfaced as
  warnings); missing routes.json refuses to load; agent dir without
  SKILL.md is a violation, not a skip

Install: pip install -e .  plus  pip install -e <open-mind clone>.
Test: python -m pytest tests/
Day 4 (demo/, after_action.py, hub.escalate): P11 end-to-end on the real
identity, after-action per schema from log only, escalation transport
instrumented and KPI-computed. Day 5 (final): loop protection per
(context,intent) - threshold 20 PROVISIONAL AND ARBITRARY, retries never
counted; manners re-injection instrumentation (constant triggers enforced,
backstop named PROVISIONAL); Ed25519 authority tier (verifier cannot forge)
+ signable boot manifest registering MANNERS.md hash per its own spec.
`cryptography` is the package's ONLY dependency and only for Ed25519 - 
HMAC deployments stay zero-dep. Every claim: tests/test_day3.py.
Day 6 (spokes.py, runs.py, territory.py): REAL spokes - deterministic
work, self-submitted thought traces, reactive chaining (driver injects 2
signals, spokes do the rest; Spoke03 is the labeled dark-trace exhibit);
heartbeat watchdog (observer names gap incidents, no invented uptime %);
playbook started/step/completed events -> completion KPI with incomplete
runs NAMED; territories implemented per §100-110 - signed transfer,
fail-closed verify, persist-before-adopt, sequence continues gapless
across hubs, release only on receiver ack. Nothing in DISPATCHER_CORE
remains unimplemented except conduct-efficacy testing, which is empirical.

Final wiring (dispatcher/pillars.py): all six pillars imported from their
packages and seam-bound - before-turn (turn entry), open-mind (reflections),
agent-open-mind (taint gate, pillar function is the single source),
pre-response-selfcheck (exit gate, FAIL holds envelope), sleep-marks
(territory transfer carry/restore), splitvantage (second opinion on
flagged drift). Install all six: pip install -e <each pillar clone>.
Unarmed gates audit their own absence. 57 tests.

Baseline (demo/baseline_run.py + baseline/ null identity +
PILLAR_TESTING_MANUAL.md): all six pillars auto-hooked and fired on a bare
dispatcher in one deterministic command; fails loudly if any pillar is
silent. Exit gate auto-runs on every delivery when armed (FAIL holds the
envelope: persisted, never delivered, never acked); splitvantage auto-runs
on drift flags when a reviewer pair is configured; before-turn auto-runs
at turn entry, no model needed. Unarmed gates audit once at boot. The
baseline KPI row is the reference for every identity. 58 tests.
