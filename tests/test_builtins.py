"""Built-in sources of value: the arguments, the environment."""

import os

from helpers import out, run_failing


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


# ------------------------------------------------- folders, padding, sorting

def test_a_folder_can_be_tested_for():
    """`file "x" exists` had no directory twin, and scripts check directories
    constantly."""
    assert out('if "/tmp" is a folder then put "yes"') == "yes"
    assert out('if "/tmp/nope-not-here" is a folder then put "yes"') == ""


def test_a_file_is_not_a_folder(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert out(f'if "{f}" is a folder then put "yes"') == ""
    assert out(f'if file "{f}" exists then put "yes"') == "yes"


def test_padding_aligns_a_column():
    """Scripts produce tables, and reaching for printf or column puts a second
    dialect back in a file whose whole argument is that it needs one."""
    assert out('put "[" & the padded "ab" to 6 & "]"') == "[ab    ]"
    assert out('put "[" & the padded 42 to 6 on the left & "]"') == "[    42]"


def test_padding_never_truncates():
    assert out('put the padded "abcdef" to 3') == "abcdef"


def test_a_negative_width_is_refused():
    _, error = run_failing('put the padded "a" to -1')
    assert "cannot be negative" in error.msg


def test_a_duration_reads_as_a_person_would_say_it():
    assert out("put the duration of 90") == "1 minute 30 seconds"
    assert out("put the duration of 3725") == "1 hour 2 minutes 5 seconds"
    assert out("put the duration of 1") == "1 second"
    assert out("put the duration of 0") == "0 seconds"
    assert out("put the duration of 0.25") == "250 milliseconds"


def test_sorting_by_a_key_uses_the_key_not_the_whole_value():
    assert out(r'put the sorted ("c 3\na 10\nb 9" split by "\n") '
               r'by the second word of each joined by "|"') \
        == "c 3|b 9|a 10"


def test_sorting_by_a_key_is_numeric_when_the_keys_are():
    """Sorting ["10", "9"] lexically puts 10 first, which is never what anyone
    means when the values came out of a column."""
    assert out('put the sorted (the words of "a10 b9 c100") '
               'by the number of characters in each joined by ","') \
        == "b9,a10,c100"


def test_each_outside_a_sort_key_says_what_it_is_for():
    _, error = run_failing("put each")
    assert "no value here" in error.msg
    assert "sort key" in error.hint


def test_sorting_an_empty_list_by_a_key():
    assert out("put the number of items in "
               "(the sorted (the empty list) by each)") == "0"
