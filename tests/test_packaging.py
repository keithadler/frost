"""The parts that are not the language: packaging, CI, editor support.

These rot silently. A version declared in two places drifts, a CI workflow
stops mentioning a Python version the package claims to support, and a
hand-written keyword list for an editor falls behind the parser, and none of
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


def _f_strings_needing_312(path):
    """Lines holding an f-string that only Python 3.12 and later accept.

    Two shapes, both legal now and a SyntaxError on 3.10:

      * a single-delimiter f-string whose expression runs onto the next line
      * an expression reusing the quote character that delimits the f-string

    Decided with the real tokenizer rather than a regex, because working out
    where an f-string ends is exactly the job a regex gets wrong. On an
    interpreter older than 3.12 there is no FSTRING_START token and nothing to
    find, which is the right answer: that interpreter would already have
    refused the file outright.
    """
    import tokenize

    if not hasattr(tokenize, "FSTRING_START"):
        return []

    bad = []
    with open(path, "rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))

    depth, opener, quote = 0, None, None
    for tok in tokens:
        if tok.type == tokenize.FSTRING_START:
            depth += 1
            if depth == 1:
                opener = tok
                quote = tok.string.lstrip("fFrRbB")
        elif tok.type == tokenize.FSTRING_END:
            depth -= 1
            if depth == 0:
                triple = quote.startswith('"""') or quote.startswith("'''")
                if not triple and tok.end[0] != opener.start[0]:
                    bad.append((opener.start[0], "runs onto the next line"))
                opener, quote = None, None
        elif depth and quote and tok.type == tokenize.STRING:
            if tok.string.lstrip("fFrRbB").startswith(quote):
                bad.append((tok.start[0],
                            "reuses the quote that delimits it"))
    return bad


def test_no_module_uses_an_f_string_the_oldest_python_rejects():
    """The floor is a promise, and this is how it gets broken.

    An f-string written across two lines is legal from 3.12 and a SyntaxError
    on 3.10. Written on a newer machine it passes every local check, imports
    fine, and fails at *collection* on the oldest CI job, after the push,
    which is the wrong place to learn it.

    The first version of this test asked `ast.parse` for `feature_version`
    (3, 10) and passed happily on the exact line that had just broken CI:
    that flag governs a short list of grammar features and does not reach
    f-string tokenising at all. A check decided by something other than the
    thing under test is worse than none, so the one below verifies this guard
    against the source that actually failed.
    """
    roots = [os.path.join(REPO, "frostlang"), os.path.join(REPO, "tools")]
    offences, checked = [], 0
    for root in roots:
        for folder, _, names in os.walk(root):
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(folder, name)
                for line, why in _f_strings_needing_312(path):
                    offences.append(
                        os.path.relpath(path, REPO) + ":" + str(line)
                        + " " + why)
                checked += 1

    assert checked > 20, "only checked %d files; the walk is wrong" % checked
    assert not offences, (
        "these need Python 3.12, and pyproject promises 3.10:\n  "
        + "\n  ".join(offences))


BROKE_CI = '\n'.join([
    "def f(name, line):",
    '    return (f"reading the environment {name or ' + "'(built at '",
    "             '" + 'runtime)' + "'" + '}", line)',
    "",
])


def test_that_guard_would_have_caught_it():
    """The guard, pointed at the source that actually broke the 3.10 job.

    Which mechanism does the catching depends on the interpreter running the
    suite, and both are asserted rather than one being skipped. On 3.12 and
    later the source is legal and the tokenizer walk has to flag it. On
    anything older there is no FSTRING_START to walk and the guard finds
    nothing, correctly: that interpreter refuses the source outright, so the
    thing to assert is that it does. The first version asserted only the first
    case and failed on every job it was written to protect.
    """
    import tempfile
    import tokenize

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(BROKE_CI)
        path = fh.name
    try:
        if hasattr(tokenize, "FSTRING_START"):
            assert _f_strings_needing_312(path), (
                "the guard does not catch the syntax it exists to catch")
        else:
            with pytest.raises(SyntaxError):
                compile(BROKE_CI, path, "exec")
            assert _f_strings_needing_312(path) == [], (
                "nothing to find before 3.12; the walk should stay quiet")
    finally:
        os.unlink(path)


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


def test_the_chunk_corpus_is_not_passed_to_node_on_the_command_line():
    """It is over a megabyte of JSON, under macOS's argument limit and over
    Linux's, so as argv it passed locally and failed in CI with
    `Argument list too long`. It goes in on stdin instead."""
    source = read("tools", "verify_chunks.py")
    assert "process.argv" not in source
    assert "input=json.dumps(cases)" in source


def test_the_corpus_really_is_too_big_for_argv():
    """Guards the test above from becoming a rule nobody needs: if the corpus
    ever shrank below the limit, the constraint would be invisible."""
    import sys
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import verify_chunks
    payload = json.dumps([{"expr": e, "subject": s}
                          for s in verify_chunks.SUBJECTS.values()
                          for e in verify_chunks.corpus()])
    # Linux caps a single argument at 128 KiB regardless of total ARG_MAX.
    assert len(payload) > 128 * 1024, (
        f"the corpus is only {len(payload)} bytes; argv would now work, and "
        f"the stdin requirement above would be untested folklore")


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
