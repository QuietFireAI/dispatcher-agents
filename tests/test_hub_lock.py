"""The hub's concurrency contract (J10, 2026-10-12), as stated in the
hub.py docstring: one Hub delivers one envelope at a time; separate Hub
instances run side by side; nested sends from several threads on one hub
finish (the lock is reentrant and there is only one, so no lock-order
deadlock is possible)."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog   # noqa: E402
from dispatcher.hub import Hub                            # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")
SLOW = 0.2


def _env(ctx, frm="01", to="02", intent="lead.captured"):
    return Envelope(from_agent=frm, to_agent=to, intent=intent, client_context_id=ctx,
                    payload={}, provenance={"source": "test", "captured_at": "now"})


def _slow_hub(path):
    hub = Hub(Routes(FIX), AuditLog(path))
    hub.register("02", lambda env: time.sleep(SLOW))
    return hub


def _run(targets):
    threads = [threading.Thread(target=t) for t in targets]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a send never finished"
    return time.monotonic() - t0


def test_one_hub_delivers_one_envelope_at_a_time(tmp_path):
    hub = _slow_hub(str(tmp_path / "a.jsonl"))
    results = []
    took = _run([lambda i=i: results.append(hub.send(_env(f"c{i}"))) for i in range(8)])
    assert [r["status"] for r in results] == ["ack"] * 8
    assert took >= 8 * SLOW * 0.95                       # serialized
    assert hub.audit.verify_chain()["ok"]


def test_separate_hubs_do_not_wait_on_each_other(tmp_path):
    hubs = [_slow_hub(str(tmp_path / f"h{i}.jsonl")) for i in range(8)]
    results = []
    took = _run([lambda h=h, i=i: results.append(h.send(_env(f"c{i}"))) for i, h in enumerate(hubs)])
    assert [r["status"] for r in results] == ["ack"] * 8
    assert took < 4 * SLOW                               # side by side, not 8 x SLOW


def test_nested_sends_from_many_threads_on_one_hub_all_finish(tmp_path):
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "n.jsonl")))
    delivered = []

    def at_02(env):                      # 02 hands every lead on to 03 (lead.nurture)
        hub.send(_env(env.client_context_id, frm="02", to="03", intent="lead.nurture"))
    hub.register("02", at_02)
    hub.register("03", lambda env: delivered.append(env.client_context_id))
    _run([lambda i=i: hub.send(_env(f"n{i}")) for i in range(16)])
    assert sorted(delivered) == sorted(f"n{i}" for i in range(16))
    assert hub.audit.verify_chain()["ok"]
