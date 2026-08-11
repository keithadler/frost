"""The documentation has to be true.

README.md, LANGUAGE.md and MODEL-SPEC.md are full of frost. MODEL-SPEC.md in
particular is meant to be dropped into a system prompt, so a model will copy
its syntax verbatim, a stale example there teaches every generated script the
wrong grammar. Every frost block in the docs is extracted here and parsed, and
every policy block is fed to the policy parser.

Fence convention in these files: a bare ``` block is frost, and a labelled
one (```text, ```bash, ```policy, ```ebnf) is whatever the label says.
"""

import json
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
    # A snippet that imports cannot have its handler names resolved on its
    # own: the names it may call are its own plus what it imported, and the
    # imported file is not here. That is the same reason the module loader
    # parses without resolution and checks names once the closure is known.
    resolve = not re.search(r"^\s*use\s+\"", source, re.M)
    try:
        parse(source, resolve=resolve)
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
        candidates = [rel] + [os.path.join(d, rel) for d in
                              ("frostlang", "tools", "web", "tests")]
        assert any(os.path.exists(os.path.join(REPO, c)) for c in candidates), \
            f"README Layout names {rel}, which does not exist"


def test_the_documented_reserved_words_are_the_real_ones():
    """The list of reserved words is the one thing a reader will trust
    absolutely when picking a variable name, and it had already drifted."""
    from frostlang.parser import HARD_WORDS
    with open(os.path.join(REPO, "LANGUAGE.md")) as fh:
        text = fh.read()
    block = re.search(
        r"### Reserved words\n\nThese cannot appear inside a name:\n\n"
        r"```text\n(.*?)\n```", text, re.S)
    assert block, "the reserved words section is missing"
    documented = set(block.group(1).split())
    assert documented == HARD_WORDS, (
        f"documented but not reserved: {sorted(documented - HARD_WORDS)}; "
        f"reserved but not documented: {sorted(HARD_WORDS - documented)}")


def test_the_words_said_to_be_available_really_are():
    """The section promises `line count` and friends still work. If one of
    them ever became reserved, that promise would silently be a lie."""
    from frostlang.parser import HARD_WORDS
    with open(os.path.join(REPO, "LANGUAGE.md")) as fh:
        text = fh.read()
    claim = text.split("Notably absent:", 1)[1].split("These are recognised")[0]
    for word in re.findall(r"`([a-z]+)`", claim):
        assert word not in HARD_WORDS, \
            f"LANGUAGE.md says {word!r} is available for names, but it is reserved"


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
    """A stale count in the README is the classic rotted claim.

    Only meaningful with every optional extra installed. Without
    `cryptography` the keystore module is not collected at all, so the count
    is legitimately lower, and asserting the full number there made CI fail
    on a job whose entire purpose was to prove frost works without it.
    """
    import subprocess
    import sys

    pytest.importorskip(
        "cryptography",
        reason="fewer tests are collected without the optional extras, so "
               "the README's full count is not the right thing to compare")

    with open(os.path.join(REPO, "README.md")) as fh:
        readme = fh.read()
    claimed = re.search(r"([\d,]+)\s+tests? cover", readme)
    if claimed is None:
        pytest.skip("the README no longer claims a test count")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        capture_output=True, text=True, cwd=REPO)
    actual = re.search(r"(\d+) tests? collected", result.stdout)
    assert actual, result.stdout[-2000:]
    assert int(claimed.group(1).replace(",", "")) == int(actual.group(1)), (
        f"README claims {claimed.group(1)} tests; there are {actual.group(1)}")


def test_nothing_here_uses_an_em_dash():
    """A house style, enforced rather than remembered.

    Swept once by hand across the documentation, the source, the tests and the
    manifest's own output. A sweep that is not enforced is a sweep somebody
    repeats in six months, and the ones in program output had gone stale in
    the docs before anyone noticed: samples pasted into README.md still showed
    a separator the manifest had stopped printing.

    Both characters, because an en dash reads as an em dash to everyone who
    is not setting type.
    """
    offenders = []
    roots = [("", [f for f in os.listdir(REPO) if f.endswith(".md")]),
             ("frostlang", None), ("tests", None), ("tools", None)]
    for folder, names in roots:
        base = os.path.join(REPO, folder) if folder else REPO
        if names is None:
            names = [f for f in sorted(os.listdir(base)) if f.endswith(".py")]
        for name in names:
            path = os.path.join(base, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    # Built rather than written, so this file does not
                    # trip its own check.
                    for dash in (chr(8212), chr(8211)):
                        if dash in line:
                            offenders.append(
                                f"{os.path.join(folder, name)}:{number}")
    assert not offenders, (
        "em dashes are house style here, and these have one:\n  "
        + "\n  ".join(offenders[:40]))


# ------------------------------------- pasted manifest output in the docs
#
# README.md and LANGUAGE.md quote `--explain` output. Those samples drifted
# once already: the manifest stopped using a dash between the subject and the
# line number and every pasted sample still showed one, with nothing in the
# suite to notice.
#
# Generating them is not possible without rewriting the documentation, because
# most quote a fragment of the manifest of a script that is never shown in
# full, and printing the whole thing would make the page worse. What is
# checkable is that a pasted sample uses vocabulary the manifest can actually
# produce: a section header it really emits, and the separator it really
# prints. That is exactly the drift that happened, and it is the drift that
# will happen again.

def manifest_vocabulary():
    """Section headers the real manifest emits, from the real examples."""
    import contextlib
    import io as _io

    from frostlang import cli

    heads = set()
    folder = os.path.join(REPO, "examples")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".frost"):
            continue
        # The whole of --explain, not just describe(): the driver adds
        # sections of its own, and a vocabulary missing them would call a
        # correct sample stale.
        captured = _io.StringIO()
        with contextlib.redirect_stdout(captured):
            with contextlib.suppress(SystemExit):
                cli.main(["--explain", os.path.join(folder, name)])
        for line in captured.getvalue().split("\n"):
            if line.endswith(":") and not line.startswith(" "):
                heads.add(line)
    return heads


