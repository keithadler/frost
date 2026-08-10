"""The CLI driver, called in process.

test_cli.py runs the real executable, which is what proves the entry point and
the exit codes. It cannot see inside: a subprocess contributes nothing to
coverage, so every branch in cli.py was untested by construction. These call
`main()` directly, which is fast and reaches the paths the subprocess tests
exercise only from the outside.

Both layers are worth having. If only these existed, a broken shebang or a
missing console-script entry would pass.
"""

import io
import json
import os
import sys

import pytest

from frostlang import cli, __version__

from helpers import REPO, EXAMPLES


@pytest.fixture
def run_cli(capsys):
    """Call main() with arguments; return (status, stdout, stderr)."""
    def go(*args):
        status = cli.main(list(args))
        captured = capsys.readouterr()
        return status, captured.out, captured.err
    return go


@pytest.fixture
def script(tmp_path):
    def make(source, name="s.frost"):
        path = tmp_path / name
        path.write_text(source)
        return str(path)
    return make


def example(name):
    return os.path.join(EXAMPLES, name)


# ---------------------------------------------------------------- version

def test_version_prints_the_package_version(run_cli):
    with pytest.raises(SystemExit) as e:
        run_cli("--version")
    assert e.value.code == 0


def test_the_version_matches_pyproject():
    """Two places to change is one place to forget."""
    with open(os.path.join(REPO, "pyproject.toml")) as fh:
        declared = [l for l in fh if l.startswith("version = ")][0]
    assert __version__ in declared


# ---------------------------------------------------------------- running

def test_running_a_script(run_cli, script):
    assert run_cli(script('put "hi"')) == (0, "hi\n", "")


def test_no_arguments_prints_help(run_cli):
    status, out, _ = run_cli()
    assert status == 2
    assert "usage" in out.lower()


def test_an_unreadable_script(run_cli):
    status, _, err = run_cli("/no/such/file.frost")
    assert status == 2
    assert "cannot read" in err


def test_a_syntax_error_is_reported_with_its_line(run_cli, script):
    status, _, err = run_cli(script('put "a"\nput the frobnitz'))
    assert status == 2
    assert "Syntax error" in err and ":2" in err


def test_a_lex_error_is_reported(run_cli, script):
    status, _, err = run_cli(script('put "unterminated'))
    assert status == 2
    assert "Syntax error" in err


def test_a_runtime_error_shows_the_source_line_and_hint(run_cli, script):
    status, _, err = run_cli(script("put missing thing"))
    assert status == 1
    assert "put missing thing" in err
    assert "hint:" in err


def test_arguments_are_passed_to_the_script(run_cli, script):
    status, out, _ = run_cli(script("put item 2 of the arguments"), "a", "b")
    assert (status, out) == (0, "b\n")


def test_a_script_flag_is_not_taken_by_frost(run_cli, script):
    status, out, _ = run_cli(script("put item 1 of the arguments"), "--explain")
    assert (status, out.strip()) == (0, "--explain")


def test_recursion_is_reported_rather_than_traced(run_cli, script):
    path = script("to loop\n    loop\nend loop\nloop")
    status, _, err = run_cli(path)
    assert status == 1
    assert "nested too deeply" in err


# ------------------------------------------------------------ split_argv

@pytest.mark.parametrize("argv,own,rest", [
    (["s.frost"], ["s.frost"], []),
    (["--check", "s.frost"], ["--check", "s.frost"], []),
    (["s.frost", "--check"], ["s.frost"], ["--check"]),
    (["--policy", "p", "s.frost", "-x"], ["--policy", "p", "s.frost"], ["-x"]),
    (["--policy=p", "s.frost", "-x"], ["--policy=p", "s.frost"], ["-x"]),
    (["--try"], ["--try"], []),
    ([], [], []),
    (["s.frost", "a", "b"], ["s.frost"], ["a", "b"]),
    (["-"], ["-"], []),
])
def test_argv_splits_at_the_script(argv, own, rest):
    assert cli.split_argv(argv) == (own, rest)


# ----------------------------------------------------------------- modes

def test_check_reports_the_statement_count(run_cli, script):
    status, out, _ = run_cli("--check", script('put "a"\nput "b"'))
    assert status == 0
    assert "2 top-level statements" in out


def test_ast_dumps_nodes(run_cli, script):
    status, out, _ = run_cli("--ast", script('put "a"'))
    assert (status, "Put(" in out) == (0, True)


def test_trace_names_each_statement(run_cli, script):
    status, out, err = run_cli("--trace", script('put "a"\nput "b"'))
    assert (status, out) == (0, "a\nb\n")
    assert err.count("[frost] line") == 2


