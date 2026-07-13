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
  before-turn at turn entry (hub re-reads its own recent reflections and
  answers the pillar's check questions, audited); open-mind's Comparator
  on every hub reasoning-vs-action artifact; agent-open-mind's taint gate
  at spoke-trace ingestion (the pillar function IS the gate - absent
  thought = tainted, never silently admitted); pre-response-selfcheck as
  the exit gate on outbound text (FAIL verdict holds the envelope in
  clarification); sleep-marks riding every territory transfer (the
  outgoing crew's reasoning state restores on the receiving hub);
  splitvantage as the second-opinion diff on drift-flagged decisions.
  Two gates (selfcheck, splitvantage) need reviewer models - deployment
  config; UNARMED IS AUDITED, never silent. Every binding has a test.
- **Structural tier** - TelsonBase: allow/gate/block per tool call, below
  the model, for deployments that need enforcement rather than detection.

Instruction < detection < structural. The org is the hierarchy.

## Runtime (in this repo)

`dispatcher/` - the hub: envelope schema with enforced confidence vocabulary,
tuple-legality routing, append-only fsynced audit log, HMAC authority
signatures, boot attestation (hash manifest, fail-closed verification), JIT
priority classes with live-hold siding, identity side-load loader, KPI
computation from the log only, after-action generation per schema. Tests are
doctrine as executable assertions, run against a real 35-route vertical
track - see README-BUILD.md for the claim-to-test map and EVIDENCE-runtime.md
for the evidence classes.

## Verticals (identity side-loads)

| Vertical | Repo | Status |
|---|---|---|
| Real-estate listing agent | [listing-agents](https://github.com/QuietFireAI/listing-agents) | v0.17 - 21 agents, 15 playbooks, 35 ratified route tuples, DRAFT priority classes; runtime-driven in the P11 end-to-end demo. First of several. |

## Quickstart

```
git clone https://github.com/QuietFireAI/dispatcher-agents
cd dispatcher-agents
pip install -e ".[pillars,crypto,dev]"              # core hub is zero-dep; pillars+Ed25519 are extras
git clone https://github.com/QuietFireAI/listing-agents ../listing-agents
IDENTITY_DIR=../listing-agents python3 -m pytest tests/       # 78 doctrine tests vs the real 35-route track
IDENTITY_DIR=../listing-agents python3 demo/run_p11_demo.py   # real spokes chain 11 envelopes from 2 signals

# Identity-gated tests target the repo bundled at IDENTITY_DIR (default: a fixture track); point IDENTITY_DIR at listing-agents for the real 35-route run.
# The core hub alone installs with plain `pip install -e .` (no extras).
```

## This Repo

This is the hub: the manifesto, the claims ledger, and the findings.

- [MANIFESTO.md](./MANIFESTO.md) - what the stack is and why
- [EVIDENCE.md](./EVIDENCE.md) - the claims ledger (MEASURED / OBSERVED / HYPOTHESIS / DESIGN CLAIM / POSITION)
- [findings/](./findings/) - documented observations, each with its evidence class and, where applicable, a preregistered validation protocol

## Status

v0.3, July 2026. Hash-chained audit log, login-based signer enforcement on all `.authority` lanes (78-test suite). All six pillars exist as code with reviewed documentation,
and the dispatcher runtime exists with a green doctrine-test suite against a
real vertical track. The stack's quantitative claims are classified in
EVIDENCE.md; highest-priority open items are the A/B validation of the
[context-load finding](./findings/FINDING_context_load_replaces_reconstruction.md)
and the tuple-enforcement A/B (HYPOTHESIS class, protocol in the runtime's
after-action schema).

## License

MIT - [QuietFireAI](https://github.com/QuietFireAI)
