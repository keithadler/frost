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

from helpers import REPO, EXAMPLES, needs_coreutils


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


# ---------------------------------------------------- secrets and keystore

cryptography = pytest.importorskip("cryptography", reason="optional extra")

from frostlang.keystore import Keystore                      # noqa: E402


@pytest.fixture
def keystore(tmp_path, monkeypatch):
    """A keystore with one readable secret, unlocked from the environment."""
    monkeypatch.setenv("FROST_PASSPHRASE", "pw")
    path = str(tmp_path / "k.keystore")
    ks = Keystore.create(path)
    ks.add_role("deploy", "pw")
    ks.add_role("other", "pw")
    ks.set_secret("db password", "hunter2", ["deploy"])
    ks.save()
    return path


def test_a_script_reads_a_secret_and_redacts_it(run_cli, script, keystore):
    path = script('put the secret "db password" into pw\nput "using" && pw')
    status, out, err = run_cli("--keystore", keystore, "--role", "deploy",
                               path)
    assert status == 0, err
    assert out.strip() == "using «secret db password»"
    assert "hunter2" not in out


def test_the_wrong_role_is_refused_before_running(run_cli, script, keystore,
                                                  tmp_path):
    marker = tmp_path / "ran.txt"
    path = script(f'put "x" into file "{marker}"\n'
                  'put the secret "db password" into pw')
    status, _, err = run_cli("--keystore", keystore, "--role", "other", path)
    assert status == 3
    assert "may not read it" in err and "allowed: deploy" in err
    assert not marker.exists()


def test_a_secret_missing_from_the_keystore_is_refused(run_cli, script,
                                                       keystore):
    path = script('put the secret "nope" into pw')
    status, _, err = run_cli("--keystore", keystore, "--role", "deploy", path)
    assert (status, "no such secret" in err) == (3, True)


def test_no_role_is_refused(run_cli, script, keystore):
    path = script('put the secret "db password" into pw')
    status, _, err = run_cli("--keystore", keystore, path)
    assert (status, "no role was given" in err) == (3, True)


def test_no_keystore_at_all_is_refused(run_cli, script):
    path = script('put the secret "db password" into pw')
    status, _, err = run_cli(path)
    assert (status, "no keystore is open" in err) == (3, True)


def test_an_unreadable_keystore_file_is_reported(run_cli, script, monkeypatch):
    monkeypatch.setenv("FROST_PASSPHRASE", "pw")
    path = script('put the secret "x" into pw')
    status, _, err = run_cli("--keystore", "/no/such.keystore",
                             "--role", "deploy", path)
    assert (status, "no keystore at" in err) == (2, True)


def test_a_wrong_passphrase_is_reported(run_cli, script, keystore,
                                        monkeypatch):
    monkeypatch.setenv("FROST_PASSPHRASE", "not-the-passphrase")
    path = script('put the secret "db password" into pw')
    status, _, err = run_cli("--keystore", keystore, "--role", "deploy", path)
    assert (status, "wrong passphrase" in err) == (2, True)


def test_a_runtime_secret_name_cannot_be_pre_checked(run_cli, script,
                                                     keystore):
    """It is unknowable before the script runs, so it is allowed to start and
    fails at the line that reads it — the same rule the manifest follows."""
    path = script('put "db" & " password" into name\n'
                  'put the secret name into pw\nput "got" && pw')
    status, out, err = run_cli("--keystore", keystore, "--role", "deploy",
                               path)
    assert status == 0, err
    assert "got «secret db password»" in out


def test_check_does_not_require_the_keystore(run_cli, script):
    """Checking a script must not need the credentials it will use, or it
    stops being usable as a pre-commit hook."""
    path = script('put the secret "db password" into pw')
    status, out, _ = run_cli("--check", path)
    assert (status, "ok" in out) == (0, True)


def test_explain_does_not_require_the_keystore(run_cli, script):
    path = script('put the secret "db password" into pw')
    status, out, _ = run_cli("--explain", path)
    assert status == 0
    assert "db password" in out


def test_the_keystore_subcommand_is_routed(run_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FROST_PASSPHRASE", "pw")
    path = str(tmp_path / "new.keystore")
    status, out, _ = run_cli("keystore", "init", path, "--role", "deploy")
    assert (status, "created" in out) == (0, True)


def test_find_secret_names_reports_runtime_names(script):
    from frostlang.parser import parse
    tree = parse('put the secret "literal" into a\n'
                 'put "x" into n\nput the secret n into b')
    found = cli.find_secret_names(tree)
    assert ("literal", 1) in found
    assert any(name is None for name, _ in found)


# ------------------------------------------ modules, recording, sandboxing

@pytest.fixture
def workspace(tmp_path):
    def write(name, source):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.lstrip("\n"))
        return str(path)
    write.root = tmp_path
    return write


LIBRARY = 'to shout with w\n    return the uppercase w & "!"\nend shout\n'


def test_a_program_with_modules_runs(run_cli, workspace):
    workspace("lib/text.frost", LIBRARY)
    entry = workspace("entry.frost",
                      'use "lib/text.frost" for the shout\n'
                      'put the shout of "hi"\n')
    assert run_cli(entry) == (0, "HI!\n", "")


def test_an_unresolvable_module_fails_closed(run_cli, workspace):
    entry = workspace("entry.frost", 'use "lib/missing.frost" for the a\n')
    status, out, err = run_cli("--explain", entry)
    assert status == 2
    assert "Verdict" not in out
    assert "no module at" in err


