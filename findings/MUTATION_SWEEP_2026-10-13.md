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
| 5. Whole sweep again, packaged tool | ccbfb21, 288 tests | 808 | 804 | 4 - the 4 below, 0 timeouts |

Run 5 confirmed commit ccbfb21. A cold review by a separate agent then
found defects in the 1.1/1.2 code (below); fixing them added code, so the
sweep was run again on the fixed tree:

| Run | Code / tests | Mutants | Killed | Survived |
|---|---|---|---|---|
| 6. After the review fixes | working tree, 306 tests | 860 | 845 + 1 timeout (an infinite-loop mutant: caught) | 14: the 4 below and 10 in the new code |
| 7. Those 14 again | working tree, 314 tests | 14 | 11 | 3 - the 3 equivalents still in the code |
| 8. Whole sweep | 7ded6a6, 314 tests | 859 | 855 + 1 timeout (the same infinite-loop mutant: caught) | 3 - the 3 equivalents |

Run 8 confirmed commit 7ded6a6. The same reviewer then checked its
findings against the fixes and found more (below). The owner chose an
operating-system lock for the log. After those changes:

| Run | Code / tests | Mutants | Killed | Survived |
|---|---|---|---|---|
| 9. Whole sweep | lock added, 324 tests | 930 | 896 + 1 timeout | 33: the 3 equivalents and 30 in the new lock code |
| 10. Whole sweep | 008fb8a, 330 tests | 921 | 908 + 1 timeout | 12: 3 equivalent, 1 that only changes CPU use, 8 on Windows-only lines |
| 11. Whole sweep | after the third review, 338 tests | 942 | 924 + 1 timeout | 17: the 12 above and 5 in the new lock code |
| 12. Those 17 again | a0006ac, 339 tests (tests added, no core code changed since run 11) | 17 | 5 | 12: 3 equivalent, 1 CPU-only, 8 on Windows-only lines |
| 13. Whole sweep | after the recheck before merge, 341 tests | 931 | 917 + 1 timeout | 13: the 11 below and 2 in the new code |
| 14. Those 13 again | this commit, 344 tests (tests added, no core code changed since run 13) | 13 | 2 | 11: 2 equivalent, 1 CPU-only, 8 on Windows-only lines |

Run 9 found 30 survivors in the lock code: the reader not tested to wait for the lock, the file handle not tested to be closed, the owner kept on repair, mixed old and new pairing, a nesting counter that the outer reset made meaningless (now a flag), redundant return values and a redundant POSIX unlock (removed). Windows-only lines cannot run on Linux; see below. Run 10 confirmed 008fb8a. A third review found that reads needed write access to the log's folder (now: shared lock for reads, read-only fallback), that a busy lock was not handled in listing-assistants, and that two AuditLog instances in one process could wait on each other (now one lock state per file per process). Runs 11 and 12 confirmed a0006ac. A recheck before merging found that opening a log edited at line 2 and cut off at its end advised the repair, which then refused; the error now names the first break. The owner also asked for the four items left open at the gate to be fixed: the after-action report's run filter, the redundant tuple_legal clauses (removed, which removed one equivalent mutant), entry counts with blank lines, and the desk feed's style for a repair. Runs 13 and 14 are the confirmation for this commit: 931 mutants, 919 killed, 1 timeout, 11 survived.

## 2026-10-14: after Phases 2 and 3

The Phase 2 and 3 changes (signed intents declared by the identity, the
demo spokes moved out, closed file handles, the restart- and
transfer-aware sequence KPI, the notifier sending nothing with placeholder
credentials, `notifier.unarmed`) touched core.py, hub.py, kpi.py,
notifier.py, after_action.py, territory.py, loader.py and
signer_registry.py, so the whole sweep was run again, with 2 jobs on a
2-CPU machine so that timing-sensitive lock tests are not run under
overload (a timing failure would count a mutant as killed).

| Run | Code / tests | Mutants | Killed | Survived |
|---|---|---|---|---|
| 15. Whole sweep | 56c717d, 355 tests | 963 | 947 + 1 timeout (the same infinite-loop mutant) | 15: the 11 known and 4 in the new code |
| 16. Those 4 again | 359 tests (tests/test_phase2_coverage.py added, no core code changed) | 4 | 4 | 0 |

The 4 were: the KPI's check that a transfer record's high-water mark is a
whole number in a dict (`and` -> `or` let a malformed record raise), the
placeholder check (`or` -> `and` would send with one real and one
placeholder credential), the 320-character cap (no test reached it once
free text was dropped from the message), and the channel name on a
not-configured result. In run 16 each was killed by its own new test; a
first attempt was killed by the counts test instead (the suite had grown
and the docs had not), so the docs were updated and the run repeated.
Result for the merge: 963 mutants, 951 killed, 1 timeout, 11 survived (the
2 equivalent, 1 CPU-only and 8 Windows-only lines described below).

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

