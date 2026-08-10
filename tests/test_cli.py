"""The command line, driven as a real subprocess.

Everything else in this suite calls into the library. This file runs the
`frost` executable the way a person or an agent would, because the exit status
is part of the contract: a CI job, a git hook, or a wrapper script branches on
it, and nothing else in the suite would notice if it changed.

The codes, once, in one place:

    0  ran, or answered a question (--check, --explain on a clean script)
    1  the script failed at runtime, or --explain judged it dangerous
    2  the script could not be read or parsed, or the arguments were wrong
    3  a policy refused it; nothing was run
  130  interrupted
"""

import os
import subprocess
import sys

import pytest

from helpers import REPO, EXAMPLES

FROST = os.path.join(REPO, "frost")


def frost(*args, stdin=None, cwd=None, env=None):
    """Run the CLI. Returns (status, stdout, stderr)."""
    environ = dict(os.environ)
    environ.pop("PYTHONPATH", None)
    environ["PYTHONPATH"] = REPO
    if env:
        environ.update(env)
    p = subprocess.run(
        [sys.executable, FROST, *args],
        capture_output=True, text=True, input=stdin,
        cwd=cwd or REPO, env=environ, timeout=60)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def script(tmp_path):
    """Write a script to a temp file and return its path."""
    def make(source, name="s.frost"):
        path = tmp_path / name
        path.write_text(source)
        return str(path)
    return make


# ------------------------------------------------------------ running

def test_runs_a_script_and_exits_zero(script):
    status, out, _ = frost(script('put "hello"'))
    assert (status, out) == (0, "hello\n")


def test_hello_example_runs():
    status, out, err = frost(os.path.join(EXAMPLES, "hello.frost"))
    assert status == 0, err
    assert out.strip()


def test_runtime_failure_exits_one(script):
    status, _, err = frost(script('put nothing here'))
    assert status == 1
    assert "no variable named" in err


def test_a_runtime_error_names_the_file_and_line(script):
    path = script('put "ok"\nput "ok"\nput missing thing')
    status, _, err = frost(path)
    assert status == 1
    assert f"{path}:3" in err
    assert "hint:" in err


def test_quit_with_status_is_the_exit_status(script):
    assert frost(script("quit with status 7"))[0] == 7


def test_arguments_reach_the_script(script):
    path = script("put item 1 of the arguments\nput item 2 of the arguments")
    status, out, _ = frost(path, "alpha", "beta")
    assert (status, out) == (0, "alpha\nbeta\n")


def test_an_argument_that_looks_like_a_flag_is_not_eaten(script):
    """A script's own arguments must not be parsed as frost's."""
    path = script("put item 1 of the arguments")
    status, out, _ = frost(path, "--check")
    assert (status, out.strip()) == (0, "--check")


# ------------------------------------------------------------ bad input

def test_a_missing_file_exits_two():
    status, _, err = frost("/no/such/script.frost")
    assert status == 2
    assert "cannot read" in err


def test_a_syntax_error_exits_two(script):
    status, _, err = frost(script("if 1 is 1\nput \"x\"\nend if"))
    assert status == 2
    assert "Syntax error" in err


def test_a_syntax_error_shows_the_offending_line(script):
    status, _, err = frost(script('put "a"\nput the frobnitz of "x"'))
    assert status == 2
    assert "put the frobnitz" in err
    assert ":2" in err


def test_no_arguments_prints_help_and_exits_two():
    status, out, _ = frost()
    assert status == 2
    assert "usage" in out.lower()


# ------------------------------------------------------------- --check

def test_check_reports_ok_without_running(script, tmp_path):
    marker = tmp_path / "ran.txt"
    path = script(f'put "x" into file "{marker}"')
    status, out, _ = frost("--check", path)
    assert status == 0
    assert "ok" in out
    assert not marker.exists(), "--check must not run the script"


def test_check_on_a_broken_script_exits_two(script):
    assert frost("--check", script("repeat 3 times"))[0] == 2


# --------------------------------------------------------------- --ast

def test_ast_dumps_the_tree(script):
    status, out, _ = frost("--ast", script('put "x"'))
    assert status == 0
    assert "Put(" in out


# ------------------------------------------------------------- --trace

def test_trace_writes_each_statement_to_stderr(script):
    status, out, err = frost("--trace", script('put "a"\nput "b"'))
    assert (status, out) == (0, "a\nb\n")
    assert err.count("[frost]") == 2


# ----------------------------------------------------------- --explain

def test_explain_does_not_run_the_script(script, tmp_path):
    marker = tmp_path / "ran.txt"
    path = script(f'put "x" into file "{marker}"')
    frost("--explain", path)
    assert not marker.exists()


