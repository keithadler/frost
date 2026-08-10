"""Regression guards on the front end, and nothing more.

There is a temptation to encode the README's argument as a test — "parsing is
cheaper than spawning a process" — and two rounds of CI showed why that does
not work. The cost of `fork`/`exec` swings by platform far more than parsing
does: `true` is about 0.7ms on Linux and 2.4ms on macOS, and `git --version`
is about 1.2ms on Linux and 12ms on macOS. A comparison between the two is a
statement about the machine, not about frost, and it passed here while
failing in CI.

It is also not the comparison that matters. A script is parsed *once* and
spawns commands *every time*, so what the design relies on is that parsing is
a fixed cost, not that it wins a race against a single spawn.

So the numbers live in `tools/benchmark.py`, where a person can read them,
and what is asserted here is only what is stable everywhere: absolute bounds
loose enough that no healthy machine trips them, and a ratio that catches an
accidentally quadratic parser. Timing assertions are skipped under coverage
instrumentation, which slows the interpreter roughly threefold and makes any
number meaningless.
"""

import os
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

# Coverage installs a trace function that slows everything down by several
# times. Measuring under it produces numbers that mean nothing.
instrumented = pytest.mark.skipif(
    sys.gettrace() is not None,
    reason="timings are meaningless under coverage instrumentation")


def largest_example():
    return max((example(n) for n in example_names()), key=len)


@instrumented
def test_the_front_end_is_fast_in_absolute_terms():
    """An 80-line script, parsed and fully audited, in a few milliseconds.

    The bound is generous on purpose: it exists to catch something going
    badly wrong, not to police a machine's speed.
    """
    source = largest_example()
    elapsed = benchmark.median_seconds(
        lambda: find_dangers(audit(parse(source))), 20)
    assert elapsed < 0.05, (
        f"parsing and auditing {len(source.splitlines())} lines took "
        f"{elapsed * 1e3:.0f}ms; it should be single-digit milliseconds")


def test_parsing_is_roughly_linear_in_script_length():
    """A ratio rather than a duration, so it holds on any machine.

    An accidentally quadratic parser would pass every absolute bound above on
    small examples and fall over on a real script.
    """
    unit = 'put "alpha beta gamma" into line one\n'

    def cost(source):
        return benchmark.median_seconds(lambda: parse(source), 9)

    ratio = cost(unit * 800) / cost(unit * 200)
    assert ratio < 8, (
        f"parsing 4x the lines took {ratio:.1f}x the time, which is not "
        f"linear enough to be an accident")


def test_auditing_is_roughly_linear_too():
    unit = 'run "echo" with "x"\n'

    def cost(source):
        tree = parse(source)
        return benchmark.median_seconds(lambda: find_dangers(audit(tree)), 9)

    ratio = cost(unit * 400) / cost(unit * 100)
    assert ratio < 10, (
        f"auditing 4x the commands took {ratio:.1f}x the time")


@instrumented
def test_a_long_script_parses_promptly():
    source = 'put "x" into a\n' * 5000
    start = time.perf_counter()
    parse(source)
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"5,000 statements took {elapsed:.1f}s"


@instrumented
def test_deeply_nested_chunks_do_not_blow_up():
    """Chunk expressions nest, and nesting is where a naive evaluator goes
    exponential."""
    expr = "it"
    for _ in range(40):
        expr = f"the first word of {expr}"
    start = time.perf_counter()
    parse(f"put {expr}")
    assert time.perf_counter() - start < 5.0


# ------------------------------------------------------------ the tool

def run_benchmark():
    return subprocess.run(
        [sys.executable, os.path.join(benchmark.HERE, "tools",
                                      "benchmark.py")],
        capture_output=True, text=True, timeout=600)


def test_the_benchmark_tool_runs():
    """It is documentation that executes, so it has to keep executing."""
    result = run_benchmark()
    assert result.returncode == 0, result.stderr
    assert "fork+exec" in result.stdout


def test_every_example_is_measured_by_it():
    """A benchmark that quietly stopped covering half the examples would
    still print a confident table."""
    result = run_benchmark()
    for name in os.listdir(EXAMPLES):
        if name.endswith(".frost"):
            assert name in result.stdout, f"{name} is not in the benchmark"


def test_the_benchmark_reports_both_baselines():
    """Reporting only `true` is what produced the overstated claim in the
    first place: it is the cheapest process that can exist, and nothing a
    script runs is that cheap."""
    out = run_benchmark().stdout
    assert "the floor" in out
    assert "a real command" in out or "something a script would run" in out


def test_the_benchmark_does_not_claim_parsing_beats_spawning():
    """That claim is false on Linux. If it comes back, it should come back
    with evidence, not by someone restoring a nicer sentence."""
    out = run_benchmark().stdout.lower()
    assert "varies by platform" in out or "moves a lot by platform" in out
