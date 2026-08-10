"""Tests that shell out to real programs and assert on wall-clock
behaviour, plus the `it` / `the result` state machine. Skipped where
sleep / true / false / echo are not on PATH."""

import subprocess
import time as _time

import pytest

from frostlang.interp import FrostError

from helpers import out, run, needs_sleep, needs_coreutils


@needs_sleep
def test_timeout_actually_interrupts_a_slow_command():
    """A 200ms deadline on a 10s sleep must return in well under a second."""
    start = _time.monotonic()
    with pytest.raises(FrostError) as e:
        run('run "sleep" with "10" within 200 milliseconds')
    elapsed = _time.monotonic() - start
    assert elapsed < 2.0, f"timeout did not interrupt the child ({elapsed:.2f}s)"
    assert "ran longer than" in e.value.msg


@needs_sleep
def test_timeout_sets_status_124_and_keeps_running():
    src = '''
    try to run "sleep" with "10" within 200 milliseconds
    put the result
    put "still here"
    '''
    start = _time.monotonic()
    text = out(src)
    elapsed = _time.monotonic() - start
    assert elapsed < 2.0
    assert text == "124\nstill here"


@needs_sleep
def test_a_command_that_finishes_within_its_deadline_succeeds():
    src = '''
    run "sleep" with "0.05" within 5 seconds
    put the result
    '''
    assert out(src) == "0"


@needs_sleep
def test_pipe_timeout_interrupts_and_reaps():
    """The whole pipe has one deadline; a slow stage must not hang the run."""
    src = '''
    try to pipe within 200 milliseconds
        run "sleep" with "10"
        run "wc" with "-l"
    end pipe
    put the result
    '''
    start = _time.monotonic()
    assert out(src) == "124"
    assert _time.monotonic() - start < 2.0


@needs_sleep
def test_no_orphan_processes_after_a_timeout():
    """Killed children must be reaped, not left running in the background."""
    import subprocess
    before = subprocess.run(["pgrep", "-c", "sleep"], capture_output=True,
                            text=True).stdout.strip() or "0"
    run('try to run "sleep" with "30" within 200 milliseconds')
    _time.sleep(0.3)
    after = subprocess.run(["pgrep", "-c", "sleep"], capture_output=True,
                           text=True).stdout.strip() or "0"
    assert int(after) <= int(before), "a timed-out sleep is still running"


# ------------------------------------------------ `the result` and `it` state
#
# `the result` mirrors $? : it always reflects the LAST command, so a success
# after a failure resets it, and a failure after a success sets it. `it` holds
# the last command's stdout and is never a stale value from an earlier one.

@needs_coreutils
def test_result_reflects_each_command_in_turn():
    src = '''
    try to run "false"
    put the result
    try to run "true"
    put the result
    '''
    assert out(src) == "1\n0"


@needs_coreutils
def test_a_success_clears_an_earlier_failures_status():
    src = '''
    try to run "false"
    run "true"
    put the result
    '''
    assert out(src) == "0"


@needs_coreutils
def test_a_failure_sets_status_after_an_earlier_success():
    src = '''
    run "true"
    try to run "false"
    put the result
    '''
    assert out(src) == "1"


@needs_coreutils
def test_it_is_replaced_not_appended():
    src = '''
    run "echo" with "first"
    run "echo" with "second"
    put it
    '''
    assert out(src) == "second"


@needs_coreutils
def test_it_becomes_empty_when_a_command_emits_nothing():
    # `it` must not leak the previous command's output.
    src = '''
    run "echo" with "leftover"
    run "true"
    put "[" & it & "]"
    '''
    assert out(src) == "[]"


@needs_coreutils
def test_a_pipe_updates_both_it_and_result():
    src = '''
    run "echo" with "stale"
    pipe
        run "printf" with "b\\na\\nc\\n"
        run "sort"
    end pipe
    put it
    put the result
    '''
    assert out(src) == "a\nb\nc\n0"


@needs_coreutils
def test_result_survives_reads_without_changing():
    # Reading `the result` must not itself alter it.
    src = '''
    try to run "false"
    put the result
    put the result
    '''
    assert out(src) == "1\n1"
