# TUNING_MANUAL - dispatcher-agents (core runtime)

Every configurable parameter and deliberate placeholder in the core.
Rule: any commit introducing a tunable updates this manual in the same
commit. Identity repos carry their own manuals for identity content;
this one covers the chassis every identity rides on.

---

## TOP OF LIST - Deliberate placeholders & ratified constants

| Item | Where | Status | Notes |
|---|---|---|---|
| `loop_threshold = 20` | `dispatcher/hub.py` | **RATIFIED as deliberate placeholder (owner, 2026-07-17)** | No empirical basis existed for a better number; revisit after production traffic. |
| MANNERS `N = 10` | `dispatcher/hub.py` | **RATIFIED as deliberate placeholder (owner, 2026-07-17)** | Same: deliberate, revisit with real data. |
| Twilio credentials | `dispatcher/notifier.py` (env vars) | **PLACEHOLDER - declared** | Real implementation; placeholder creds fail with a genuine 401, never a fake success. |
| SMS destination | `dispatcher/notifier.py` (+1-555-555-0100) | **PLACEHOLDER - declared** | NANP fiction block; replace at deployment. |
| `config/authority_signers.json` | this repo | **UNRATIFIED TEMPLATE - fails closed** | Loader refuses to arm on UNRATIFIED status, zero entries, missing mfa_required, or placeholder dates. Identity repos ship their own ratified versions. |
| `priority.json` absent-file behavior | `dispatcher/priority.py` | **KNOWN SOFT SPOT** | Warns-and-proceeds; should fail closed. Tracked on the roadmap since 2026-07. |

---

## Core doctrine constants (not tunable)

- **Enforcement order on authority intents:** crypto signature ->
  registry identity -> IdP session liveness -> hash-chained signer stamp.
- **Reconciliation tolerance:** $0.00, permeating every identity
  blueprint (owner decision 2026-07-18). The core provides the lanes;
  identities provide the books.
- **Fail-closed everywhere:** absence of the expected artifact means
  human review, never silent-admit.
- **Audit chain:** SHA-256 prev/entry linkage from GENESIS;
  `verify_chain()` names tamper, deletion, or reorder by line number.

Identity-specific knobs live in each identity repo's
`docs/TUNING_MANUAL.md` - the manuals shipped 2026-07-18 across
listing, medbilling, claim, reservation, freight, enrollment, and hr.
