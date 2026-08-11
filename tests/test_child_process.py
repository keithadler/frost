"""What a child process is given: input, a folder, and an environment.

Three gaps closed at once, because they are the same gap, the script could
choose a program and its arguments, but nothing else about the context it ran
in.

  reading EXPR        text on the child's standard input (the heredoc)
  in folder EXPR      the child's working directory
  put X into the environment variable "N"   what children inherit

The environment and folder forms deliberately reuse `put ... into <target>`
rather than inventing a `set` keyword: `the environment variable "N"` and
`the current folder` were already readable, so this just makes them writable.
"""

import os

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run, caps_for, needs_coreutils

needs_sort = pytest.mark.skipif(
    not __import__("shutil").which("sort"), reason="no 'sort' on PATH")
needs_wc = pytest.mark.skipif(
    not __import__("shutil").which("wc"), reason="no 'wc' on PATH")


# --------------------------------------------------------- standard input

@needs_sort
def test_text_can_be_fed_to_a_program():
    assert out(r'run "sort" reading "c\na\nb"') == ""
    assert out(r'run "sort" reading "c\na\nb"' "\nput it") == "a\nb\nc"


@needs_wc
def test_input_is_combined_with_arguments():
    assert out(r'run "wc" with "-l" reading "a\nb\nc"' "\nput the first word of it") == "3"


@needs_sort
def test_input_can_come_from_a_variable():
    src = r'''
    put "delta\nalpha\ncharlie" into names
    run "sort" reading names
    put the first line of it
    '''
    assert out(src) == "alpha"


@needs_sort
def test_input_can_come_from_a_file(tmp_path):
    data = tmp_path / "names.txt"
    data.write_text("delta\nalpha\ncharlie\n")
    src = f'''
    run "sort" reading file "{data}"
    put the first line of it
    '''
    assert out(src) == "alpha"


@needs_coreutils
def test_a_trailing_newline_is_added_if_missing():
    """A program reading lines should see one line, not a truncated one."""
    assert out(r'run "cat" reading "one line"' "\nput it") == "one line"


@needs_sort
def test_input_survives_hostile_text():
    """The whole point: stdin is data, and data never becomes syntax."""
    src = r'''
    put "x; rm -rf /\nalpha" into hostile
    run "sort" reading hostile
    put the last line of it
    '''
    assert out(src) == "x; rm -rf /"


@needs_coreutils
def test_a_command_without_input_gets_nothing_not_the_terminal():
    """Without `reading`, a child must not inherit and block on our stdin."""
    assert out('try to run "cat"\nput "did not hang"') == "did not hang"


@needs_sort
def test_a_pipe_can_be_given_input():
    src = r'''
    pipe reading "delta\nalpha\ncharlie\nalpha"
        run "sort"
        run "uniq"
    end pipe
    put it
    '''
    assert out(src) == "alpha\ncharlie\ndelta"


@needs_sort
def test_a_large_input_does_not_deadlock_a_pipe():
    """Writing into stage one while waiting on the last stage is the classic
    pipeline deadlock; the input goes through a temporary file instead."""
    src = r'''
    put "" into blob
    repeat 4000 times
        put "some reasonably long line of text\n" after blob
    end repeat
    pipe reading blob
        run "sort"
        run "uniq"
    end pipe
    put the number of lines in it
    '''
    assert out(src) == "1"


def test_a_stage_cannot_have_its_own_input():
    src = '''
    pipe
        run "sort" reading "x"
        run "uniq"
    end pipe
    '''
    with pytest.raises(ParseError) as e:
        parse(src)
    assert "stage reads from the stage before it" in e.value.msg


def test_only_one_reading_clause_is_allowed():
    with pytest.raises(ParseError) as e:
        parse('run "cat" reading "a" reading "b"')
    assert "only one 'reading'" in e.value.msg


def test_reading_cannot_be_used_as_a_name():
    with pytest.raises(ParseError):
        parse("put 1 into reading list")


