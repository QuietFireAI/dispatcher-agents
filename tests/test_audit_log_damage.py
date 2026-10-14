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
def test_a_repair_that_dies_before_the_swap_leaves_the_log_as_it_was(tmp_path, monkeypatch, crlf):
    # The repaired log is written to a new file and swapped in with one
    # os.replace (2026-10-13 cold review: the earlier repair cut the log and
    # then appended, so a crash between the two left a cut log with no record
    # of why). Dying before the swap must leave the original untouched, no
    # temporary or side file behind, and the repair still possible.
    from dispatcher import core
    p = three(tmp_path)
    data = raw(p)
    if crlf:
        data = data.replace(b"\n", b"\r\n")
    open(p, "wb").write(data + b'{"half')

    def died(a, b):
        raise RuntimeError("process died")
    monkeypatch.setattr(core.os, "replace", died)
    with pytest.raises(RuntimeError):
        AuditLog.repair_torn_tail(p)
    assert raw(p) == data + b'{"half'
    assert not [f for f in os.listdir(str(tmp_path)) if ".repair-" in f or ".torn-" in f]
    monkeypatch.undo()
    rec = AuditLog.repair_torn_tail(p)                    # runs again
    assert raw(p).startswith(data) and AuditLog.check_file(p)["ok"]
    assert len([f for f in os.listdir(str(tmp_path)) if ".torn-" in f]) == 1
    assert rec["complete_entries"] == 3


# ------------------------------------------- the file changed under a writer
# Found by the 2026-10-13 cold review: AuditLog checked the file only when it
# was opened, so a running desk kept appending to a log damaged under it,
# and two writers on one file forked the chain.
def test_a_writer_refuses_to_append_after_the_file_was_damaged_under_it(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)
    lines = raw(p).split(b"\n")
    lines[1] = b'{"garbage'
    open(p, "wb").write(b"\n".join(lines))
    before = raw(p)
    with pytest.raises(AuditLogDamaged) as ei:
        log.append("y", {"after": "damage"})
    assert ei.value.line == 2 and "changed since this process last wrote" in str(ei.value)
    assert raw(p) == before


def test_a_writer_does_not_write_after_another_writers_cut_off_line(tmp_path):
    p = three(tmp_path)
    a = AuditLog(p)
    open(p, "ab").write(b'{"half')                      # another writer died mid-line
    before = raw(p)
    with pytest.raises(AuditLogDamaged) as ei:
        a.append("y", {})
    assert ei.value.torn_tail is True and raw(p) == before
    AuditLog.repair_torn_tail(p)                         # still repairable
    assert AuditLog.check_file(p)["ok"]


def test_two_writers_on_one_file_continue_one_chain(tmp_path):
    p = three(tmp_path)
    a, b = AuditLog(p), AuditLog(p)
    a.append("from-a", {})
    b.append("from-b", {})                              # b sees a's entry and follows it
    a.append("from-a", {})
    assert AuditLog.check_file(p) == {"ok": True, "entries": 6, "break_at": None}


def test_a_writer_open_during_a_repair_follows_the_repaired_chain(tmp_path):
    p = three(tmp_path)
    w = AuditLog(p)
    open(p, "ab").write(b'{"half')
    AuditLog.repair_torn_tail(p)
    w.append("after-repair", {})
    assert AuditLog.check_file(p)["ok"]
    assert [e["kind"] for e in AuditLog(p).read()][-2:] == ["audit.tail_quarantined", "after-repair"]


@pytest.mark.parametrize("how", ["deleted", "cut back", "replaced"])
def test_a_writer_whose_entries_are_gone_refuses(tmp_path, how):
    p = three(tmp_path)
    w = AuditLog(p)
    if how == "deleted":
        os.remove(p)
    elif how == "cut back":
        first = raw(p).split(b"\n")[0] + b"\n"           # read before opening for write
        open(p, "wb").write(first)
        assert AuditLog.check_file(p) == {"ok": True, "entries": 1, "break_at": None}
    else:
        os.remove(p)
        other = AuditLog(p)
        other.append("x", {"n": "new history"})
    before = raw(p) if os.path.exists(p) else None
    with pytest.raises(AuditLogDamaged, match="no longer in the file"):
        w.append("y", {})
    assert (raw(p) if os.path.exists(p) else None) == before


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


# --------------------------------------------------------------- the KPI gate
def gate(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", "kpi_gate.py"), *args],
                          capture_output=True, text=True)


