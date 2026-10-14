"""The test counts the docs state match the suite (repair plan Phase 3,
2026-10-14). Stale counts shipped more than once (89, 132/137, 198, ...);
now a wrong number fails CI instead.

What is checked: the total number of tests (README quickstart, SKILL.md,
PILLAR_TESTING_MANUAL.md) equals what pytest collects, and each "N passed +
K skipped" adds up to that total."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collected():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p",
                          "no:cacheprovider", "tests/"], cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    assert m, out[-500:]
    return int(m.group(1))


def text(name):
    return open(os.path.join(ROOT, name), encoding="utf-8").read()


def test_the_documented_test_counts_match_the_suite():
    total = collected()
    readme = int(re.search(r"# (\d+) tests vs the real 52-route track", text("README.md")).group(1))
    alone, skipped, full = map(int, re.search(
        r"# (\d+) passed \+ (\d+) skipped with no listing-assistants clone beside this repo; "
        r"(\d+) passed with one", text("SKILL.md")).groups())
    m_alone, m_skipped = map(int, re.search(r"EXPECTED: (\d+) passed, (\d+) skipped\.",
                                            text("PILLAR_TESTING_MANUAL.md")).groups())
    m_full = int(re.search(r"then (\d+) passed, as", text("PILLAR_TESTING_MANUAL.md")).group(1))
    assert readme == full == m_full == total, (readme, full, m_full, total)
    assert alone + skipped == total and (m_alone, m_skipped) == (alone, skipped)
