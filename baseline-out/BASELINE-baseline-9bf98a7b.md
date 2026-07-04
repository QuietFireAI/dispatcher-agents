# After-Action - B01 run baseline-9bf98a7b

## run
- playbook: B01
- run_id: baseline-9bf98a7b
- client_context_id: diag-1
- started: 2026-07-04T13:46:10.850227+00:00
- ended: 2026-07-04T13:46:10.885201+00:00

## outcome
- completed

## steps
- step 1 [human->p1] config.update: executed=yes, proof=ack on 00af9f63-d883-4d16-a4a7-d5755e6bc6f6 (audit), latency=0.0013s
- step 2 [human->p1] diag.echo: executed=yes, proof=ack on 6a44b89b-a5f3-4d81-8c8b-665bba82336f (audit), latency=0.0013s
- step 3 [p1->p2] diag.relay: executed=yes, proof=ack on 52590c85-d14d-44da-87aa-af7bb7489640 (audit), latency=0.0013s
- step 4 [p2->p1] diag.report: executed=NO ACK ON LOG (envelope dce46f1b-0d9f-4c69-8535-04ed62747440) - unproven, not counted done

## gates
- hot-lead escalation gate: not triggered this run
- absent-thought taint gate: TRIGGERED x1 (evidence: agentopenmind.tainted)

## deviations
- agentopenmind.tainted: {'ts': 1783172770.863199, 'kind': 'agentopenmind.tainted', 'agent': 'p2', 'envelope_id': '52590c85-d14d-44da-87aa-af7bb7489640', 'tainted': True, 'reason': 'absent thought trace at ingestion - tainted, held for review'}

## escalations
- none

## errors
- none on log

## kpis (full-log DISPATCHER_CORE set)
- ack integrity rate: 1.0
- routing latency p50/max: 0.0013146400451660156 / 0.0013403892517089844 s
- escalation transport: {'computable': False, 'missing': ['escalation.raised/human.notified pairs']}
- sequence gaps: 0, dedupe hits: 0, rejects: 0
- queue health: {'dead_letter': 0, 'dead_letter_rate': 0.0, 'holds_clarification': 0, 'integrity_violations': 0, 'tainted_spoke_traces': 1, 'siding_holds': 0, 'siding_resumes': 0}
- drift: {'hub_reflections_analyzed': 4, 'hub_reflections_flagged': 1, 'spoke_traces_scored': 1, 'spoke_traces_flagged': 0}

## manners re-injections
- none on log this run (instrumented; zero fired)
