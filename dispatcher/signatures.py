"""Envelope signatures — Day 3.

Replaces Day 1's deny-all placeholder verifier with real verification:
HMAC-SHA256 over a canonical serialization of the envelope's identity-bearing
fields. Constant-time comparison. Stdlib only — the package keeps its
zero-dependency property.

Canonical fields: envelope_id, from_agent, to_agent, intent,
client_context_id, payload (sorted-key JSON). sequence is EXCLUDED —
signatures are applied by the sender, sequence is stamped by the hub after
persist; signing it would break verification on every legitimate envelope.

Limitation, stated plainly: HMAC is symmetric — any party holding the key
can sign, not just verify. That is acceptable while the only authorized
signer is the human principal's own tooling holding the only key. It is NOT
sufficient for multi-party authority. The upgrade path is Ed25519
(asymmetric), deferred because it requires the `cryptography` dependency —
a deployment decision, not a default.
"""
from __future__ import annotations

import hashlib
import hmac
import json


def _canonical(env) -> bytes:
    return json.dumps({
        "envelope_id": env.envelope_id,
        "from_agent": env.from_agent,
        "to_agent": env.to_agent,
        "intent": env.intent,
        "client_context_id": env.client_context_id,
        "payload": env.payload,
    }, sort_keys=True, separators=(",", ":")).encode()


class HmacSigner:
    """Holds the authority key. .sign() stamps env.signature;
    .verifier() plugs straight into Hub(signature_verifier=...)."""

    def __init__(self, key: bytes):
        if not key or len(key) < 16:
            raise ValueError("authority key must be at least 16 bytes")
        self._key = key

    def sign(self, env) -> str:
        env.signature = hmac.new(self._key, _canonical(env),
                                 hashlib.sha256).hexdigest()
        return env.signature

    def verify(self, env) -> bool:
        if not env.signature:
            return False               # absent signature is never valid
        expected = hmac.new(self._key, _canonical(env),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, env.signature)

    def verifier(self):
        return self.verify
