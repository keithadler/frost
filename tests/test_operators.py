"""Concatenation, arithmetic, comparison and logical operators."""

import pytest

from frostlang.interp import FrostError

from helpers import out, run


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
