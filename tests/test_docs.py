"""The documentation has to be true.

README.md, LANGUAGE.md and MODEL-SPEC.md are full of frost. MODEL-SPEC.md in
particular is meant to be dropped into a system prompt, so a model will copy
its syntax verbatim — a stale example there teaches every generated script the
wrong grammar. Every frost block in the docs is extracted here and parsed, and
every policy block is fed to the policy parser.

Fence convention in these files: a bare ``` block is frost, and a labelled
one (```text, ```bash, ```policy, ```ebnf) is whatever the label says.
"""

import os
import re

import pytest

from frostlang.parser import parse, ParseError
from frostlang.lexer import LexError
from frostlang.audit import parse_policy, PolicyError

from helpers import REPO

DOCS = ["README.md", "LANGUAGE.md", "MODEL-SPEC.md"]


def code_blocks(filename):
    """Yield (language, first_line_number, source) for every fenced block."""
    path = os.path.join(REPO, filename)
    with open(path) as fh:
        lines = fh.read().split("\n")

    out = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip() or "frost"
            start = i + 1
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            out.append((lang, start + 1, "\n".join(body)))
        i += 1
    return out


def blocks_of(lang):
    """Every block of one language across every doc, as pytest params."""
    found = []
    for doc in DOCS:
        for kind, line, body in code_blocks(doc):
            if kind == lang and body.strip():
                found.append(pytest.param(body, id=f"{doc}:{line}"))
    return found


FROST_BLOCKS = blocks_of("frost")
POLICY_BLOCKS = blocks_of("policy")


def test_the_extractor_found_the_documentation():
    """Without this, an extractor bug would silently empty the suite below."""
    assert len(FROST_BLOCKS) >= 40, \
        f"only found {len(FROST_BLOCKS)} frost blocks; the extractor is broken"
    assert len(POLICY_BLOCKS) >= 1
    per_doc = {doc for doc, *_ in
               (p.id.split(":") for p in FROST_BLOCKS)}
    assert per_doc == set(DOCS), f"no frost found in {set(DOCS) - per_doc}"


@pytest.mark.parametrize("source", FROST_BLOCKS)
def test_every_documented_frost_snippet_parses(source):
    try:
        parse(source)
    except (ParseError, LexError) as e:
        raise AssertionError(
            f"documented snippet does not parse: {e.msg} (line {e.line})"
            f"\n---\n{source}\n---") from e


@pytest.mark.parametrize("source", POLICY_BLOCKS)
def test_every_documented_policy_snippet_parses(source):
    try:
        rules = parse_policy(source)
    except PolicyError as e:
        raise AssertionError(
            f"documented policy does not parse: {e}\n---\n{source}\n---") from e
    assert rules, "policy block parsed to no rules"


# ------------------------------------------------------- claims about files

def test_every_file_named_in_the_readme_layout_exists():
    """The Layout section is a map; a wrong entry sends a reader nowhere."""
    with open(os.path.join(REPO, "README.md")) as fh:
        readme = fh.read()
    layout = readme.split("## Layout", 1)[1].split("```")[1]
    for match in re.finditer(r"^\s*(\S+\.(?:py|md|html|js))\s{2,}", layout,
                             re.M):
        rel = match.group(1)
        candidates = [rel, os.path.join("frostlang", rel),
                      os.path.join("tools", rel), os.path.join("web", rel)]
        assert any(os.path.exists(os.path.join(REPO, c)) for c in candidates), \
            f"README Layout names {rel}, which does not exist"


def test_every_usage_flag_is_a_real_flag():
    """The Usage block is what a reader copies; it must match the CLI."""
    from frostlang.cli import main
    import argparse
    import contextlib
    import io

    with open(os.path.join(REPO, "README.md")) as fh:
        readme = fh.read()
    usage = readme.split("## Usage", 1)[1].split("```")[1]
    documented = set(re.findall(r"--[a-z-]+", usage))

    help_text = io.StringIO()
    with contextlib.redirect_stdout(help_text):
        with contextlib.suppress(SystemExit):
            main(["--help"])
    real = set(re.findall(r"--[a-z-]+", help_text.getvalue()))

    missing = documented - real
    assert not missing, f"README documents flags that do not exist: {missing}"


def test_the_readme_status_section_matches_the_real_test_count():
    """A stale count in the README is the classic rotted claim."""
    import subprocess
    import sys
    with open(os.path.join(REPO, "README.md")) as fh:
        readme = fh.read()
    claimed = re.search(r"(\d+)\s+tests? cover", readme)
    if claimed is None:
        pytest.skip("the README no longer claims a test count")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True, text=True, cwd=REPO)
    actual = re.search(r"(\d+) tests? collected", result.stdout)
    assert actual, result.stdout[-2000:]
    assert int(claimed.group(1)) == int(actual.group(1)), (
        f"README claims {claimed.group(1)} tests; there are {actual.group(1)}")
