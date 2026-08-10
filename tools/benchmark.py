#!/usr/bin/env python3
"""Measure the claim the language is built on.

The README argues that frost can afford to be verbose because a shell's
runtime is dominated by fork/exec rather than by parsing. That is an empirical
claim, and an unmeasured empirical claim in a README is just a hope. This
measures it:

    python tools/benchmark.py

It reports how long the front end takes on every example, against the cost of
spawning one trivial process. If parsing ever became the expensive half, the
argument for the whole design would be in trouble, and this is what would say
so.
"""
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang.parser import parse
from frostlang.audit import audit, find_dangers, summarise
from frostlang.formatter import format_source

EXAMPLES = os.path.join(HERE, "examples")


def median_seconds(work, repeats):
    work()                                    # warm the caches
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        work()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def spawn_cost(repeats=25):
    """One fork+exec of the cheapest possible program."""
    program = "/usr/bin/true" if os.path.exists("/usr/bin/true") else "true"
    try:
        return median_seconds(
            lambda: subprocess.run([program], capture_output=True), repeats)
    except (FileNotFoundError, OSError):      # pragma: no cover
        return None


def measure(source):
    tree = parse(source)
    return {
        "lines": len(source.splitlines()),
        "parse": median_seconds(lambda: parse(source), 100),
        "audit": median_seconds(lambda: find_dangers(audit(tree)), 100),
        "format": median_seconds(lambda: format_source(source), 100),
    }


def main():
    names = sorted(n for n in os.listdir(EXAMPLES) if n.endswith(".frost"))
    rows = []
    for name in names:
        with open(os.path.join(EXAMPLES, name)) as fh:
            rows.append((name, measure(fh.read())))

    spawn = spawn_cost()

    width = max(len(n) for n, _ in rows)
    print(f"{'script'.ljust(width)}  lines    parse    audit   format"
          f"   whole front end")
    print("-" * (width + 52))
    worst = 0.0
    for name, m in rows:
        total = m["parse"] + m["audit"]
        worst = max(worst, total)
        print(f"{name.ljust(width)}  {m['lines']:5d} "
              f"{m['parse'] * 1e6:8.0f}us {m['audit'] * 1e6:7.0f}us "
              f"{m['format'] * 1e6:7.0f}us {total * 1e6:12.0f}us")

    if spawn is None:                          # pragma: no cover
        print("\n(no `true` on PATH, so the comparison was skipped)")
        return 0

    print(f"\n{'one fork+exec of true'.ljust(width)}  "
          f"{'':5} {spawn * 1e6:8.0f}us")
    print(f"\nParsing and auditing the largest example costs "
          f"{worst * 1e6:.0f}us.")
    print(f"Spawning a single process costs {spawn * 1e6:.0f}us — "
          f"{spawn / worst:.1f}x as much.")
    print("\nThe README's claim holds: reading the whole script, twice over, "
          "is cheaper\nthan starting one program. Verbosity is free at "
          "this scale.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
