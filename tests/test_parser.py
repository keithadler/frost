"""Parsing: multi-word names, syntax errors, and the lexical scope of
loop control."""

import textwrap

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run


def test_multiword_variable():
    assert out('put 5 into error count\nput error count') == "5"


def test_variable_starting_with_chunk_noun():
    # `line count` must be a legal name even though `line` is a chunk noun.
    assert out('put 7 into line count\nput line count') == "7"


def test_variable_named_word_total():
    assert out('put 2 into word total\nadd 3 to word total\nput word total') == "5"


def test_undefined_variable_is_an_error():
    with pytest.raises(FrostError) as e:
        run("put nothing here")
    assert "no variable named" in e.value.msg


@pytest.mark.parametrize("src", [
    'if 1 is 1\n    put "x"\nend if',        # missing 'then'
    'repeat with i from 1 to 3\n    put i',  # missing 'end repeat'
    'put the frobnitz of "x"',               # unknown property
    'put "a" into',                          # missing target
    'to f\n    put "x"\nend g',              # mismatched handler name
])
def test_syntax_errors_are_caught(src):
    with pytest.raises(ParseError):
        parse(src)


def test_parse_errors_carry_a_line_number():
    with pytest.raises(ParseError) as e:
        parse('put "ok"\nput "ok"\nput the frobnitz of "x"')
    assert e.value.line == 3


# ------------------------------------------------- loop control is lexical

def test_exit_repeat_outside_a_loop_is_rejected():
    with pytest.raises(ParseError) as e:
        parse('exit repeat')
    assert "not inside a repeat" in e.value.msg


def test_loop_control_cannot_cross_a_handler_boundary():
    src = """
    to check with n
        if n is 2 then exit repeat
    end check

    repeat with i from 1 to 4
        check with i
    end repeat
    """
    with pytest.raises(ParseError):
        parse(textwrap.dedent(src))


def test_a_loop_inside_a_handler_is_fine():
    src = """
    to find target with text
        repeat for each word in text as w
            if w is "two" then return w
        end repeat
        return "none"
    end find target

    find target with "one two three"
    put it
    """
    assert out(src) == "two"


def test_nested_loops_exit_only_the_inner_one():
    src = """
    put 0 into hits
    repeat with i from 1 to 3
        repeat with j from 1 to 3
            if j is 2 then exit repeat
            add 1 to hits
        end repeat
    end repeat
    put hits
    """
    assert out(src) == "3"
