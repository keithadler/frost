"""The parts that are not the language: packaging, CI, editor support.

These rot silently. A version declared in two places drifts, a CI workflow
stops mentioning a Python version the package claims to support, and a
hand-written keyword list for an editor falls behind the parser — and none of
it shows up when you run the interpreter.
"""

import json
import os
import re

import pytest

from frostlang import __version__
from frostlang.parser import HARD_WORDS

from helpers import REPO

EDITORS = os.path.join(REPO, "editors")


def read(*parts):
    with open(os.path.join(REPO, *parts)) as fh:
        return fh.read()


# ---------------------------------------------------------------- version

def test_the_version_is_declared_once_and_agrees_everywhere():
    pyproject = re.search(r'^version = "([^"]+)"', read("pyproject.toml"),
                          re.M)
    assert pyproject, "pyproject.toml has no version"
    assert pyproject.group(1) == __version__


def test_the_changelog_mentions_the_current_version():
    assert __version__ in read("CHANGELOG.md")


def test_the_tour_example_announces_the_right_version():
    """It prints its own version, which is the sort of thing nobody updates."""
    assert f"frost {__version__}" in read("examples", "tour.frost")


# ---------------------------------------------------------------- package

def test_the_console_script_points_at_something_real():
    entry = re.search(r'frost = "([\w.]+):(\w+)"', read("pyproject.toml"))
    assert entry, "no [project.scripts] entry for frost"
    module, function = entry.groups()
    import importlib
    assert callable(getattr(importlib.import_module(module), function))


def test_the_declared_python_floor_is_one_we_test():
    floor = re.search(r'requires-python = ">=([\d.]+)"', read("pyproject.toml"))
    assert floor
    workflow = read(".github", "workflows", "ci.yml")
    assert f'"{floor.group(1)}"' in workflow, (
        f"pyproject requires Python >= {floor.group(1)}, which CI never runs")


def test_the_dev_extra_covers_what_the_builders_import():
    """`pip install -e .[dev]` has to be enough to run everything in tools/."""
    dev = read("pyproject.toml").split("dev = [")[1].split("]")[0]
    assert "pytest" in dev
    assert "markdown" in dev, "tools/build_docs.py imports markdown"


def test_the_interpreter_itself_has_no_dependencies():
    """The headline install claim: Python 3.10+ and nothing else."""
    body = read("pyproject.toml").split("[project.optional-dependencies]")[0]
    assert "dependencies = [" not in body


# --------------------------------------------------------------------- CI

def test_ci_runs_the_test_suite():
    workflow = read(".github", "workflows", "ci.yml")
    assert "pytest tests/" in workflow


def test_ci_checks_that_generated_files_are_current():
    """A generated file committed stale is a lie that survives review."""
    workflow = read(".github", "workflows", "ci.yml")
    assert "git diff --exit-code" in workflow
    for builder in ("build_model_spec", "build_docs", "build_play",
                    "build_audit"):
        assert builder in workflow, f"CI never runs tools/{builder}.py"


def test_ci_verifies_the_two_evaluators_agree():
    assert "verify_chunks" in read(".github", "workflows", "ci.yml")


def test_ci_runs_on_more_than_one_operating_system():
    workflow = read(".github", "workflows", "ci.yml")
    assert "ubuntu-latest" in workflow and "macos-latest" in workflow


# ----------------------------------------------------------------- editors

def test_the_editor_grammar_is_valid_json():
    grammar = json.loads(read("editors", "frost.tmLanguage.json"))
    assert grammar["scopeName"] == "source.frost"
    assert grammar["fileTypes"] == ["frost"]


def test_every_grammar_pattern_compiles():
    grammar = json.loads(read("editors", "frost.tmLanguage.json"))
    for name, rule in grammar["repository"].items():
        for pattern in rule.get("patterns", [rule]):
            for key in ("match", "begin", "end"):
                if key in pattern:
                    re.compile(pattern[key])       # raises if malformed


def test_every_reserved_word_is_highlighted():
    """A word the parser treats as structural but the editor shows as a name
    is worse than no highlighting: it tells the reader the wrong thing."""
    grammar = json.loads(read("editors", "frost.tmLanguage.json"))
    covered = set()
    for name in ("block", "control", "keyword"):
        pattern = grammar["repository"][name]["match"]
        covered |= set(pattern.strip("\\b()").split("|"))
    missing = HARD_WORDS - covered
    assert not missing, f"not highlighted: {sorted(missing)}"


def test_no_word_is_highlighted_that_is_not_reserved():
    grammar = json.loads(read("editors", "frost.tmLanguage.json"))
    for name in ("block", "control", "keyword"):
        pattern = grammar["repository"][name]["match"]
        for word in pattern.strip("\\b()").split("|"):
            assert word in HARD_WORDS, \
                f"{word!r} is highlighted as a keyword but is not reserved"


def test_the_grammar_is_up_to_date_with_the_parser():
    """Regenerating must be a no-op, or someone changed HARD_WORDS and did
    not run tools/build_editors.py."""
    import subprocess
    import sys
    before = read("editors", "frost.tmLanguage.json")
    subprocess.run([sys.executable, os.path.join(REPO, "tools",
                                                 "build_editors.py")],
                   capture_output=True, check=True, cwd=REPO)
    assert read("editors", "frost.tmLanguage.json") == before, (
        "editors/ is out of date; run python tools/build_editors.py")


def test_the_vscode_manifest_points_at_the_grammar():
    package = json.loads(read("editors", "package.json"))
    grammar = package["contributes"]["grammars"][0]
    assert grammar["scopeName"] == "source.frost"
    assert os.path.exists(os.path.join(EDITORS,
                                       os.path.basename(grammar["path"])))
    language = package["contributes"]["languages"][0]
    assert os.path.exists(
        os.path.join(EDITORS, os.path.basename(language["configuration"])))


def test_the_editor_indent_rules_match_the_formatter():
    """Two implementations of the same rule; they must agree on the block
    keywords, or typing in an editor fights `--format`."""
    from frostlang.formatter import INDENT_AFTER, DEDENT_BEFORE
    config = json.loads(read("editors", "language-configuration.json"))
    rules = config["indentationRules"]
    assert rules["increaseIndentPattern"].lstrip("^\\s*") \
        == INDENT_AFTER.pattern.lstrip("^")
    assert rules["decreaseIndentPattern"].lstrip("^\\s*") \
        == DEDENT_BEFORE.pattern.lstrip("^")


# ------------------------------------------------------------------- docs

def test_the_contributing_guide_lists_the_real_builders():
    guide = read("CONTRIBUTING.md")
    for builder in os.listdir(os.path.join(REPO, "tools")):
        if builder.startswith("build_") and builder.endswith(".py"):
            assert builder in guide, f"CONTRIBUTING.md never mentions {builder}"