def test_explain_lists_programs_and_verdict(script):
    status, out, _ = frost("--explain", script('run "git" with "status"'))
    assert status == 0
    assert "git" in out
    assert "Verdict: clean" in out


def test_explain_exits_one_on_a_dangerous_script(script):
    status, out, _ = frost("--explain", script('run "rm" with "-rf", "/tmp/x"'))
    assert status == 1
    assert "Verdict: dangerous" in out


def test_explain_json_is_valid_json(script):
    import json
    status, out, _ = frost("--explain", "--json",
                           script('run "curl" with "https://x"'))
    assert status == 0
    data = json.loads(out)
    assert data["commands"][0]["program"] == "curl"
    assert data["verdict"] in ("clean", "caution", "dangerous")
    assert "summary" in data and "findings" in data


@pytest.mark.parametrize("name", ["hello", "tour", "healthcheck", "logreport",
                                  "deploy", "backup", "danger", "exfiltrate"])
def test_explain_handles_every_example(name):
    status, out, err = frost("--explain", os.path.join(EXAMPLES,
                                                       f"{name}.frost"))
    assert status in (0, 1), err
    assert "Verdict:" in out


# ------------------------------------------------------------ --policy

POLICY = 'forbid running "rm" with "-rf"\nforbid writing to "/etc/*"\n'


def test_policy_refusal_exits_three_without_running(script, tmp_path):
    marker = tmp_path / "ran.txt"
    rules = tmp_path / "p.policy"
    rules.write_text(POLICY)
    path = script(f'run "rm" with "-rf", "/tmp/x"\n'
                  f'put "x" into file "{marker}"')
    status, _, err = frost("--policy", str(rules), path)
    assert status == 3
    assert "REFUSED" in err
    assert not marker.exists(), "a refused script must not run"


def test_a_passing_policy_lets_the_script_run(script, tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text(POLICY)
    status, out, _ = frost("--policy", str(rules), script('put "fine"'))
    assert (status, out) == (0, "fine\n")


def test_a_missing_policy_file_exits_two(script):
    status, _, err = frost("--policy", "/no/such.policy", script('put "x"'))
    assert status == 2
    assert "cannot read policy" in err


def test_an_unreadable_policy_rule_exits_two(script, tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text("forbid the impossible\n")
    status, _, err = frost("--policy", str(rules), script('put "x"'))
    assert status == 2


def test_the_production_policy_blocks_the_demo_attack():
    status, _, err = frost("--policy", os.path.join(EXAMPLES,
                                                    "production.policy"),
                           os.path.join(EXAMPLES, "exfiltrate.frost"))
    assert status == 3
    assert "REFUSED" in err


# ------------------------------------------------------------ --format

def test_format_prints_canonical_layout_without_writing(script):
    path = script('put   "a"   into greeting\n')
    status, out, _ = frost("--format", path)
    assert status == 0
    assert out == 'put "a" into greeting\n'
    assert open(path).read() == 'put   "a"   into greeting\n'


def test_format_write_rewrites_in_place(script):
    path = script('put   "a"   into greeting\n')
    status, out, _ = frost("--format", "--write", path)
    assert status == 0
    assert open(path).read() == 'put "a" into greeting\n'
    assert "formatted" in out


def test_format_write_is_quiet_when_already_canonical(script):
    path = script('put "a" into greeting\n')
    status, out, _ = frost("--format", "--write", path)
    assert status == 0
    assert "already formatted" in out


def test_format_refuses_a_broken_script(script):
    path = script("repeat 3 times\n")
    before = open(path).read()
    status, _, err = frost("--format", "--write", path)
    assert status == 2
    assert "refusing to format" in err
    assert open(path).read() == before, "a broken file must not be rewritten"


@pytest.mark.parametrize("name", ["hello", "tour", "healthcheck", "logreport",
                                  "deploy", "backup", "danger", "exfiltrate"])
def test_every_example_is_already_canonically_formatted(name):
    path = os.path.join(EXAMPLES, f"{name}.frost")
    status, out, _ = frost("--format", path)
    assert status == 0
    assert out == open(path).read(), f"{name}.frost is not canonical"


# ------------------------------------------------------------- signals

def test_interrupt_exits_130(script):
    """Ctrl-C during a long run reports 130, the shell convention."""
    import signal
    import time
    path = script('run "sleep" with "30"')
    p = subprocess.Popen(
        [sys.executable, FROST, path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=REPO, env={**os.environ, "PYTHONPATH": REPO})
    time.sleep(1.0)
    p.send_signal(signal.SIGINT)
    _, err = p.communicate(timeout=30)
    assert p.returncode == 130, err
    assert "interrupted" in err
