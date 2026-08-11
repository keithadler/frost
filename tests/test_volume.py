"""Ceilings on how much data a run may move.

A deadline bounds how long a command may take and says nothing about a command
that answers instantly with a gigabyte. `cat` of the wrong file, `yes` with no
`head`, a log query with no date range: each is cheap to write, none of them
looks dangerous in a manifest, and all three take a runner down.

The thing worth testing here is not that the number is reported. It is that
the child is *stopped*. A limit measured after the output was captured would
print an accurate message about a machine that has already run out of memory,
and would pass every test written the easy way, so the tests below run a
producer that never ends and assert the run finishes.
"""

import os
import subprocess
import sys

import pytest

from frostlang.audit import parse_policy, volume_limits, PolicyError
from frostlang.cli import parse_size
from frostlang.interp import Interpreter, VolumeExceeded, format_bytes
from frostlang.parser import parse

from helpers import REPO, needs_coreutils


def frost(*args, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


def run_with(source, **limits):
    interp = Interpreter()
    interp.volume = limits
    interp.run_program(parse(source))
    return interp


# ------------------------------------------------------------ the policy

def test_a_policy_can_bound_output_a_command_and_writes():
    rules = parse_policy("""
require at most 10 megabytes of output
require at most 2 megabytes from one command
require at most 500 kilobytes written to files
""".strip())
    assert volume_limits(rules) == {"output": 10_000_000,
                                    "command": 2_000_000,
                                    "written": 500_000}


def test_the_tightest_limit_wins_when_a_policy_says_it_twice():
    """Composition must never widen. A site policy and a project policy are
    concatenated, and the second must not be able to raise the first."""
    rules = parse_policy("require at most 10 megabytes of output\n"
                         "require at most 4 megabytes of output")
    assert volume_limits(rules)["output"] == 4_000_000


def test_the_units_are_the_ones_the_words_mean():
    """Decimal, not binary. A policy that says megabytes and means 1,048,576
    misleads exactly the person working out which limit fired."""
    rules = parse_policy("require at most 1 megabytes of output")
    assert volume_limits(rules)["output"] == 1_000_000


@pytest.mark.parametrize("line", [
    "require at most 10 furlongs of output",
    "require at most 0 megabytes of output",
    "require at most many megabytes of output",
])
def test_a_size_that_is_not_one_is_refused_at_parse_time(line):
    with pytest.raises(PolicyError):
        parse_policy(line)


def test_a_volume_rule_is_not_a_static_finding():
    """How much a command returns is not knowable from the text. Reporting it
    at --check time would be a guess presented as a fact."""
    from frostlang.audit import audit, check
    rules = parse_policy("require at most 1 megabytes of output")
    assert check(audit(parse('run "echo" with "hi"')), rules) == []


# -------------------------------------------------------------- the flag

@pytest.mark.parametrize("text,expected", [
    ("10MB", 10_000_000),
    ("10 mb", 10_000_000),
    ("500kb", 500_000),
    ("2048", 2048),
    ("2.5GB", 2_500_000_000),
])
def test_a_size_on_the_command_line_reads_the_way_people_write_it(
        text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "MB", "-1MB", "0MB", "10 furlongs"])
