"""Shared test helpers.

Every test module imports from here rather than re-deriving the plumbing, so
there is exactly one definition of "run this source and give me stdout".
"""

import io
import os
import shutil
import sys
import textwrap

import pytest

from frostlang.parser import parse
from frostlang.interp import Interpreter, FrostError
from frostlang.audit import audit, find_dangers
from frostlang.repl import Repl

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(REPO, "examples")


# ---------------------------------------------------------------- running

def run(src, argv=None, cwd=None):
    """Run a script, return (stdout, exit status)."""
    src = textwrap.dedent(src)
    tree = parse(src)
    interp = Interpreter(argv=argv or [], cwd=cwd)
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        status = interp.run_program(tree)
        return sys.stdout.getvalue(), status
    finally:
        sys.stdout = old


def out(src, **kw):
    """Run a script and return its stdout, stripped."""
    return run(src, **kw)[0].strip()


def run_failing(src, **kw):
    """Run a script expected to raise. Returns (stdout, the FrostError).

    `run` loses stdout when the script raises, which is exactly the output a
    cleanup-block test needs to see.
    """
    src = textwrap.dedent(src)
    tree = parse(src)
    interp = Interpreter(argv=kw.get("argv") or [], cwd=kw.get("cwd"))
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        interp.run_program(tree)
        raise AssertionError("the script was expected to fail, but did not")
    except FrostError as e:
        return sys.stdout.getvalue(), e
    finally:
        sys.stdout = old


def err(src, **kw):
    """Run a script and return its stderr, stripped."""
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        run(src, **kw)
        return sys.stderr.getvalue().strip()
    finally:
        sys.stderr = old


# ------------------------------------------------------------- analysis

def caps_for(src):
    return audit(parse(textwrap.dedent(src)))


def dangers_for(src):
    return find_dangers(caps_for(src))


def titles(src):
    return [f.title for f in dangers_for(src)]


def severities(src, needle):
    """Severities of every finding whose title contains `needle`."""
    return [f.severity for f in dangers_for(src) if needle in f.title]


# ---------------------------------------------------------------- repl

def repl_lines(*lines, subject=None):
    buf = io.StringIO()
    r = Repl(subject=subject, out=buf)
    for line in lines:
        r.handle(line)
    return buf.getvalue().strip().split("\n")


# ------------------------------------------------------------- examples

def example(name):
    """Source of an example script, by bare name or filename."""
    if not name.endswith((".frost", ".policy")):
        name += ".frost"
    with open(os.path.join(EXAMPLES, name)) as fh:
        return fh.read()


def example_names():
    return sorted(n for n in os.listdir(EXAMPLES) if n.endswith(".frost"))


def example_capabilities(name):
    """Capabilities of an example over its whole import closure.

    An example that imports cannot be audited by parsing one file: the point
    of the module design is that the manifest covers the closure, so the
    tests have to ask the same question the CLI does.
    """
    from frostlang import modules as M
    from frostlang.program_audit import audit_program
    if not name.endswith(".frost"):
        name += ".frost"
    return audit_program(M.load(os.path.join(EXAMPLES, name))).merged


# ------------------------------------------------------ platform guards

_HAS_SLEEP = shutil.which("sleep") is not None
_HAS_COREUTILS = all(shutil.which(c) for c in ("true", "false", "echo"))

needs_sleep = pytest.mark.skipif(not _HAS_SLEEP, reason="no 'sleep' on PATH")
needs_coreutils = pytest.mark.skipif(
    not _HAS_COREUTILS, reason="no true/false/echo on PATH")
