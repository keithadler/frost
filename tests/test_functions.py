"""String and number functions, and handlers used inside expressions.

Three gaps at once.

*No string functions*: no uppercase, no trim. These are `the uppercase X`
rather than a new keyword, because after `the` the parser is already in a
controlled position: `uppercase total` stays a perfectly good variable name.

*Handlers could not be called from inside an expression*; a result arrived in
`it`, so composing two of them meant three statements and a temporary. Now
`the double of 5` reads like every other `the ... of ...` in the language, and
an unknown name is caught when the script is checked rather than when the line
happens to run.

*Output was always captured*, so a long build showed nothing until it finished
and interactive programs did not work at all. `showing output` hands the
terminal to the child.
"""

import os
import subprocess
import sys

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import REPO, out, run, needs_coreutils


# ------------------------------------------------------------------ strings

def test_uppercase_and_lowercase():
    assert out('put the uppercase "Hello"') == "HELLO"
    assert out('put the lowercase "Hello"') == "hello"


def test_trimmed_removes_surrounding_whitespace():
    assert out('put "[" & the trimmed "  padded  " & "]"') == "[padded]"


def test_trimmed_leaves_the_inside_alone():
    assert out('put the trimmed "  a  b  "') == "a  b"


def test_case_functions_accept_of_as_well():
    """`the uppercase of X` reads better in some sentences; both work."""
    assert out('put the uppercase of "hi"') == "HI"


def test_string_functions_compose_with_chunks():
    assert out('put the uppercase (the first word of "alpha beta")') == "ALPHA"


def test_string_functions_work_on_a_variable():
    assert out('put "quiet" into s\nput the uppercase s') == "QUIET"


def test_the_motivating_case_case_insensitive_comparison():
    src = '''
    put "PRODUCTION" into target
    if the lowercase target is "production" then put "matched"
    '''
    assert out(src) == "matched"


def test_trimming_the_output_of_a_command():
    src = r'''
    put "  spaced  \n" into raw
    put "[" & the trimmed raw & "]"
    '''
    assert out(src) == "[spaced]"


# ------------------------------------------------------------------ numbers

@pytest.mark.parametrize("value,expected", [
    ("2.4", "2"), ("2.5", "2"), ("2.6", "3"), ("-2.6", "-3"), ("7", "7"),
])
def test_rounded(value, expected):
    assert out(f"put the rounded {value}") == expected


@pytest.mark.parametrize("value,expected", [
    ("5", "5"), ("-5", "5"), ("0", "0"), ("-2.5", "2.5"),
])
def test_absolute(value, expected):
    assert out(f"put the absolute {value}") == expected


def test_rounded_on_something_that_is_not_a_number_is_an_error():
    with pytest.raises(FrostError) as e:
        run('put the rounded "banana"')
    assert "not a number" in e.value.msg


def test_number_functions_compose():
    assert out("put the rounded the absolute -3.7") == "4"


def test_an_average_can_be_rounded():
    assert out('put the rounded (the average of the words of "1 2 4")') == "2"


# ------------------------------------------- handlers inside an expression

def test_a_handler_can_be_called_in_an_expression():
    src = '''
    to double with n
        return n * 2
    end double
    put the double of 5
    '''
    assert out(src) == "10"


def test_a_call_composes_with_other_expressions():
    src = '''
    to double with n
        return n * 2
    end double
    put the double of 5 + the double of 10
    '''
    assert out(src) == "30"


def test_calls_nest():
    src = '''
    to double with n
        return n * 2
    end double
    put the double of the double of 3
    '''
    assert out(src) == "12"


def test_a_handler_with_several_arguments():
    src = '''
    to join up with a, b
        return a & "-" & b
    end join up
    put the join up of "x", "y"
    '''
    assert out(src) == "x-y"


def test_a_multi_word_handler_name():
    src = '''
    to shout loudly with word
        return the uppercase word & "!"
    end shout loudly
    put the shout loudly of "hey"
    '''
    assert out(src) == "HEY!"


def test_a_handler_may_be_defined_after_it_is_used():
    src = '''
    put the double of 4
    to double with n
        return n * 2
    end double
    '''
    assert out(src) == "8"


def test_a_handler_that_returns_nothing_gives_empty_text():
    src = '''
    to quiet with n
        put "" into ignored
    end quiet
    put "[" & the quiet of 1 & "]"
    '''
    assert out(src) == "[]"


def test_a_handler_that_takes_nothing_is_called_without_of():
    src = '''
    to greeting
        return "hello"
    end greeting
    put the greeting & " there"
    '''
    assert out(src) == "hello there"


def test_an_expression_call_does_not_disturb_it():
    """`it` means the last command's output. An expression buried inside
    another expression must not quietly replace it."""
    src = '''
    to double with n
        return n * 2
    end double
    run "echo" with "kept"
    put the double of 2
    put it
    '''
    assert out(src) == "4\nkept"


def test_the_statement_form_still_lands_in_it():
    src = '''
    to double with n
        return n * 2
    end double
    double with 6
    put it
    '''
    assert out(src) == "12"


def test_a_recursive_handler_works_in_an_expression():
    src = '''
    to countdown with n
        if n is at most 0 then return "go"
        return n & " " & the countdown of (n - 1)
    end countdown
    put the countdown of 3
    '''
    assert out(src) == "3 2 1 go"


