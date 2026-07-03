# EVIDENCE - dispatcher-agents runtime build (this thread's claims)

Append-candidates for the org EVIDENCE.md ledger. Classes per ledger
convention: MEASURED / OBSERVED / HYPOTHESIS / DESIGN CLAIM / POSITION.
Ledger wins; README is a bug.

| Claim | Class | Evidence |
|---|---|---|
| Hub enforces (from → intent → to) tuples at runtime against the real 35-route listing track | MEASURED | tests/test_hub.py 12 assertions incl. Agent-15 defect-class regression; 51/51 suite green, re-run from fresh clone of the live repo |
| Ack only after persist AND delivery; failed delivery never acks | MEASURED | test_hub.py ordering + dead-letter tests against audit log |
| Absent spoke thought = tainted at ingestion, flagged, never scored, never silent | MEASURED | test_day3.py taint tests; fired live in P11 demo (report on file) |
| open-mind Comparator wired to hub reflection artifacts (pillar source, imported) | MEASURED | test_analysis_kpi.py; ported 0.3 weight + broadened regex pinned by test |
| KPIs computable only from audit log; uninstrumented KPIs declared, never estimated | MEASURED | test_analysis_kpi.py + demo report shows NOT INSTRUMENTED fields |
| JIT class contention: higher sides lower live, auto-resume, class beats arrival | MEASURED | test_day3.py siding tests; doctrine text → executable assertions |
| P11 speed-to-lead runs end-to-end on the runtime (stub spokes, real identity) | MEASURED | test_p11_demo_end_to_end; after-action report generated from log only |
| HMAC signature layer sufficient for single-principal authority | DESIGN CLAIM | symmetric-key limitation stated in module; Ed25519 = upgrade path, untested here |
| Tuple-level runtime enforcement prevents the defect class that survived 4 verification passes | HYPOTHESIS | regression test proves detection; prevention-in-production pending Jeff's A/B (metrics in AFTER_ACTION schema) |
| Boot attestation detects drift between reviewed and running code | MEASURED (detection) / DESIGN CLAIM (authenticity) | tamper/absence/unattested-file tests; manifest unsigned until deployment wiring |
| Stub-spoke demo latencies (~0.6ms routing) generalize to real spokes | POSITION | in-process no-op handlers; real-spoke numbers require Jeff's runtime, not claimed |

NOT claimed: production readiness; multi-party authority; loop protection
(unimplemented); heartbeat watchdog (unimplemented); territories (forward
spec); licensed legal review of any vertical content.
