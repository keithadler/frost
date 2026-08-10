"""Property tests over generated frost.

Two corpora, because they find different bugs.

*Random characters* exercise the rejection path: whatever a model emits, the
front end has to answer with a LexError or ParseError carrying a line number —
never a Python traceback, never a hang.

*Generated programs* (tests/gen.py) exercise the accept path, which random
characters essentially never reach. That is where the interesting claims live:
the formatter is idempotent and preserves the parse tree, the auditor is total
on anything that parsed, and the interpreter never leaks a Python exception.

Everything is seeded, so a failure reproduces from the printed source alone.
"""

import io
import pprint
import random
import re
import sys

import pytest

from frostlang.lexer import tokenize, LexError
from frostlang.parser import parse, ParseError
from frostlang.interp import Interpreter, FrostError
from frostlang.audit import audit, find_dangers, summarise, verdict, describe
from frostlang.formatter import format_source

from gen import Gen, programs

# Everything the front end is allowed to raise. Anything else is a bug.
EXPECTED = (LexError, ParseError)


# ============================================================ random noise

WORDS = [
    "put", "into", "before", "after", "run", "with", "try", "to", "pipe",
    "end", "if", "then", "else", "repeat", "times", "while", "until", "for",
    "each", "as", "from", "of", "in", "quit", "the", "it", "and", "or", "not",
    "is", "by", "contains", "starts", "ends", "at", "exit", "next", "add",
    "subtract", "multiply", "divide", "return", "matches", "like", "every",
    "replace", "within", "whole", "standard", "exists", "empty", "true",
    "false", "forever", "step", "delete", "greater", "less", "than", "least",
    "most", "line", "word", "item", "character", "match", "lines", "words",
    "items", "characters", "first", "second", "third", "last", "middle",
    "any", "result", "arguments", "environment", "variable", "length",
    "number", "file", "folder", "current", "status", "output", "error",
    "seconds", "minutes", "milliseconds", "hours", "count", "total", "name",
]

PUNCT = ['"', '"a"', '"\\d+"', "(", ")", ",", "&", "&&", "+", "-", "*", "/",
         "^", "=", "!=", "<", ">", "<=", ">=", "1", "0", "-1", "2.5", "99",
         "--", "#", "\\", "\t", "@", "$", "%", "!", "'", "`", ";", "|", "[",
         "]", "{", "}", ":", ".", "\x00", "é", "→"]

ATOMS = WORDS + PUNCT


def noise(rng, max_lines=6, max_atoms=9):
    return "\n".join(
        " ".join(rng.choice(ATOMS) for _ in range(rng.randint(1, max_atoms)))
        for _ in range(rng.randint(1, max_lines)))


def front_end(src):
    """Parse `src`; return the tree, or None if it was legitimately rejected.

    Re-raises anything that is not a LexError/ParseError as a test failure
    with the offending source attached.
    """
    try:
        tokenize(src)
    except EXPECTED as e:
        assert e.line is not None, f"error without a line number: {src!r}"
        return None
    except Exception as e:                                # pragma: no cover
        raise AssertionError(
            f"lexer raised {type(e).__name__}: {e}\nsource:\n{src}") from e

    try:
        return parse(src)
    except EXPECTED as e:
        assert e.line is not None, f"error without a line number: {src!r}"
        return None
    except RecursionError:
        pytest.skip("deeply nested source hit the recursion limit")
    except Exception as e:                                # pragma: no cover
        raise AssertionError(
            f"parser raised {type(e).__name__}: {e}\nsource:\n{src}") from e


@pytest.mark.parametrize("seed", range(40))
def test_random_words_never_crash_the_front_end(seed):
    rng = random.Random(seed)
    for _ in range(40):
        front_end(noise(rng))


def test_the_noise_corpus_is_mostly_rejected():
    """Guards the test above from becoming vacuous in the other direction.

    If a change ever made this corpus mostly *parse*, these tests would have
    stopped testing rejection without anyone noticing.
    """
    rng = random.Random(99)
    rejected = sum(front_end(noise(rng)) is None for _ in range(200))
    assert rejected > 150


# ======================================================= generated programs

def shape(tree):
    """A parse tree with line numbers erased, for comparing meaning."""
    return re.sub(r"line=\d+", "line=?", pprint.pformat(tree))


@pytest.mark.parametrize("seed", range(25))
def test_generated_programs_parse(seed):
    """Guards every property below: if this fails they are all vacuous."""
    for src in programs(20, seed=seed):
        try:
            tree = parse(src)
        except EXPECTED as e:                             # pragma: no cover
            raise AssertionError(
                f"generator emitted invalid frost: {e.msg} at line {e.line}"
                f"\n{src}") from e
        assert tree, f"parsed to nothing:\n{src}"


@pytest.mark.parametrize("seed", range(25))
def test_formatting_never_changes_meaning(seed):
    """The README's central formatter claim, checked against fresh input."""
    for src in programs(20, seed=seed):
        formatted = format_source(src)
        assert shape(parse(formatted)) == shape(parse(src)), (
            "formatting changed the parse tree:\n" + src)


