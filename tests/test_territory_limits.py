"""Territories are EXPERIMENTAL (owner decision D6, 2026-10-14). These tests
pin today's known limits, listed in dispatcher/territory.py. Each one is
meant to fail when the limit is fixed, so the code, the docstring and the
docs change together - not to say the behaviour is right."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import AuditLog, Envelope, Routes  # noqa: E402
from dispatcher.hub import Hub  # noqa: E402
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier  # noqa: E402
from dispatcher.territory import build_transfer, confirm_release, receive_transfer  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def hub(tmp_path, name):
    h = Hub(Routes(FIX), AuditLog(str(tmp_path / f"{name}.jsonl")))
    h.register("02", lambda e: None)
    return h


def env(ctx="ctx-1"):
    return Envelope(from_agent="01", to_agent="02", intent="lead.captured", client_context_id=ctx,
                    payload={"n": 1}, provenance={"source": "t"})


def moved(tmp_path):
    s = Ed25519Signer()
    a, b = hub(tmp_path, "a"), hub(tmp_path, "b")
    for _ in range(3):
        a.send(env())
    rec = build_transfer(a, ["ctx-1"], s)
    return a, b, rec, Ed25519Verifier(s.public_key_bytes())


def test_known_limit_an_old_transfer_received_again_sets_the_sequence_back(tmp_path):
    a, b, rec, v = moved(tmp_path)
    assert receive_transfer(b, rec, v)["status"] == "ack"
    b.send(env())
    b.send(env())
    assert b.seq["ctx-1"] == 5
    assert receive_transfer(b, rec, v)["status"] == "ack"     # replayed: no nonce, no expiry
    assert b.seq["ctx-1"] == 3


def test_known_limit_holds_dedupe_and_loop_counts_stay_with_the_sender(tmp_path):
    a, b, rec, v = moved(tmp_path)
    receive_transfer(b, rec, v)
    assert a.seen_ids and not b.seen_ids
    assert a.loop_counts and not b.loop_counts
    assert "_absolute_holds" not in rec and "seen_ids" not in rec and "loop_counts" not in rec


def test_known_limit_the_senders_queue_items_stay_after_release(tmp_path):
    s = Ed25519Signer()
    a, b = hub(tmp_path, "a"), hub(tmp_path, "b")
    a.send(env())
    item = {"client_context_id": "ctx-1", "x": 1}
    a.queues["clarification.request"].append(item)
    rec = build_transfer(a, ["ctx-1"], s)
    ack = receive_transfer(b, rec, Ed25519Verifier(s.public_key_bytes()))
    confirm_release(a, ["ctx-1"], ack)
    assert "ctx-1" not in a.seq                                 # counters cleared
    assert item in b.queues["clarification.request"]            # carried over
    assert item in a.queues["clarification.request"]            # and still here: in both regions