def test_the_kpi_gate_fails_a_damaged_or_edited_log_by_name(tmp_path):
    p = three(tmp_path)
    r = gate(p)
    assert r.returncode == 0 and "KPI GATE PASS" in r.stdout
    open(p, "ab").write(b'{"half')
    r = gate(p)
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert "FAIL audit chain broken at line 4: not a readable entry (a write cut off at the end)" in r.stdout
    q = three(tmp_path, "b.jsonl")
    lines = raw(q).split(b"\n")
    lines[0] = lines[0].replace(b'"n": 0', b'"n": 5')
    open(q, "wb").write(b"\n".join(lines))
    r = gate(q)
    assert r.returncode == 1 and "FAIL audit chain broken at line 1: does not link" in r.stdout


# --------------------------------- details the 2026-10-13 re-sweep asked about
def test_a_second_repair_in_the_same_second_gets_its_own_side_file(tmp_path, monkeypatch):
    from dispatcher import core
    monkeypatch.setattr(core.time, "strftime", lambda *a: "20261013T000000Z")
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    first = AuditLog.repair_torn_tail(p)
    open(p, "ab").write(b'{"half')                       # the same bytes, cut again
    second = AuditLog.repair_torn_tail(p)
    sha12 = first["sha256"][:12]
    assert first["side_file"] == f"a.jsonl.torn-20261013T000000Z-{sha12}"
    assert second["side_file"] == f"a.jsonl.torn-20261013T000000Z-{sha12}-2"


def test_the_repaired_log_keeps_the_files_own_line_endings(tmp_path):
    # LF and CRLF files built byte for byte, so the test means the same on
    # Windows, where text-mode appends write CRLF (core CI run 38458144436)
    p = three(tmp_path)
    data = raw(p).replace(b"\r\n", b"\n")
    open(p, "wb").write(data + b'{"half')
    AuditLog.repair_torn_tail(p)
    assert b"\r" not in raw(p)
    q = three(tmp_path, "b.jsonl")
    data = raw(q).replace(b"\r\n", b"\n")              # read before opening for write
    open(q, "wb").write(data.replace(b"\n", b"\r\n") + b'{"half')
    AuditLog.repair_torn_tail(q)
    assert raw(q).endswith(b"}\r\n") and raw(q).count(b"\r\n") == 4


def test_the_repair_swaps_in_a_temporary_file_named_for_the_cut(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    seen = []
    real = os.replace
    monkeypatch.setattr(core.os, "replace", lambda a, b: (seen.append((a, b)), real(a, b)))
    rec = AuditLog.repair_torn_tail(p)
    stamp = rec["side_file"].split(".torn-")[1].split("-")[0]
    assert seen == [(f"{p}.repair-{stamp}-{rec['sha256'][:12]}", p)]


def test_the_repair_closes_every_file_handle_it_opens(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    opened, closed = [], []
    real_open, real_close = os.open, os.close

    def track_open(*a, **k):
        fd = real_open(*a, **k)
        opened.append(fd)
        return fd

    def track_close(fd):
        closed.append(fd)
        return real_close(fd)
    monkeypatch.setattr(core.os, "open", track_open)
    monkeypatch.setattr(core.os, "close", track_close)
    AuditLog.repair_torn_tail(p)
    assert sorted(opened) == sorted(closed)
    assert len(opened) == (0 if os.name == "nt" else 1)   # the folder, to sync the swap


def test_a_writer_whose_entries_are_gone_is_not_told_it_was_a_cut_off_write(tmp_path):
    p = three(tmp_path)
    w = AuditLog(p)
    os.remove(p)
    with pytest.raises(AuditLogDamaged) as ei:
        w.append("y", {})
    assert ei.value.torn_tail is False and ei.value.line is None
    assert "damaged (no single line)" in str(ei.value)



# ------------------- the OS lock and the stamp (owner decision 2026-10-13, second review)
WRITER = """
import sys
sys.path.insert(0, sys.argv[1])
from dispatcher.core import AuditLog
log = AuditLog(sys.argv[2])
for n in range(int(sys.argv[4])):
    log.append("w", {"who": sys.argv[3], "n": n})
"""


def test_two_processes_appending_at_once_keep_one_chain(tmp_path):
    p = str(tmp_path / "a.jsonl")
    script = tmp_path / "writer.py"
    script.write_text(WRITER, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), ROOT, p, who, "150"])
             for who in ("A", "B", "C")]
    assert [pr.wait(timeout=120) for pr in procs] == [0, 0, 0]
    assert AuditLog.check_file(p) == {"ok": True, "entries": 450, "break_at": None}
    got = [(e["who"], e["n"]) for e in AuditLog.reader(p).read()]
    assert sorted(got) == sorted((w, n) for w in "ABC" for n in range(150))


