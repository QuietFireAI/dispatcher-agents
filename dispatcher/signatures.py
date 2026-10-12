"""Envelope signatures - Ed25519 only.

The authority holds a private key and signs; the hub holds only the public
key and verifies. A party that can verify cannot sign. Requires the
`cryptography` package (`pip install -e ".[crypto]"`); the import is
deferred, so routing, the audit log and every unsigned lane run without it.

Signed bytes (canonical, sorted-key JSON): envelope_id, from_agent,
to_agent, intent, client_context_id, payload and provenance. provenance is
signed because it carries the signer stamp (provenance.signer) that the
signer registry checks: changing who an envelope says signed it breaks the
signature. sequence is EXCLUDED - the hub stamps it after persist, so
signing it would break verification on every legitimate envelope.

History (J10, 2026-10-12):
  - provenance was not signed before this change, although
    signer_registry.py said the stamp was bound into the signature. Anyone
    able to alter an envelope in transit could change the claimed signer
    without breaking the signature. Signatures made before this change do
    not verify after it.
  - the HMAC-SHA256 shared-secret tier (HmacSigner) was removed. With a
    shared secret every verifier can also sign, so one stolen key forges any
    authority. Sign with Ed25519Signer; give the hub Ed25519Verifier.
"""
from __future__ import annotations

import json


def _canonical(env) -> bytes:
    return json.dumps({
        "envelope_id": env.envelope_id,
        "from_agent": env.from_agent,
        "to_agent": env.to_agent,
        "intent": env.intent,
        "client_context_id": env.client_context_id,
        "payload": env.payload,
        "provenance": env.provenance,
    }, sort_keys=True, separators=(",", ":")).encode()


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

    def verifier(self):
        """A verifier for this signer's public key (the hub needs only
        that; handy where the authority and the hub share a process)."""
        return Ed25519Verifier(self.public_key_bytes()).verifier()


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
