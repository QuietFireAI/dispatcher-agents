"""A hub with no human_notifier says so at boot (2026-10-14). Before, it was
the one unarmed mode that declared nothing: escalations were raised and the
log never said no person could be told."""
import json
import os

from dispatcher.core import AuditLog, Routes
from dispatcher.hub import Hub

FIX = os.path.join(os.path.dirname(__file__), "routes_fixture.json")


def kinds(path):
    return [json.loads(l)["kind"] for l in open(path, encoding="utf-8")]


def test_no_notifier_is_declared_once_at_boot(tmp_path):
    log = str(tmp_path / "a.jsonl")
    Hub(Routes(FIX), AuditLog(log))
    assert kinds(log).count("notifier.unarmed") == 1


def test_a_configured_notifier_is_not_declared_unarmed(tmp_path):
    log = str(tmp_path / "a.jsonl")
    Hub(Routes(FIX), AuditLog(log), human_notifier=lambda q, r: {"status": "sent"})
    assert "notifier.unarmed" not in kinds(log)
