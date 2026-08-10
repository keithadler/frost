"""run and pipe: capture, failure, injection resistance."""

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

import os

from helpers import out, run


def test_run_captures_stdout_into_it():
    assert out('run "echo" with "hi"\nput it') == "hi"


def test_run_sets_the_result():
    assert out('try to run "false"\nput the result') == "1"


def test_unchecked_failure_aborts():
    with pytest.raises(FrostError) as e:
        run('run "false"\nput "never"')
    assert "failed with status 1" in e.value.msg


def test_try_to_run_does_not_abort():
    assert out('try to run "false"\nput "still here"') == "still here"


def test_missing_program_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run('run "definitely-not-a-real-program-xyz"')
    assert "no program named" in e.value.msg


def test_arguments_are_never_reparsed(tmp_path):
    """The core safety property: a value can never become syntax."""
    evil = "notes.txt; rm -rf *"
    src = f'''
    put "{evil}" into evil name
    run "touch" with evil name
    '''
    run(src, cwd=str(tmp_path))
    survivors = os.listdir(tmp_path)
    assert evil in survivors


def test_spaces_in_filenames_need_no_quoting(tmp_path):
    src = 'run "touch" with "my file.txt"'
    run(src, cwd=str(tmp_path))
    assert os.listdir(tmp_path) == ["my file.txt"]


def test_run_with_a_command_line_is_rejected():
    with pytest.raises(ParseError) as e:
        parse('run "ls -la"')
    assert "not a command line" in e.value.msg
    assert 'with "-la"' in e.value.hint


def test_pipe_chains_stages():
    src = """
    pipe
        run "printf" with "b\\na\\nc\\n"
        run "sort"
    end pipe
    put it
    """
    assert out(src) == "a\nb\nc"


def test_pipe_reports_failure_of_a_middle_stage():
    """bash's default would report success here. That is the bug."""
    src = """
    try to pipe
        run "cat" with "/nonexistent-file-xyz"
        run "wc" with "-l"
    end pipe
    put the result
    """
    assert out(src) != "0"


def test_pipe_needs_two_stages():
    with pytest.raises(ParseError):
        parse('pipe\n    run "ls"\nend pipe')


def test_pipe_stages_must_be_runs():
    with pytest.raises(ParseError) as e:
        parse('pipe\n    put "x"\n    run "ls"\nend pipe')
    assert "must be a 'run'" in e.value.msg