def test_a_writer_that_cannot_get_the_lock_writes_nothing(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    log = AuditLog(p)
    before = raw(p)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.2)
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from dispatcher.core import _FileLock;"
        "l = _FileLock(sys.argv[2]); l.__enter__(); print('held', flush=True); time.sleep(5)"),
        ROOT, p], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(core.AuditLogBusy, match="nothing was written"):
            log.append("x", {})
    finally:
        holder.kill()
        holder.wait()
    assert raw(p) == before
    log.append("x", {})                                 # free again
    assert AuditLog.check_file(p)["ok"]


def test_an_edit_that_keeps_the_size_is_still_refused(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)
    st = os.stat(p)
    data = raw(p)
    open(p, "wb").write(data.replace(b'"n": 1', b'"n": 7'))    # same size
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert os.path.getsize(p) == st.st_size
    with pytest.raises(AuditLogDamaged) as ei:
        log.append("y", {})
    assert ei.value.line == 2 and "does not link" in ei.value.reason


def test_ensure_intact_checks_without_writing(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)
    log.ensure_intact()                                  # nothing changed: fine
    other = AuditLog(p)
    other.append("x", {})
    log.ensure_intact()                                  # another writer's valid entry: fine
    before = raw(p)
    open(p, "ab").write(b'{"half')
    with pytest.raises(AuditLogDamaged):
        log.ensure_intact()
    assert raw(p) == before + b'{"half'


def test_a_reader_reads_a_damaged_log_without_opening_it_for_writing(tmp_path):
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    r = AuditLog.reader(p)
    assert r.verify_chain()["torn_tail"] is True
    with pytest.raises(AuditLogDamaged):
        r.read()


def test_the_repair_keeps_the_logs_permissions(tmp_path):
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    os.chmod(p, 0o640)
    before = os.stat(p).st_mode
    AuditLog.repair_torn_tail(p)
    assert os.stat(p).st_mode == before


def test_the_cli_reports_a_failed_swap_and_changes_nothing(tmp_path):
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    before = raw(p)
    script = tmp_path / "fail_replace.py"
    script.write_text(
        "import os, runpy, sys\n"
        "def no(a, b):\n    raise PermissionError(13, 'in use by another program')\n"
        "os.replace = no\n"
        "sys.argv = [sys.argv[1], sys.argv[2]]\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), os.path.join(ROOT, "tools", "audit_repair.py"), p],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
    out = json.loads(r.stdout)
    assert out["repaired"] is False and out["changed"] == "nothing" and "in use" in out["refused"]
    assert raw(p) == before
    assert not [f for f in os.listdir(str(tmp_path)) if ".torn-" in f or ".repair-" in f]


# ----------------------------------------- the lock, as the third sweep asked
HOLD = ("import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from dispatcher.core import _FileLock;"
        "l = _FileLock(sys.argv[2]); l.__enter__(); print('held', flush=True); time.sleep(5)")


def held_by_another_process(p):
    pr = subprocess.Popen([sys.executable, "-c", HOLD, ROOT, p], stdout=subprocess.PIPE, text=True)
    assert pr.stdout.readline().strip() == "held"
    return pr


def test_a_reader_waits_for_a_writer_in_another_process(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.2)
    pr = held_by_another_process(p)
    try:
        with pytest.raises(core.AuditLogBusy):
            AuditLog.reader(p).read()
        with pytest.raises(core.AuditLogBusy):
            AuditLog.check_file(p)
    finally:
        pr.kill()
        pr.wait()
    assert len(AuditLog.reader(p).read()) == 3


def test_a_reader_cannot_append(tmp_path):
    p = three(tmp_path)
    with pytest.raises(TypeError, match="read-only"):
        AuditLog.reader(p).append("x", {})


def test_a_nested_check_does_not_wait_on_its_own_lock_and_releases_it(tmp_path, monkeypatch):
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.5)
    a, b = AuditLog(p), AuditLog(p)
    b.append("x", {})
    a.append("y", {})              # a's file changed: _resync -> verify_chain inside the lock
    from dispatcher import core
    assert core._PROCESS_HELD[os.path.realpath(p)].flock is None     # released
    pr = subprocess.run([sys.executable, "-c",
                         "import sys; sys.path.insert(0, sys.argv[1]);"
                         "from dispatcher.core import _FileLock;"
                         "l = _FileLock(sys.argv[2], 0.5); l.__enter__(); print('got it')",
                         ROOT, p], capture_output=True, text=True, timeout=30)
    assert pr.stdout.strip() == "got it"


