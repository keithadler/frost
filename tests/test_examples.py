"""The examples, end to end.

The examples carry a lot of weight in this repo: they are the formatter's
style reference, the audit page's input, and the first frost anyone reads. So
they get three kinds of check.

*Golden manifests.* Every example's `--explain` output is recorded under
tests/golden/. Any change to the static analysis shows up here as a readable
diff against a real script rather than as a broken unit test on a fragment.
Regenerate deliberately with:

    FROST_UPDATE_GOLDEN=1 python -m pytest tests/test_examples.py

*Real runs.* The examples that touch nothing outside the process are executed
and their stdout compared exactly.

*Refusals.* The two demonstration attacks must stay refused, and must never
run, a test that merely checked the verdict would still pass if the script
executed first.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from frostlang import __version__

from helpers import REPO, EXAMPLES, example_names

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
UPDATING = os.environ.get("FROST_UPDATE_GOLDEN") == "1"

NAMES = example_names()


def frost(*args, cwd=REPO, stdin=None):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, cwd=cwd, env=env,
                       input=stdin, timeout=60)
    return p.returncode, p.stdout, p.stderr


def check_golden(name, actual):
    """Compare against tests/golden/<name>, or write it when updating."""
    path = os.path.join(GOLDEN, name)
    if UPDATING:
        os.makedirs(GOLDEN, exist_ok=True)
        with open(path, "w") as fh:
            fh.write(actual)
        pytest.skip(f"updated {name}")
    assert os.path.exists(path), (
        f"no golden file for {name}; regenerate with FROST_UPDATE_GOLDEN=1")
    with open(path) as fh:
        expected = fh.read()
    assert actual == expected, (
        f"{name} changed.\n--- expected ---\n{expected}\n--- actual ---\n"
        f"{actual}")


def test_there_are_examples_to_check():
    """Keeps every parametrised test below from silently covering nothing."""
    assert len(NAMES) >= 7, NAMES


# --------------------------------------------------------- golden manifests

@pytest.mark.parametrize("name", NAMES)
def test_explain_manifest_is_unchanged(name):
    status, out, err = frost("--explain", f"examples/{name}")
    assert status in (0, 1), err
    check_golden(f"{name}.explain.txt", out)


@pytest.mark.parametrize("name", NAMES)
def test_explain_json_is_unchanged(name):
    status, out, err = frost("--explain", "--json", f"examples/{name}")
    assert status == 0, err
    # Re-serialise so the golden file is insensitive to key ordering.
    check_golden(f"{name}.explain.json",
                 json.dumps(json.loads(out), indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------- real runs

def test_hello_runs():
    status, out, err = frost("examples/hello.frost")
    assert status == 0, err
    # The date is the only part that moves.
    normalised = re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", out)
    assert normalised == (
        "What frost looks like:\n"
        "Today is <date>\n"
        "hello world\n"
        "11 characters\n")


def test_tour_runs_and_is_fully_deterministic():
    status, out, err = frost("examples/tour.frost")
    assert status == 0, err
    assert out == (
        "=== frost " + __version__ + " ===\n"
        "second person is grace\n"
        "roles: engineer, cryptanalyst\n"
        "total name characters: 12\n"
        "alan is a cryptanalyst\n"
        "first two sorted: alpha, bravo\n")


LOG = """\
10.0.0.1 - GET 200 /index.html
10.0.0.2 - GET 404 /missing
10.0.0.1 - GET 500 /api/orders
10.0.0.1 - GET 200 /about
10.0.0.3 - GET 503 /api/users
10.0.0.2 - GET 404 /nope
"""


def test_logreport_runs_against_a_fixture(tmp_path):
    log = tmp_path / "access.log"
    log.write_text(LOG)
    status, out, err = frost("examples/logreport.frost", str(log))
    assert status == 0, err
    assert "requests:      6" in out
    assert "server errors: 2" in out
    assert "not found:     2" in out
    assert "  3 requests from 10.0.0.1" in out


def test_logreport_alerts_and_exits_one_on_too_many_errors(tmp_path):
    log = tmp_path / "access.log"
    log.write_text(LOG + "10.0.0.9 - GET 500 /x\n10.0.0.9 - GET 502 /y\n")
    status, out, err = frost("examples/logreport.frost", str(log))
    assert status == 1
    assert "ALERT" in err


def test_logreport_without_an_argument_exits_two():
    status, _, err = frost("examples/logreport.frost")
    assert status == 2
    assert "usage:" in err


def test_logreport_on_a_missing_file_exits_one():
    status, _, err = frost("examples/logreport.frost", "/no/such.log")
    assert status == 1
    assert "no log file at" in err


def test_deploy_rejects_an_unknown_environment():
    status, _, err = frost("examples/deploy.frost", "wherever")
    assert status == 2
    assert "unknown environment" in err


def test_healthcheck_without_an_argument_exits_two():
    status, _, err = frost("examples/healthcheck.frost")
    assert status == 2
    assert "usage:" in err


def test_backup_without_arguments_exits_two():
    status, _, err = frost("examples/backup.frost")
    assert status == 2
    assert "usage:" in err


# --------------------------------------------------------------- refusals

ATTACKS = ["danger.frost", "exfiltrate.frost"]


@pytest.mark.parametrize("name", ATTACKS)
def test_the_demonstration_attacks_are_refused_before_running(name, tmp_path):
    """Exit 3 is necessary but not sufficient: nothing may have run first."""
    canary = tmp_path / "canary"
    canary.mkdir()
    # Absolute paths, but run from the empty directory: if any part of the
    # script executed, it would leave a trace here.
    status, _, err = frost("--policy",
                           os.path.join(EXAMPLES, "production.policy"),
                           os.path.join(EXAMPLES, name), cwd=str(canary))
    assert status == 3
    assert "REFUSED" in err
    assert "was not run" in err
    assert os.listdir(canary) == [], "the refused script left something behind"


@pytest.mark.parametrize("name", ATTACKS)
def test_the_demonstration_attacks_explain_as_dangerous(name):
    status, out, _ = frost("--explain", f"examples/{name}")
    assert status == 1
    assert "Verdict: dangerous" in out


@pytest.mark.parametrize("name", NAMES)
def test_every_example_passes_check(name):
    status, out, err = frost("--check", f"examples/{name}")
    assert status == 0, err


@pytest.mark.parametrize("name", NAMES)
def test_the_benign_examples_pass_the_production_policy(name):
    if name in ATTACKS:
        pytest.skip("this one is meant to be refused")
    status, _, err = frost("--policy", "examples/production.policy",
                           "--check", f"examples/{name}")
    assert status == 0, err
