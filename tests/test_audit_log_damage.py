"""A damaged or cut-off audit log is named, never a crash, and only a person
repairs it (repair plan 1.2, owner decision D4, 2026-10-13).

Found in the 2026-10-13 snapshot: a partial last line (a process dying
part-way through an append) made AuditLog() itself raise a bare
JSONDecodeError, so nothing could start on that log, and verify_chain() -
which promises to name the first bad line - raised instead.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dispatcher.core import AuditLog, AuditLogDamaged  # noqa: E402


def three(tmp_path, name="a.jsonl"):
    p = str(tmp_path / name)
    log = AuditLog(p)
    for n in range(3):
        log.append("x", {"n": n, "note": "café"})
    return p


def raw(p):
    return open(p, "rb").read()


# ------------------------------------------------------------- refusing to open
@pytest.mark.parametrize("tail", [b'{"n": 3, "ts": 1, "ki', b'{"note": "caf\xc3', b"{", b"garbage"])
def test_a_cut_off_last_write_is_named_not_a_crash(tmp_path, tail):
    p = three(tmp_path)
    open(p, "ab").write(tail)
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog(p)
    e = ei.value
    assert e.line == 4 and e.torn_tail is True and e.path == p
    assert "damaged at line 4" in str(e) and "repair_torn_tail" in str(e)
    assert "Nothing was written" in str(e)
    c = AuditLog.check_file(p)
    assert c == {"ok": False, "entries": 4, "break_at": 4,
                 "reason": "not a readable entry", "torn_tail": True}


def test_a_damaged_line_before_the_end_is_not_called_a_cut_off_write(tmp_path):
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[1] = b"{this is not json"
    open(p, "wb").write(b"\n".join(lines))
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog(p)
    assert ei.value.line == 2 and ei.value.torn_tail is False
    assert "investigate" in str(ei.value) and "repair_torn_tail(path) (" not in str(ei.value)
    c = AuditLog.check_file(p)
    assert c["break_at"] == 2 and c["torn_tail"] is False


def test_an_unreadable_last_line_that_ends_with_a_newline_is_damage_not_a_cut_off(tmp_path):
    p = three(tmp_path)
    open(p, "ab").write(b"not json at all\n")
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog(p)
    assert ei.value.torn_tail is False
    with pytest.raises(ValueError, match="no cut-off write to repair"):
        AuditLog.repair_torn_tail(p)


def test_read_names_the_line_too(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)                      # opened while intact
    open(p, "ab").write(b'{"half')
    with pytest.raises(AuditLogDamaged) as ei:
        log.read()
    assert ei.value.line == 4 and ei.value.torn_tail is True
    assert log.verify_chain()["break_at"] == 4


def test_an_edited_line_still_reads_as_a_link_break(tmp_path):
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[0] = lines[0].replace(b'"n": 0', b'"n": 9')
    open(p, "wb").write(b"\n".join(lines))
    c = AuditLog.check_file(p)
    assert c["ok"] is False and c["break_at"] == 1 and c["torn_tail"] is False
    assert "edited, deleted or reordered" in c["reason"]


# ----------------------------------------------------------------- the repair
def test_repair_moves_the_partial_line_aside_and_records_it(tmp_path):
    p = three(tmp_path)
    before = raw(p)
    tail = b'{"n": 3, "ts": 1, "kind": "x", "prev_ha'
    open(p, "ab").write(tail)
    rec = AuditLog.repair_torn_tail(p)
    assert rec["line"] == 4 and rec["bytes"] == len(tail) and rec["complete_entries"] == 3
    assert rec["sha256"] == hashlib.sha256(tail).hexdigest()
    side = os.path.join(str(tmp_path), rec["side_file"])
    assert open(side, "rb").read() == tail
    assert rec["side_file"].startswith("a.jsonl.torn-") and rec["side_file"].endswith(rec["sha256"][:12])
    after = raw(p)
    assert after.startswith(before)                      # every complete entry kept, byte for byte
    log = AuditLog(p)
    entries = log.read()
    assert len(entries) == 4 and entries[-1]["kind"] == "audit.tail_quarantined"
    assert entries[-1]["sha256"] == rec["sha256"]
    assert log.verify_chain() == {"ok": True, "entries": 4, "break_at": None}
    log.append("y", {"after": True})
    assert AuditLog.check_file(p)["ok"] and len(AuditLog(p).read()) == 5


@pytest.mark.parametrize("crlf", [False, True])
def test_a_repair_interrupted_before_it_records_itself_leaves_exactly_the_complete_entries(tmp_path, monkeypatch, crlf):
    # The process can die between the cut and the audit.tail_quarantined entry.
    # What is left on disk must then be the complete entries byte for byte -
    # a clean, newline-terminated log, not one cut a byte short.
    p = three(tmp_path)
    data = raw(p)
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    open(p, "wb").write(data + b'{"half')

    def died(self, *a, **k):
        raise RuntimeError("process died")
    monkeypatch.setattr(AuditLog, "append", died)
    with pytest.raises(RuntimeError):
        AuditLog.repair_torn_tail(p)
    assert raw(p) == data
    monkeypatch.undo()
    assert AuditLog.check_file(p) == {"ok": True, "entries": 3, "break_at": None}


def test_repair_refuses_when_the_chain_is_broken_before_the_cut_and_changes_nothing(tmp_path):
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[0] = lines[0].replace(b'"n": 0', b'"n": 9')
    open(p, "wb").write(b"\n".join(lines) + b'{"half')
    before = raw(p)
    with pytest.raises(ValueError, match="chain breaks at line 1"):
        AuditLog.repair_torn_tail(p)
    assert raw(p) == before
    assert not [f for f in os.listdir(str(tmp_path)) if ".torn-" in f]


def test_repair_refuses_when_an_earlier_line_is_unreadable(tmp_path):
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[1] = b"{nope"
    open(p, "wb").write(b"\n".join(lines) + b'{"half')
    before = raw(p)
    with pytest.raises(AuditLogDamaged) as ei:
        AuditLog.repair_torn_tail(p)
    assert ei.value.line == 2 and raw(p) == before


def test_repair_refuses_when_there_is_nothing_to_repair(tmp_path):
    p = three(tmp_path)
    before = raw(p)
    with pytest.raises(ValueError, match="no cut-off write"):
        AuditLog.repair_torn_tail(p)
    assert raw(p) == before


# ------------------------------------------- a complete entry missing its newline
def test_a_complete_last_entry_without_its_newline_opens_and_appends_cleanly(tmp_path):
    p = three(tmp_path)
    data = raw(p)                          # read before opening for write
    open(p, "wb").write(data.rstrip(b"\n"))
    with pytest.raises(ValueError, match="only lacks its newline"):
        AuditLog.repair_torn_tail(p)
    log = AuditLog(p)
    log.append("y", {"next": 1})
    assert log.verify_chain() == {"ok": True, "entries": 4, "break_at": None}
    assert len(log.read()) == 4


# ------------------------------------------------------------------- Windows
def test_windows_line_endings_check_and_repair(tmp_path):
    p = three(tmp_path)
    data = raw(p)                          # read before opening for write
    open(p, "wb").write(data.replace(b"\n", b"\r\n") + b'{"half')
    assert AuditLog.check_file(p)["break_at"] == 4
    rec = AuditLog.repair_torn_tail(p)
    assert rec["complete_entries"] == 3
    assert AuditLog(p).verify_chain()["ok"]


# ------------------------------------------------------------------- anchors
def test_an_anchor_before_the_cut_still_verifies(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)
    anchor = log.anchor()
    open(p, "ab").write(b'{"half')
    assert log.verify_anchor(anchor) == {"ok": True, "reason": None}
    assert log.anchor()["entries"] == 4


# --------------------------------------------------------------------- the CLI
def cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "audit_repair.py"), *args],
                          capture_output=True, text=True)


def test_the_cli_checks_repairs_and_refuses(tmp_path):
    p = three(tmp_path)
    r = cli(p, "--check")
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True
    open(p, "ab").write(b'{"half')
    r = cli(p, "--check")
    assert r.returncode == 1 and json.loads(r.stdout)["torn_tail"] is True
    r = cli(p)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["repaired"] is True and out["chain"]["ok"] is True
    r = cli(p)
    assert r.returncode == 2 and json.loads(r.stdout)["repaired"] is False
    r = cli(str(tmp_path / "missing.jsonl"))
    assert r.returncode == 2