def manifest_samples():
    """Every fenced block in the docs that quotes manifest output."""
    import re

    found = []
    for doc in ("README.md", "LANGUAGE.md"):
        for kind, line, bodytext in code_blocks(doc):
            if kind not in ("text", ""):
                continue
            if not re.search(r"\bline \d", bodytext):
                continue
            if not any(l.rstrip().endswith(":") for l in bodytext.split("\n")):
                continue
            found.append((doc, line, bodytext))
    return found


def test_the_extractor_found_the_pasted_manifests():
    """Without this the two tests below would pass on an empty list, which is
    how a check that has stopped looking reports success."""
    assert len(manifest_samples()) >= 2, \
        "no pasted manifest output found; the extractor is broken"


def test_every_pasted_manifest_uses_the_real_separator():
    import re

    wrong = []
    for doc, at, bodytext in manifest_samples():
        for offset, line in enumerate(bodytext.split("\n")):
            # A finding prints "[DANGER ] line 12", which is its own format
            # and not the subject-and-location columns this checks.
            if line.lstrip().startswith("["):
                continue
            if re.search(r"\bline \d", line) and "at line" not in line:
                wrong.append(f"{doc}:{at + offset}  {line.strip()}")
    assert not wrong, (
        "the manifest prints 'at line N'; these samples show something else "
        "and have gone stale:\n  " + "\n  ".join(wrong))


def test_every_pasted_manifest_uses_a_real_section_header():
    known = manifest_vocabulary()
    unknown = []
    for doc, at, bodytext in manifest_samples():
        for offset, line in enumerate(bodytext.split("\n")):
            stripped = line.rstrip()
            if (stripped.endswith(":") and stripped == stripped.lstrip()
                    and stripped not in known):
                unknown.append(f"{doc}:{at + offset}  {stripped}")
    assert not unknown, (
        "these look like manifest sections and the manifest never prints "
        "them:\n  " + "\n  ".join(unknown)
        + "\n  it prints: " + ", ".join(sorted(known)))


# ------------------------------------------- the published output schema

def test_the_explain_schema_describes_every_recorded_manifest():
    """`--explain --json` carries "schema": 1 and nothing said what 1 was.

    A schema is a promise to whoever consumes the output, and a promise
    nothing checks is a comment. Every golden manifest in the suite is
    validated against it, so the schema cannot drift from the program: the
    goldens are regenerated from real runs, and a field that changed shape
    fails here rather than in somebody's parser.
    """
    jsonschema = pytest.importorskip("jsonschema")

    with open(os.path.join(REPO, "schema", "explain-1.schema.json")) as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    folder = os.path.join(REPO, "tests", "golden")
    checked = 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".explain.json"):
            continue
        with open(os.path.join(folder, name)) as fh:
            payload = json.load(fh)
        errors = sorted(validator.iter_errors(payload), key=str)
        assert not errors, (
            f"{name} does not match the published schema:\n  "
            + "\n  ".join(f"{list(e.path)}: {e.message}" for e in errors[:5]))
        checked += 1

    assert checked >= 10, f"only checked {checked} manifests; the walk broke"


def test_the_schema_would_reject_a_manifest_missing_a_field():
    """The guard, pointed at something it must refuse. A schema that accepts
    anything validates nothing, and would pass the test above unchanged."""
    jsonschema = pytest.importorskip("jsonschema")

    with open(os.path.join(REPO, "schema", "explain-1.schema.json")) as fh:
        schema = json.load(fh)
    validator = jsonschema.Draft202012Validator(schema)

    assert list(validator.iter_errors({"script": "x.frost"})), \
        "the schema accepts a manifest with almost nothing in it"
    assert list(validator.iter_errors(
        {"script": "x", "summary": "s", "verdict": "maybe", "commands": [],
         "reads": [], "writes": [], "deletes": [], "environment": [],
         "exits": [], "findings": []})), \
        "the schema accepts a verdict frost never produces"
