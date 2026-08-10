"""Built-in sources of value: the arguments, the environment."""

import os

from helpers import out


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
