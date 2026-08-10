"""The --try scratchpad."""

from helpers import repl_lines

from frostlang.repl import Repl
import io as _io


def test_repl_evaluates_a_chunk_expression():
    assert repl_lines("the first word of it",
                      subject="alpha beta") == ["alpha"]


def test_repl_nested_chunks():
    assert repl_lines("the second word of line 2 of it",
                      subject="a b\nc d") == ["d"]


def test_repl_renders_a_list_with_a_count():
    out = repl_lines(r'every match of "\d+" in it', subject="a1 b22")
    assert out == ["2 items: 1, 22"]


def test_repl_renders_empty_clearly():
    assert repl_lines("word 99 of it", subject="a b") == ["(empty)"]


def test_repl_accepts_statements_too():
    out = repl_lines('put "x" into greeting', "greeting", subject="")
    assert out[-1] == "x"


def test_repl_prefers_the_expression_error():
    out = repl_lines("the frobnitz", subject="a")
    assert "must be followed by a property or chunk" in out[0]


def test_repl_reports_an_unknown_handler_call():
    """`the X of Y` is a handler call now, so this is a name error rather
    than a syntax one. In the REPL it lands at evaluation, because handlers
    defined on earlier lines are not in the line being parsed."""
    out = repl_lines("the frobnitz of it", subject="a")
    assert "no handler named 'frobnitz'" in out[0]


def test_repl_commands():
    out = repl_lines(":text one two three", "the last word of it")
    assert out[-1] == "three"


def test_repl_quit_stops():
    r = Repl(out=_io.StringIO())
    assert r.handle(":quit") is False


def test_repl_unknown_command_is_reported():
    assert "unknown command" in repl_lines(":nope")[0]
