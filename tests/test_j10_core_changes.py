"""J10 (2026-10-12): JEV boot line says what the hub knows, the text
command parser is gone, the core no longer names TelsonBase."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dispatcher.core import Routes, AuditLog              # noqa: E402
from dispatcher.hub import Hub                             # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")
ROOT = os.path.join(os.path.dirname(__file__), "..")


def _boot(tmp_path, monkeypatch, key):
    for k in ("JEV_API_KEY", "JEV_ENDPOINT_URL"):
        monkeypatch.delenv(k, raising=False)
    if key is not None:
        monkeypatch.setenv("JEV_API_KEY", key)
    hub = Hub(Routes(FIX), AuditLog(str(tmp_path / f"a{key}.jsonl")))
    return [e for e in hub.audit.read() if e["kind"].startswith("jev.")]


def test_a_jev_key_is_reported_as_present_never_as_live(tmp_path, monkeypatch):
    [e] = _boot(tmp_path, monkeypatch, "real-looking-key")
    assert e["kind"] == "jev.key_present" and e["coprocessor_status"] == "KEY_PRESENT"
    assert e["endpoint"] == "https://api.typesafe.ai/v1/decisions"
    assert "live" not in e["kind"] and "LIVE" not in e["coprocessor_status"]
    assert "decision adapter" in e["reason"]


def test_no_key_or_a_placeholder_key_is_the_local_rules_line(tmp_path, monkeypatch):
    for key in (None, "", "YOUR_KEY_HERE", "PLACEHOLDER"):
        [e] = _boot(tmp_path, monkeypatch, key)
        assert e["kind"] == "jev.unarmed_fallback" and e["is_fallback"] is True
        assert e["reason"].startswith("JEV_API_KEY unconfigured")


def test_text_command_parser_is_removed():
    import dispatcher.notifier as n
    assert not hasattr(n, "parse_inbound_field_command")


def test_core_files_do_not_name_telsonbase():
    pkg = os.path.join(ROOT, "dispatcher")
    for f in os.listdir(pkg):
        if f.endswith(".py"):
            assert "telsonbase" not in open(os.path.join(pkg, f)).read().lower(), f