# ----------------------------------------------------------- child folder

@needs_coreutils
def test_a_command_runs_in_the_folder_it_is_given(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    src = f'''
    run "ls" in folder "{tmp_path}"
    put it
    '''
    assert out(src) == "marker.txt"


@needs_coreutils
def test_the_folder_does_not_leak_to_the_next_command(tmp_path):
    """`in folder` is per-command, so a reader need not track ambient state."""
    (tmp_path / "only_here.txt").write_text("x")
    src = f'''
    run "ls" in folder "{tmp_path}"
    run "pwd"
    put it
    '''
    assert out(src, cwd=os.getcwd()) == os.getcwd()


def test_a_missing_folder_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run('run "ls" in folder "/no/such/folder/anywhere"')
    assert "no folder at" in e.value.msg


@needs_coreutils
def test_a_pipe_runs_in_the_folder_it_is_given(tmp_path):
    (tmp_path / "b.txt").write_text("x")
    (tmp_path / "a.txt").write_text("x")
    src = f'''
    pipe in folder "{tmp_path}"
        run "ls"
        run "sort"
    end pipe
    put the first line of it
    '''
    assert out(src) == "a.txt"


def test_a_stage_cannot_have_its_own_folder():
    src = '''
    pipe
        run "ls" in folder "/tmp"
        run "sort"
    end pipe
    '''
    with pytest.raises(ParseError) as e:
        parse(src)
    assert "on the pipe" in e.value.msg


@needs_coreutils
def test_a_timeout_and_a_folder_compose(tmp_path):
    src = f'run "ls" in folder "{tmp_path}" within 10 seconds\nput the result'
    assert out(src) == "0"


@needs_coreutils
def test_clause_order_does_not_matter(tmp_path):
    a = f'run "cat" reading "x" in folder "{tmp_path}" within 5 seconds'
    b = f'run "cat" within 5 seconds in folder "{tmp_path}" reading "x"'
    assert out(a + "\nput it") == out(b + "\nput it") == "x"


# ------------------------------------------------------ working folder

def test_the_current_folder_can_be_changed(tmp_path):
    src = f'''
    put "{tmp_path}" into the current folder
    put the current folder
    '''
    assert out(src) == str(tmp_path)


def test_changing_the_folder_changes_what_a_relative_path_means(tmp_path):
    (tmp_path / "notes.txt").write_text("found me\n")
    src = f'''
    put "{tmp_path}" into the current folder
    put file "notes.txt"
    '''
    assert out(src) == "found me"


def test_changing_to_a_missing_folder_is_an_error():
    with pytest.raises(FrostError) as e:
        run('put "/no/such/folder" into the current folder')
    assert "no folder at" in e.value.msg


def test_the_current_folder_cannot_be_appended_to(tmp_path):
    with pytest.raises(FrostError) as e:
        run(f'put "{tmp_path}" after the current folder')
    assert "only be set with 'into'" in e.value.msg


# ------------------------------------------------ child environment

def test_an_environment_variable_can_be_set_and_read_back():
    src = '''
    put "hello" into the environment variable "FROST_TEST_SET"
    put the environment variable "FROST_TEST_SET"
    '''
    assert out(src) == "hello"


def test_setting_a_variable_does_not_leak_into_this_process():
    """The script gets its own copy; the test runner's environment is not
    quietly rewritten by a script it merely ran."""
    run('put "leaked" into the environment variable "FROST_LEAK_CHECK"')
    assert "FROST_LEAK_CHECK" not in os.environ


@needs_coreutils
def test_a_child_inherits_what_the_script_sets():
    src = '''
    put "from frost" into the environment variable "FROST_CHILD_VAR"
    run "sh" with "-c", "printf %s \\"$FROST_CHILD_VAR\\""
    put it
    '''
    assert out(src) == "from frost"


@needs_coreutils
def test_a_child_of_a_pipe_inherits_it_too():
    src = '''
    put "piped" into the environment variable "FROST_PIPE_VAR"
    pipe
        run "sh" with "-c", "printf %s \\"$FROST_PIPE_VAR\\""
        run "cat"
    end pipe
    put it
    '''
    assert out(src) == "piped"


def test_a_variable_can_be_appended_to():
    src = '''
    put "one" into the environment variable "FROST_PATHLIKE"
    put ":two" after the environment variable "FROST_PATHLIKE"
    put the environment variable "FROST_PATHLIKE"
    '''
    assert out(src) == "one:two"


def test_a_variable_can_be_prepended_to():
    src = '''
    put "two" into the environment variable "FROST_PATHLIKE2"
    put "one:" before the environment variable "FROST_PATHLIKE2"
    put the environment variable "FROST_PATHLIKE2"
    '''
    assert out(src) == "one:two"


def test_the_starting_environment_is_visible():
    os.environ["FROST_PREEXISTING"] = "already here"
    assert out('put the environment variable "FROST_PREEXISTING"') \
        == "already here"


def test_the_name_can_be_built_at_runtime():
    src = '''
    put "FROST_" & "BUILT" into name
    put "yes" into the environment variable name
    put the environment variable "FROST_BUILT"
    '''
    assert out(src) == "yes"


@pytest.mark.parametrize("name", ['""', '"has=equals"'])
def test_an_unusable_variable_name_is_rejected(name):
    with pytest.raises(FrostError) as e:
        run(f'put "x" into the environment variable {name}')
    assert "not a usable environment variable name" in e.value.msg


# --------------------------------------------------------------- the audit

def test_the_manifest_records_an_environment_write():
    caps = caps_for('put "x" into the environment variable "BUILD_ID"')
    assert caps.env_writes == [("BUILD_ID", 1)]


def test_the_manifest_records_a_folder_change():
    caps = caps_for('put "/tmp/build" into the current folder')
    assert caps.folder_changes == [("/tmp/build", 1)]


def test_the_manifest_records_that_a_command_is_given_input():
    caps = caps_for('run "sort" reading "x"')
    assert caps.commands[0].stdin is True


def test_the_manifest_records_a_command_folder():
    caps = caps_for('run "make" in folder "/src"')
    assert caps.commands[0].folder == "/src"


def test_a_pipe_folder_reaches_every_stage():
    caps = caps_for('''
    pipe in folder "/src"
        run "ls"
        run "sort"
    end pipe
    ''')
    assert [c.folder for c in caps.commands] == ["/src", "/src"]


def test_a_pipe_input_reaches_only_the_first_stage():
    caps = caps_for('''
    pipe reading "x"
        run "sort"
        run "uniq"
    end pipe
    ''')
    assert [c.stdin for c in caps.commands] == [True, False]


def test_analysing_a_pipe_does_not_modify_the_tree():
    """The auditor is read-only; running after --explain must be unaffected."""
    tree = parse('pipe reading "x" in folder "/src"\n'
                 '    run "sort"\n    run "uniq"\nend pipe')
    from frostlang.audit import audit
    audit(tree)
    audit(tree)
    stages = tree[0].stages
    assert [s.stdin for s in stages] == [None, None]
    assert [s.folder for s in stages] == [None, None]


def test_describe_mentions_input_and_folder():
    from frostlang.audit import describe
    text = describe(caps_for('run "make" with "all" in folder "/src"'
                             ' reading "input"'))
    assert "given input" in text
    assert "/src" in text


def test_the_summary_mentions_setting_the_environment():
    from frostlang.audit import summarise
    text = summarise(caps_for('put "x" into the environment variable "CC"'))
    assert "sets 1 environment variable (CC)" in text


def test_the_summary_mentions_changing_folder():
    from frostlang.audit import summarise
    assert "changes the working folder" in summarise(
        caps_for('put "/tmp" into the current folder'))
