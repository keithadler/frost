"""The one performance claim the design rests on.

The README argues frost can afford to be verbose because a shell's runtime is
dominated by fork/exec rather than by parsing. Everything else in the language
— the long keywords, the closed vocabulary, the fact that `--explain` re-walks
the tree — is spent against that budget. If parsing ever became the expensive
half, the argument would be in trouble.

So it is measured rather than asserted. These are deliberately loose: a shared
CI runner is a noisy place, and a test that fails when a neighbouring job gets
busy teaches people to ignore it. The margin here is wide enough that only a
real regression — an accidentally quadratic parser, say — would trip it.
"""

import os
import statistics
import subprocess
import sys
import time

import pytest

from frostlang.parser import parse
from frostlang.audit import audit, find_dangers

from helpers import EXAMPLES, example, example_names

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))
import benchmark


def largest_example():
    return max((example(n) for n in example_names()), key=len)


def test_the_front_end_is_cheaper_than_starting_one_process():
    """The claim, directly. Parsing *and* auditing the biggest example must
    cost less than a single fork+exec of the cheapest program there is."""
    spawn = benchmark.spawn_cost(repeats=15)
    if spawn is None:                                     # pragma: no cover
        pytest.skip("no 'true' on PATH to compare against")

    source = largest_example()
    tree = parse(source)
    front_end = benchmark.median_seconds(
        lambda: find_dangers(audit(parse(source))), 30)

    assert front_end < spawn, (
        f"the front end now costs {front_end * 1e6:.0f}us against "
        f"{spawn * 1e6:.0f}us to spawn a process. The README's argument for "
        f"verbosity assumes the opposite.")


def test_parsing_is_roughly_linear_in_script_length():
    """An accidentally quadratic parser would still pass the test above on
    small examples and fall over on a real script."""
    unit = 'put "alpha beta gamma" into line one\n'
    small = unit * 200
    large = unit * 800                       # four times the work

    def cost(source):
        return benchmark.median_seconds(lambda: parse(source), 9)

    ratio = cost(large) / cost(small)
    assert ratio < 8, (
        f"parsing 4x the lines took {ratio:.1f}x the time, which is not "
        f"linear enough to be an accident")


def test_a_long_script_parses_promptly():
    source = 'put "x" into a\n' * 5000
    start = time.perf_counter()
    parse(source)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"5,000 statements took {elapsed:.1f}s"


def test_deeply_nested_chunks_do_not_blow_up():
    """Chunk expressions nest, and nesting is where a naive evaluator goes
    exponential."""
    expr = "it"
    for _ in range(40):
        expr = f"the first word of {expr}"
    start = time.perf_counter()
    parse(f"put {expr}")
    assert time.perf_counter() - start < 2.0


def test_the_benchmark_tool_runs():
    """It is documentation that executes, so it has to keep executing."""
    result = subprocess.run(
        [sys.executable, os.path.join(benchmark.HERE, "tools",
                                      "benchmark.py")],
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    assert "fork+exec" in result.stdout


def test_every_example_is_measured_by_it():
    """A benchmark that quietly stopped covering half the examples would
    still print a confident table."""
    names = [n for n in os.listdir(EXAMPLES) if n.endswith(".frost")]
    result = subprocess.run(
        [sys.executable, os.path.join(benchmark.HERE, "tools",
                                      "benchmark.py")],
        capture_output=True, text=True, timeout=300)
    for name in names:
        assert name in result.stdout, f"{name} is not in the benchmark"
