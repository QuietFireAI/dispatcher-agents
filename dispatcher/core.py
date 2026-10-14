"""dispatcher.core - the dispatcher message core (Day 1).

Doctrine encoded as executable behavior:
- An ack is a factual claim: issued only after persist (audit) AND delivery.
- The (from -> intent -> to) tuple is enforced at runtime - the track is closed.
- envelope_id is the idempotency key; duplicates process once.
- sequence is hub-assigned per client_context_id; hub is the single writer.
- Authority intents require a verified signature; sender fields are forgeable.
- Unroutable / ambiguous traffic HOLDS live (restricted-speed), never drops.
Spec sources: DISPATCHER_CORE.md, 00-dispatcher/SKILL.md, SWARM.md v0.16.
"""
from __future__ import annotations
import contextlib
import hashlib
import json, os, threading, time, uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

CONFIDENCE = {"source_verified", "stated_by_party", "unknown"}
SPECIAL = {"human", "external", "queue", "any"}

# --- ABSOLUTE SIGNAL, disclosure half -------------------------------------
# Every route that leaves the swarm to `external` declares WHO is on the far
# end. The class decides whether an outbound message may pass or must hold for
# a human. There is no fourth option: a route to `external` that names no
# audience is not waved through - it fails closed to the most restricted class.
AUDIENCE = {"principal", "counterparty", "public"}

# The principal is the one party the identity serves. Sending to the principal
# is disclosure to the person whose information it already is - never gated.
# `counterparty` (the other side) and `public` (an open feed) are where a
# position leaks, so both hold for HITL in this strict first version.
AUDIENCE_GATED = {"counterparty", "public"}

# Fail-closed default: an external route with no declared audience is treated
# as counterparty. Absence of a claim is never read as the permissive state.
AUDIENCE_DEFAULT = "counterparty"


@dataclass
class Envelope:
    from_agent: str
    to_agent: str
    intent: str
    client_context_id: str
    payload: dict
    provenance: dict
    confidence: str = "unknown"
    escalation_flag: bool = False
    in_reply_to: Optional[str] = None
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sequence: Optional[int] = None  # hub-assigned; senders submit None
    signature: Optional[str] = None

    def validate_schema(self) -> list[str]:
        errs = []
        for f in ("from_agent", "to_agent", "intent", "client_context_id"):
            if not getattr(self, f):
                errs.append(f"missing field: {f}")
        if self.confidence not in CONFIDENCE:
            errs.append(f"illegal confidence: {self.confidence!r}")
        if self.sequence is not None:
            errs.append("sequence is hub-assigned; senders must submit None")
        if not isinstance(self.provenance, dict) or "source" not in self.provenance:
            errs.append("provenance.source required")
        return errs

    def to_record(self) -> dict:
        return {k: getattr(self, k) for k in (
            "envelope_id", "from_agent", "to_agent", "intent", "in_reply_to",
            "sequence", "client_context_id", "payload", "provenance",
            "confidence", "escalation_flag")}


class Routes:
    """The closed track. Loaded from an identity side-load (routes.json)."""

    def __init__(self, path: str):
        data = json.load(open(path, encoding="utf-8"))
        self.version = data.get("version", "?")
        self.entries = [(r["intent"], set(r["senders"]), set(r["receivers"]))
                        for r in data["routes"]]
        # Audience metadata rides alongside, keyed by intent, and only for
        # routes that actually target `external`. Keeping it in a separate map
        # leaves entries/matches/tuple_legal byte-for-byte unchanged so no
        # existing route logic or test can shift under this.
        self.audience: dict[str, str] = {}
        self.audience_verified: dict[str, bool] = {}
        for r in data["routes"]:
            if "external" in r.get("receivers", []):
                # fail closed: unlabeled external route -> counterparty
                aud = r.get("audience", AUDIENCE_DEFAULT)
                if aud not in AUDIENCE:
                    aud = AUDIENCE_DEFAULT
                self.audience[r["intent"]] = aud
                # a label is a claim about behavior; until a real playbook run
                # confirms where the message lands, the claim is provisional.
                self.audience_verified[r["intent"]] = bool(
                    r.get("audience_verified", False))

    def audience_for(self, intent: str) -> Optional[str]:
        """Audience class for an external-bound intent, or None if the route
        does not leave to `external`. Fail-closed at the source: an external
        route we have no record for is treated as the guarded default, never
        as ungated."""
        for i in self.audience:
            if i == intent or (i.endswith(".*") and intent.startswith(i[:-1])):
                return self.audience[i]
        return None

    def audience_is_verified(self, intent: str) -> bool:
        for i in self.audience_verified:
            if i == intent or (i.endswith(".*") and intent.startswith(i[:-1])):
                return self.audience_verified[i]
        return False

    def matches(self, intent: str):
        for i, s, r in self.entries:
            if i == intent or (i.endswith(".*") and intent.startswith(i[:-1])):
                yield s, r

    def tuple_legal(self, frm: str, intent: str, to: str) -> bool:
        for s, r in self.matches(intent):
            # 2026-10-13: two further clauses, (frm == "human" and "human" in s)
            # and (to in SPECIAL and to in r), were removed: each implied the
            # membership test before it, so neither could change the answer
            # (checked over every receiver set; findings/MUTATION_SWEEP_2026-10-13.md)
            frm_ok = frm in s or "any" in s
            to_ok = to in r or "any" in r
            if frm_ok and to_ok:
                return True
        return False


