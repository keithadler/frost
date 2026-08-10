"""Statements: put, arithmetic, if, repeat, quit, handlers."""

import pytest

from frostlang.interp import FrostError

from helpers import out, run


def test_put_before_and_after():
    src = 'put "b" into s\nput "c" after s\nput "a" before s\nput s'
    assert out(src) == "abc"


def test_add_subtract_multiply_divide():
    assert out("put 10 into n\nadd 5 to n\nput n") == "15"
    assert out("put 10 into n\nsubtract 4 from n\nput n") == "6"
    assert out("put 10 into n\nmultiply 3 into n\nput n") == "30"
    assert out("put 10 into n\ndivide 4 into n\nput n") == "2.5"


def test_if_else_chain():
    src = """
    put 5 into n
    if n is greater than 10 then
        put "big"
    else if n is greater than 3 then
        put "medium"
    else
        put "small"
    end if
    """
    assert out(src) == "medium"


def test_single_line_if():
    assert out('put 1 into n\nif n is 1 then put "yes"') == "yes"


def test_repeat_times():
    assert out('repeat 3 times\n    put "x"\nend repeat') == "x\nx\nx"


def test_repeat_with_range():
    assert out("repeat with i from 1 to 3\n    put i\nend repeat") == "1\n2\n3"


def test_repeat_with_step_down():
    src = "repeat with i from 3 to 1 by -1\n    put i\nend repeat"
    assert out(src) == "3\n2\n1"


def test_repeat_for_each():
    src = """
    put "a,b,c" into s
    repeat for each item in s as piece
        put piece
    end repeat
    """
    assert out(src) == "a\nb\nc"


def test_repeat_while_and_exit():
    src = """
    put 0 into n
    repeat while n is less than 10
        add 1 to n
        if n is 3 then exit repeat
    end repeat
    put n
    """
    assert out(src) == "3"


def test_next_repeat_skips():
    src = """
    repeat with i from 1 to 4
        if i is 2 then next repeat
        put i
    end repeat
    """
    assert out(src) == "1\n3\n4"


def test_quit_with_status():
    _, status = run('put "bye"\nquit with status 3')
    assert status == 3


def test_handler_with_return():
    src = """
    to double with n
        return n * 2
    end double

    double with 21
    put it
    """
    assert out(src) == "42"


def test_handler_arity_is_checked():
    src = "to f with a, b\n    return a\nend f\nf with 1"
    with pytest.raises(FrostError) as e:
        run(src)
    assert "expects 2" in e.value.msg


def test_handler_locals_do_not_leak():
    src = """
    to helper with x
        put x into scratch
        return x
    end helper

    helper with 1
    put "ok"
    """
    assert out(src) == "ok"
