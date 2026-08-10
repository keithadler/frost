"""Canonical layout. The examples are the style reference."""

import pytest

from frostlang.parser import parse, ParseError
from frostlang.lexer import LexError
from frostlang.formatter import format_source

from helpers import EXAMPLES


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


def test_formatter_leaves_a_decimal_literal_alone():
    """Found by the fuzzer: 5.0 was being rewritten as 5.

    The values behave identically, but the rewrite swapped a float literal for
    an int one, which breaks the identical-tree guarantee this file claims.
    """
    assert format_source("put 5.0\n") == "put 5.0\n"
    assert tree_shape("put 5.0") == tree_shape(format_source("put 5.0"))


@pytest.mark.parametrize("literal", ["0", "5", "5.0", "2.5", "1.25", "100",
                                     "0.5", "300.75"])
def test_number_literals_survive_a_format_round_trip(literal):
    src = f"put {literal}\n"
    assert tree_shape(format_source(src)) == tree_shape(src)


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
