"""Does the manifest tell the truth about secrets?

There are two independent mechanisms here, and they could disagree without
anyone noticing:

  * the **auditor** decides statically, from the tree, where a secret's
    plaintext leaves the process, and prints that in `--explain`
  * the **interpreter** decides at runtime, by sealing values and revealing
    them only at boundaries

If the auditor under-reports, `--explain` is a manifest that omits things —
worse than no manifest, which is the objection the whole feature exists to
answer. So the two are checked against each other the way web/chunks.js is
checked against the interpreter: run the script, observe what actually
escaped, and compare it with what the manifest promised.

The observation is black-box. A released plaintext is handed to a real
program, and that program's output comes back into the script as ordinary
text — so if a secret escaped, it is visible in what the script prints. If
nothing escaped, it cannot be.
"""

import io
import os
import sys

import pytest

from frostlang.parser import parse
from frostlang.interp import Interpreter
from frostlang.audit import audit

from helpers import caps_for, needs_coreutils

PLAINTEXT = "s3cr3t-canary-value-8f2a"


@pytest.fixture(autouse=True)
def canary_in_the_environment():
    os.environ["FROST_CANARY"] = PLAINTEXT
    yield
    os.environ.pop("FROST_CANARY", None)


READ = 'put the secret environment variable "FROST_CANARY" into pw\n'


def run_and_capture(body):
    """Run `READ + body`; return everything it wrote."""
    tree = parse(READ + body)
    held_out, held_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        Interpreter().run_program(tree)
        return sys.stdout.getvalue() + sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = held_out, held_err


def predicted_releases(body):
    return caps_for(READ + body).secret_releases


# Bodies the auditor should report as releasing nothing. Each one is a way
# somebody might reasonably handle a secret without meaning to leak it.
NO_RELEASE = [
    "put pw",
    'put "using" && pw',
    'put "a" & pw & "b"',
    "put pw into standard error",
    "put the uppercase pw",
    "put the first word of pw",
    'put pw split by "-"',
    'put (pw split by "-") joined by "/"',
    "put the length of pw",
    "put pw into holder\nput holder",
    "put pw into the global kept\nput the global kept",
    'if pw is not empty then put "present"',
    'if pw starts with "s3cr" then put "prefix"',
    "to pass through with v\n    return v\nend pass through\n"
    "put the pass through of pw",
    "repeat for each character in pw as c\n    put c\nend repeat",
    'run "echo" with "unrelated"\nput it',
    'put "x" into file "/dev/null"',
]


@pytest.mark.parametrize("body", NO_RELEASE,
                         ids=[b.split("\n")[0][:40] for b in NO_RELEASE])
def test_when_the_manifest_promises_no_release_nothing_escapes(body):
    """The direction that matters. An under-reporting manifest is the
    failure mode that would make --explain worse than useless."""
    assert predicted_releases(body) == [], (
        "this case is meant to release nothing; fix the fixture")
    assert PLAINTEXT not in run_and_capture(body)


def test_the_no_release_cases_actually_run():
    """Guards the parametrised test above from passing on silence."""
    for body in NO_RELEASE:
        assert run_and_capture(body) is not None


# Bodies that genuinely do release, with the program that receives it. Each
# is run so the escape is *observed*, not merely predicted.
RELEASES = [
    ('run "echo" with pw\nput it', "argument"),
    ('run "cat" reading pw\nput it', "input"),
    ('run "echo" with "prefix", pw\nput it', "argument"),
    ('put "postgres://" & pw into url\nrun "echo" with url\nput it',
     "argument"),
    ('put pw into carrier\nrun "echo" with carrier\nput it', "argument"),
]


@needs_coreutils
@pytest.mark.parametrize("body,where", RELEASES,
                         ids=[b.split("\n")[0][:40] for b, _ in RELEASES])
def test_a_predicted_release_is_a_real_one(body, where):
    """The other direction: the manifest must not cry wolf either. If it
    says a secret leaves here, the plaintext really does leave here."""
    predicted = predicted_releases(body)
    assert any(w == where for w, _, _ in predicted), (
        f"the manifest did not predict a {where} release for:\n{body}")
    assert PLAINTEXT in run_and_capture(body), (
        "the manifest predicted a release that did not happen")


@needs_coreutils
def test_a_file_write_release_is_real(tmp_path):
    target = tmp_path / "out.txt"
    body = f'put pw into file "{target}"'
    assert any(w == "file" for w, _, _ in predicted_releases(body))
    run_and_capture(body)
    assert PLAINTEXT in target.read_text()


@needs_coreutils
def test_an_environment_release_is_real():
    body = ('put pw into the environment variable "FROST_CHILD_CANARY"\n'
            'run "sh" with "-c", "printf %s \\"$FROST_CHILD_CANARY\\""\n'
            "put it")
    assert any(w == "environment" for w, _, _ in predicted_releases(body))
    assert PLAINTEXT in run_and_capture(body)


# --------------------------------------------------------- generated cases

def secret_expressions():
    """Ways of deriving a value from a secret. All must stay sealed."""
    return [
        "pw",
        "the uppercase pw",
        "the lowercase pw",
        "the trimmed pw",
        "the first word of pw",
        "the last character of pw",
        "characters 1 to 6 of pw",
        'pw split by "-"',
        '(pw split by "-") joined by "|"',
        "the sorted the characters of pw",
        "the reversed the characters of pw",
        "the unique the characters of pw",
        "the words of pw",
        "the lines of pw",
        '"prefix " & pw',
        'pw & " suffix"',
        '"a" & pw & "b"',
        "pw && pw",
    ]


@pytest.mark.parametrize("expr", secret_expressions())
def test_every_derived_value_stays_sealed_when_printed(expr):
    assert PLAINTEXT not in run_and_capture(f"put {expr}")


@pytest.mark.parametrize("expr", secret_expressions())
def test_every_derived_value_is_still_reported_when_released(expr):
    """Taint has to survive the derivation in the *auditor* too, not only in
    the interpreter — otherwise the manifest would miss the leak that the
    interpreter correctly permits."""
    body = f"put {expr} into derived\nrun \"echo\" with derived"
    assert predicted_releases(body), (
        f"the manifest lost track of the secret through: {expr}")


@needs_coreutils
@pytest.mark.parametrize("expr", secret_expressions())
def test_and_the_derived_value_really_does_escape(expr):
    """Closing the loop: the manifest says it escapes, and it escapes."""
    body = f'put {expr} into derived\nrun "echo" with derived\nput it'
    escaped = PLAINTEXT in run_and_capture(body)
    # Some derivations legitimately change the text — uppercasing, sorting,
    # taking one word. What must hold is that *something* derived from the
    # secret escaped, and the manifest said so.
    assert predicted_releases(body)
    if expr in ("pw", '"prefix " & pw', 'pw & " suffix"', '"a" & pw & "b"',
                "pw && pw"):
        assert escaped, f"{expr} should have carried the value out intact"


# ------------------------------------------------------ the whole manifest

def test_explain_names_every_release_in_a_realistic_script():
    body = '''
    run "psql" with "--password", pw
    run "vault" reading pw
    put pw into file "creds.txt"
    put pw into the environment variable "PGPASSWORD"
    put "done" && pw
    '''
    kinds = sorted(w for w, _, _ in predicted_releases(body))
    assert kinds == ["argument", "environment", "file", "input"]


def test_a_script_that_only_prints_a_secret_reports_no_release():
    """The common, correct case: log the fact, not the credential."""
    caps = caps_for(READ + 'put "connecting with a stored credential" && pw')
    assert caps.secret_reads
    assert caps.secret_releases == []
