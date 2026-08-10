"""The surfaces a pipeline and a reviewer touch.

`--json` is frost's own shape and the right thing for an agent, which can be
told what the fields mean. A pull request cannot be told anything, so findings
also come out as SARIF, which every code-scanning tool already reads. Exit
codes stop being folklore. And `--policy-from` exists because the policy
engine is the most useful thing here and the least used, since the first step
was a blank file.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import sarif, scaffold, cli
from frostlang.audit import audit, parse_policy, check
from frostlang.parser import parse

from helpers import REPO


def frost(*args, cwd=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, text, name="s.frost"):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


DANGEROUS = 'run "curl" with "https://x.example/i" within 30 seconds\nput it\n'


# ------------------------------------------------------------------- SARIF

def test_a_syntax_error_becomes_a_sarif_result(tmp_path):
    """The finding most worth annotating on a diff, and it happens before the
    script parses, so it cannot wait for the --check path."""
    path = script(tmp_path, 'if 1 is 1\n    put "x"\nend if\n')
    status, out, _ = frost("--check", "--sarif", path, cwd=str(tmp_path))
    assert status == 2
    log = json.loads(out)
    assert log["version"] == "2.1.0"
    result = log["runs"][0]["results"][0]
    assert result["ruleId"] == "missing-then"
    assert result["level"] == "error"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 1


def test_a_repair_becomes_an_applicable_fix(tmp_path):
    path = script(tmp_path, 'if 1 is 1\n    put "x"\nend if\n')
    _, out, _ = frost("--check", "--sarif", path, cwd=str(tmp_path))
    fix = json.loads(out)["runs"][0]["results"][0]["fixes"][0]
    replacement = fix["artifactChanges"][0]["replacements"][0]
    assert replacement["insertedContent"]["text"] == "if 1 is 1 then"


def test_a_guess_is_never_offered_as_a_fix():
    """The confidence levels exist so that a guess is not applied unattended,
    and a one-click fix in a review tool is exactly unattended."""
    from frostlang.diagnostics import Diagnostic, Repair, GUESS, HIGH
    d = Diagnostic("error", "x", "m", 1, repairs=[
        Repair("replace-line", 1, "guessed", GUESS)])
    assert "fixes" not in sarif.report("s.frost", [d])["runs"][0]["results"][0]
    d.repairs = [Repair("replace-line", 1, "certain", HIGH)]
    assert "fixes" in sarif.report("s.frost", [d])["runs"][0]["results"][0]


def test_findings_become_results_with_rules(tmp_path):
    path = script(tmp_path, DANGEROUS)
    status, out, _ = frost("--explain", "--sarif", path, cwd=str(tmp_path))
    log = json.loads(out)
    assert log["runs"][0]["tool"]["driver"]["name"] == "frost"
    assert log["runs"][0]["tool"]["driver"]["rules"], "no rules described"
    assert log["runs"][0]["results"], "no findings reported"


def test_a_danger_is_a_warning_not_an_error():
    """frost grades findings for somebody reading a manifest. In a review tool
    an error means the code is broken, and a dangerous script is not broken."""
    assert sarif._level("danger") == "warning"
    assert sarif._level("error") == "error"
    assert sarif._level("note") == "note"


def test_the_sarif_is_valid_enough_to_upload(tmp_path):
    """Not a schema validation, but the fields the uploader requires."""
    path = script(tmp_path, DANGEROUS)
    _, out, _ = frost("--explain", "--sarif", path, cwd=str(tmp_path))
    log = json.loads(out)
    assert log["$schema"].endswith("sarif-schema-2.1.0.json")
    for result in log["runs"][0]["results"]:
        assert result["message"]["text"]
        assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]


# ------------------------------------------------------------- exit codes

def test_exit_codes_are_published():
    status, out, _ = frost("--exit-codes")
    assert status == 0
    for code in ("0", "1", "2", "3", "4", "130", "141"):
        assert code in out


def test_exit_codes_as_json():
    status, out, _ = frost("--exit-codes", "--json")
    payload = json.loads(out)
    assert {c["code"] for c in payload["exit_codes"]} == \
        {0, 1, 2, 3, 4, 130, 141}


def test_every_published_code_has_a_meaning():
    for code, name, meaning in cli.EXIT_CODES:
        assert name and meaning, code


# ------------------------------------------------------------- completion

@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_completion_is_generated_for_each_shell(shell):
    status, out, _ = frost("--completion", shell)
    assert status == 0
    assert "--policy" in out and "--sandbox" in out


def test_completion_comes_from_the_parser_not_a_list():
    """Written beside the parser it would go stale, which is what happened to
    the list of value-taking options."""
    _, out, _ = frost("--completion", "bash")
    for action in cli.build_parser()._actions:
        for flag in action.option_strings:
            assert flag in out, f"{flag} is missing from completion"


# --------------------------------------------------------- policy scaffold

def test_a_scaffold_describes_what_the_script_does(tmp_path):
    path = script(tmp_path, DANGEROUS)
    status, out, _ = frost("--policy-from", path, cwd=str(tmp_path))
    assert status == 0
    assert 'warn running "curl"' in out
    assert 'require reaching only "x.example"' in out


def test_a_scaffold_parses_as_a_policy(tmp_path):
    """A starter nobody can run is worse than none."""
    path = script(tmp_path, DANGEROUS)
    _, out, _ = frost("--policy-from", path, cwd=str(tmp_path))
    assert parse_policy(out), "the generated policy produced no rules"


def test_a_scaffold_does_not_refuse_the_script_it_came_from(tmp_path):
    """A scaffold that fails the build immediately is one people delete rather
    than edit, so anything that would refuse is emitted commented out."""
    path = script(tmp_path, DANGEROUS)
    _, out, _ = frost("--policy-from", path, cwd=str(tmp_path))
    caps = audit(parse(open(path).read()))
    blocked = [f for f in check(caps, parse_policy(out))
               if f.severity == "forbid"]
    assert blocked == [], [f.what for f in blocked]


def test_a_scaffold_marks_the_rules_it_had_to_comment_out(tmp_path):
    path = script(tmp_path, 'try to run "rm" with "-rf", "/tmp/x"\n')
    _, out, _ = frost("--policy-from", path, cwd=str(tmp_path))
    assert "refuses the script as it stands" in out


def test_a_scaffold_says_when_a_name_is_unknowable(tmp_path):
    path = script(tmp_path,
                  'put the standard input into tool\nrun tool with "x"\n')
    _, out, _ = frost("--policy-from", path, cwd=str(tmp_path))
    assert "build the program name at runtime" in out


# ------------------------------------------------------- explain --against

def test_against_reports_a_widening_and_exits_three(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    frost("--approve", path, cwd=str(tmp_path))
    (tmp_path / "s.frost").write_text(
        'run "echo" with "a"\nput it\n' + DANGEROUS)
    status, out, _ = frost("--explain", "--against", path + ".approved", path,
                           cwd=str(tmp_path))
    assert status == 3
    assert "wider:    it can now run curl" in out


def test_against_says_so_when_nothing_changed(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    frost("--approve", path, cwd=str(tmp_path))
    status, out, _ = frost("--explain", "--against", path + ".approved", path,
                           cwd=str(tmp_path))
    assert status == 0
    assert "unchanged" in out


def test_against_does_not_run_the_script(tmp_path):
    path = script(tmp_path, 'put "SHOULD NOT APPEAR"\n')
    frost("--approve", path, cwd=str(tmp_path))
    _, out, _ = frost("--explain", "--against", path + ".approved", path,
                      cwd=str(tmp_path))
    assert "SHOULD NOT APPEAR" not in out


def test_against_a_missing_approval_is_refused(tmp_path):
    path = script(tmp_path, 'put "x"\n')
    status, _, err = frost("--explain", "--against", "/no/such.approved", path,
                           cwd=str(tmp_path))
    assert status == 2
    assert "no approval" in err


# ------------------------------------------------------ require an approval

def test_a_policy_can_demand_an_approval_exists(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    policy = tmp_path / "p.policy"
    policy.write_text("require an approval\n")
    status, out, err = frost("--policy", str(policy), path, cwd=str(tmp_path))
    assert status == 2
    assert "no approval" in err
    assert out == "", "the script ran without the approval the policy demanded"


def test_the_demand_is_satisfied_by_approving(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    policy = tmp_path / "p.policy"
    policy.write_text("require an approval\n")
    frost("--approve", path, cwd=str(tmp_path))
    status, out, err = frost("--policy", str(policy), path, cwd=str(tmp_path))
    assert status == 0, err
    assert "a" in out


def test_the_rule_parses_and_produces_no_findings_of_its_own():
    """Whether a file exists is not something `check` can see, and giving it
    the filesystem to answer would undo the reason it takes only a tree."""
    rules = parse_policy("require an approval\n")
    assert rules and rules[0].kind == "approval"
    assert check(audit(parse('put "x"\n')), rules) == []
