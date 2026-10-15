"""A failed audit write (2026-10-12, revised 2026-10-14).

2026-10-12: a failed write must not mark the envelope as seen. Before that
fix the id was added to seen_ids and the sequence bumped before the write,
so a retry came back "duplicate" and the envelope was never delivered.

2026-10-14 (Agent 00 tuples: "audit write fails, full stop everything; no
persist = no ack = no work; run on a log or do not run" and "audit log
unwritable or queue overflow, stop accepting envelopes entirely; fail
closed, loudly"): the failure now halts the hub. Every later envelope is
refused, the human is alerted out of band, and after a restart on a log it
can write the same envelope is delivered once - the 2026-10-12 guarantee
still holds across the restart.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog   # noqa: E402
from dispatcher.hub import Hub                            # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def _env(ctx="c1"):
    return Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                    client_context_id=ctx, payload={},
                    provenance={"source": "test", "captured_at": "now"})


def _flaky_hub(path, alarms):
    hub = Hub(Routes(FIX), AuditLog(path),
              human_notifier=lambda q, r: alarms.append((q, r)))
    real = hub.audit.append
    fail = {"left": 1}

    def flaky(kind, rec):
        if kind == "envelope.persisted" and fail["left"]:
            fail["left"] -= 1
            raise OSError("disk full")
        return real(kind, rec)
    hub.audit.append = flaky
    return hub


def test_a_failed_audit_write_halts_the_hub_loudly(tmp_path):
    alarms = []
    hub = _flaky_hub(str(tmp_path / "a.jsonl"), alarms)
    got = []
    hub.register("02", lambda env: got.append(env.envelope_id))
    env = _env()
    res = hub.send(env)
    assert res["status"] == "halted" and "disk full" in res["reason"]
    assert got == [] and env.sequence is None and env.envelope_id not in hub.seen_ids
    assert hub.halted and hub.halted["envelope_id"] == env.envelope_id
    assert [q for q, _ in alarms] == ["escalation.system_halt"]
    # the log works again, but the hub stays stopped: nothing after the failure runs
    other = _env("c2")
    assert hub.send(other)["status"] == "halted" and got == []
    assert hub.send(env)["status"] == "halted"
    assert len(alarms) == 1, "one alarm for one halt"


def test_after_a_restart_the_same_envelope_is_delivered_once(tmp_path):
    path = str(tmp_path / "a.jsonl")
    hub = _flaky_hub(path, [])
    env = _env()
    assert hub.send(env)["status"] == "halted"
    restarted = Hub(Routes(FIX), AuditLog(path))
    got = []
    restarted.register("02", lambda e: got.append(e.envelope_id))
    res = restarted.send(env)
    assert res["status"] == "ack" and got == [env.envelope_id]
    assert env.sequence == 1                       # the failed try used no sequence
    assert restarted.send(env)["status"] == "duplicate"
    assert restarted.audit.verify_chain()["ok"]
