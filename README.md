# DispatcherAgents

**A governance stack for AI agents** - tools that redirect an agent's attention to what it is actually doing: before it responds, after it responds, while it reasons, across sessions, and when it delegates.

Read the [MANIFESTO](./MANIFESTO.md) for the full architecture. Read [EVIDENCE.md](./EVIDENCE.md) for every claim in the stack, classified by evidence status - the stack audits itself before anyone else gets the chance. Agent runtimes (Claude, OpenClaw, Hermes): [SKILL.md](./SKILL.md) is the drop-in skill for running this dispatcher as a governed tool.

## The Stack

| Pillar | Repo | Role |
|---|---|---|
| 1 | [before-turn](https://github.com/QuietFireAI/before-turn) | Governs entry - reads prior thinking before every response |
| 2 | [pre-response-selfcheck](https://github.com/QuietFireAI/pre-response-selfcheck) | Governs exit - reads output as a cold reader before delivering |
| 3 | [agent-open-mind](https://github.com/QuietFireAI/agent-open-mind) | Reads what sub-agents thought, not what they said |
| 4 | [open-mind](https://github.com/QuietFireAI/open-mind) | Compares what the agent thought to what it said |
| 5 | [sleep-marks](https://github.com/QuietFireAI/sleep-marks) | Restores reasoning state across session breaks |
| 6 | [splitvantage](https://github.com/QuietFireAI/splitvantage) | Sends the same task to two models, surfaces divergence (CrossPol) |

Enterprise extension (referenced, not required): [TelsonBase](https://github.com/QuietFireAI/TelsonBase) - permissions, audit, trust levels.

## How the tiers fit (the whole org, one sentence each)

- **Instruction tier** - the dispatcher runtime in this repo: a closed-track
  message hub where every (from → intent → to) tuple is enforced at send time,
  acks exist only after persist-and-delivery, and the audit log is the sole
  source of every KPI and after-action report.
- **Detection tier** - the six pillars, each imported from its own
  package and bound to a named runtime seam (dispatcher/pillars.py):
  before-turn at turn entry (the hub writes the pillar's check questions to
  the log with its own recent reflections; nothing answers them yet);
  open-mind's Comparator on every hub reasoning-vs-action artifact (both
  sides are template text the hub writes itself, so the score measures that
  wording, not reasoning); agent-open-mind's taint gate
  at spoke-trace ingestion (the pillar function IS the gate - absent
  thought = tainted, never silently admitted); pre-response-selfcheck as
  the exit gate on outbound text (FAIL verdict holds the envelope in
  clarification); sleep-marks riding every territory transfer (the
  outgoing hub's mark is captured and logged on the receiving hub; nothing
  in the receiving hub is restored from it, and territories are
  experimental);
  splitvantage as the second-opinion diff on drift-flagged decisions.
  Two gates (selfcheck, splitvantage) need reviewer models - deployment
  config; UNARMED IS AUDITED, never silent. Every binding has a test.
- **Structural tier** - TelsonBase: allow/gate/block per tool call, below
  the model, for deployments that need enforcement rather than detection.

Instruction < detection < structural. The org is the hierarchy.

## Runtime (in this repo)

`dispatcher/` - the hub: envelope schema with enforced confidence vocabulary,
tuple-legality routing, append-only hash-chained audit log with external anchoring, Ed25519
authority signatures (covering the signer stamp), boot attestation (hash manifest, fail-closed verification), JIT
priority classes with live-hold siding, identity side-load loader, KPI
computation from the log only, after-action generation per schema. Tests are
doctrine as executable assertions, run against a real 52-route vertical
track - EVIDENCE.md classifies every claim; README-BUILD.md is the dated
build diary.

Three things to know about the runtime as it stands:
- **Heartbeat** is partial: the Watchdog observes heartbeat events and names
  gaps, but the hub emits none on its own; a deployment has to call
  `heartbeat()` on a schedule.
- **Held outbound messages** (the absolute-signal gate) live in the hub's
  memory in a bare core. The hold entry records the envelope id, intent and
  audience (its hash is of the fixed signal text, the same for every hold).
  When a notifier is set, the notification entry carries the queued record,
  payload included; without one, the message is in memory only. A product
  that saves hub state across restarts (listing-assistants does) keeps them.
- **Territories** (hub-to-hub handoff) are experimental; their known limits
  are listed in `dispatcher/territory.py` and pinned by tests.

### What runs in listing-assistants today

This core is a library; listing-assistants, the one product built on it,
uses part of it. Checked against listing-assistants' code on 2026-10-14:

| Part | In listing-assistants |
|---|---|
| Hub: closed track, signatures, audit log and its lock, disclosure gate, notifications, decline | Runs |
| before-turn (turn entry) | Runs when the swarm is built (`on_turn_start`) |
| agent-open-mind taint gate | Runs on every agent's thought trace |
| pre-response-selfcheck exit gate | Off: no reviewer model is configured; declared once at boot (`selfcheck.unarmed`) |
| Signer registry (who signed) | Not armed; the crypto signature is checked, the login binding is off and declared |
| splitvantage second opinion | Off: no reviewer pair configured; declared at boot |
| open-mind analysis, KPIs, the core after-action report | Not called (listing writes its own end-of-day report) |
| Boot attestation, identity loader, priority and siding, heartbeat, playbook-run KPIs | Not called |
| Territories, sleep-marks | Not used |

Boot attestation is the cheapest of these to wire in next (owner decision D5,
2026-10-14).

## Verticals (identity side-loads)

| Vertical | Repo | Status |
|---|---|---|
| Real-estate listing agent | [listing-assistants](https://github.com/QuietFireAI/listing-assistants) (formerly listing-agents) | v0.19 - 21 agents, 24 playbooks, 52 route tuples, ratified priority classes (2026-07-10); runtime-driven end-to-end: P11 demo in this repo, and P01 (all three phases from one signed trigger) plus a full lead-to-close lifecycle as continuous-flow tests in the identity repo. First of several. |

## Quickstart

```
git clone https://github.com/QuietFireAI/dispatcher-agents
cd dispatcher-agents
pip install -e ".[pillars,crypto,dev]"              # routing core is zero-dep; signing needs crypto (Ed25519)
git clone https://github.com/QuietFireAI/listing-assistants ../listing-assistants
IDENTITY_DIR=../listing-assistants python3 -m pytest tests/   # 366 tests vs the real 52-route track
IDENTITY_DIR=../listing-assistants python3 demo/run_p11_demo.py   # real spokes chain 11 envelopes from 2 signals

# Identity-gated tests target the repo bundled at IDENTITY_DIR (default: a fixture track); point IDENTITY_DIR at listing-assistants for the real 52-route run.
# The core hub alone installs with plain `pip install -e .` (no extras).
```

## This Repo

This is the hub: the manifesto, the claims ledger, and the findings.

- [MANIFESTO.md](./MANIFESTO.md) - what the stack is and why
- [EVIDENCE.md](./EVIDENCE.md) - the claims ledger (MEASURED / OBSERVED / HYPOTHESIS / DESIGN CLAIM / POSITION)
- [findings/](./findings/) - documented observations, each with its evidence class and, where applicable, a preregistered validation protocol

## Status

v1.2.0, October 2026. Hash-chained audit log with external anchor, an
operating-system lock so processes take turns with it, and a named error
(never a crash) for a damaged or cut-off line, with a repair a person runs;
notifications recorded as delivered only when delivered; login-based signer
enforcement with effective-date checks; signed intents declared by each
identity. CI on every push runs the suite with the listing identity on Linux
(Python 3.10, 3.12, 3.13) and Windows, and a full mutation sweep of the core
is documented in [findings/](./findings/MUTATION_SWEEP_2026-10-13.md). All
six pillars exist as code with reviewed documentation. The stack's quantitative claims are classified in
EVIDENCE.md; highest-priority open items are the A/B validation of the
[context-load finding](./findings/FINDING_context_load_replaces_reconstruction.md)
and the tuple-enforcement A/B (HYPOTHESIS class, protocol in the runtime's
after-action schema).

## License

Apache License 2.0 - see LICENSE. Free for broad adoption and contribution. Domain identity packages that run on this chassis are licensed separately (QuietFire Identity License).
