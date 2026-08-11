"""Real lists.

The gap: `the arguments` and `every match` were the only lists the language
could produce, and `put "b" after xs` concatenated text rather than appending
an element. So there was no way to build up a collection.

The plural of a chunk noun with no index is now the whole set, `the words of
X`: which means splitting falls out of the grammar that was already there
rather than arriving as a new function. `split by` covers the delimiters the
chunk nouns cannot express, and `joined by` goes back the other way.
"""

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run


# ------------------------------------------------------------ making lists

def test_the_words_of_text_is_a_list():
    assert out('put the number of items in the words of "a b c"') == "3"


def test_the_lines_of_text_is_a_list():
    assert out(r'put the number of items in the lines of "a\nb\nc"') == "3"


def test_the_items_of_text_splits_on_commas():
    assert out('put the number of items in the items of "a,b,c"') == "3"


def test_the_characters_of_text_is_a_list():
    assert out('put the number of items in the characters of "abc"') == "3"


def test_a_list_element_is_reached_by_index():
    assert out('put item 2 of the words of "alpha beta gamma"') == "beta"


def test_the_empty_list_has_no_items():
    assert out("put the number of items in the empty list") == "0"


def test_the_words_of_nothing_is_empty():
    assert out('put the number of items in the words of ""') == "0"


def test_a_list_prints_one_element_per_line():
    assert out('put the words of "a b c"') == "a\nb\nc"


def test_lists_nest_with_the_rest_of_the_chunk_grammar():
    src = r'put the number of items in the words of the first line of "a b c\nd"'
    assert out(src) == "3"


# ---------------------------------------------------------------- building

def test_appending_to_a_list_adds_an_element():
    src = '''
    put the empty list into names
    put "alpha" after names
    put "beta" after names
    put the number of items in names
    '''
    assert out(src) == "2"


def test_an_appended_element_keeps_its_own_spaces():
    """The point of a list: an element containing a comma is still one
    element, which comma-delimited text could never manage."""
    src = '''
    put the empty list into names
    put "Smith, John" after names
    put "Doe, Jane" after names
    put the number of items in names
    put item 1 of names
    '''
    assert out(src) == "2\nSmith, John"


def test_prepending_to_a_list_adds_at_the_front():
    src = '''
    put the empty list into names
    put "beta" after names
    put "alpha" before names
    put item 1 of names
    '''
    assert out(src) == "alpha"


def test_appending_a_list_to_a_list_concatenates_them():
    src = '''
    put the words of "a b" into names
    put the words of "c d" after names
    put the number of items in names
    '''
    assert out(src) == "4"


def test_appending_to_text_still_concatenates():
    """The old behaviour is unchanged for text; only lists behave as lists."""
    assert out('put "a" into s\nput "b" after s\nput s') == "ab"


def test_a_list_can_be_built_in_a_loop():
    src = '''
    put the empty list into found
    repeat for each word in "alpha beta gamma" as w
        if w is not "beta" then put w after found
    end repeat
    put found joined by ","
    '''
    assert out(src) == "alpha,gamma"


# ------------------------------------------------------- split and join

def test_split_by_an_arbitrary_delimiter():
    assert out('put the number of items in ("a|b|c" split by "|")') == "3"


def test_split_by_a_multi_character_delimiter():
    assert out('put item 2 of ("a :: b :: c" split by " :: ")') == "b"


def test_split_keeps_empty_fields():
    """A trailing empty field is real data in a CSV row."""
    assert out('put the number of items in ("a||c" split by "|")') == "3"


def test_split_on_an_empty_separator_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run('put "abc" split by ""')
    assert "empty separator" in e.value.msg
    assert "the characters of" in (e.value.hint or "")


def test_joined_by_makes_text_again():
    assert out('put the words of "a b c" joined by ", "') == "a, b, c"


def test_joined_by_an_empty_separator_concatenates():
    assert out('put the words of "a b c" joined by ""') == "abc"


def test_split_and_join_round_trip():
    src = '''
    put "one:two:three" into raw
    put (raw split by ":") joined by ":" into again
    put again
    '''
    assert out(src) == "one:two:three"


def test_split_then_join_can_change_the_delimiter():
    assert out('put ("a:b:c" split by ":") joined by " | "') == "a | b | c"


def test_the_motivating_case_a_passwd_line():
    """`cut -d: -f1` was the reason this gap mattered."""
    src = '''
    put "root:x:0:0:root:/root:/bin/bash" into entry
    put item 1 of (entry split by ":")
    '''
    assert out(src) == "root"


