# Mutation sweep of the core, 2026-10-13

Repair plan step 1.4. Every one of the 16 core files that listing-assistants
vendors was mutated, one change at a time, and the whole core suite was run
against each change. A change the suite does not notice (a *surviving
mutant*) marks behaviour no test holds.

## Method

- **Files:** the 16 in `tools/sync_core.py`'s list on the listing side
  (`__init__.py` yields no mutants).
- **Operators:** comparison flipped; `and`/`or` swapped; `not` dropped;
  `True`/`False` swapped and integers raised by one (docstrings skipped); an
  `if`/`while`/ternary/`assert` test negated; `return <value>` made
  `return None`; `raise` made `pass`; a bare call statement made `pass`.
  Each is an exact replacement of a span of source found through the AST;
  every mutant compiles.
- **Run:** the full core suite (`pytest -x`) with `IDENTITY_DIR` at a
  listing-assistants clone, so the identity tests run too. Killed = the
  suite fails; survived = it passes; timeout after 180 s. Each job first
  runs the unmutated suite and refuses to start if it fails.
- **Isolation:** mutants are written into a copy of the repository, never
  the working tree.
- **Reproduce:** `IDENTITY_DIR=../listing-assistants python tools/mutation_sweep.py --out sweep.jsonl --jobs 2`

## Results

| Run | Code / tests | Mutants | Killed | Survived |
|---|---|---|---|---|
| 1. First sweep | fb7a04f, 198 tests | 807 | 593 (73.5%) | 214 |
| 2. Survivors again | 87aa5ff (watchdog test rewritten), 198 tests | 214 | 7 | 207 |
| 3. Survivors after the new tests | working tree, 286 tests | 207 | 200 | 7 (one of the 207 was a duplicate, see 218 below) |
| 4. The last 7 | working tree, 288 tests | 7 | 3 | 4 |
| 5. Whole sweep again, packaged tool | this commit, 288 tests | 808 | 804 | 4 - the 4 below, 0 timeouts |

Run 5 is the confirmation: the full sweep from scratch with
`tools/mutation_sweep.py` on the code and tests of this commit.

## What the survivors showed

The first sweep's 214 survivors were not spread evenly; they clustered where
the core had few or no direct tests:

- **after_action.py** (32 of 52 survived): the report had one test. Its
  steps, gates, deviations, escalations, errors and manners sections were
  unchecked. Now checked line by line from a fixed log.
- **hub.py** (40): an unsigned or registry-refused disclosure answer
  leaving no integrity violation, dead letters missing from their queue,
  the "audience label not yet confirmed" marker on a hold, the unarmed
  declarations, escalate accepting a non-escalation queue - all unheld.
- **core.py** (48): wildcard routes (`lead.*` must not cover
  `leadership.x`), sender and receiver limits on a route, the anchor
  checks, the hash format, the disk flushes.
- **kpi.py** (17), **notifier.py** (13), **territory.py** (12),
  **loader.py** (12), **runs.py** (10), **priority.py** (7) and the rest:
  the records each module leaves on the audit log, and its refusals.

New tests: `tests/test_sweep_core_hub_2026_10_13.py`,
`tests/test_sweep_reports_2026_10_13.py`,
`tests/test_sweep_modules_2026_10_13.py` (90 tests).

## Code changed because of the sweep

1. **`loader.py`**: the routes check was an `assert`. Python run with `-O`
   strips asserts, and an empty or non-list track would then load. It is
   an explicit check now; its three new mutants are killed.
2. **`core.py` `repair_torn_tail`**: a variable `n` was assigned and never
   read. Removed. Mutant 218 changed only that variable; it no longer
   exists.

## The 4 equivalent mutants

Each changes no behaviour. Evidence was run, not assumed.

| Mutant | Why nothing changes | Evidence |
|---|---|---|
| `attestation.py:26` read chunk 65536 -> 65537 | A streamed SHA-256 does not depend on the chunk size | 300,000 random bytes hashed with both chunk sizes and in one call: identical |
| `core.py:125` `to in SPECIAL and to in r` -> `to not in SPECIAL and to in r` | The whole clause implies `to in r`, which is already the first test on that line, so the clause can never change the answer | Exhaustive: all 32 receiver sets over {02, human, external, queue, any} x 6 destinations give the same result with the clause, with the mutant, and with no clause at all |
| `core.py:277` `f.flush()` -> `pass` after `truncate` | Nothing is written through that handle; truncate goes straight to the file | Size on disk read right after `truncate(3)`, before any flush: 3 |
| `hub.py:64` `super().__init__(reason)` -> `pass` in `Reject` | Python's exception constructor stores the arguments before `__init__` runs, so `args` and `str()` are the same | `Reject`-shaped class with and without the call: same `args` and `str` |

## Redundant clauses found on the way (no behaviour change, reported)

In `Routes.tuple_legal`, two clauses can never change the answer:
`(frm == "human" and "human" in s)` is covered by `frm in s`, and
`(to in SPECIAL and to in r)` is covered by `to in r`. They were left in
place: they are harmless, and changing route code was not needed to finish
this step. They read as if `human` or the special destinations were treated
differently; they are not.

## One unexplained test failure

In run 2, mutant 283 (one of the equivalents above) was reported killed
with 1 failure after 143 passing tests. With it in place, 3 full runs, 6
runs with `-x` while the other job was busy, and 16 unmutated runs of both
copies side by side all passed. The runner then kept only the summary line,
not the failing test's name, so the test is not known. Counted as survived.
The runner now records failing test names. No failure has recurred.

## What this does and does not show

It shows the core suite notices each of these kinds of change to the core,
except the 4 above. It does not prove the code correct, it does not cover
listing-assistants' own code (its spokes, desk, console), and operators
outside the list above (for example, swapping arithmetic) were not run.
