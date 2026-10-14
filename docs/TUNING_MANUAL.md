# TUNING_MANUAL - dispatcher-agents (core runtime)

Every configurable parameter, deliberate placeholder, stand-in and
partial mechanism in the core. Rule: any commit that adds a tunable, a
placeholder or a fallback mode updates this manual in the same commit.
Identity repos carry their own manuals for identity content; this one
covers the chassis every identity rides on.

Inventory taken from the code on 2026-10-14 (v1.2.0). Section A lists
the modes in which part of the core runs as a stand-in or not at all;
section B lists the fixed values put in so the core can be tested and
operated before real ones exist; section C lists the constants that are
doctrine rather than tuning.

---

## A. Stand-ins, unarmed modes and partial mechanisms

"Declares itself" names the line the core writes to the audit log, so a
reader of the log can tell the mode is on. Every mode below declares
itself except where the row says otherwise.

| Mode | Where | What happens in this mode | Declares itself | To replace |
|---|---|---|---|---|
| Exit gate unarmed (pre-response-selfcheck) | `Hub(selfcheck_model=None)`, `dispatcher/pillars.py` | Outbound text is not reviewed; the gate is not called | `selfcheck.unarmed` once at boot (`exit_gate` writes it per envelope only if a caller invokes it with no model) | Pass a reviewer callable as `selfcheck_model` |
| Second opinion unarmed (splitvantage) | `Hub(crosspol_models=None)` | Drift-flagged reflections get no second model | `splitvantage.unarmed` at boot | Pass a pair of model callables as `crosspol_models` |
| before-turn / agent-open-mind package absent | `dispatcher/hub.py` (core installed without the `[pillars]` extra) | That pillar's check does not run | `beforeturn.unarmed`, `agentopenmind.unarmed` | `pip install -e ".[pillars]"` |
| No human notifier | `Hub(human_notifier=None)` | Escalations and queue landings reach no person; with no outcome on the log the escalation KPI reports itself not computable | `notifier.unarmed` at boot (added 2026-10-14; before, this mode was silent) | Pass a notifier, e.g. one built on `dispatcher/notifier.py` |
| Twilio notifier not configured | `dispatcher/notifier.py` | While `TWILIO_ACCOUNT_SID` or `TWILIO_AUTH_TOKEN` is empty or starts with `PLACEHOLDER` or `YOUR_` (any case), or the destination or the sending number is in the fictional 555-0100..0199 block (with or without the +1), nothing is posted; the call returns `failed` with the reason and the hub records `human.notify_failed` | Yes: every notification shows as failed with that reason | Set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` (and the numbers in section B) |
| Signer registry not armed | `Hub(signer_registry=None)` | The Ed25519 signature is still required; which person signed is not checked | `signer.unarmed` on each authority envelope | `hub.arm_signer_registry(identity_root)` with the identity's ratified `config/authority_signers.json`. This repo has no `config/`, so the default path raises here; identity repos ship their own file |
| No signature verifier | `Hub(signature_verifier=None)` | Every authority envelope fails verification (fail closed), so each is logged as an `integrity.violation` even when correctly signed | `verifier.unarmed` at boot (added 2026-10-14) | Pass a verifier (`dispatcher/signatures.py`) |
| IdP session liveness | `dispatcher/signer_registry.py` | The registry checks that the stamp names an `idp_session_ref` and attests MFA; whether that session is genuine is not checked in the core | Stated in the module docstring, not on the log | The identity's IdP adapter validates the session |
| JEV decisions | `dispatcher/hub.py` boot | The hub never calls JEV; it reads only the `JEV_API_KEY` environment variable and treats it as unset when empty, `YOUR_...` or exactly `PLACEHOLDER`. An identity can take the key from elsewhere (listing-assistants also reads `config/integrations.json`), and then this boot line can say unconfigured while the identity's own line says a key is present | `jev.unarmed_fallback` (`FALLBACK_LOCAL_RULES`) at boot; `jev.key_present` when the variable is set | Set `JEV_API_KEY` (and `JEV_ENDPOINT_URL`); the identity's decision adapter decides whether to call |
| MANNERS N=10 turn backstop | `Hub.manners_reinjection` | **NOT IMPLEMENTED in the core** (owner decision D7, 2026-10-14). Nothing in the core counts turns or fires `turn_backstop`; the method records a re-injection a caller reports and re-injects nothing. listing-assistants counts turns and fires it (its `dispatcher/manners_reinjection.py`, 2026-10-14) | No (the value exists only as a ratified number) | A caller that counts turns and calls `manners_reinjection("turn_backstop")` |
| Heartbeat | `dispatcher/runs.py` | The Watchdog observes heartbeat events and names gaps; the hub emits none on its own | No | Call `heartbeat(hub)` on a schedule and run a `Watchdog(cadence_s)` |
| Boot attestation unsigned | `dispatcher/attestation.py` | With no signer the manifest proves what is running, not who approved it | `"signed": false` in the attestation event | Pass an authority signer to `attest_boot` |
| Held disclosure messages | `Hub._absolute_holds` (memory) | A bare core keeps held messages in memory only (owner decision D10). The hold entry records the envelope id, intent and audience; its hash is of the fixed signal text, the same for every hold. With a notifier set, the notification entry carries the queued record, payload included | No | The product saves hub state across restarts (listing-assistants does) |
| before-turn | `dispatcher/pillars.py` | Writes the pillar's questions and recent reflections to the log; nothing answers them | The questions are on the log | A model step that answers them |
| open-mind on the hub | `dispatcher/analysis.py` | Compares two strings the hub writes from templates, so the score measures that wording, not reasoning | No | Feed real reasoning text into the reflection artifacts |
| sleep-marks on transfer | `dispatcher/territory.py`, `pillars.restore_sleepmark` | The outgoing mark (the last 5 reflections) is logged on the receiving hub as `sleepmark.restored`, a historical name; nothing is restored from it | The mark is on the log | A restore step that reads it back into the receiving hub |
| Territories | `dispatcher/territory.py` | Experimental (owner decision D6): no nonce or expiry on a transfer; holds, dedupe ids and loop counts stay with the sender; the sender's queue items stay after release; the sender can still route a released context (the sequence KPI counts it as a gap) | Limits listed in the module docstring and pinned by `tests/test_territory_limits.py` | The fixes listed under D6, when a second office exists |
| Demo reviewers | `demo/baseline_run.py`, `demo/run_p11_demo.py` | A keyword stub stands in for the exit-gate reviewer ("guaranteed" fails, anything else passes) and two stub models give canned answers for the second opinion. Because a reviewer is set, the hub declares nothing unarmed, and the `selfcheck.verdict` and `splitvantage.review` entries, and the baseline KPIs built on them, come from the stubs | Named as stubs in the scripts and in PILLAR_TESTING_MANUAL.md; not on the log | Real reviewer models passed as `selfcheck_model` / `crosspol_models` |
| splitvantage when armed | `pillars.second_opinion` | The diff is the pillar's `analyze_diff` (word-boundary counts of hedges and length), not its `semantic_diff` | No | Call `semantic_diff` with a real model |
| Identity loader warnings | `dispatcher/loader.py` | A draft or pending `priority.json`, or a missing `MANNERS.md`, loads with an entry in `Identity.warnings` only; the loader writes nothing to an audit log | Only to the caller, in `Identity.warnings` | The caller logs the warnings |
| Demo spokes | `demo/listing_demo_spokes.py` | Listing-vertical spokes for the P11 demo and tests: deterministic in-process handlers, not the product's agents. Spoke03's nurture handler deliberately sends no thought trace, so the taint gate has something to catch | Their location (`demo/`) and module docstring | An identity's own agents (listing-assistants has 20) |

## B. Fixed values in place for testing and operation

| Value | Where | Status | Notes |
|---|---|---|---|
| `loop_threshold = 20` | `Hub(loop_threshold=20)` | **RATIFIED as deliberate placeholder (owner, 2026-07-17)** | No empirical basis existed; revisit with after-action data |
| MANNERS `N = 10` | comments in `dispatcher/hub.py` only | **RATIFIED (2026-07-17), NOT IMPLEMENTED (D7, 2026-10-14)** | See section A |
| `DEFAULT_TO_NUMBER = "+15555550100"` | `dispatcher/notifier.py` | **PLACEHOLDER** | NANP fiction range, never routes; set `TWILIO_TO_NUMBER` |
| `TWILIO_FROM_NUMBER` default `+15555550101` | `dispatcher/notifier.py` | **PLACEHOLDER** | Set the env var to the account's sending number |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` defaults `PLACEHOLDER_...` | `dispatcher/notifier.py` | **PLACEHOLDER - nothing is sent while either is in place** | Env vars |
| `TWILIO_WHATSAPP_FROM` default `whatsapp:+14155238886` | `dispatcher/notifier.py` | **PLACEHOLDER** | Twilio's shared WhatsApp sandbox number; replace with the account's approved sender |
| `TWILIO_WHATSAPP_TO` default `whatsapp:+15555550100` | `dispatcher/notifier.py` | **PLACEHOLDER** | Env var |
| SMS length cap 320 characters; HTTP timeout 5 s | `dispatcher/notifier.py` | Set in code | The text carries queue and context only, never free text |
| `JEV_ENDPOINT_URL` default `https://api.typesafe.ai/v1/decisions` | `dispatcher/hub.py` | Default, only reported at boot | The core never calls it |
| `SUPPORT_PATH = "support/engage-financial-capability"` | `dispatcher/absolute_signal.py`, quoted in `docs/FINANCIAL_CAPABILITY.md` | **PLACEHOLDER - names a path that exists in no repo** | It is part of the rebuttal text a person reads; replace with the real engagement route |
| `AuditLog.LOCK_WAIT_S = 30.0` | `dispatcher/core.py` | Set in code | How long a writer waits for another process's lock before `AuditLogBusy`; retries every 0.02 s |
| `drift_threshold = 0.4` | `dispatcher/analysis.py` | Default argument | Above it a reflection or spoke trace is flagged |
| `last_n = 3` | `pillars.before_turn_check` | Default argument | Reflections read at turn entry |
| Last 5 log entries | `Hub.on_turn_start` | Set in code | Events the hub reads back at turn entry |
| Last 5 reflections | `pillars.capture_sleepmark` | Set in code | What a sleep-mark carries |
| Watchdog gap = 2 x cadence | `dispatcher/runs.py` | Set in code | Cadence itself has no default; the caller sets it |
| Reason text cap 500 characters; error text cap 300 | `dispatcher/hub.py`, `dispatcher/notifier.py` (Twilio error body) | Set in code | Keeps log lines bounded |
| `AUDIENCE_DEFAULT = "counterparty"` | `dispatcher/core.py` | Doctrine default | An envelope with no audience is treated as gated |
| `NOTIFY_DELIVERED = {"sent", "delivered", "shown"}` | `dispatcher/hub.py` | Set in code | Only these notifier statuses count as a person notified |
| Test credentials `ACtest` / `test-token` | `tests/` (monkeypatched) | Tests only | Never read outside the test run |
| `tests/routes_fixture.json` | `tests/` | Tests only | The fixture track used when no identity is present |