class AuditLogDamaged(Exception):
    """The audit log has a line that is not a readable entry (added
    2026-10-13, repair plan 1.2; owner decision D4: refuse, name the line,
    and say how to recover - never clean up silently).

    torn_tail is True when the unreadable line is the LAST one and the file
    does not end with a newline: the signature of a write cut off part-way
    (a crash or power loss during append). Only that case has a recovery
    path, AuditLog.repair_torn_tail(path), which a person runs. Any other
    unreadable line is damage or tampering and has no automatic repair."""

    def __init__(self, path: str, line, reason: str, torn_tail: bool):
        self.path, self.line, self.reason, self.torn_tail = path, line, reason, torn_tail
        if torn_tail:
            how = ("this looks like a write that was cut off (for example by a "
                   "power loss). To recover, stop every process using the log; "
                   "then a person runs AuditLog.repair_torn_tail(path) "
                   "(dispatcher-agents: tools/audit_repair.py; a product built "
                   "on this core may give it its own command): it moves "
                   "that partial line to a side file, keeps every complete "
                   "entry, and records the repair on the chain")
        else:
            how = ("this is not a cut-off last write; nothing repairs it "
                   "automatically. Keep the file as it is and investigate")
        where = f"at line {line}" if line else "(no single line)"
        super().__init__(f"audit log {path} is damaged {where}: {reason}. "
                         f"Nothing was written. {how}.")


def _log_lines(path: str):
    """The file's lines as (line_number, bytes), 1-based, blank lines
    skipped, read as bytes so a cut-off multi-byte character cannot stop the
    read. Also returns whether the file ends with a newline."""
    with open(path, "rb") as f:
        data = f.read()
    out = []
    for i, raw in enumerate(data.split(b"\n"), 1):
        if raw.strip():
            out.append((i, raw.rstrip(b"\r")))
    return out, data.endswith(b"\n") or not data


def _fsync_dir(d: str) -> None:
    """Make a rename in folder d durable. POSIX only: Windows cannot open a
    folder to sync it, and its rename is already ordered with the data."""
    if os.name == "nt":
        return
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _parse(raw: bytes):
    """One log line as a dict, or None if it is not a readable entry."""
    try:
        e = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return e if isinstance(e, dict) else None


class AuditLogBusy(RuntimeError):
    """Another process held the log's lock for longer than the wait allows."""