## The equivalent mutants

Each changes no behaviour. Evidence was run, not assumed. The third row's
code no longer exists: the repair now writes a new file and swaps it in
instead of truncating, so 3 remain.

| Mutant | Why nothing changes | Evidence |
|---|---|---|
| `attestation.py:26` read chunk 65536 -> 65537 | A streamed SHA-256 does not depend on the chunk size | 300,000 random bytes hashed with both chunk sizes and in one call: identical |
| (clause removed before merge) `core.py` `tuple_legal`: `to in SPECIAL and to in r` -> `to not in SPECIAL and to in r` | The whole clause implies `to in r`, which is already the first test on that line, so the clause can never change the answer | Exhaustive: all 32 receiver sets over {02, human, external, queue, any} x 6 destinations give the same result with the clause, with the mutant, and with no clause at all |
| (removed with the truncate) `core.py:277` `f.flush()` -> `pass` after `truncate` | Nothing is written through that handle; truncate goes straight to the file | Size on disk read right after `truncate(3)`, before any flush: 3 |
| `core.py` `_FileLock.__enter__` `time.sleep(0.02)` -> `pass` | Only the CPU use while waiting for another process's lock changes: the loop spins instead of sleeping, and still waits for the same deadline | The lock-wait tests pass with it; no result depends on how often the lock is retried |
| `hub.py:64` `super().__init__(reason)` -> `pass` in `Reject` | Python's exception constructor stores the arguments before `__init__` runs, so `args` and `str()` are the same | `Reject`-shaped class with and without the call: same `args` and `str` |

## Lines that a Linux sweep cannot run

8 surviving mutants are on the Windows branch of `_FileLock`
(`msvcrt.locking` and the `seek(0)` before it, lock and unlock). On Linux
that branch never runs, so no Linux test can notice a change to it. The
unmutated Windows branch is exercised by core CI on windows-latest, where
`test_two_processes_appending_at_once_keep_one_chain` (three processes, 450
entries), the lock-wait and the closed-handle tests run. No mutation run
was made on Windows.

## Redundant clauses found on the way (removed before merge)

In `Routes.tuple_legal`, two clauses could never change the answer:
`(frm == "human" and "human" in s)` is covered by `frm in s`, and
`(to in SPECIAL and to in r)` is covered by `to in r`. They read as if
`human` or the special destinations were treated differently; they were
not. Removed on the owner's request before the merge; the route tests and
the exhaustive check above show the answers are unchanged.

## One unexplained test failure

In run 2, mutant 283 (one of the equivalents above) was reported killed
with 1 failure after 143 passing tests. With it in place, 3 full runs, 6
runs with `-x` while the other job was busy, and 16 unmutated runs of both
copies side by side all passed. The runner then kept only the summary line,
not the failing test's name, so the test is not known. Counted as survived.
The runner now records failing test names. No failure has recurred.

## What the cold review found that the sweep could not

Mutation testing only asks whether the tests notice a change to code that
exists. The review found behaviour that was missing or wrong under every
test: a running desk kept writing to a log damaged under it; a later page
on the same queue covered an earlier failed one in the KPI and the report;
the report gave a transport time to a notifier that reported nothing; an
odd notifier answer raised out of the hub; a decision alert in
listing-assistants called the notifier around the hub; two writers could
fork the chain. All fixed with tests on 2026-10-13. One test it prompted me
to recheck was passing for the wrong reason (its "cut back" case emptied
the file); fixed.

## What the second review found

- Stopping a desk after the record broke saved a state snapshot the record
  never logged, so after the documented repair the desk refused to start.
  The swarm now checks the log can take the record before it writes the
  store (`AuditLog.ensure_intact`).
- The change check compared sizes only; it now compares size and
  modification time. An edit that keeps both is still not seen at append
  time; `verify_chain` finds it.
- With two processes writing one log cleanly, escalations from both on one
  queue could be paired with each other's page. Each outcome now names the
  entry hash of the escalation it answers.
- Core CI was red on Windows: a test of mine assumed LF line endings.
- The repair reset the log's permissions and owner; it keeps them now. A
  failed swap left a side file and gave a traceback; now nothing is left
  and the reason is printed.
- A test assertion that could not fail (the decision-alert test) is fixed.
- Owner decision: writers take an OS lock on `<log>.lock` (flock on
  POSIX, msvcrt.locking on Windows), so two processes no longer append at
  the same instant. Shown by three processes appending 450 entries at
  once; the test fails without the lock.

## What this does and does not show

It shows the core suite notices each of these kinds of change to the core,
except the 4 above. It does not prove the code correct, it does not cover
listing-assistants' own code (its spokes, desk, console), and operators
outside the list above (for example, swapping arithmetic) were not run.