def test_a_module_error_reports_its_hint(run_cli, workspace):
    entry = workspace("entry.frost", 'use "/etc/passwd" for the a\n')
    status, _, err = run_cli(entry)
    assert (status, "absolute" in err) == (2, True)


def test_the_lockfile_round_trips(run_cli, workspace):
    workspace("lib/text.frost", LIBRARY)
    entry = workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                                     'put the shout of "x"\n')
    status, out, _ = run_cli("--lock", entry)
    assert (status, "wrote" in out) == (0, True)
    assert run_cli("--frozen", entry)[0] == 0


def test_frozen_refuses_drift(run_cli, workspace):
    workspace("lib/text.frost", LIBRARY)
    entry = workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                                     'put the shout of "x"\n')
    run_cli("--lock", entry)
    workspace("lib/text.frost", LIBRARY + 'to extra\n    put 1\nend extra\n')
    status, _, err = run_cli("--frozen", entry)
    assert (status, "has changed" in err) == (3, True)


def test_a_breached_ceiling_refuses(run_cli, workspace):
    workspace("lib/m.frost", 'to act\n    run "curl"\nend act\n')
    entry = workspace("entry.frost", 'use "lib/m.frost" for the act\nact\n')
    status, _, err = run_cli(entry)
    assert status == 3
    assert "may not run curl" in err


def test_a_ceiling_breach_is_available_as_json(run_cli, workspace):
    workspace("lib/m.frost", 'to act\n    run "curl"\nend act\n')
    entry = workspace("entry.frost", 'use "lib/m.frost" for the act\nact\n')
    status, out, _ = run_cli("--json", entry)
    payload = json.loads(out)
    assert (status, payload["ok"]) == (3, False)


@needs_coreutils
def test_record_then_replay(run_cli, workspace, tmp_path):
    entry = workspace("s.frost", 'run "echo" with "one"\nput it\n')
    recording = str(tmp_path / "run.json")
    status, out, err = run_cli("--record", recording, entry)
    assert (status, out.strip()) == (0, "one"), err
    assert run_cli("--replay", recording, entry) == (0, "one\n", "")


def test_replaying_a_missing_recording(run_cli, workspace):
    entry = workspace("s.frost", 'put "x"\n')
    status, _, err = run_cli("--replay", "/no/such.json", entry)
    assert (status, "cannot read" in err) == (2, True)


def test_replaying_a_malformed_recording(run_cli, workspace, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    entry = workspace("s.frost", 'put "x"\n')
    assert run_cli("--replay", str(bad), entry)[0] == 2


def test_sandbox_without_a_policy(run_cli, workspace):
    entry = workspace("s.frost", 'put "x"\n')
    status, _, err = run_cli("--sandbox", entry)
    assert (status, "needs a policy" in err) == (2, True)


def test_sandbox_with_a_policy_that_declares_nothing(run_cli, workspace):
    workspace("p.policy", 'forbid running "sudo"\n')
    entry = workspace("s.frost", 'put "x"\n')
    status, _, err = run_cli("--policy", str(workspace.root / "p.policy"),
                             "--sandbox", entry)
    assert (status, "declares no sandbox boundary" in err) == (2, True)


def test_policy_hints_reach_the_output(run_cli, workspace):
    workspace("p.policy",
              'forbid running "sudo"   -- the deploy role already has it\n')
    entry = workspace("s.frost", 'run "sudo" with "ls"\n')
    status, _, err = run_cli("--policy", str(workspace.root / "p.policy"),
                             entry)
    assert status == 3
    assert "why: the deploy role already has it" in err


def test_repair_prints_the_repaired_source(run_cli, workspace):
    entry = workspace("s.frost", 'run "ls -la"\n')
    status, out, _ = run_cli("--repair", entry)
    assert (status, out.strip()) == (0, 'run "ls" with "-la"')


def test_repair_write_changes_the_file(run_cli, workspace):
    entry = workspace("s.frost", 'run "ls -la"\n')
    status, out, _ = run_cli("--repair", "--write", entry)
    assert status == 0
    assert open(entry).read().strip() == 'run "ls" with "-la"'


def test_repair_with_nothing_to_do(run_cli, workspace):
    entry = workspace("s.frost", "put the frobnitz\n")
    status, _, err = run_cli("--repair", entry)
    assert (status, "nothing to repair" in err) == (1, True)


def test_repair_as_json(run_cli, workspace):
    entry = workspace("s.frost", 'run "ls -la"\n')
    status, out, _ = run_cli("--repair", "--json", entry)
    payload = json.loads(out)
    assert (status, payload["ok"]) == (0, True)
    assert payload["applied"][0]["confidence"] == "high"


def test_check_as_json_reports_the_verdict(run_cli, workspace):
    entry = workspace("s.frost", 'run "git" with "status"\n')
    status, out, _ = run_cli("--check", "--json", entry)
    payload = json.loads(out)
    assert (status, payload["verdict"]) == (0, "clean")
    assert payload["statements"] == 1


def test_format_works_on_a_file_with_a_broken_import(run_cli, workspace):
    """Layout is lexical; a broken import should still be tidyable."""
    entry = workspace("s.frost", 'use   "lib/missing.frost"   for the a\n')
    status, out, _ = run_cli("--format", entry)
    assert (status, out.strip()) == (0, 'use "lib/missing.frost" for the a')