## C. Doctrine constants (not tunable)

- **Enforcement order on authority intents:** crypto signature ->
  registry identity -> hash-chained signer stamp. IdP session liveness
  sits between the last two in the doctrine; the core checks that the
  stamp carries a session reference and leaves validating it to the
  identity's IdP adapter (section A).
- **Signed intents:** the core requires a signature for `config.update`
  and any `.authority` intent; each identity declares the rest in its
  `routes.json` `signed_intents` (owner decision D2, 2026-10-14).
- **Reconciliation tolerance:** $0.00, permeating every identity
  blueprint (owner decision 2026-07-18). The core provides the lanes;
  identities provide the books.
- **Fail-closed everywhere:** absence of the expected artifact means
  human review, never silent-admit. `priority.json` absent: the loader
  refuses with a named error (owner decision C1, 2026-07-18).
- **Audit chain:** SHA-256 prev/entry linkage from GENESIS;
  `verify_chain()` names tamper, deletion or reorder by entry number,
  and a damaged or cut-off line stops the log from opening, naming the
  first break.

- **Protocol names (fixed in code, swept 2026-10-14).** Changing one changes
  the protocol for every trade.

  | Name | Where | Value |
  |---|---|---|
  | `CONFIDENCE` | `dispatcher/core.py` | `source_verified`, `stated_by_party`, `unknown`: the only confidence labels an envelope may carry |
  | `SPECIAL` | `dispatcher/core.py` | `human`, `any`, `external`, `queue`: the names that are not agents. Nothing reads this set since the route check stopped using it; it documents the names |
  | `AUDIENCE_GATED` | `dispatcher/core.py` | `public`, `counterparty`: audiences held for a signed release |
  | `RESERVED` | `dispatcher/core.py` `AuditLog` | `ts`, `kind`, `prev_hash`, `entry_hash`. Nothing reads this name; `append()` itself overwrites those four keys in a caller's record, so the framing is the log's |
  | `AUTHORITY_INTENTS` | `dispatcher/hub.py` | `config.update`, plus any intent ending in `.authority` and each trade's declared `signed_intents` |
  | `MANNERS_TRIGGERS` | `dispatcher/hub.py` | `phase_gate`, `post_compaction`, `turn_backstop`: the only triggers `manners_reinjection()` records |
  | `NOTIFY_OUTCOMES`, `BOOT_KINDS` | `dispatcher/kpi.py` | The log kinds the KPIs read for a notification outcome and for a restart |
  | `_DEVIATION_KINDS` | `dispatcher/after_action.py` | The log kinds the after-action report counts as deviations |
  | `SIGNAL_TEXT`, `CAPABILITY_DOC` | `dispatcher/absolute_signal.py` | The disclosure-gate signal text (single source, sha256-checked) and the doc that quotes it |
  | Priority classes 1 to 4 | `dispatcher/loader.py` | The only classes `priority.json` may use |

Identity-specific knobs live in each identity repo's
`docs/TUNING_MANUAL.md`.
