---
name: dispatcher-agents
description: >
  Run a governed multi-agent swarm on a closed track. Use this skill whenever
  agents must message each other under enforcement: only pre-approved
  (from, intent, to) tuples move, every decision leaves an audit record, and
  all six DispatcherAgents pillars fire at their named seams (before-turn on
  turn entry, open-mind on hub reasoning, agent-open-mind taint gate on spoke
  traces, pre-response-selfcheck on outbound text, sleep-marks on territory
  transfers, splitvantage on drift-flagged decisions). Trigger on: dispatching
  work to sub-agents, routing messages between agents, loading a swarm
  identity (e.g. listing-agents), verifying an agent swarm, or any request
  for auditable agent orchestration. Ambiguous traffic is held at restricted
  speed, never dropped and never silently admitted.
---

# dispatcher-agents

## What this is

A governance-first message hub for agent swarms, doctrine as executable
behavior. Agents are trains; the dispatcher owns the track. No agent moves
without dispatcher authority, and the hub audit log is the single source of
truth for everything that happened.

The runtime is the open credibility layer of the DispatcherAgents stack.
An **identity** (a directory of routes, priority classes, agents, and
playbooks — e.g. QuietFireAI/listing-agents) turns the generic dispatcher
into a working vertical: "dispatcher-agents wearing an identity."

## Doctrine the runtime enforces (each rule is a passing test)

1. **Closed track** — only (from, intent, to) tuples present in routes.json
   are legal. Unknown tuples do not route.
2. **Restricted-speed holds** — unknown-intent traffic reduces to stop and
   HOLDS live in clarification with telemetry running; it resumes only on
   dispatcher direction. Never dropped.
3. **Ack after persist AND delivery** — never before, never on failure.
   envelope_id idempotency with dedupe-first retry semantics.
4. **Authority requires signature** — unsigned authority intents are
   rejected and flagged as integrity violations. Boot attestation is a hash
   manifest with fail-closed verification.
5. **Absent provenance = tainted** — a spoke result without a thinking trace
   is flagged for human review, never scored, never silently admitted.
6. **Unarmed is audited** — pillars that need a model callable
   (pre-response-selfcheck, splitvantage) audit their unarmed state instead
   of pretending to run.

## The six pillar seams

| Seam | Pillar | Audit kind |
|---|---|---|
| Hub.on_turn_start | before-turn | beforeturn.check |
| Hub reflection artifacts | open-mind Comparator | openmind.drift |
| Hub.ingest_spoke_trace | agent-open-mind taint gate | agentopenmind.tainted |
| Outbound envelope text | pre-response-selfcheck | selfcheck.verdict / selfcheck.unarmed |
| Territory transfers | sleep-marks | sleepmark.captured / sleepmark.restored |
| Drift-flagged decisions | splitvantage | splitvantage.review / splitvantage.unarmed |

All six are imported from their pillar repos — never vendored. Repackaging
drift is a named defect class.

## Using this skill (any agent runtime: Claude, OpenClaw, Hermes)

### Install

```bash
git clone https://github.com/QuietFireAI/dispatcher-agents.git
cd dispatcher-agents
pip install -e ".[pillars,crypto,dev]"   # pillars pulled from source, never vendored
```

### Verify before claiming anything works

```bash
python -m pytest tests/                  # 78 doctrine tests (identity-gated tests activate with IDENTITY_DIR)
python demo/baseline_run.py              # must print all six pillar events nonzero
python tools/kpi_gate.py baseline-out/audit-<id>.jsonl \
    --taints-expected 1 --selfcheck-bait-expected 1
```

A run where any pillar event count is zero, or the KPI gate fails, is a
failed run. Report it as failed. Do not summarize a partial run as working.

### Load an identity and route traffic

```python
from dispatcher.loader import load_identity
from dispatcher.hub import Hub
from dispatcher.core import Routes, AuditLog

ident = load_identity("/path/to/identity-repo")   # refuses to load without routes.json
hub = Hub(Routes(ident.routes_path), AuditLog("audit.jsonl"), None)
hub.register("02", handler)
hub.send(envelope)   # ack only if the tuple is legal, persisted, and delivered
```

### Arm the authority tier (signed money lanes)

Every intent ending `.authority` (plus `config.update`) is authority-classed:
it requires a verified cryptographic signature, and - when the signer
registry is armed - a signer stamp naming an authorized human login
(IdP + MFA doctrine, owner decision 2026-07-11). The audit log is
hash-chained; `AuditLog.verify_chain()` proves nothing was edited, deleted,
or reordered.

```python
from dispatcher.signatures import HmacSigner
from dispatcher.signer_registry import SignerRegistry

signer = HmacSigner(key)                          # or the Ed25519 tier
registry = SignerRegistry.load(ident.root)        # fail-closed: refuses UNRATIFIED templates
hub = Hub(Routes(ident.routes_path), AuditLog("audit.jsonl"),
          signature_verifier=signer.verifier(), signer_registry=registry)
# authority envelopes must carry provenance["signer"] =
#   {"signer_login": ..., "idp_session_ref": ..., "mfa": True}
```

Unarmed states are audited (`signer.unarmed`), never silent. What the
runtime proves: the stamp names a login the ratified registry authorizes for
that intent, sealed under the envelope signature, on a tamper-evident log.
What it does not prove: that the IdP session is genuine - that is the IdP
seam adapter's job (each identity's INTEGRATIONS.md).

`IDENTITY_DIR` selects the identity for the demo and the identity-gated
tests: `IDENTITY_DIR=/path/to/listing-agents python -m pytest tests/`.

## Rules for the agent running this skill

- Never invent a route. If the tuple is not in routes.json, the correct
  behavior is a clarification hold, not a workaround.
- Never mark a held envelope as delivered. Holds are live and audited;
  resuming is a dispatcher decision.
- Never treat an unarmed pillar as a passed check. Unarmed is a state, and
  it is in the audit log.
- Every "done / verified / works" claim must be backed by the test suite,
  the demo pillar counts, or the KPI gate output — quote the actual result.
- The audit log is append-only ground truth. Answer questions about what
  happened from the audit log, not from memory of what was intended.

## Repo map

- `dispatcher/core.py` — Routes (closed track) + hash-chained append-only AuditLog
- `dispatcher/hub.py` — the hub: ack discipline, holds, sequence, pillar seams
- `dispatcher/pillars.py` — the six pillar bindings (imports, never copies)
- `dispatcher/analysis.py` — open-mind comparator + taint scoring over the log
- `dispatcher/signatures.py`, `dispatcher/attestation.py` — authority crypto + boot trust
- `dispatcher/signer_registry.py` — login-based signer enforcement (fail-closed arming)
- `dispatcher/priority.py` — JIT run-priority (siding scheduler, pacing over braking)
- `dispatcher/territory.py` — territory transfers carrying sleep-marks
- `dispatcher/loader.py` — identity loading, fail-closed on missing track
- `tools/kpi_gate.py` — invariant gate over the audit log
- `demo/baseline_run.py`, `demo/run_p11_demo.py` — wire demos; all six pillars must fire

Part of the DispatcherAgents stack by QuietFireAI. Pillars: before-turn,
open-mind, agent-open-mind, pre-response-selfcheck, sleep-marks, splitvantage.
