"""The --try scratchpad."""

import pytest

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


# -- the rest of the commands

def test_help_lists_the_commands():
    out = "\n".join(repl_lines(":help"))
    for command in (":text", ":load", ":show", ":vars", ":quit"):
        assert command in out


@pytest.mark.parametrize("alias", [":quit", ":q", ":exit"])
def test_every_quit_alias_stops(alias):
    assert Repl(out=_io.StringIO()).handle(alias) is False


@pytest.mark.parametrize("alias", [":help", ":h", ":?"])
def test_every_help_alias_works(alias):
    assert "scratchpad" in "\n".join(repl_lines(alias))


def test_show_numbers_the_lines():
    # repl_lines strips the block, so only later lines keep their indent.
    out = repl_lines(":show", subject="alpha\nbeta")
    assert out == ["1  alpha", "   2  beta"]


def test_text_replaces_the_subject_and_reports_its_size():
    out = repl_lines(":text hello there", subject="old")
    assert "11 characters" in out[0]


def test_text_understands_an_escaped_newline():
    out = repl_lines(r":text one\ntwo", "the number of lines in it")
    assert out[-1] == "2"


def test_load_reads_a_file(tmp_path):
    path = tmp_path / "subject.txt"
    path.write_text("alpha beta\ngamma delta\n")
    out = repl_lines(f":load {path}", "the last word of it")
    assert "2 lines" in out[0]
    assert out[-1] == "delta"


def test_load_reports_a_missing_file():
    assert "error" in repl_lines(":load /no/such/file.txt")[0]


def test_vars_says_so_when_there_are_none():
    assert "no variables yet" in repl_lines(":vars")[0]


def test_vars_lists_what_has_been_assigned():
    out = repl_lines('put "x" into greeting', ":vars")
    assert any("greeting = x" in line for line in out)


def test_vars_truncates_a_long_value():
    out = repl_lines('put "y" into padding', ":text " + "z" * 200,
                     'put it into big', ":vars")
    line = [l for l in out if "big =" in l][0]
    assert line.endswith("...")
    assert len(line) < 80


def test_a_blank_line_does_nothing():
    r = Repl(out=_io.StringIO())
    assert r.handle("   ") is True


def test_run_consumes_a_stream_until_it_ends():
    out = _io.StringIO()
    status = Repl(subject="alpha beta", out=out).run(
        stream=_io.StringIO("the first word of it\nthe last word of it\n"),
        interactive=False)
    assert status == 0
    assert out.getvalue().split() == ["alpha", "beta"]


def test_run_stops_at_quit():
    out = _io.StringIO()
    Repl(subject="a b", out=out).run(
        stream=_io.StringIO("the first word of it\n:quit\nthe last word of it\n"),
        interactive=False)
    assert "b" not in out.getvalue()


def test_interactive_mode_prints_a_banner_and_a_prompt():
    out = _io.StringIO()
    Repl(out=out).run(stream=_io.StringIO(""), interactive=True)
    assert "scratchpad" in out.getvalue()
    assert "frost>" in out.getvalue()


def test_a_runtime_error_is_reported_with_its_hint():
    out = repl_lines("put nothing here")
    assert "error:" in out[0]
    assert any("hint:" in line for line in out)


def test_the_new_expression_forms_work_in_the_scratchpad():
    assert repl_lines("the sorted the words of it joined by \",\"",
                      subject="c a b") == ["a,b,c"]
    assert repl_lines("the uppercase the first word of it",
                      subject="alpha beta") == ["ALPHA"]