# ---------------------------------------------------------- transformations

def test_sorted_orders_a_list():
    assert out('put the sorted (the words of "c a b") joined by ","') == "a,b,c"


def test_sorted_is_numeric_when_the_values_are_numbers():
    """Lexical order puts 10 before 9, which is never what a counter meant."""
    assert out('put the sorted (the words of "10 9 100 2") joined by ","') \
        == "2,9,10,100"


def test_sorted_is_alphabetical_for_text():
    assert out('put the sorted (the words of "pear Apple fig") joined by ","') \
        == "Apple,fig,pear"


def test_reversed_reverses_a_list():
    assert out('put the reversed (the words of "a b c") joined by ","') \
        == "c,b,a"


def test_unique_removes_duplicates_and_keeps_order():
    assert out('put the unique (the words of "b a b c a") joined by ","') \
        == "b,a,c"


def test_transformations_chain():
    src = 'put the sorted (the unique (the words of "b a b c")) joined by ","'
    assert out(src) == "a,b,c"


def test_sorted_on_text_sorts_its_lines():
    assert out(r'put the sorted "c\na\nb" joined by ","') == "a,b,c"


def test_the_original_list_is_not_modified():
    src = '''
    put the words of "c a b" into names
    put the sorted names joined by "," into ordered
    put names joined by ","
    '''
    assert out(src) == "c,a,b"


# ------------------------------------------------------------- aggregates

def test_the_sum_of_a_list():
    assert out('put the sum of the words of "1 2 3 4"') == "10"


def test_the_largest_and_smallest():
    src = '''
    put the words of "5 3 9 1" into numbers
    put the largest of numbers
    put the smallest of numbers
    '''
    assert out(src) == "9\n1"


def test_the_average():
    assert out('put the average of the words of "2 4 6"') == "4"


def test_an_aggregate_of_nothing_is_an_error():
    with pytest.raises(FrostError) as e:
        run("put the sum of the empty list")
    assert "undefined" in e.value.msg


def test_an_aggregate_of_non_numbers_is_an_error():
    with pytest.raises(FrostError) as e:
        run('put the sum of the words of "a b"')
    assert "not a number" in e.value.msg


def test_aggregates_work_over_a_command_output_shape():
    src = r'''
    put "3\n1\n2" into counts
    put the sum of the lines of counts
    '''
    assert out(src) == "6"


# ----------------------------------------------------------------- looping

def test_repeat_for_each_item_walks_a_list():
    src = '''
    put the empty list into seen
    repeat for each item in the words of "a b c" as one
        put one after seen
    end repeat
    put seen joined by "-"
    '''
    assert out(src) == "a-b-c"


def test_a_list_of_arguments_still_works():
    src = "put the sorted the arguments joined by \",\""
    assert out(src, argv=["c", "a", "b"]) == "a,b,c"


def test_every_match_is_a_list_that_the_new_forms_accept():
    src = r'''
    put every match of "\d+" in "a1 b22 c3" into numbers
    put the sum of numbers
    put the sorted numbers joined by ","
    '''
    assert out(src) == "26\n1,3,22"


# ----------------------------------------------------------------- parsing

def test_a_plural_chunk_noun_with_an_index_is_still_a_range():
    assert out('put words 1 to 2 of "a b c"') == "a b"


def test_the_matches_property_still_works():
    src = r'''
    if "abc" matches "(a)(b)" then
        put the number of items in the matches
    end if
    '''
    assert out(src) == "2"


@pytest.mark.parametrize("src", [
    'put the words of',
    'put "a" split by',
    'put "a" split "b"',
    'put "a" joined "b"',
    "put the empty",
])
def test_malformed_list_expressions_are_rejected(src):
    with pytest.raises(ParseError):
        parse(src)


def test_split_is_a_reserved_word():
    with pytest.raises(ParseError):
        parse("put 1 into split count")


def test_sorted_is_not_a_reserved_word():
    """These are recognised only after `the`, so they cost nothing from the
    identifier vocabulary."""
    assert out("put 3 into sorted count\nput sorted count") == "3"


@pytest.mark.parametrize("name", ["unique", "average", "largest", "trimmed",
                                  "uppercase", "reversed", "absolute"])
def test_transform_words_are_still_legal_names(name):
    assert out(f"put 7 into {name} total\nput {name} total") == "7"
