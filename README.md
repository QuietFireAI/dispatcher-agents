# DispatcherAgents

**A governance stack for AI agents** -- tools that redirect an agent's attention to what it is actually doing: before it responds, after it responds, while it reasons, across sessions, and when it delegates.

Read the [MANIFESTO](./MANIFESTO.md) for the full architecture. Read [EVIDENCE.md](./EVIDENCE.md) for every claim in the stack, classified by evidence status -- the stack audits itself before anyone else gets the chance.

## The Stack

| Pillar | Repo | Role |
|---|---|---|
| 1 | [before-turn](https://github.com/QuietFireAI/before-turn) | Governs entry -- reads prior thinking before every response |
| 2 | [pre-response-selfcheck](https://github.com/QuietFireAI/pre-response-selfcheck) | Governs exit -- reads output as a cold reader before delivering |
| 3 | [agent-open-mind](https://github.com/QuietFireAI/agent-open-mind) | Reads what sub-agents thought, not what they said |
| 4 | [open-mind](https://github.com/QuietFireAI/open-mind) | Compares what the agent thought to what it said |
| 5 | [sleep-marks](https://github.com/QuietFireAI/sleep-marks) | Restores reasoning state across session breaks |
| 6 | [splitvantage](https://github.com/QuietFireAI/splitvantage) | Sends the same task to two models, surfaces divergence (CrossPol) |

Enterprise extension (referenced, not required): [TelsonBase](https://github.com/QuietFireAI/TelsonBase) -- permissions, audit, trust levels.

## This Repo

This is the hub: the manifesto, the claims ledger, and the findings.

- [MANIFESTO.md](./MANIFESTO.md) -- what the stack is and why
- [EVIDENCE.md](./EVIDENCE.md) -- the claims ledger (MEASURED / OBSERVED / HYPOTHESIS / DESIGN CLAIM / POSITION)
- [findings/](./findings/) -- documented observations, each with its evidence class and, where applicable, a preregistered validation protocol

## Status

v0.1, June 2026. All six pillars exist as code with reviewed documentation. The stack's quantitative claims are classified in EVIDENCE.md; the highest-priority open item is the A/B validation of the [context-load finding](./findings/FINDING_context_load_replaces_reconstruction.md).

## License

MIT -- QuietFireAI / [dispatcheragents.com](https://dispatcheragents.com)
