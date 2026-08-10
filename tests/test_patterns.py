"""Regular expressions and globs: matches, is like, every match, replace."""

import pytest

from frostlang.interp import FrostError

from helpers import out, run


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


def test_match_is_still_usable_as_a_variable_name():
    assert out('put 3 into match count\nput match count') == "3"
