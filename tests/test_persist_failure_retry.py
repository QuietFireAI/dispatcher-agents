"""A failed audit write must not mark the envelope as seen (2026-10-12).
Before the fix the id was added to seen_ids and the sequence bumped before
the write, so after a failed write the retry came back "duplicate" and the
envelope was never delivered."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Envelope, Routes, AuditLog   # noqa: E402
from dispatcher.hub import Hub                            # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def test_a_retry_after_a_failed_audit_write_is_delivered_once(tmp_path):
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / "a.jsonl")))
    got = []
    hub.register("02", lambda env: got.append(env.envelope_id))
    real = hub.audit.append
    fail = {"left": 1}

    def flaky(kind, rec):
        if kind == "envelope.persisted" and fail["left"]:
            fail["left"] -= 1
            raise OSError("disk full")
        return real(kind, rec)
    hub.audit.append = flaky
    env = Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                   client_context_id="c1", payload={},
                   provenance={"source": "test", "captured_at": "now"})
    with pytest.raises(OSError, match="disk full"):
        hub.send(env)
    assert got == [] and env.sequence is None and env.envelope_id not in hub.seen_ids
    res = hub.send(env)
    assert res["status"] == "ack" and got == [env.envelope_id]
    assert env.sequence == 1                       # the failed try used no sequence
    assert hub.send(env)["status"] == "duplicate"  # and a real duplicate still dedupes
    assert hub.audit.verify_chain()["ok"]
