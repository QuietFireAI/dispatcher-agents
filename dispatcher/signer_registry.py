"""Login-based signer registry - runtime enforcement of the 2026-07-11
owner decision recorded in every identity's config/authority_signers.json.

What this enforces (and what it honestly cannot):

  ENFORCED HERE - an authority envelope must carry a signer stamp
  (env.provenance["signer"]) naming a signer_login that the identity's
  ratified registry authorizes FOR THAT INTENT, with mfa attested true,
  an idp_session_ref present, and the registry row effective and not
  revoked. The stamp is bound into the envelope's cryptographic signature
  (payload/provenance are caller-supplied before signing) and every verdict
  lands on the hash-chained audit log.

  NOT ENFORCED HERE - whether the IdP session is genuine. Validating the
  session against the IdP is the IdP seam adapter's job (INTEGRATIONS.md);
  this registry verifies stamp-vs-registry consistency. Stated plainly:
  the runtime proves WHO the deployment claims signed and that the claim
  was authorized and cryptographically sealed; the deployment's IdP proves
  the human. Both are required; neither pretends to be the other.

Fail-closed rules (all refusals, never warnings):
  - registry file marked UNRATIFIED TEMPLATE  -> refuses to arm
  - registry with zero usable entries         -> refuses to arm
  - entry with mfa_required != True           -> refuses to arm (doctrine)
  - armed + authority envelope without stamp  -> reject
  - stamp login not authorized for the intent -> reject
  - row revoked or not yet effective          -> reject
Absent registry (None) = UNARMED IS AUDITED: the hub says so on the log,
once per authority envelope, and applies crypto-signature checks only.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class SignerVerdict:
    ok: bool
    reason: str


class SignerRegistry:
    """Loads and enforces config/authority_signers.json for an identity."""

    def __init__(self, entries: list[dict]):
        if not entries:
            raise ValueError("signer registry has zero usable entries - "
                             "refusing to arm (fail closed)")
        for e in entries:
            if e.get("mfa_required") is not True:
                raise ValueError(
                    f"signer entry for {e.get('intent')!r} lacks "
                    "mfa_required=true - doctrine violation, refusing to arm")
            if not e.get("signer_login") or not e.get("intent"):
                raise ValueError("signer entry missing intent or signer_login")
        self._by_intent: dict[str, list[dict]] = {}
        for e in entries:
            self._by_intent.setdefault(e["intent"], []).append(e)

    @classmethod
    def load(cls, identity_root: str) -> "SignerRegistry":
        path = os.path.join(identity_root, "config", "authority_signers.json")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"authority_signers.json absent at {path} - an identity with "
                "authority intents cannot arm signer enforcement without it")
        doc = json.load(open(path))
        status = str(doc.get("_status", ""))
        if "UNRATIFIED" in status.upper():
            raise ValueError(
                "authority_signers.json is an UNRATIFIED TEMPLATE - loads "
                "fail closed while that line stands (edit, ratify, reload)")
        entries = [e for e in doc.get("entries", [])
                   if not str(e.get("signer_login", "")).startswith("<")]
        return cls(entries)

    def check(self, env, today: str | None = None) -> SignerVerdict:
        """today: ISO date for effective-date enforcement. Defaults to the
        real system date - this is live authorization, not a simulation
        seam. Callers with a controlled clock (tests) pass it explicitly.

        Fixed 2026-07-17: the 'effective' field was schema-present,
        enforcement-absent - check() never read it, so a signer entry
        dated to take effect next year authorized today. Now: a missing
        effective date fails closed (an entry that never says when it
        starts never starts), and a future-dated entry denies until its
        date arrives."""
        import datetime
        today_d = (datetime.date.fromisoformat(today) if today
                   else datetime.date.today())
        stamp = (env.provenance or {}).get("signer")
        if not isinstance(stamp, dict):
            return SignerVerdict(False, "authority envelope carries no signer "
                                        "stamp (provenance.signer)")
        login = stamp.get("signer_login")
        if not login:
            return SignerVerdict(False, "signer stamp missing signer_login")
        if stamp.get("mfa") is not True:
            return SignerVerdict(False, "signer stamp does not attest MFA")
        if not stamp.get("idp_session_ref"):
            return SignerVerdict(False, "signer stamp missing idp_session_ref")
        rows = self._by_intent.get(env.intent, [])
        for r in rows:
            if r["signer_login"] == login:
                if r.get("revoked"):
                    return SignerVerdict(False,
                        f"signer {login!r} is revoked for {env.intent!r}")
                effective = r.get("effective")
                if not effective:
                    return SignerVerdict(False,
                        f"signer entry for {login!r} has no effective date - "
                        f"fail closed, an entry that never says when it "
                        f"starts never starts")
                try:
                    eff_d = datetime.date.fromisoformat(str(effective))
                except ValueError:
                    return SignerVerdict(False,
                        f"signer entry for {login!r} has unparseable "
                        f"effective date {effective!r} - fail closed")
                if eff_d > today_d:
                    return SignerVerdict(False,
                        f"signer {login!r} not effective until {effective} "
                        f"(today {today_d.isoformat()})")
                return SignerVerdict(True, "authorized")
        return SignerVerdict(False,
            f"login {login!r} not authorized for intent {env.intent!r}")