class _FileLock:
    """An operating-system lock on <log>.lock, so processes take turns with
    the log (owner decision 2026-10-13, after the cold review: two processes
    appending at the same instant forked the chain). POSIX: flock, shared
    for reads and exclusive for writes; Windows: msvcrt.locking on the first
    byte, exclusive for both. The lock file is never removed. A process
    waits up to `timeout` seconds, then AuditLogBusy is raised and nothing
    is written.

    Reading needs no write access (third review): if the lock file cannot be
    opened for writing, a read opens it read-only, and if it cannot be
    opened at all (absent in a read-only folder) the read goes ahead
    unlocked - nothing can be writing to a log there through this class."""

    def __init__(self, path: str, timeout: float = 30.0, shared: bool = False):
        self.path, self.timeout, self.shared = path + ".lock", timeout, shared
        self.f, self.exclusive = None, False

    def __enter__(self):
        if not os.path.isdir(os.path.dirname(os.path.abspath(self.path))):
            return                           # no folder: nothing to read or write yet
        try:
            self.f = open(self.path, "a+b")
        except OSError:
            if not self.shared:
                raise
            try:
                self.f = open(self.path, "rb")
            except OSError:
                return                       # unlockable, read-only: read without a lock
        self._acquire(exclusive=os.name == "nt" or not self.shared)

    def _acquire(self, exclusive: bool) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(),
                                (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                self.exclusive = exclusive
                return
            except OSError:
                if time.monotonic() > deadline:
                    self.f.close()
                    self.f = None
                    raise AuditLogBusy(f"{self.path}: held by another program for more "
                                       f"than {self.timeout:.0f} s - nothing was written")
                time.sleep(0.02)

    def upgrade(self) -> None:
        """A write inside a read this process already holds: make the lock
        exclusive (POSIX converts the flock; Windows already holds it so)."""
        if self.exclusive:
            return
        if self.f is None:
            self.f = open(self.path, "a+b")
        self._acquire(exclusive=True)

    def __exit__(self, *exc):
        if self.f is None:
            return
        try:
            if os.name == "nt":                # Windows: unlock before closing
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            # POSIX: closing the file releases its flock
        finally:
            self.f.close()
            self.f = None


class _Held:
    """What this process holds on one log file: shared by every AuditLog
    instance on that file, so two instances in one process never wait on
    each other (third review), and threads take turns through the RLock."""

    def __init__(self):
        self.rlock = threading.RLock()
        self.flock = None


_PROCESS_HELD: dict = {}
_PROCESS_HELD_LOCK = threading.Lock()


class AuditLog:
    """Append-only, HASH-CHAINED JSONL. Persist happens BEFORE delivery; the
    log is the single source of truth for KPIs - no self-reported metrics
    exist.

    Chain doctrine (login-based signer decision, 2026-07-11): every entry
    carries prev_hash (the entry_hash of the line before it, or GENESIS) and
    entry_hash (sha256 of this entry's canonical content + prev_hash).
    Editing, deleting, or reordering any line breaks every hash after it -
    integrity and non-repudiation of the record come from this chain, not
    from trust in the file."""

    GENESIS = "GENESIS"
    LOCK_WAIT_S = 30.0          # how long a writer waits for another process's lock

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._guard():
            self._prev = self._recover_tip()
            self._stamp = self._file_stamp()

    @classmethod
    def reader(cls, path: str) -> "AuditLog":
        """A read-only view of a log: read(), verify_chain(), anchor checks.
        Opens nothing for writing and works on a damaged log (the
        constructor refuses one)."""
        o = cls.__new__(cls)
        o.path = path
        o._prev, o._stamp, o._read_only = cls.GENESIS, None, True
        return o

    @contextlib.contextmanager
    def _guard(self, shared: bool = False):
        """Threads of this process one at a time, other processes through the
        OS lock (shared for reads, exclusive for writes). Re-entrant: a call
        made while this process already holds the lock on this file - from
        any AuditLog instance on it - does not take it again, and a write
        inside a read upgrades it."""
        key = os.path.realpath(self.path)
        with _PROCESS_HELD_LOCK:
            h = _PROCESS_HELD.setdefault(key, _Held())
        with h.rlock:
            if h.flock is not None:
                if not shared:
                    h.flock.upgrade()
                yield
                return
            fl = _FileLock(self.path, self.LOCK_WAIT_S, shared=shared)
            with fl:
                h.flock = fl
                try:
                    yield
                finally:
                    h.flock = None

    def holding(self):
        """Hold the log's lock across several steps, e.g. check it, write a
        state store, then record that on the log - so no other process can
        change the log in between (third review)."""
        return self._guard()

    def _file_stamp(self):
        """(size, modification time) of the file, or None if it is absent."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_size, st.st_mtime_ns)

    def ensure_intact(self) -> None:
        """Raise AuditLogDamaged now, writing nothing, if the next append
        would refuse. For a caller about to write something ELSE (a state
        store) that the log must then record: check first, so the other
        write never happens without its record (2026-10-13 cold review: a
        desk stopped after damage saved a snapshot the log never recorded)."""
        with self._guard():
            if self._file_stamp() != self._stamp:
                self._resync()
                self._stamp = self._file_stamp()

    def _resync(self) -> None:
        """The file's size or modification time is not what this process
        left: someone else wrote, cut, edited or replaced it (found by the
        2026-10-13 cold review: a desk kept appending to a log damaged under
        it, and a second writer forked the chain). Append again only if the
        whole chain verifies AND still holds this process's last entry; then
        continue from the file's real tip. Otherwise raise AuditLogDamaged
        and write nothing.

        Writers that use this class take the OS lock, so they never append
        at the same instant. An edit that keeps both the size and the
        modification time is not seen here; verify_chain() still finds it."""
        chain = self.verify_chain()
        if not chain["ok"]:
            raise AuditLogDamaged(
                self.path, chain["break_at"],
                f"{chain['reason']} (the file changed since this process last wrote it)",
                torn_tail=chain["torn_tail"])
        if self._prev != self.GENESIS and (
                not os.path.exists(self.path)
                or self._prev not in {(_parse(r) or {}).get("entry_hash")
                                      for _, r in _log_lines(self.path)[0]}):
            raise AuditLogDamaged(
                self.path, None, "entries this process wrote are no longer in the "
                "file - it was cut back, replaced or deleted while in use",
                torn_tail=False)
        self._prev = self._recover_tip()

    def _recover_tip(self) -> str:
        """Resume the chain across process restarts: tip = last entry_hash.
        An unreadable line raises AuditLogDamaged naming it (2026-10-13; it
        used to raise a bare JSONDecodeError). A complete last entry that
        only lost its newline is kept, and the next append starts a new line."""
        self._needs_newline = False
        if not os.path.exists(self.path):
            return self.GENESIS
        lines, ends_nl = _log_lines(self.path)
        tip = self.GENESIS
        for n, (i, raw) in enumerate(lines):
            e = _parse(raw)
            if e is None:
                raise self._first_problem()
            tip = e.get("entry_hash", tip)
        self._needs_newline = bool(lines) and not ends_nl
        return tip

    def _first_problem(self) -> "AuditLogDamaged":
        """The error for the FIRST break in the file, not merely the first
        unreadable line (recheck before merge, 2026-10-13: a log edited at
        line 2 and cut off at its end was reported as "cut off, run the
        repair", and the repair then refused because of line 2). A cut-off
        last write is reported as one only when every line before it
        verifies - exactly when the repair will accept it."""
        chain = self._verify_chain()
        return AuditLogDamaged(self.path, chain["break_at"], chain["reason"],
                               torn_tail=chain["torn_tail"])

    @classmethod
    def check_file(cls, path: str) -> dict:
        """verify_chain() for a log that may be too damaged to open for
        writing (AuditLog(path) refuses a damaged file). Reads only."""
        return cls.reader(path).verify_chain()

    @classmethod
    def repair_torn_tail(cls, path: str) -> dict:
        """Recovery for a write cut off part-way - run by a person, never
        automatically (owner decision D4, 2026-10-13). Refuses (raises
        AuditLogDamaged or ValueError, changing nothing) unless ALL of:
          - the bytes after the last newline are not a readable entry, and
          - every line before them is readable and the chain over them verifies.
        Then: the partial bytes are written to a new side file
        <path>.torn-<UTC time>-<sha12> (flushed to disk first), and the log is
        replaced in one step (os.replace) by every complete entry, byte for
        byte, followed by an audit.tail_quarantined entry (line, byte count,
        sha256 of the partial bytes, side file), so the repair itself is on
        the chain. Returns that record.

        Stop every process using the log first. The repair takes the log's
        OS lock, so it never runs during another process's write; a process
        that still holds the log open sees the file changed at its next
        append, finds the chain intact with its own entries in it, and
        continues after the repair entry (AuditLog._resync). The repaired
        file keeps the old one's permissions, and its owner where this
        process may set it. If the swap fails, the log is unchanged and the
        side file is removed again. The repair cannot tell a crash from
        someone cutting the log back and adding a partial line: an anchor
        saved earlier (anchor() / verify_anchor()) is what shows that."""
        if not os.path.exists(path):
            raise ValueError(f"{path}: no such audit log")
        with cls.reader(path)._guard():          # exclusive, shared with this process's holders
            return cls._repair_torn_tail(path)

    @classmethod
    def _repair_torn_tail(cls, path: str) -> dict:
        with open(path, "rb") as f:
            data = f.read()
        cut = data.rfind(b"\n") + 1           # start of the bytes after the last newline
        tail = data[cut:]
        if not tail.strip():
            raise ValueError(f"{path}: the file ends with a complete line - "
                             f"there is no cut-off write to repair")
        if _parse(tail.rstrip(b"\r")) is not None:
            raise ValueError(f"{path}: the last line is a complete entry that only "
                             f"lacks its newline - nothing to repair; the log opens as is")
        prefix = data[:cut]
        prev = cls.GENESIS
        for i, raw in enumerate(prefix.split(b"\n"), 1):
            if not raw.strip():
                continue
            e = _parse(raw.rstrip(b"\r"))
            if e is None:
                raise AuditLogDamaged(path, i, "not a readable entry (before the "
                                      "last line)", torn_tail=False)
            body = {k: v for k, v in e.items() if k not in ("prev_hash", "entry_hash")}
            if e.get("prev_hash") != prev or e.get("entry_hash") != cls._entry_hash(body, prev):
                raise ValueError(f"{path}: the chain breaks at line {i}, before the "
                                 f"cut-off line - this is not only a cut-off write; "
                                 f"nothing was changed")
            prev = e["entry_hash"]
        torn_line = prefix.count(b"\n") + 1
        digest = hashlib.sha256(tail).hexdigest()
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        side = f"{path}.torn-{stamp}-{digest[:12]}"
        k = 1
        while os.path.exists(side):            # never overwrite an earlier side file
            k += 1
            side = f"{path}.torn-{stamp}-{digest[:12]}-{k}"
        with open(side, "xb") as f:
            f.write(tail)
            f.flush()
            os.fsync(f.fileno())
        rec = {"line": torn_line, "bytes": len(tail), "sha256": digest,
               "side_file": os.path.basename(side),
               "complete_entries": sum(1 for r in prefix.split(b"\n") if r.strip())}
        # One step (2026-10-13 cold review): the repaired log - every complete
        # entry byte for byte, then the entry recording this repair - is
        # written to a new file and swapped in with os.replace. A crash leaves
        # either the old log or the repaired one, never a cut log without the
        # record of why it was cut.
        body = {**rec, "ts": time.time(), "kind": "audit.tail_quarantined"}
        entry = json.dumps({**body, "prev_hash": prev,
                            "entry_hash": cls._entry_hash(body, prev)})
        nl = b"\r\n" if prefix.endswith(b"\r\n") else b"\n"
        tmp = f"{path}.repair-{stamp}-{digest[:12]}"
        try:
            with open(tmp, "wb") as f:
                f.write(prefix + entry.encode("utf-8") + nl)
                f.flush()
                os.fsync(f.fileno())
            st = os.stat(path)
            os.chmod(tmp, st.st_mode & 0o7777)
            if hasattr(os, "chown"):
                try:
                    os.chown(tmp, st.st_uid, st.st_gid)
                except PermissionError:
                    pass                     # not allowed to give it away: kept as ours
            os.replace(tmp, path)
        except BaseException:
            for leftover in (tmp, side):     # nothing changed: leave nothing behind
                if os.path.exists(leftover):
                    os.remove(leftover)
            raise
        _fsync_dir(os.path.dirname(os.path.abspath(path)))
        return rec

    RESERVED = ("ts", "kind", "prev_hash", "entry_hash")  # framing fields the log owns, never the caller

    @staticmethod
    def _entry_hash(body: dict, prev: str) -> str:
        canon = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((prev + canon).encode()).hexdigest()

    def append(self, kind: str, record: dict) -> str:
        # Framing wins: a caller-supplied record can never shadow the log's own
        # ts/kind/chain fields. Without this, any splatted untrusted dict (see
        # Hub.escalate) could forge or erase an event kind - the audit log is
        # the single source of truth, so its framing is not caller-writable.
        if getattr(self, "_read_only", False):
            raise TypeError("AuditLog.reader() is read-only; open the log with AuditLog(path) to append")
        with self._guard():
            if self._file_stamp() != self._stamp:
                self._resync()
            body = {**record, "ts": time.time(), "kind": kind}
            for k in ("prev_hash", "entry_hash"):
                body.pop(k, None)
            eh = self._entry_hash(body, self._prev)
            line = json.dumps({**body, "prev_hash": self._prev, "entry_hash": eh})
            with open(self.path, "a", encoding="utf-8") as f:
                if self._needs_newline:          # last entry lost only its newline
                    f.write("\n")
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._stamp = self._file_stamp()   # after close: the final size and time
            self._needs_newline = False
            self._prev = eh
            return eh

    def anchor(self) -> dict:
        """Export an external anchor: {entries, head_hash}. verify_chain
        detects tamper WITHIN the file, but wholesale deletion and
        regeneration from a fresh GENESIS is undetectable from inside -
        the regenerated chain is internally consistent. An anchor stored
        OUTSIDE the file (separate store, signed commit message, printed
        in a handoff) closes that: a regenerated log cannot reproduce the
        anchored head hash at the anchored position. Gap named in the
        2026-07-17 review, closed 2026-07-18."""
        with self._guard(shared=True):
            n = len(_log_lines(self.path)[0]) if os.path.exists(self.path) else 0
            return {"entries": n, "head_hash": self._prev}

    def verify_anchor(self, anchor: dict) -> dict:
        with self._guard(shared=True):
            return self._verify_anchor(anchor)

    def _verify_anchor(self, anchor: dict) -> dict:
        """Verify a previously exported anchor against the current file.
        The entry at the anchored position must carry exactly the
        anchored hash, and the chain up to it must verify. Returns
        {ok, reason}. Fail-closed: a malformed anchor is a failure, not
        a pass."""
        n, head = anchor.get("entries"), anchor.get("head_hash")
        if not isinstance(n, int) or n < 0 or not head:
            return {"ok": False, "reason": "malformed anchor - fail closed"}
        if n == 0:
            return {"ok": head == self.GENESIS,
                    "reason": None if head == self.GENESIS
                    else "empty-log anchor must carry GENESIS"}
        chain = self.verify_chain()
        if not chain["ok"] and chain["break_at"] is not None \
                and chain["break_at"] <= n:
            return {"ok": False,
                    "reason": f"chain breaks at line {chain['break_at']}, "
                              f"before the anchored position {n}"}
        if not os.path.exists(self.path):
            return {"ok": False,
                    "reason": f"anchor names {n} entries; log file absent - "
                              f"wholesale deletion"}
        lines = [raw for _, raw in _log_lines(self.path)[0]]
        if len(lines) < n:
            return {"ok": False,
                    "reason": f"anchor names {n} entries; log has only "
                              f"{len(lines)} - the anchored history is gone"}
        e = _parse(lines[n - 1])
        actual = e.get("entry_hash") if e else None
        if actual != head:
            return {"ok": False,
                    "reason": f"entry {n} hash mismatch - the log was "
                              f"regenerated or rewritten past the anchor"}
        return {"ok": True, "reason": None}

    def verify_chain(self) -> dict:
        with self._guard(shared=True):
            return self._verify_chain()

    def _verify_chain(self) -> dict:
        """Walk the file and recompute every link. Returns
        {ok, entries, break_at} - break_at names the first bad line (1-based).
        On a break it also returns "reason" and "torn_tail". Since 2026-10-13
        an unreadable line (a cut-off write, damage) is a named break, never
        an exception."""
        prev, n = self.GENESIS, 0
        if not os.path.exists(self.path):
            return {"ok": True, "entries": 0, "break_at": None}
        lines, ends_nl = _log_lines(self.path)
        for k, (i, raw) in enumerate(lines):
            e = _parse(raw)
            if e is None:
                return {"ok": False, "entries": k + 1, "break_at": i,
                        "reason": "not a readable entry",
                        "torn_tail": k == len(lines) - 1 and not ends_nl}
            body = {key: v for key, v in e.items()
                    if key not in ("prev_hash", "entry_hash")}
            if e.get("prev_hash") != prev or \
               e.get("entry_hash") != self._entry_hash(body, prev):
                return {"ok": False, "entries": k + 1, "break_at": i,
                        "reason": "does not link to the entry before it "
                                  "(edited, deleted or reordered)",
                        "torn_tail": False}
            prev = e["entry_hash"]
            n = k + 1         # entries, not line numbers: blank lines are skipped
        return {"ok": True, "entries": n, "break_at": None}

    def read(self) -> list[dict]:
        with self._guard(shared=True):
            return self._read()

    def _read(self) -> list[dict]:
        """Every entry. An unreadable line raises AuditLogDamaged naming it
        (2026-10-13; it used to raise a bare JSONDecodeError)."""
        if not os.path.exists(self.path):
            return []
        lines, ends_nl = _log_lines(self.path)
        out = []
        for k, (i, raw) in enumerate(lines):
            e = _parse(raw)
            if e is None:
                raise self._first_problem()
            out.append(e)
        return out