def test_the_lock_closes_its_file_every_time(tmp_path, monkeypatch):
    import gc
    import warnings
    from dispatcher import core
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.2)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        AuditLog(p).append("x", {})                     # lock taken and given back
        pr = held_by_another_process(p)
        try:
            with pytest.raises(core.AuditLogBusy):
                AuditLog.reader(p).read()               # lock never got: file closed too
        finally:
            pr.kill()
            pr.wait()
        gc.collect()
    leaks = [w for w in seen if issubclass(w.category, ResourceWarning) and ".lock" in str(w.message)]
    assert leaks == []


def test_the_repair_gives_the_new_file_the_old_owner_where_it_can(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    open(p, "ab").write(b'{"half')
    st = os.stat(p)
    calls = []
    if hasattr(os, "chown"):
        real = os.chown
        monkeypatch.setattr(core.os, "chown", lambda f, u, g: (calls.append((f, u, g)), real(f, u, g)))
    AuditLog.repair_torn_tail(p)
    if hasattr(os, "chown"):
        assert len(calls) == 1 and calls[0][0].startswith(p + ".repair-")
        assert calls[0][1:] == (st.st_uid, st.st_gid)
    else:
        assert calls == []


# ------------------------------------------- the lock, third review
def _no_write_open(real_open, writable_lock_exists):
    """open() as in a folder this process may not write to."""
    def fake(f, mode="r", *a, **k):
        if str(f).endswith(".lock") and ("a" in mode or "w" in mode):
            raise PermissionError(13, "read-only folder", str(f))
        if str(f).endswith(".lock") and not writable_lock_exists:
            raise FileNotFoundError(2, "no lock file", str(f))
        return real_open(f, mode, *a, **k)
    return fake


@pytest.mark.parametrize("lock_file_there", [True, False])
def test_a_log_in_a_folder_you_cannot_write_can_still_be_checked_and_read(tmp_path, monkeypatch, lock_file_there):
    import builtins
    from dispatcher import core
    p = three(tmp_path)
    monkeypatch.setattr(core, "open", _no_write_open(builtins.open, lock_file_there), raising=False)
    assert AuditLog.check_file(p) == {"ok": True, "entries": 3, "break_at": None}
    assert len(AuditLog.reader(p).read()) == 3
    with pytest.raises(PermissionError):                 # writing there still needs the lock
        AuditLog(p).append("x", {})


def test_two_instances_in_one_process_never_wait_on_each_other(tmp_path, monkeypatch):
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.5)
    a, b = AuditLog(p), AuditLog(p)
    with a.holding():
        b.append("inside", {})                           # same thread, other instance
        assert AuditLog.check_file(p)["ok"]
    assert AuditLog.check_file(p) == {"ok": True, "entries": 4, "break_at": None}


def test_another_thread_waits_for_the_holder_in_this_process(tmp_path):
    import threading
    p = three(tmp_path)
    a, b = AuditLog(p), AuditLog(p)
    order = []
    with a.holding():
        t = threading.Thread(target=lambda: (b.append("t", {}), order.append("thread wrote")))
        t.start()
        t.join(0.3)
        order.append("holder done")
    t.join(10)
    assert order == ["holder done", "thread wrote"]


SHARED = ("import sys, time; sys.path.insert(0, sys.argv[1]);"
          "from dispatcher.core import _FileLock;"
          "l = _FileLock(sys.argv[2], shared=True); l.__enter__(); print('held', flush=True); time.sleep(5)")


def test_readers_share_the_lock_and_a_writer_waits_for_them(tmp_path, monkeypatch):
    from dispatcher import core
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.3)
    pr = subprocess.Popen([sys.executable, "-c", SHARED, ROOT, p], stdout=subprocess.PIPE, text=True)
    try:
        assert pr.stdout.readline().strip() == "held"
        if os.name == "nt":                              # Windows has no shared lock: readers wait too
            with pytest.raises(core.AuditLogBusy):
                AuditLog.reader(p).read()
        else:
            r = AuditLog.reader(p)                       # other readers: no wait
            assert len(r.read()) == 3 and r.verify_chain()["ok"]
            assert r.anchor()["entries"] == 3
            assert r.verify_anchor({"entries": 0, "head_hash": AuditLog.GENESIS})["ok"]
        with pytest.raises(core.AuditLogBusy):           # a writer waits for the reader
            AuditLog(p)
    finally:
        pr.kill()
        pr.wait()


