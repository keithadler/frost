"""Capability boundaries the operating system holds.

A sandbox nobody checked is a sandbox nobody has, so almost every test here
does the thing the boundary forbids and then looks at the filesystem. Asserting
that the wrapper was constructed would prove nothing: the question is whether
the write happened.

The tests are skipped where no backend exists rather than passing vacuously,
and `test_the_backend_actually_confines` is the canary — if that fails, every
other result in this file is meaningless.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import sandbox as S
from frostlang.sandbox import Boundary, Sandbox, SandboxError, self_test
from frostlang.audit import parse_policy, boundary_from, PolicyError

from helpers import REPO

BACKEND = S.detect_backend()
needs_sandbox = pytest.mark.skipif(
    BACKEND == S.BACKEND_NONE,
    reason=f"no sandbox backend on this platform ({sys.platform})")


def frost(*args, cwd=None, timeout=90):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def project(tmp_path):
    """A directory with a script, a policy, and somewhere it may write."""
    (tmp_path / "build").mkdir()

    def write(name, text):
        path = tmp_path / name
        path.write_text(text.lstrip("\n"))
        return path
    write.root = tmp_path
    return write


# ------------------------------------------------------------- the canary

@needs_sandbox
def test_the_backend_actually_confines():
    """If this fails, nothing else in this file means anything."""
    working, detail = self_test()
    assert working, f"the backend did not confine a test write: {detail}"


def test_the_backend_is_named_honestly():
    assert S.describe_backend() in (
        "macOS sandbox-exec", "Linux bubblewrap",
        "none available on this platform")


# --------------------------------------------------- declaring a boundary

def test_a_policy_declares_the_boundary():
    boundary = boundary_from(parse_policy("""
        sandbox may run "git", "make"
        sandbox may read "*"
        sandbox may write "build/*"
        sandbox may reach the network
    """))
    assert boundary.declared
    assert boundary.programs == ["git", "make"]
    assert boundary.writes == ["build/*"]
    assert boundary.network is True


def test_a_policy_without_a_sandbox_line_declares_nothing():
    assert not boundary_from(parse_policy('forbid running "sudo"')).declared


def test_a_boundary_defaults_to_nothing():
    boundary = Boundary()
    assert boundary.describe() == "nothing"
    assert not boundary.allows_program("git")
    assert not boundary.allows_write("/tmp/x")
    assert boundary.network is False


def test_a_per_host_rule_is_refused_rather_than_faked():
    """The single most important refusal here. macOS filters on addresses,
    not names, and a Linux namespace is all or nothing — so a host allow-list
    would be accepted and not enforced, and somebody would rely on it."""
    with pytest.raises(PolicyError) as e:
        parse_policy('sandbox may reach "api.github.com"')
    assert "cannot allow one host" in str(e.value)
    assert "not enforced" in str(e.value)


def test_the_network_rule_says_what_it_means():
    assert boundary_from(
        parse_policy("sandbox may reach the network")).network is True


def test_an_allowance_must_name_something():
    with pytest.raises(PolicyError) as e:
        parse_policy("sandbox may run")
    assert "cannot read" in str(e.value) or "in quotes" in str(e.value)


@pytest.mark.parametrize("verb,field", [
    ("run", "programs"), ("read", "reads"),
    ("write", "writes"), ("delete", "deletes"),
])
def test_every_verb_reaches_the_boundary(verb, field):
    boundary = boundary_from(parse_policy(f'sandbox may {verb} "a", "b"'))
    assert getattr(boundary, field) == ["a", "b"]


# ------------------------------------------------------- actual blocking

ESCAPE = '''
try to run "sh" with "-c", "echo inside > build/allowed.txt"
try to run "sh" with "-c", "echo escaped > {outside}"
put "finished"
'''

POLICY = '''
sandbox may run "sh"
sandbox may read "*"
sandbox may write "build/*"
'''


@needs_sandbox
def test_a_write_outside_the_boundary_is_blocked(project, tmp_path):
    outside = tmp_path / "escaped.txt"
    project("rules.policy", POLICY)
    project("s.frost", ESCAPE.format(outside=outside))

    status, out, err = frost("--policy", "rules.policy", "--sandbox",
                             "s.frost", cwd=str(tmp_path))
    assert status == 0, err
    assert not outside.exists(), "the sandbox did not block the write"


@needs_sandbox
def test_the_same_script_escapes_without_the_sandbox(project, tmp_path):
    """Guards the test above from passing for the wrong reason — the write
    has to be one that would otherwise succeed."""
    outside = tmp_path / "escaped.txt"
    project("rules.policy", POLICY)
    project("s.frost", ESCAPE.format(outside=outside))

    frost("s.frost", cwd=str(tmp_path))
    assert outside.exists(), "the escape did not work even unsandboxed"


@needs_sandbox
def test_a_write_inside_the_boundary_still_succeeds(project, tmp_path):
    """A sandbox that blocks everything is easy and useless."""
    outside = tmp_path / "escaped.txt"
    project("rules.policy", POLICY)
    project("s.frost", ESCAPE.format(outside=outside))

    frost("--policy", "rules.policy", "--sandbox", "s.frost",
          cwd=str(tmp_path))
    assert (tmp_path / "build" / "allowed.txt").exists()


@needs_sandbox
def test_a_program_the_boundary_does_not_name_is_refused(project, tmp_path):
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n')
    project("s.frost", 'run "curl" with "https://example.com"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert "does not allow running 'curl'" in err
    assert 'run "echo"' in err


@needs_sandbox
def test_a_pipe_stage_is_confined_too(project, tmp_path):
    """Every child, not just the ones spawned by `run`."""
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n')
    project("s.frost", "pipe\n    run \"echo\" with \"x\"\n"
                       "    run \"curl\"\nend pipe\n")
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert "does not allow running 'curl'" in err


# -------------------------------------------- frost's own file operations

@needs_sandbox
def test_frosts_own_write_is_checked(project, tmp_path):
    """`put X into file` never becomes a child process, so the kernel never
    sees it. Enforced by the interpreter, which is a weaker claim than the
    one covering commands and is documented as one."""
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            'sandbox may write "build/*"\n')
    project("s.frost", f'put "data" into file "{tmp_path / "escaped.txt"}"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert "does not allow writing" in err
    assert not (tmp_path / "escaped.txt").exists()


@needs_sandbox
def test_frosts_own_write_inside_the_boundary_succeeds(project, tmp_path):
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            f'sandbox may write "{tmp_path}/build/*"\n')
    project("s.frost", f'put "data" into file "{tmp_path}/build/out.txt"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 0, err
    assert (tmp_path / "build" / "out.txt").exists()


@needs_sandbox
def test_frosts_own_delete_is_checked(project, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("still here\n")
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n')
    project("s.frost", f'delete file "{victim}"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert victim.exists(), "the sandbox did not stop the delete"


@needs_sandbox
def test_frosts_own_read_is_checked(project, tmp_path):
    secret = tmp_path / "private.txt"
    secret.write_text("contents\n")
    project("rules.policy", 'sandbox may run "echo"\n'
                            'sandbox may read "build/*"\n')
    project("s.frost", f'put file "{secret}"\n')
    status, out, err = frost("--policy", "rules.policy", "--sandbox",
                             "s.frost", cwd=str(tmp_path))
    assert status == 1
    assert "does not allow reading" in err
    assert "contents" not in out


# ------------------------------------------------------------- fail closed

def test_sandbox_without_a_policy_is_refused(project, tmp_path):
    project("s.frost", 'put "x"\n')
    status, _, err = frost("--sandbox", "s.frost", cwd=str(tmp_path))
    assert status == 2
    assert "needs a policy" in err


def test_a_policy_that_declares_no_boundary_is_refused(project, tmp_path):
    project("rules.policy", 'forbid running "sudo"\n')
    project("s.frost", 'put "x"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 2
    assert "declares no sandbox boundary" in err
    assert "has to say what is allowed" in err


def test_a_missing_backend_refuses_rather_than_warning(monkeypatch):
    """The decision the whole feature rests on: no enforcement means no run.
    Warning and continuing would leave someone relying on a boundary that was
    never held."""
    monkeypatch.setattr(S, "detect_backend", lambda: S.BACKEND_NONE)
    with pytest.raises(SandboxError) as e:
        S.require_backend()
    assert "no way to enforce" in e.value.msg
    assert "--sandbox" in (e.value.hint or "")


def test_a_backend_that_does_not_confine_is_refused(monkeypatch, project,
                                                    tmp_path):
    """Present is not the same as working."""
    project("rules.policy", 'sandbox may run "echo"\n')
    project("s.frost", 'put "x"\n')
    monkeypatch.setenv("FROST_FAKE_BROKEN_SANDBOX", "1")
    import frostlang.sandbox as module
    original = module.self_test
    monkeypatch.setattr(module, "self_test",
                        lambda backend=None: (False, "deliberately broken"))
    from frostlang import cli

    class Options:
        policy = str(tmp_path / "rules.policy")
        script = str(tmp_path / "s.frost")
        sandbox = True

    guard, problem = cli.open_sandbox(Options())
    assert guard is None
    assert "did not confine" in problem
    assert "worse than none" in problem


# ------------------------------------------------------------- the pieces

def test_wrapping_refuses_a_program_outside_the_boundary(tmp_path):
    boundary = Boundary()
    boundary.declared = True
    boundary.programs = ["echo"]
    if BACKEND == S.BACKEND_NONE:
        pytest.skip("no backend")
    guard = Sandbox(boundary, str(tmp_path))
    with pytest.raises(SandboxError) as e:
        guard.wrap(["curl", "https://example.com"])
    assert "does not allow running" in e.value.msg
    guard.close()


@pytest.mark.skipif(BACKEND != S.BACKEND_MACOS, reason="macOS profile")
def test_the_macos_profile_denies_by_default(tmp_path):
    boundary = Boundary()
    boundary.writes = ["build/*"]
    profile = S.macos_profile(boundary, str(tmp_path))
    assert "(deny file-write*)" in profile
    assert "(deny network*)" in profile
    assert str(tmp_path / "build") in profile


@pytest.mark.skipif(BACKEND != S.BACKEND_MACOS, reason="macOS profile")
def test_the_macos_profile_opens_the_network_only_when_asked(tmp_path):
    closed = S.macos_profile(Boundary(), str(tmp_path))
    assert "(allow network*)" not in closed

    boundary = Boundary()
    boundary.network = True
    assert "(allow network*)" in S.macos_profile(boundary, str(tmp_path))


@pytest.mark.skipif(BACKEND != S.BACKEND_LINUX, reason="Linux backend")
def test_the_bubblewrap_argv_unshares_the_network_by_default(tmp_path):
    argv = S.bubblewrap_argv(Boundary(), str(tmp_path))
    assert "--unshare-net" in argv
    boundary = Boundary()
    boundary.network = True
    assert "--unshare-net" not in S.bubblewrap_argv(boundary, str(tmp_path))


def test_a_relative_pattern_is_anchored_to_the_script(tmp_path):
    """`build/*` means this project's build directory, not any other."""
    boundary = Boundary()
    boundary.writes = ["build/*"]
    if BACKEND == S.BACKEND_MACOS:
        assert str(tmp_path / "build") in S.macos_profile(boundary,
                                                          str(tmp_path))
    else:
        assert S._writable_root("build/*", str(tmp_path)) == str(
            tmp_path / "build")


@pytest.mark.parametrize("pattern,path,allowed", [
    ("build/*", "build/x.txt", True),
    ("build/*", "other/x.txt", False),
    ("/tmp/frost-*", "/tmp/frost-a", True),
    ("/tmp/frost-*", "/tmp/other", False),
    ("*", "anything", True),
])
def test_boundary_matching(pattern, path, allowed):
    boundary = Boundary()
    boundary.writes = [pattern]
    assert boundary.allows_write(path) is allowed


def test_an_unknowable_path_is_never_inside_a_boundary():
    """The case the whole feature exists for: the analyser could not resolve
    it, so it cannot be judged allowed."""
    boundary = Boundary()
    boundary.writes = ["*"]
    assert boundary.allows_write(None) is False


# ---------------------------------------------------------------- the doc

def test_the_documentation_does_not_promise_per_host_rules():
    """The claim that would be most tempting to make, and cannot be kept."""
    with open(os.path.join(REPO, "frostlang", "sandbox.py")) as fh:
        text = fh.read()
    assert "cannot do" in text.lower()
    assert "per-host" in text.lower() or "one host" in text.lower()
