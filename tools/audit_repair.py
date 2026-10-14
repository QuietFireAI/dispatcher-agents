"""audit_repair - check an audit log, and recover from a write that was cut off.

Added 2026-10-13 (repair plan 1.2, owner decision D4). A process that dies
part-way through writing an audit line leaves a partial last line. The hub
then refuses to open the log (AuditLogDamaged names the line) rather than
guess. This tool is the person's way back:

  python3 tools/audit_repair.py <audit.jsonl> --check
      Reads only. Prints the chain check as JSON: ok, entries, break_at,
      and on a break the reason and whether it is a cut-off last write.
      Exit 0 when the chain is intact, 1 when it is not.

  python3 tools/audit_repair.py <audit.jsonl>
      Repairs a cut-off last write ONLY: the partial line is moved to a
      side file next to the log (<log>.torn-<UTC time>-<sha12>), every
      complete entry is kept, and an audit.tail_quarantined entry records
      the repair on the chain. Anything else - a damaged or edited line
      before the last one, a broken chain - is refused and nothing changes.
      Exit 0 when repaired, 2 when refused.

Stop whatever process writes to the log (a console session, a desk) first.
The repair takes the log's lock (<log>.lock), so it never runs in the
middle of another process's write; if the old log cannot be replaced
(Windows, another program holding it open) nothing changes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dispatcher.core import AuditLog, AuditLogBusy, AuditLogDamaged  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", help="path to the audit log (.jsonl)")
    ap.add_argument("--check", action="store_true", help="check only; change nothing")
    args = ap.parse_args(argv)
    if not os.path.exists(args.log):
        print(json.dumps({"error": f"{args.log}: no such file"}))
        return 2
    if args.check:
        r = AuditLog.check_file(args.log)
        print(json.dumps(r))
        return 0 if r["ok"] else 1
    try:
        rec = AuditLog.repair_torn_tail(args.log)
    except (AuditLogDamaged, AuditLogBusy, ValueError) as e:
        print(json.dumps({"repaired": False, "refused": str(e)}))
        return 2
    except OSError as e:      # e.g. Windows: another program holds the log open
        print(json.dumps({"repaired": False, "changed": "nothing",
                          "refused": f"the repaired log could not replace the old one: {e!r}; "
                                     f"stop every program using it and run this again"}))
        return 2
    print(json.dumps({"repaired": True, **rec,
                      "chain": AuditLog.check_file(args.log)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
