"""Comparing two versions, masking what a child prints, and saying what a
policy would have to permit.

Each answers a question somebody asks after a refusal or before a merge, and
each is deliberately less than it could be. The diff compares capabilities and
not behaviour. The mask is exact-match and not detection. The policy report is
a report and not a patch, because the thing asking is usually the thing being
constrained.
"""

import os
import subprocess
import sys

import pytest

from frostlang.audit import audit, parse_policy, check
from frostlang.parser import parse
from frostlang.whynot import explain_refusals

from helpers import REPO


def frost(*args, cwd=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    env.pop("FROST_AUTOMATED", None)
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


V1 = 'run "echo" with "deploying"\nput it\n'
V2 = (V1 + 'run "curl" with "https://telemetry.example" within 30 seconds\n')


# ------------------------------------------------------------------- diff

def test_a_diff_names_what_the_change_added(tmp_path):
    a, b = script(tmp_path, "a.frost", V1), script(tmp_path, "b.frost", V2)
    status, out, err = frost("diff", a, b, cwd=str(tmp_path))
    assert status == 3, err
    assert "wider:    it can now run curl" in out
    assert "it can now reach telemetry.example" in out


def test_a_diff_that_removes_a_capability_does_not_fail(tmp_path):
    """Narrowing is not a finding. Only a widening is the answer CI acts on,
    exactly as for an approval."""
    a, b = script(tmp_path, "a.frost", V2), script(tmp_path, "b.frost", V1)
    status, out, _ = frost("diff", a, b, cwd=str(tmp_path))
    assert status == 0
    assert "narrower:" in out


def test_an_unchanged_pair_says_so(tmp_path):
    a, b = script(tmp_path, "a.frost", V1), script(tmp_path, "b.frost", V1)
    status, out, _ = frost("diff", a, b, cwd=str(tmp_path))
    assert (status, "unchanged" in out) == (0, True)


def test_a_diff_runs_neither_script(tmp_path):
    a = script(tmp_path, "a.frost", 'put "SHOULD NOT APPEAR"\n')
    b = script(tmp_path, "b.frost", 'put "NOR THIS"\n')
    _, out, _ = frost("diff", a, b, cwd=str(tmp_path))
    assert "SHOULD NOT APPEAR" not in out and "NOR THIS" not in out


def test_a_diff_of_something_that_does_not_parse_says_which(tmp_path):
    a = script(tmp_path, "a.frost", V1)
    b = script(tmp_path, "b.frost", "if 1 is 1\n")
    status, _, err = frost("diff", a, b, cwd=str(tmp_path))
    assert status == 2
    assert "b.frost does not parse" in err


def test_diff_needs_two_scripts():
    status, _, err = frost("diff", "only-one.frost")
    assert status == 2
    assert "usage: frost diff" in err


# ------------------------------------------------------------- the mask

def test_a_secret_a_program_prints_back_is_masked(tmp_path):
    """The ordinary leak: a credential is handed to a program and the program
    puts it in an error message, which goes to a build log kept for a year."""
    (tmp_path / "pw.txt").write_text("hunter2secret")
    path = script(tmp_path, "s.frost",
                  'put the secret file "pw.txt" into pw\n'
                  'try to run "sh" with "-c", "echo failed for hunter2secret"\n'
                  "put it\n")
    status, out, err = frost(path, cwd=str(tmp_path))
    assert "hunter2secret" not in out + err
    assert "«secret pw.txt»" in out


def test_it_is_masked_on_standard_error_too(tmp_path):
    (tmp_path / "pw.txt").write_text("hunter2secret")
    path = script(tmp_path, "s.frost",
                  'put the secret file "pw.txt" into pw\n'
                  'try to run "sh" with "-c", '
                  '"echo failed for hunter2secret >&2"\n'
                  'put "saw:" && the error output\n')
    _, out, err = frost(path, cwd=str(tmp_path))
    assert "hunter2secret" not in out + err


def test_a_short_secret_is_not_used_as_a_mask(tmp_path):
    """A one-character secret would match everywhere and turn every line into
    markers, which is a redaction nobody can read and therefore one people
    switch off."""
    (tmp_path / "pw.txt").write_text("x")
    path = script(tmp_path, "s.frost",
                  'put the secret file "pw.txt" into pw\n'
                  'try to run "echo" with "extra text here"\nput it\n')
    _, out, _ = frost(path, cwd=str(tmp_path))
    assert "extra text here" in out


def test_output_with_no_secret_is_untouched(tmp_path):
    path = script(tmp_path, "s.frost",
                  'try to run "echo" with "ordinary output"\nput it\n')
    _, out, _ = frost(path, cwd=str(tmp_path))
    assert "ordinary output" in out


def test_the_mask_does_not_claim_to_be_detection():
    """It scrubs values it was told about. It does not recognise a credential
    by shape, and the docstring says so rather than implying otherwise."""
    from frostlang.interp import Interpreter
    assert "does not detect sensitive data by shape" in Interpreter.mask.__doc__
    assert "pipe" in Interpreter.mask.__doc__, \
        "the pipe hole should be named where somebody will read it"


# --------------------------------------------------- what would have to change

def test_a_refusal_says_what_would_have_to_change():
    caps = audit(parse('run "curl" with "https://x.example" within 30 seconds\n'
                       "put it\n"))
    rules = parse_policy('forbid running "curl"\n')
    report = explain_refusals(check(caps, rules), rules)
    assert "What would have to change" in report
    assert 'forbid running "curl"' in report


def test_it_says_what_the_change_would_allow_beyond_this_script():
    """A policy change is global. A minimal-delta framing hides exactly the
    fact a reviewer needs."""
    caps = audit(parse('run "curl" with "https://x.example" within 30 seconds\n'
                       "put it\n"))
    rules = parse_policy('forbid running "curl"\n')
    report = explain_refusals(check(caps, rules), rules)
    assert "in every script this policy covers" in report


def test_an_allow_list_is_widened_rather_than_deleted():
    """Narrowest is not smallest. Offering only the deletion is offering the
    change a tired reviewer will take."""
    caps = audit(parse('run "curl" with "https://new.example" '
                       "within 30 seconds\nput it\n"))
    rules = parse_policy('require reaching only "api.github.com"\n')
    report = explain_refusals(check(caps, rules), rules)
    assert 'require reaching only "api.github.com", "new.example"' in report


def test_nothing_is_suggested_when_nothing_was_refused():
    caps = audit(parse('put "x"\n'))
    rules = parse_policy('forbid running "curl"\n')
    assert explain_refusals(check(caps, rules), rules) == ""


def test_an_automated_run_is_not_handed_its_own_permission_slip():
    """The thing asking is usually the thing being constrained."""
    caps = audit(parse('run "curl" with "https://x.example" within 30 seconds\n'
                       "put it\n"))
    rules = parse_policy('forbid running "curl"\n')
    report = explain_refusals(check(caps, rules), rules, automated=True)
    assert "decision for a person" in report
    assert 'forbid running "curl"' not in report


def test_the_report_never_writes_a_policy(tmp_path):
    policy = tmp_path / "p.policy"
    policy.write_text('forbid running "curl"\n')
    before = policy.read_text()
    path = script(tmp_path, "s.frost",
                  'run "curl" with "https://x.example" within 30 seconds\n'
                  "put it\n")
    status, _, err = frost("--policy", str(policy), path, cwd=str(tmp_path))
    assert status == 3
    assert "What would have to change" in err
    assert policy.read_text() == before, "the report edited the policy"


def test_a_runtime_destination_is_told_it_cannot_be_covered():
    caps = audit(parse("put the standard input into u\n"
                       'run "curl" with u within 30 seconds\n'))
    rules = parse_policy('require reaching only "api.github.com"\n')
    report = explain_refusals(check(caps, rules), rules)
    assert "built at runtime" in report


# ------------------------------------------- one case per kind of refusal
#
# The report has a branch per rule kind, and a branch that is never exercised
# is a branch that says whatever it said when it was written. These go through
# the real policy engine rather than hand-built findings, so a change to what
# `check` reports shows up here instead of being mirrored in a fixture.

REFUSALS = [
    ('forbid reading "/etc/*"', 'put file "/etc/passwd" into t',
     "every read of /etc/*"),
    ('forbid writing to "/etc/*"', 'put "x" into file "/etc/hosts"',
     "every write of /etc/*"),
    ('forbid deleting "/var/*"', 'delete file "/var/log/x"',
     "every delete of /var/*"),
    ('forbid reaching "evil.example"',
     'run "curl" with "https://evil.example/x"',
     "every connection to evil.example"),
    ('forbid setting "PATH"',
     'put "/tmp" into the environment variable "PATH"',
     "wherever this policy applies"),
    ('forbid changing folder',
     'put "/tmp/build" into the current folder',
     "wherever this policy applies"),
]


@pytest.mark.parametrize("policy,body,expected", REFUSALS,
                         ids=[p[:34] for p, _, _ in REFUSALS])
def test_every_kind_of_refusal_says_what_lifting_it_would_allow(
        policy, body, expected):
    rules = parse_policy(policy)
    findings = check(audit(parse(body)), rules)
    assert any(f.severity == "forbid" for f in findings), (
        "this case is meant to be refused; fix the fixture, not the report")
    report = explain_refusals(findings, rules)
    assert "allows:" in report
    assert expected in report, report


def test_an_environment_allow_list_offers_the_widened_list():
    """The narrow change, not the deletion. A reviewer shown only `remove the
    rule` will remove the rule."""
    rules = parse_policy('require reading only the environment "PATH", "HOME"')
    body = 'put the environment variable "AWS_SECRET_ACCESS_KEY" into k'
    report = explain_refusals(check(audit(parse(body)), rules), rules)
    assert 'require reading only the environment "PATH", "HOME", ' \
           '"AWS_SECRET_ACCESS_KEY"' in report
    assert "reading AWS_SECRET_ACCESS_KEY anywhere, not only here" in report


def test_a_limit_offers_the_raised_number_and_names_the_reach():
    rules = parse_policy("require at most 1 command")
    body = 'run "echo" with "a"\nrun "echo" with "b"\n'
    report = explain_refusals(check(audit(parse(body)), rules), rules)
    assert "raise the limit on" in report
    assert "every script this policy covers" in report


def test_a_destination_built_at_runtime_gets_an_honest_non_answer():
    """There is no allow-list entry for a host that does not exist until the
    script runs, and saying so is better than inventing one."""
    rules = parse_policy('require reaching only "api.github.com"')
    body = ('put the environment variable "TARGET" into host\n'
            'run "curl" with "https://" & host & "/x"\n')
    findings = check(audit(parse(body)), rules)
    if not any(f.severity == "forbid" for f in findings):
        pytest.skip("a runtime destination is not refused by this rule")
    report = explain_refusals(findings, rules)
    assert "built at runtime" in report
    assert "change:" not in report.split("effect:")[0].split("allows:")[0] \
        or "no allow-list can cover it" in report
