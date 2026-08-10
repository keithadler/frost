"""Explicit globals.

The gap this closes: `put 99 into total` inside a handler silently created a
local, even when a global of that name was readable two lines up. Reading
already reached the global; writing quietly did not, so the bug was invisible
at the write and only showed up later at a read.

The fix is a form that says so at the point of the write — `the global total`
— rather than a declaration at the top of the handler that a reader has to
carry in their head for the next forty lines. `global` is now a reserved word,
so the near-miss `put 5 into global total` is an error rather than a local
named "global total".
"""

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run


# ----------------------------------------------------------------- reading

def test_a_handler_reads_a_global():
    src = """
    put 10 into total
    to show
        put total
    end show
    show
    """
    assert out(src) == "10"


def test_the_global_form_reads_the_same_value():
    src = """
    put 10 into total
    to show
        put the global total
    end show
    show
    """
    assert out(src) == "10"


def test_the_global_form_reaches_past_a_local_of_the_same_name():
    src = """
    put "outer" into name
    to show with name
        put name
        put the global name
    end show
    show with "inner"
    """
    assert out(src) == "inner\nouter"


def test_reading_a_global_that_does_not_exist_is_an_error():
    with pytest.raises(FrostError) as e:
        run("put the global missing thing")
    assert "no global named" in e.value.msg


# ----------------------------------------------------------------- writing

def test_a_plain_put_in_a_handler_still_makes_a_local():
    """The old behaviour is kept — it is now the explicit choice, not a trap."""
    src = """
    put 10 into total
    to bump
        put 99 into total
    end bump
    bump
    put total
    """
    assert out(src) == "10"


def test_the_global_form_writes_through():
    src = """
    put 10 into total
    to bump
        put 99 into the global total
    end bump
    bump
    put total
    """
    assert out(src) == "99"


def test_a_global_can_be_created_from_inside_a_handler():
    src = """
    to start
        put "ready" into the global state
    end start
    start
    put state
    """
    assert out(src) == "ready"


def test_add_reaches_a_global():
    src = """
    put 0 into total
    to count one
        add 1 to the global total
    end count one
    count one
    count one
    count one
    put total
    """
    assert out(src) == "3"


def test_subtract_multiply_and_divide_reach_a_global():
    src = """
    put 20 into n
    to work
        subtract 4 from the global n
        multiply 3 into the global n
        divide 2 into the global n
    end work
    work
    put n
    """
    assert out(src) == "24"


def test_put_after_appends_to_a_global():
    src = """
    put "a" into log
    to note
        put "b" after the global log
    end note
    note
    note
    put log
    """
    assert out(src) == "abb"


def test_put_before_prepends_to_a_global():
    src = """
    put "a" into log
    to note
        put "x" before the global log
    end note
    note
    put log
    """
    assert out(src) == "xa"


def test_replace_reaches_a_global():
    src = """
    put "one two" into phrase
    to shout
        replace "two" with "three" in the global phrase
    end shout
    shout
    put phrase
    """
    assert out(src) == "one three"


def test_writing_a_global_that_does_not_exist_yet_creates_it():
    assert out('put 5 into the global fresh\nput fresh') == "5"


def test_add_to_a_missing_global_is_an_error():
    with pytest.raises(FrostError) as e:
        run("add 1 to the global nope")
    assert "no global named" in e.value.msg


# ---------------------------------------------------------- accumulator use

def test_the_motivating_case_a_counter_across_handler_calls():
    src = """
    put 0 into error total

    to record with status code
        if status code is not "200" then
            add 1 to the global error total
        end if
    end record

    repeat for each item in "200,500,404,200,503" as code
        record with code
    end repeat

    put error total
    """
    assert out(src) == "3"


# ----------------------------------------------------------------- parsing

def test_global_alone_is_a_reserved_word():
    with pytest.raises(ParseError) as e:
        parse("put 5 into global total")
    assert "reserved" in e.value.msg
    assert "the global" in (e.value.hint or "")


def test_global_cannot_be_part_of_a_name():
    with pytest.raises(ParseError):
        parse("put 5 into my global counter")


def test_a_global_name_may_be_several_words():
    assert out('put 1 into the global error total\nput error total') == "1"


@pytest.mark.parametrize("src", [
    "put 5 into the",
    "put 5 into the global",
    "put 5 into the nonsense",
    "add 1 to the global",
])
def test_malformed_global_targets_are_rejected(src):
    with pytest.raises(ParseError):
        parse(src)


def test_assigning_to_an_unwritable_property_is_a_clear_error():
    with pytest.raises(ParseError) as e:
        parse("put 5 into the result")
    assert "the global" in (e.value.hint or "")
