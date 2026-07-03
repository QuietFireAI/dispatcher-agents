"""Envelope signatures - Day 3.

Replaces Day 1's deny-all placeholder verifier with real verification:
HMAC-SHA256 over a canonical serialization of the envelope's identity-bearing
fields. Constant-time comparison. Stdlib only - the package keeps its
zero-dependency property.

Canonical fields: envelope_id, from_agent, to_agent, intent,
client_context_id, payload (sorted-key JSON). sequence is EXCLUDED - 
signatures are applied by the sender, sequence is stamped by the hub after
persist; signing it would break verification on every legitimate envelope.

Limitation, stated plainly: HMAC is symmetric - any party holding the key
can sign, not just verify. That is acceptable while the only authorized
signer is the human principal's own tooling holding the only key. It is NOT
sufficient for multi-party authority. The upgrade path is Ed25519
(asymmetric), deferred because it requires the `cryptography` dependency - 
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


# --------------------------------------------------------------- Ed25519 tier
# Asymmetric authority: private key signs, public key verifies. Verifiers
# cannot forge - this is the multi-party upgrade HMAC cannot provide.
# Requires `cryptography` (the package's ONLY optional dependency; import is
# deferred so HMAC deployments stay zero-dep).

class Ed25519Signer:
    """Authority side: holds the private key."""

    def __init__(self, private_key_bytes: bytes | None = None):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        self._sk = (Ed25519PrivateKey.from_private_bytes(private_key_bytes)
                    if private_key_bytes else Ed25519PrivateKey.generate())

    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def private_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._sk.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())

    def sign_bytes(self, data: bytes) -> str:
        return self._sk.sign(data).hex()

    def sign(self, env) -> str:
        env.signature = self.sign_bytes(_canonical(env))
        return env.signature


class Ed25519Verifier:
    """Hub side: public key only - can verify, cannot sign."""

    def __init__(self, public_key_bytes: bytes):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        self._pk = Ed25519PublicKey.from_public_bytes(public_key_bytes)

    def verify_bytes(self, data: bytes, signature_hex: str) -> bool:
        from cryptography.exceptions import InvalidSignature
        try:
            self._pk.verify(bytes.fromhex(signature_hex), data)
            return True
        except (InvalidSignature, ValueError):
            return False

    def verify(self, env) -> bool:
        if not env.signature:
            return False               # absent signature is never valid
        return self.verify_bytes(_canonical(env), env.signature)

    def verifier(self):
        return self.verify
