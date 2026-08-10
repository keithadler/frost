"""Deadlines on run and pipe. These use real `sleep`; see
test_external.py for the ones that assert on wall-clock behaviour."""

import textwrap

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run


def test_fast_command_under_timeout_succeeds():
    assert out('run "sleep" with "0.05" within 5 seconds\nput the result') == "0"


def test_slow_command_times_out_with_124():
    src = '''
    try to run "sleep" with "10" within 200 milliseconds
    put the result
    '''
    assert out(src) == "124"


def test_unchecked_timeout_aborts_the_script():
    with pytest.raises(FrostError) as e:
        run('run "sleep" with "10" within 200 milliseconds')
    assert "ran longer than" in e.value.msg


def test_pipe_timeout():
    src = '''
    try to pipe within 200 milliseconds
        run "sleep" with "10"
        run "wc" with "-l"
    end pipe
    put the result
    '''
    assert out(src) == "124"


def test_timeout_units():
    for clause in ["within 1 second", "within 1 seconds", "within 2 minutes",
                   "within 500 milliseconds", "within 100 ms",
                   "within 1 hour"]:
        parse(f'run "true" {clause}')


def test_timeout_requires_a_unit():
    with pytest.raises(ParseError) as e:
        parse('run "sleep" with "1" within 5')
    assert "needs a unit" in e.value.msg


def test_timeout_must_be_positive():
    with pytest.raises(FrostError):
        run('run "true" within 0 seconds')


def test_timeout_belongs_on_the_pipe_not_a_stage():
    src = '''
    pipe
        run "echo" with "x" within 1 second
        run "cat"
    end pipe
    '''
    with pytest.raises(ParseError) as e:
        parse(textwrap.dedent(src))
    assert "on the pipe" in e.value.msg
