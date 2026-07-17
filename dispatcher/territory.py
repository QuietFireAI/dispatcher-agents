"""Territories - dispatcher-to-dispatcher handoff (DISPATCHER_CORE §Territories).

The spec, implemented literally:
- Transfer record = client-context ownership + sequence high-water mark per
  context + open holds/queue items + the attested resource manifest.
- The record is SIGNED. The receiving hub verifies fail-closed - an unsigned
  or badly signed transfer is refused, audited, and queued for human review;
  contexts are never adopted on trust.
- The receiver acks the transfer the way it acks any envelope: persisted to
  its audit log FIRST, then confirmed. No context is ever in two regions:
  the sender marks contexts released only on the receiver's ack.
- Spoke invariance: nothing here touches spokes. A transferred context's
  next envelope routes on the receiving hub with the sequence continuing
  from the high-water mark - the train never knows the dispatcher changed.
"""
from __future__ import annotations

import json


def _canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def build_transfer(hub, contexts: list[str], signer,
                   manifest: dict | None = None) -> dict:
    """Sender side. Requires a signer (authority): an unsigned transfer
    record is not a transfer record."""
    record = {
        "kind": "territory.transfer",
        "contexts": {c: {"sequence_hwm": hub.seq.get(c, 0)} for c in contexts},
        "open_items": {
            q: [i for i in items
                if isinstance(i, dict)
                and i.get("client_context_id") in contexts]
            for q, items in hub.queues.items()
        },
        "attested_manifest": manifest or {},
    }
    record["open_items"] = {q: v for q, v in record["open_items"].items() if v}
    # crew change carries the outgoing crew's reasoning state (sleep-marks)
    from .pillars import capture_sleepmark
    record["sleepmark"] = capture_sleepmark(
        hub, context_summary=f"territory transfer of {contexts}")["mark"]
    record["signature"] = signer.sign_bytes(_canonical(record))
    hub.audit.append("territory.transfer.sent",
                     {"contexts": contexts,
                      "hwm": record["contexts"],
                      "signed": True})
    return record


def receive_transfer(hub, record: dict, verifier) -> dict:
    """Receiver side. Verify -> persist -> adopt -> ack. Fail-closed."""
    sig = record.get("signature")
    if not sig or not verifier.verify_bytes(_canonical(record), sig):
        flag = {"reason": "territory transfer signature absent or invalid - "
                          "refused, contexts NOT adopted, held for review",
                "contexts": list(record.get("contexts", {}))}
        hub.queue_and_notify("integrity.violation", flag)
        hub.audit.append("territory.transfer.refused", flag)
        return {"status": "refused", **flag}
    # persist before adopt (same order as envelope persist-before-deliver)
    hub.audit.append("territory.transfer.received",
                     {"contexts": record["contexts"],
                      "open_item_queues": list(record["open_items"])})
    if record.get("sleepmark"):
        from .pillars import restore_sleepmark
        restore_sleepmark(hub, record["sleepmark"])
    for ctx, meta in record["contexts"].items():
        hub.seq[ctx] = meta["sequence_hwm"]
    for q, items in record["open_items"].items():
        hub.queues.setdefault(q, []).extend(items)
    hub.audit.append("territory.transfer.ack",
                     {"contexts": list(record["contexts"])})
    return {"status": "ack", "contexts": list(record["contexts"])}


def confirm_release(hub, contexts: list[str], receiver_ack: dict) -> None:
    """Sender releases ownership ONLY on the receiver's ack - never before."""
    if receiver_ack.get("status") != "ack":
        raise ValueError("no receiver ack - contexts stay owned here; "
                         "a context is never in two regions and never in none")
    for c in contexts:
        hub.seq.pop(c, None)
    hub.audit.append("territory.transfer.released", {"contexts": contexts})