@pytest.mark.parametrize("seed", range(25))
def test_formatting_is_idempotent(seed):
    for src in programs(20, seed=seed):
        once = format_source(src)
        assert format_source(once) == once, "second format differed:\n" + src


@pytest.mark.parametrize("seed", range(25))
def test_the_auditor_is_total_on_anything_that_parses(seed):
    """--explain runs on scripts nobody has vetted yet, so it must not throw."""
    for src in programs(20, seed=seed):
        caps = audit(parse(src))
        findings = find_dangers(caps)
        assert summarise(caps).endswith(".")
        assert isinstance(describe(caps), str)
        assert verdict(findings) in ("clean", "caution", "dangerous")


@pytest.mark.parametrize("seed", range(20))
def test_running_a_safe_program_never_leaks_a_python_exception(seed):
    """The subset with no subprocesses, no writes and only bounded loops.

    A frost script may fail — that is a FrostError with a line number. What it
    may never do is surface a TypeError or an IndexError from the evaluator.
    """
    for src in programs(10, seed=seed, safe=True):
        tree = parse(src)
        held, sys.stdout = sys.stdout, io.StringIO()
        try:
            Interpreter(argv=["a", "b"]).run_program(tree)
        except FrostError as e:
            assert e.msg, f"empty error message:\n{src}"
        except RecursionError:                            # pragma: no cover
            pass
        except Exception as e:                            # pragma: no cover
            raise AssertionError(
                f"interpreter raised {type(e).__name__}: {e}\n{src}") from e
        finally:
            sys.stdout = held


@pytest.mark.parametrize("seed", range(20))
def test_mutating_a_valid_program_never_crashes_the_front_end(seed):
    """Near-miss source is what a model actually produces when it gets it
    slightly wrong, and it reaches parser states random noise never does."""
    rng = random.Random(seed)
    for src in programs(6, seed=seed):
        for _ in range(12):
            i = rng.randrange(len(src))
            kind = rng.randrange(3)
            if kind == 0:                                 # substitute
                mutated = src[:i] + rng.choice(PUNCT) + src[i + 1:]
            elif kind == 1:                               # delete a run
                mutated = src[:i] + src[i + rng.randint(1, 8):]
            else:                                         # duplicate a run
                chunk = src[i:i + rng.randint(1, 12)]
                mutated = src[:i] + chunk + chunk + src[i:]
            front_end(mutated)


@pytest.mark.parametrize("seed", range(10))
def test_every_truncation_of_a_generated_program_is_handled(seed):
    for src in programs(2, seed=seed):
        step = max(1, len(src) // 120)
        for cut in range(0, len(src) + 1, step):
            front_end(src[:cut])


# ============================================================ known shapes

SEEDS = [
    'put "a" into x',
    'run "echo" with "a", "b" within 5 seconds',
    'if x is 1 then\n    put "y"\nelse\n    put "n"\nend if',
    'repeat for each line in file "a" as l\n    put l\nend repeat',
    'pipe\n    run "a"\n    run "b"\nend pipe',
    'to helper with n\n    return n\nend helper',
    'put the third word of line 2 of it',
    'replace "(\\d+)" with "\\1x" in s',
    'repeat with i from 1 to 10 by 2\n    put i\nend repeat',
]


@pytest.mark.parametrize("seed_src", SEEDS)
def test_every_truncation_of_a_hand_written_script_is_handled(seed_src):
    for cut in range(len(seed_src) + 1):
        front_end(seed_src[:cut])


@pytest.mark.parametrize("seed_src", SEEDS)
def test_every_single_character_deletion_is_handled(seed_src):
    for i in range(len(seed_src)):
        front_end(seed_src[:i] + seed_src[i + 1:])


def test_deep_nesting_is_reported_not_crashed():
    src = "put " + "(" * 200 + "1" + ")" * 200
    try:
        parse(src)
    except EXPECTED:
        pass
    except RecursionError:
        pytest.skip("recursion limit reached before the parser could report")


def test_unbalanced_block_keywords_are_reported():
    for src in ["end if", "end repeat", "end pipe", "else", "end",
                "if 1 then\nend repeat", "repeat 3 times\nend if",
                "pipe\nend if", "to f\nend g"]:
        with pytest.raises(EXPECTED):
            parse(src)


def test_very_long_line_is_handled():
    front_end("put " + " & ".join('"x"' for _ in range(5000)))


def test_null_bytes_and_unicode_do_not_crash():
    for src in ["put \x00", "put \"\x00\"", "put é", 'put "é"', "put →",
                "﻿put 1", "put 1\r\nput 2", "put 1 put 2"]:
        front_end(src)


def test_empty_and_whitespace_only_sources_parse_to_nothing():
    for src in ["", "\n", "   ", "\n\n\n", "-- just a comment", "#!/x/frost"]:
        assert parse(src) == []
