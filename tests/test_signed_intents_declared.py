"""Signed intents are declared by the identity (owner decision D2, 2026-10-14).

The core requires a verified signature on `.authority` intents and on its own
config.update. Anything else an identity wants signed it names in its
routes.json under "signed_intents"; the core used to hard-code the listing
identity's listing.change.authorized instead."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from dispatcher.core import AuditLog, Envelope, Routes  # noqa: E402
from dispatcher.hub import AUTHORITY_INTENTS, Hub, is_authority  # noqa: E402
from dispatcher.signatures import Ed25519Signer  # noqa: E402

LISTING = os.path.abspath(os.environ.get("IDENTITY_DIR", os.path.join(HERE, "..", "..", "listing-assistants")))


def routes(tmp_path, signed=None, extra=()):
    doc = {"version": "t", "routes": [
        {"intent": "record.change", "senders": ["human"], "receivers": ["05"]},
        *extra]}
    if signed is not None:
        doc["signed_intents"] = signed
    p = tmp_path / "routes.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return Routes(str(p))


def send(tmp_path, r, sign):
    s = Ed25519Signer()
    hub = Hub(r, AuditLog(str(tmp_path / "a.jsonl")), signature_verifier=s.verifier())
    hub.register("05", lambda e: None)
    e = Envelope(from_agent="human", to_agent="05", intent="record.change", client_context_id="c",
                 payload={}, provenance={"source": "t"})
    if sign:
        s.sign(e)
    return hub.send(e)["status"]


def test_the_core_hard_codes_no_identitys_intent():
    assert AUTHORITY_INTENTS == {"config.update"}
    assert not is_authority("listing.change.authorized")
    assert is_authority("listing.change.authorized", {"listing.change.authorized"})
    assert is_authority("pay.authority") and is_authority("config.update")


def test_a_declared_intent_needs_a_signature_and_an_undeclared_one_does_not(tmp_path):
    assert send(tmp_path, routes(tmp_path, ["record.change"]), sign=False) == "reject"
    assert send(tmp_path, routes(tmp_path, ["record.change"]), sign=True) == "ack"
    assert send(tmp_path, routes(tmp_path), sign=False) == "ack"


def test_a_signed_intent_that_is_not_on_the_track_refuses_to_load(tmp_path):
    with pytest.raises(ValueError, match=r"signed_intents names \['record.chnage'\], which is not on the track"):
        routes(tmp_path, ["record.chnage"])


def test_a_wildcard_route_carries_a_declared_intent_and_a_wildcard_name_is_refused(tmp_path):
    # 2026-10-14 cold review: `pay.*` declared protected nothing, and
    # `pay.release` carried by a `pay.*` route was refused as not on the track
    wild = [{"intent": "pay.*", "senders": ["human"], "receivers": ["05"]}]
    r = routes(tmp_path, ["pay.release"], extra=wild)
    assert "pay.release" in r.signed_intents
    s = Ed25519Signer()
    hub = Hub(r, AuditLog(str(tmp_path / "w.jsonl")), signature_verifier=s.verifier())
    hub.register("05", lambda e: None)
    e = Envelope(from_agent="human", to_agent="05", intent="pay.release", client_context_id="c",
                 payload={}, provenance={"source": "t"})
    assert hub.send(e)["status"] == "reject"
    with pytest.raises(ValueError, match=r"name each intent exactly, not with a wildcard"):
        routes(tmp_path, ["pay.*"], extra=wild)


@pytest.mark.skipif(not os.path.isfile(os.path.join(LISTING, "identity", "routes.json")),
                    reason="listing identity not present (the with-identity CI job runs this)")
def test_the_listing_identity_declares_its_signed_listing_change(tmp_path):
    r = Routes(os.path.join(LISTING, "identity", "routes.json"))
    assert "listing.change.authorized" in r.signed_intents
    hub = Hub(r, AuditLog(str(tmp_path / "a.jsonl")), signature_verifier=Ed25519Signer().verifier())
    hub.register("05", lambda e: None)
    unsigned = Envelope(from_agent="human", to_agent="05", intent="listing.change.authorized",
                        client_context_id="c", payload={}, provenance={"source": "t"})
    assert hub.send(unsigned)["status"] == "reject"
