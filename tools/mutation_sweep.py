"""mutation_sweep - one mutant at a time over the core files, the full suite
against each (repair plan 1.4, 2026-10-13).

    python tools/mutation_sweep.py --out sweep.jsonl [--jobs 2] [--files core.py hub.py]

Your checkout is never touched: the repo is copied to a temporary folder per
job and mutants are written there. Set IDENTITY_DIR to a listing-assistants
clone so the identity tests run too (the 2026-10-13 sweep did).

Operators (exact source-span replacement from the AST; every mutant compiles):
  CMP   flip one comparison (== !=, < >=, <= >, in / not in, is / is not)
  BOOL  and <-> or
  NOT   drop a `not`
  CONST True <-> False; int n -> n+1 (docstrings skipped)
  IF    negate an if / while / ternary / assert test
  RET   return <value> -> return None
  RAISE raise ... -> pass
  CALL  a bare call statement -> pass

A mutant is KILLED when the suite fails with it in place (pytest -x: the first
failure is enough), SURVIVED when the suite passes, TIMEOUT after 180 s. Each
job first runs the unmutated suite and refuses to start if it fails - a
failing baseline would make every mutant look killed. One JSON line per
mutant goes to --out, with the failing test's name when there is one.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_FILES = ["__init__.py", "absolute_signal.py", "after_action.py", "analysis.py",
              "attestation.py", "core.py", "hub.py", "kpi.py", "loader.py", "notifier.py",
              "pillars.py", "priority.py", "runs.py", "signatures.py",
              "signer_registry.py", "territory.py"]
FLIP = {ast.Eq: "!=", ast.NotEq: "==", ast.Lt: ">=", ast.GtE: "<", ast.Gt: "<=",
        ast.LtE: ">", ast.In: "not in", ast.NotIn: "in", ast.Is: "is not", ast.IsNot: "is"}


def mutants(files):
    out = []
    for f in files:
        src = open(os.path.join(ROOT, "dispatcher", f), "rb").read()   # AST offsets are bytes
        tree = ast.parse(src)
        starts, pos = [0], 0
        for line in src.split(b"\n"):
            pos += len(line) + 1
            starts.append(pos)

        def span(n):
            return (starts[n.lineno - 1] + n.col_offset,
                    starts[n.end_lineno - 1] + n.end_col_offset)

        def seg(n):
            a, b = span(n)
            return src[a:b].decode("utf-8")

        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}

        def add(n, op, rep):
            a, b = span(n)
            out.append({"file": f, "line": n.lineno, "op": op, "start": a, "end": b,
                        "rep": rep, "orig": src[a:b].decode("utf-8")})

        for n in ast.walk(tree):
            if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in FLIP:
                add(n, "CMP", f"({seg(n.left)}) {FLIP[type(n.ops[0])]} ({seg(n.comparators[0])})")
            elif isinstance(n, ast.BoolOp):
                j = " or " if isinstance(n.op, ast.And) else " and "
                add(n, "BOOL", "(" + j.join(f"({seg(v)})" for v in n.values) + ")")
            elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
                add(n, "NOT", f"({seg(n.operand)})")
            elif isinstance(n, ast.Constant) and id(n) not in docs:
                if n.value is True:
                    add(n, "CONST", "False")
                elif n.value is False:
                    add(n, "CONST", "True")
                elif type(n.value) is int:
                    add(n, "CONST", str(n.value + 1))
            if isinstance(n, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                add(n.test, "IF", f"(not ({seg(n.test)}))")
            elif isinstance(n, ast.Return) and n.value is not None and not (
                    isinstance(n.value, ast.Constant) and n.value.value is None):
                add(n, "RET", "return None")
            elif isinstance(n, ast.Raise):
                add(n, "RAISE", "pass")
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                add(n, "CALL", "pass")
    seen, res = set(), []
    for m in out:
        k = (m["file"], m["start"], m["end"], m["rep"])
        if k not in seen:
            seen.add(k)
            res.append({"id": len(res), **m})
    return res


def run_suite(wt, env):
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider",
                            "tests/"], cwd=wt, env=env, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return "timeout", "timeout after 180s", []
    lines = r.stdout.strip().splitlines()
    failed = [l for l in lines if l.startswith(("FAILED", "ERROR"))][:3]
    return r.returncode, (lines[-1] if lines else r.stderr[-200:]), failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--files", nargs="*", default=CORE_FILES)
    args = ap.parse_args(argv)
    ms = mutants(args.files)
    print(json.dumps({"mutants": len(ms), "files": len(args.files), "jobs": args.jobs}), flush=True)
    lock = threading.Lock()
    out = open(args.out, "w", encoding="utf-8")
    errors = []

    def worker(k):
        tmp = tempfile.mkdtemp(prefix=f"mutsweep{k}-")
        wt = os.path.join(tmp, "repo")
        shutil.copytree(ROOT, wt, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.egg-info", "baseline-out", "after-action"))
        env = dict(os.environ, PYTHONPATH=wt, PYTHONDONTWRITEBYTECODE="1")
        try:
            rc, tail, _ = run_suite(wt, env)
            if rc != 0:
                errors.append(f"job {k}: baseline failed: {tail}")
                return
            for m in ms:
                if m["id"] % args.jobs != k:
                    continue
                p = os.path.join(wt, "dispatcher", m["file"])
                src = open(p, "rb").read()
                t0 = time.time()
                try:
                    open(p, "wb").write(src[:m["start"]] + m["rep"].encode("utf-8") + src[m["end"]:])
                    rc, tail, failed = run_suite(wt, env)
                finally:
                    open(p, "wb").write(src)
                status = "survived" if rc == 0 else ("timeout" if rc == "timeout" else "killed")
                with lock:
                    out.write(json.dumps({**{x: m[x] for x in ("id", "file", "line", "op")},
                                          "orig": m["orig"][:200], "rep": m["rep"][:200],
                                          "status": status, "tail": tail, "failed": failed,
                                          "secs": round(time.time() - t0, 1)}) + "\n")
                    out.flush()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(args.jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.close()
    if errors:
        print(json.dumps({"error": errors}))
        return 2
    res = [json.loads(l) for l in open(args.out, encoding="utf-8")]
    counts = {s: sum(1 for r in res if r["status"] == s) for s in ("killed", "survived", "timeout")}
    print(json.dumps({"done": len(res), **counts}))
    return 0 if len(res) == len(ms) else 1


if __name__ == "__main__":
    sys.exit(main())