def test_explain_prints_a_manifest(run_cli, script):
    status, out, _ = run_cli("--explain", script('run "git" with "status"'))
    assert status == 0
    assert "Runs these programs" in out and "Verdict: clean" in out


def test_explain_exits_one_when_dangerous(run_cli, script):
    status, out, _ = run_cli("--explain", script('run "rm" with "-rf", "/"'))
    assert (status, "Verdict: dangerous" in out) == (1, True)


def test_explain_json_round_trips(run_cli, script):
    status, out, _ = run_cli("--explain", "--json",
                             script('put file "a.txt" into data'))
    data = json.loads(out)
    assert status == 0
    assert data["reads"][0]["path"] == "a.txt"
    assert data["reads"][0]["source"] == 'put file "a.txt" into data'


def test_explain_json_reports_findings(run_cli, script):
    _, out, _ = run_cli("--explain", "--json",
                        script('run "rm" with "-rf", "/tmp/x"'))
    findings = json.loads(out)["findings"]
    assert any(f["severity"] == "danger" for f in findings)


def test_explain_on_an_inert_script(run_cli, script):
    status, out, _ = run_cli("--explain", script('put "hello"'))
    assert status == 0
    assert "nothing observable" in out


# ---------------------------------------------------------------- policy

def test_policy_refuses_and_reports(run_cli, script, tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text('forbid running "rm"\n')
    status, _, err = run_cli("--policy", str(rules),
                             script('run "rm" with "x"'))
    assert status == 3
    assert "REFUSED" in err


def test_policy_warnings_do_not_block(run_cli, script, tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text('warn running "curl"\n')
    status, out, err = run_cli("--policy", str(rules),
                               script('put "ran"'))
    assert (status, out) == (0, "ran\n")


def test_a_warning_is_printed_when_the_script_still_runs(run_cli, script,
                                                         tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text('warn running "echo"\n')
    status, out, err = run_cli("--policy", str(rules),
                               script('try to run "echo" with "x"'))
    assert status == 0
    assert "policy passed with warnings" in err


def test_an_unreadable_policy(run_cli, script):
    status, _, err = run_cli("--policy", "/no/such.policy",
                             script('put "x"'))
    assert (status, "cannot read policy" in err) == (2, True)


def test_a_malformed_policy_rule(run_cli, script, tmp_path):
    rules = tmp_path / "p.policy"
    rules.write_text("forbid the impossible\n")
    status, _, err = run_cli("--policy", str(rules), script('put "x"'))
    assert status == 2
    assert "cannot read" in err


def test_policy_then_check_does_not_run_the_script(run_cli, script, tmp_path,
                                                   ):
    marker = tmp_path / "ran.txt"
    rules = tmp_path / "p.policy"
    rules.write_text('warn running "echo"\n')
    status, out, _ = run_cli("--policy", str(rules), "--check",
                             script(f'put "x" into file "{marker}"'))
    assert status == 0
    assert not marker.exists()


# --------------------------------------------------------------- format

def test_format_prints(run_cli, script):
    status, out, _ = run_cli("--format", script('put   "a"\n'))
    assert (status, out) == (0, 'put "a"\n')


def test_format_write_changes_the_file(run_cli, script):
    path = script('put   "a"\n')
    status, out, _ = run_cli("--format", "--write", path)
    assert status == 0
    assert open(path).read() == 'put "a"\n'
    assert "formatted" in out


def test_format_write_says_when_nothing_changed(run_cli, script):
    status, out, _ = run_cli("--format", "--write", script('put "a"\n'))
    assert "already formatted" in out


def test_format_refuses_a_broken_script(run_cli, script):
    path = script("repeat 3 times\n")
    before = open(path).read()
    status, _, err = run_cli("--format", "--write", path)
    assert status == 2
    assert "refusing to format" in err
    assert open(path).read() == before


# ------------------------------------------------------------------ try

def test_try_mode_reads_from_stdin(run_cli, monkeypatch):
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO("the first word of it\n:quit\n"))
    status, out, _ = run_cli("--try")
    assert status == 0
    assert "10.0.0.1" in out


def test_try_mode_can_load_a_subject(run_cli, monkeypatch, tmp_path):
    subject = tmp_path / "text.txt"
    subject.write_text("alpha beta\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("the last word of it\n"))
    status, out, _ = run_cli("--try", str(subject))
    assert (status, "beta" in out) == (0, True)


def test_try_mode_with_an_unreadable_subject(run_cli):
    status, _, err = run_cli("--try", "/no/such/text.txt")
    assert (status, "cannot read" in err) == (2, True)
