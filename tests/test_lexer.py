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