def test_a_write_inside_a_read_this_process_holds_takes_the_lock_for_writing(tmp_path):
    p = three(tmp_path)
    log = AuditLog(p)
    probe = ("import sys; sys.path.insert(0, sys.argv[1]);"
             "from dispatcher.core import _FileLock, AuditLogBusy;"
             "l = _FileLock(sys.argv[2], 0.3, shared=True)\n"
             "try:\n    l.__enter__(); print('read allowed')\nexcept AuditLogBusy:\n    print('kept out')")
    import gc
    import warnings
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        with log._guard(shared=True):
            log.append("inside a read", {})
            r = subprocess.run([sys.executable, "-c", probe, ROOT, p], capture_output=True, text=True,
                               timeout=30)
            assert r.stdout.strip() == "kept out"      # exclusive now: no reader while writing
        gc.collect()
    assert [w for w in seen if issubclass(w.category, ResourceWarning) and ".lock" in str(w.message)] == []
    assert AuditLog.check_file(p) == {"ok": True, "entries": 4, "break_at": None}


def test_a_write_inside_an_unlocked_read_in_a_read_only_folder_is_refused(tmp_path, monkeypatch):
    import builtins
    from dispatcher import core
    p = three(tmp_path)
    log = AuditLog(p)
    os.remove(p + ".lock")
    monkeypatch.setattr(core, "open", _no_write_open(builtins.open, False), raising=False)
    with log._guard(shared=True):                        # read: no lock file, none can be made
        with pytest.raises(PermissionError):
            log.append("x", {})                          # a write needs the lock


def test_a_read_through_a_read_only_lock_file_still_waits_for_a_writer(tmp_path, monkeypatch):
    import builtins
    from dispatcher import core
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.3)
    pr = held_by_another_process(p)                     # a writer elsewhere holds it
    try:
        monkeypatch.setattr(core, "open", _no_write_open(builtins.open, True), raising=False)
        with pytest.raises(core.AuditLogBusy):
            AuditLog.reader(p).read()
    finally:
        pr.kill()
        pr.wait()


def test_a_repair_from_inside_this_processs_lock_does_not_wait_on_itself(tmp_path, monkeypatch):
    p = three(tmp_path)
    monkeypatch.setattr(AuditLog, "LOCK_WAIT_S", 0.5)
    log = AuditLog(p)
    open(p, "ab").write(b'{"half')
    with log.holding():
        rec = AuditLog.repair_torn_tail(p)
    assert rec["complete_entries"] == 3 and AuditLog.check_file(p)["ok"]


# ---------------------------------------------- recheck before merge, 2026-10-13
def test_an_earlier_break_is_named_before_a_cut_off_end(tmp_path):
    # edited at line 2 AND cut off at the end: the first problem is line 2,
    # and the error must not send a person to a repair that will refuse
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[1] = lines[1].replace(b'"n": 1', b'"n": 8')
    open(p, "wb").write(b"\n".join(lines) + b'{"half')
    for opener in (lambda: AuditLog(p), lambda: AuditLog.reader(p).read()):
        with pytest.raises(AuditLogDamaged) as ei:
            opener()
        assert ei.value.line == 2 and ei.value.torn_tail is False
        assert "does not link" in ei.value.reason and "repair_torn_tail" not in str(ei.value)
    with pytest.raises(ValueError, match="chain breaks at line 2"):
        AuditLog.repair_torn_tail(p)


def test_entries_are_counted_not_numbered_when_the_file_has_blank_lines(tmp_path):
    p = three(tmp_path)
    data = raw(p)                                        # read before opening for write
    open(p, "wb").write(b"\n\n" + data.replace(b"\n", b"\n\n"))
    log = AuditLog.reader(p)
    assert log.verify_chain() == {"ok": True, "entries": 3, "break_at": None}
    assert log.anchor()["entries"] == 3
    open(p, "ab").write(b'{"half')
    c = AuditLog.check_file(p)
    assert c["entries"] == 4 and c["break_at"] == raw(p).count(b"\n") + 1


def test_a_link_break_counts_the_entries_up_to_it(tmp_path):
    p = three(tmp_path)
    lines = raw(p).split(b"\n")
    lines[1] = lines[1].replace(b'"n": 1', b'"n": 6')
    open(p, "wb").write(b"\n\n".join(lines))            # blank lines between, too
    c = AuditLog.check_file(p)
    assert c["entries"] == 2 and c["break_at"] == 3 and "does not link" in c["reason"]


def test_reopening_a_clean_log_adds_no_blank_lines(tmp_path):
    p = three(tmp_path)
    for _ in range(3):
        AuditLog(p).append("again", {})
    assert b"\n\n" not in raw(p) and raw(p).count(b"\n") == 6
