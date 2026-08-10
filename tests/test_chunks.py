"""Chunk expressions - the feature that carries the language."""

from helpers import out, run


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