def test_an_argument_binds_tightly_like_a_chunk_source():
    """`the double of n - 1` is `(the double of n) - 1`, the same rule that
    makes `the first word of a & b` mean `(the first word of a) & b`. Parens
    are how you say the other thing."""
    src = '''
    to double with n
        return n * 2
    end double
    put the double of 5 - 1
    put the double of (5 - 1)
    '''
    assert out(src) == "9\n8"


def test_the_wrong_number_of_arguments_is_an_error():
    src = '''
    to double with n
        return n * 2
    end double
    put the double of 1, 2
    '''
    with pytest.raises(FrostError) as e:
        run(src)
    assert "expects 1 value(s) but got 2" in e.value.msg


def test_an_unknown_handler_is_caught_when_the_script_is_checked():
    """Not when the line runs, otherwise a typo in a rarely-taken branch
    would sail past --check."""
    with pytest.raises(ParseError) as e:
        parse('put the frobnitz of "x"')
    assert "no handler named 'frobnitz'" in e.value.msg


def test_an_unknown_handler_deep_in_a_branch_is_still_caught():
    src = '''
    if false then
        put the frobnitz of "x"
    end if
    '''
    with pytest.raises(ParseError):
        parse(src)


def test_a_built_in_property_wins_over_a_handler_of_the_same_name():
    """`the length of X` must keep meaning the length."""
    src = '''
    to length with n
        return "handler"
    end length
    put the length of "abcd"
    '''
    assert out(src) == "4"


def test_the_error_for_a_bare_the_is_unchanged():
    with pytest.raises(ParseError) as e:
        parse("put the frobnitz")
    assert "must be followed by a property or chunk" in e.value.msg


# ------------------------------------------------------- the script's stdin

def frost_cli(*args, stdin=None):
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, input=stdin,
                       cwd=REPO, env={**os.environ, "PYTHONPATH": REPO},
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def script(tmp_path):
    def make(source):
        path = tmp_path / "s.frost"
        path.write_text(source)
        return str(path)
    return make


def test_the_standard_input_is_readable(script):
    status, output, err = frost_cli(script("put the standard input"),
                                    stdin="hello\n")
    assert (status, output) == (0, "hello\n")


def test_the_standard_input_works_with_chunks(script):
    path = script("put the number of lines in the standard input\n"
                  "put the second line of the standard input")
    status, output, _ = frost_cli(path, stdin="one\ntwo\nthree\n")
    assert (status, output) == (0, "3\ntwo\n")


def test_the_standard_input_is_read_once(script):
    """Reading it twice must give the same text, not an empty second read."""
    path = script("put the standard input\nput the standard input")
    status, output, _ = frost_cli(path, stdin="x\n")
    assert (status, output) == (0, "x\nx\n")


def test_empty_standard_input_is_empty_text(script):
    path = script('put "[" & the standard input & "]"')
    status, output, _ = frost_cli(path, stdin="")
    assert (status, output) == (0, "[]\n")


def test_the_motivating_case_a_filter(script):
    path = script('''
    repeat for each line in the standard input as row
        if row contains "ERROR" then put the uppercase row
    end repeat
    ''')
    status, output, _ = frost_cli(
        path, stdin="ok one\nERROR two\nok three\nERROR four\n")
    assert output == "ERROR TWO\nERROR FOUR\n"


def test_standard_input_needs_the_right_word():
    with pytest.raises(ParseError):
        parse("put the standard banana")


# --------------------------------------------------------- showing output

@needs_coreutils
def test_showing_output_writes_straight_through(script):
    path = script('run "echo" with "streamed" showing output')
    status, output, _ = frost_cli(path)
    assert (status, output) == (0, "streamed\n")


@needs_coreutils
def test_showing_output_leaves_it_empty(script):
    """Nothing is captured, so `it` must be empty rather than stale."""
    path = script('run "echo" with "captured"\n'
                  'run "echo" with "streamed" showing output\n'
                  'put "[" & it & "]"')
    status, output, _ = frost_cli(path)
    # "captured" went into `it` and was never printed; "streamed" went
    # straight to stdout; `it` is empty afterwards, not the stale "captured".
    assert output == "streamed\n[]\n"


@needs_coreutils
def test_a_streamed_command_still_sets_the_result():
    assert out('try to run "false" showing output\nput the result') == "1"


@needs_coreutils
def test_a_streamed_failure_still_aborts():
    with pytest.raises(FrostError):
        run('run "false" showing output')


def test_a_pipe_cannot_show_its_output():
    src = 'pipe showing output\n    run "a"\n    run "b"\nend pipe'
    with pytest.raises(ParseError) as e:
        parse(src)
    assert "cannot show its output" in e.value.msg


def test_a_pipe_stage_cannot_show_its_output():
    src = 'pipe\n    run "a" showing output\n    run "b"\nend pipe'
    with pytest.raises(ParseError) as e:
        parse(src)
    assert "feeds the next stage" in e.value.msg


def test_showing_output_composes_with_a_timeout():
    tree = parse('run "make" showing output within 5 minutes')
    assert tree[0].streaming is True
    assert tree[0].timeout is not None


def test_showing_needs_the_word_output():
    with pytest.raises(ParseError):
        parse('run "make" showing everything')
