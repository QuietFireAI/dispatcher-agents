"""Boot attestation — Day 3.

At boot the hub hashes what it is about to run: every file in the dispatcher
package plus the identity's routes.json. The manifest is audit-logged as
boot.attestation — the run's provenance artifact.

Verification is fail-closed per the gate principle: a missing manifest, a
missing file, or a hash mismatch is a named violation, never a silent pass.
Presence of a manifest alone proves nothing — only recomputation does.

Limitation, stated plainly: this is integrity attestation (detect drift
between what was reviewed and what is running), not authenticity — the
manifest is not yet signed, so it proves WHAT is running, not WHO approved
it. Signing the manifest rides the signature layer (dispatcher.signatures)
and is wired at deployment, not assumed here.
"""
from __future__ import annotations

import hashlib
import os


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(package_dir: str, routes_path: str) -> dict:
    """Hash every .py in the package + routes.json. Deterministic order."""
    files = sorted(
        os.path.join(package_dir, f)
        for f in os.listdir(package_dir) if f.endswith(".py")
    )
    files.append(routes_path)
    return {os.path.basename(p): _sha256(p) for p in files}


def verify_manifest(manifest: dict, package_dir: str, routes_path: str) -> list[str]:
    """Recompute and compare. Returns [] only when everything matches.
    Every deviation is named: absent file, absent manifest entry, mismatch."""
    if not manifest:
        return ["manifest absent — boot not attested; tainted, hold for review"]
    current = build_manifest(package_dir, routes_path)
    violations = []
    for name, digest in manifest.items():
        if name not in current:
            violations.append(f"{name}: in manifest, absent on disk")
        elif current[name] != digest:
            violations.append(f"{name}: hash mismatch (attested {digest[:12]}…, "
                              f"running {current[name][:12]}…)")
    for name in current:
        if name not in manifest:
            violations.append(f"{name}: on disk, absent from manifest — unattested code")
    return violations


def attest_boot(hub, package_dir: str, routes_path: str) -> dict:
    """Build the manifest and put it on the audit log. Returns the manifest."""
    manifest = build_manifest(package_dir, routes_path)
    hub.audit.append("boot.attestation", {"manifest": manifest})
    return manifest
