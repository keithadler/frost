"""Tokenising: comments, shebangs, string literals, escapes."""

import pytest

from frostlang.lexer import tokenize, LexError

from helpers import out


def test_comments_both_styles():
    assert out('-- a\n# b\nput "x"') == "x"


def test_shebang_is_skipped():
    assert out('#!/usr/bin/env frost\nput "x"') == "x"


def test_unterminated_string():
    with pytest.raises(LexError):
        tokenize('put "oops')


def test_escapes():
    assert out(r'put "a\tb"') == "a\tb"


# -- numbers

def test_integers_and_decimals():
    assert [t.value for t in tokenize("1 2.5 300") if t.kind == "NUM"] \
        == [1, 2.5, 300]


def test_a_decimal_keeps_its_type():
    """5.0 must stay a float, so the formatter cannot rewrite it as 5."""
    assert isinstance(tokenize("put 5.0")[1].value, float)
    assert isinstance(tokenize("put 5")[1].value, int)


@pytest.mark.parametrize("src", ["put 1.2.3", "put 35.39.39", "put 0..1",
                                 "put 1.2.3.4"])
def test_a_number_with_two_decimal_points_is_a_lex_error(src):
    """Found by the fuzzer: this used to escape as a bare Python ValueError."""
    with pytest.raises(LexError) as e:
        tokenize(src)
    assert "decimal point" in e.value.msg
    assert e.value.line == 1


def test_a_malformed_number_reports_the_right_line():
    with pytest.raises(LexError) as e:
        tokenize('put "a"\nput "b"\nput 1.2.3')
    assert e.value.line == 3