def test_a_size_that_is_not_one_is_refused_on_the_command_line(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_the_formatter_says_what_the_policy_would_have_said():
    assert format_bytes(5_000_000) == "5 megabytes"
    assert format_bytes(1) == "1 byte"
    assert format_bytes(1500) == "1.5 kilobytes"
    assert format_bytes(None) == "no limit"


# --------------------------------------------------------- the enforcement

@needs_coreutils
def test_a_command_that_never_stops_producing_is_stopped(tmp_path):
    """The test this file exists for.

    `yes` produces forever. A limit that measured captured output would sit
    here until the machine gave out, and would pass a test that only checked
    the error message on a small input.
    """
    path = script(tmp_path, "flood.frost",
                  'run "sh" with "-c", "yes abcdefghijklmnopqrst"\n')
    code, out, err = frost("--max-output", "2MB", path, timeout=30)
    assert code == 125, err
    assert "was stopped" in err


@needs_coreutils
def test_the_same_script_runs_when_nothing_bounds_it(tmp_path):
    """The other half of the control. Without this, the test above would pass
    just as well against a frost that refused every command."""
    path = script(tmp_path, "bulk.frost",
                  'run "sh" with "-c", "yes abcdefghij | head -c 1048576"\n'
                  "put the length of it\n")
    code, out, err = frost(path, timeout=60)
    assert code == 0, err
    assert out.strip() == "1048576"


@needs_coreutils
def test_a_run_under_the_ceiling_is_untouched(tmp_path):
    path = script(tmp_path, "small.frost",
                  'run "sh" with "-c", "printf hello"\nput it\n')
    code, out, err = frost("--max-output", "2MB", path, timeout=30)
    assert code == 0, err
    assert out.strip() == "hello"


@needs_coreutils
def test_the_whole_run_budget_is_spent_across_commands(tmp_path):
    """A per-command limit alone bounds nothing: stay under it and repeat.
    The run budget is what makes the ceiling real, so the ceiling for any one
    command is what is left of it."""
    body = 'run "sh" with "-c", "yes abcdefghij | head -c 400000"\n' * 5
    path = script(tmp_path, "repeat.frost", body)
    code, out, err = frost("--max-output", "1MB", path, timeout=60)
    assert code == 125, err
    # The third command is killed at the 200 kilobytes left of the budget,
    # rather than being allowed to finish and reported afterwards. That the
    # ceiling is the *remainder* is the whole point: a per-command limit on
    # its own bounds nothing, because a script can stay under it and repeat.
    assert "200 kilobytes" in err, err


@needs_coreutils
def test_a_flag_may_narrow_a_policy_and_not_widen_it(tmp_path):
    policy = tmp_path / "p.policy"
    policy.write_text("require at most 1 megabytes of output\n")
    path = script(tmp_path, "bulk.frost",
                  'run "sh" with "-c", "yes abcdefghij | head -c 4000000"\n')
    # A flag asking for more does not get more.
    code, out, err = frost("--policy", str(policy), "--max-output", "100MB",
                           path, timeout=60)
    assert code == 125, err


@needs_coreutils
def test_a_file_write_is_refused_before_it_happens(tmp_path):
    """Charged before the write, not after: a limit that notices afterwards
    has already filled the disk it exists to protect."""
    target = tmp_path / "big.txt"
    path = script(tmp_path, "write.frost",
                  'put "0123456789" into chunk\n'
                  "repeat 8 times\n"
                  "    put chunk & chunk into chunk\n"
                  "end repeat\n"
                  f'put chunk into file "{target}"\n')
    code, out, err = frost("--max-written", "1kb", path, timeout=30)
    assert code == 125, err
    assert not target.exists(), "the write happened anyway"


def test_a_write_under_the_ceiling_still_happens(tmp_path):
    target = tmp_path / "small.txt"
    path = script(tmp_path, "write.frost",
                  f'put "hello" into file "{target}"\n')
    code, out, err = frost("--max-written", "1kb", path, timeout=30)
    assert code == 0, err
    assert target.read_text().strip() == "hello"


# ------------------------------------------------------------- the holes

def test_streaming_output_is_not_counted_and_the_docs_say_so():
    """`showing output` connects the child to the terminal, so the bytes
    never pass through frost and no limit here can see them. Asserted rather
    than left implicit, because a limit with a silent exemption is worse than
    no limit."""
    interp = Interpreter()
    interp.volume = {"output": 10}
    from frostlang.parser import parse as p
    tree = p('run "echo" with "hi" showing output')
    interp.run_program(tree)          # no VolumeExceeded
    assert interp.bytes_out == 0

    language = open(os.path.join(REPO, "LANGUAGE.md")).read()
    assert "showing output" in language


def test_the_bytes_are_counted_even_when_nothing_is_capped():
    """The finish event reports these numbers, and the question people ask
    before setting a ceiling is what a run normally moves. Answering with a
    zero because no ceiling was set yet makes the number useless exactly
    where it is wanted."""
    interp = Interpreter()
    interp.charge_output("hello", "!", 1)
    interp.charge_write(12, "/tmp/x", 1)
    assert interp.volume == {}
    assert interp.bytes_out == 6
    assert interp.bytes_written == 12


def test_the_accounting_survives_a_command_that_returns_nothing():
    interp = run_with('run "true"\n', output=1000)
    assert interp.bytes_out == 0


def test_charging_is_exact_for_multibyte_text():
    """Bytes, not characters. A limit measured in characters would let four
    times the data through for text that is not ASCII."""
    interp = Interpreter()
    interp.volume = {"output": 1000}
    interp.charge_output("é" * 10, "", 1)
    assert interp.bytes_out == 20


def test_the_error_names_the_limit_and_what_to_do():
    interp = Interpreter()
    interp.volume = {"output": 100}
    with pytest.raises(VolumeExceeded) as caught:
        interp.charge_output("x" * 200, "", 3)
    assert "100 bytes" in caught.value.msg
    assert caught.value.hint and "--max-output" in caught.value.hint


# ------------------------------------------------ in the same process
#
# The tests above run frost as a subprocess, which proves the flags are wired
# and measures nothing: the capped spawner is a different interpreter's code.
# It has been reported at 8% coverage on this project before, while a wall of
# green subprocess tests said otherwise. These call it directly.

@needs_coreutils
def test_the_capped_spawner_kills_a_producer_that_never_stops():
    interp = Interpreter()
    interp.volume = {"command": 200_000}
    with pytest.raises(VolumeExceeded) as caught:
        interp.run_program(parse(
            'run "sh" with "-c", "yes abcdefghijklmnop"\n'))
    assert "was stopped" in caught.value.msg


@needs_coreutils
def test_the_capped_spawner_still_feeds_standard_input():
    """The stdin writer is its own thread, and a reader that never drains it
    deadlocks. Exercised with a command that reads."""
    interp = Interpreter()
    interp.volume = {"command": 1_000_000}
    interp.run_program(parse('put "hello there" into greeting\n'
                             'run "cat" reading greeting\n'))
    assert interp.it == "hello there"


@needs_coreutils
def test_the_capped_spawner_returns_both_streams_and_the_status():
    interp = Interpreter()
    interp.volume = {"command": 1_000_000}
    interp.run_program(parse(
        'try to run "sh" with "-c", "printf out; printf err >&2; exit 3"\n'))
    assert interp.it == "out"
    assert interp.error_output == "err"
    assert interp.result == 3


@needs_coreutils
def test_the_capped_spawner_honours_a_timeout_as_well_as_a_ceiling():
    """Both bounds apply to the same command, and the slow one must still
    stop even when the quiet one never fills the budget."""
    interp = Interpreter()
    interp.volume = {"command": 1_000_000}
    interp.run_program(parse(
        'try to run "sleep" with "5" within 1 second\n'))
    assert interp.result == 124


@needs_coreutils
def test_a_capped_command_that_writes_nothing_is_fine():
    interp = Interpreter()
    interp.volume = {"command": 1000}
    interp.run_program(parse('run "true"\n'))
    assert interp.result == 0
    assert interp.bytes_out == 0


@needs_coreutils
def test_the_uncapped_path_is_the_one_that_runs_without_limits():
    """The other side of the branch: no limit means the plain spawner, and
    the behaviour must not change because this feature exists."""
    interp = Interpreter()
    interp.run_program(parse('run "echo" with "plain"\n'))
    assert interp.it == "plain"
    assert interp.volume == {}
