"""Capability boundaries the operating system holds.

A sandbox nobody checked is a sandbox nobody has, so almost every test here
does the thing the boundary forbids and then looks at the filesystem. Asserting
that the wrapper was constructed would prove nothing: the question is whether
the write happened.

The tests are skipped where no backend exists rather than passing vacuously,
and `test_the_backend_actually_confines` is the canary: if that fails, every
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

# Gate on a backend that *works*, not one that exists. That distinction is the
# whole lesson of this module and the suite fell for it anyway: a machine can
# have bwrap installed and still refuse it the user namespace it needs, and
# gating on `which bwrap` turned that into a wall of failures instead of a
# reason. frost itself gets this right. It refuses to run there.
if BACKEND == S.BACKEND_NONE:
    WORKING, WHY = False, f"no backend on this platform ({sys.platform})"
else:
    WORKING, WHY = S.self_test()

# ...but a skip nobody notices is how a backend goes a year without once being
# run. CI sets this on every platform where the sandbox is supposed to work,
# and then nothing here is allowed to skip.
REQUIRED = os.environ.get("FROST_REQUIRE_SANDBOX") == "1"

needs_sandbox = pytest.mark.skipif(
    not WORKING and not REQUIRED,
    reason=f"no working sandbox backend here: {WHY}")


def test_the_sandbox_is_exercised_wherever_it_is_meant_to_be():
    """The guard on the gate above."""
    if not REQUIRED:
        pytest.skip("FROST_REQUIRE_SANDBOX is not set on this machine")
    assert WORKING, (
        f"FROST_REQUIRE_SANDBOX says the sandbox must work here, and it does "
        f"not: {WHY}")


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
    not names, and a Linux namespace is all or nothing, so a host allow-list
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

# Every policy in this section says `may reach the network`, and deliberately.
# These tests are about files and programs. Network isolation is a namespace
# with its own probe and its own tests, and on a machine that will not let one
# be entered a boundary requiring it is refused outright, which would turn
# every filesystem assertion below into a test of the refusal instead.
POLICY = '''
sandbox may run "sh"
sandbox may read "*"
sandbox may write "build/*"
sandbox may reach the network
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
    """Guards the test above from passing for the wrong reason, the write
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

    status, out, err = frost("--policy", "rules.policy", "--sandbox",
                             "s.frost", cwd=str(tmp_path))
    assert (tmp_path / "build" / "allowed.txt").exists(), (
        f"the allowed write did not happen.\n"
        f"  exit={status}\n  stdout={out!r}\n  stderr={err!r}\n"
        f"  boundary rooted at {tmp_path}")


@needs_sandbox
def test_a_program_the_boundary_does_not_name_is_refused(project, tmp_path):
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            'sandbox may reach the network\n')
    project("s.frost", 'run "curl" with "https://example.com"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert "does not allow running 'curl'" in err
    assert 'run "echo"' in err


@needs_sandbox
def test_a_pipe_stage_is_confined_too(project, tmp_path):
    """Every child, not just the ones spawned by `run`."""
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            'sandbox may reach the network\n')
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
                            'sandbox may write "build/*"\n'
                            'sandbox may reach the network\n')
    project("s.frost", f'put "data" into file "{tmp_path / "escaped.txt"}"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 1
    assert "does not allow writing" in err
    assert not (tmp_path / "escaped.txt").exists()


@needs_sandbox
def test_frosts_own_write_inside_the_boundary_succeeds(project, tmp_path):
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            f'sandbox may write "{tmp_path}/build/*"\n'
                            'sandbox may reach the network\n')
    project("s.frost", f'put "data" into file "{tmp_path}/build/out.txt"\n')
    status, _, err = frost("--policy", "rules.policy", "--sandbox", "s.frost",
                           cwd=str(tmp_path))
    assert status == 0, err
    assert (tmp_path / "build" / "out.txt").exists()


@needs_sandbox
def test_frosts_own_delete_is_checked(project, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("still here\n")
    project("rules.policy", 'sandbox may run "echo"\nsandbox may read "*"\n'
                            'sandbox may reach the network\n')
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
                            'sandbox may read "build/*"\n'
                            'sandbox may reach the network\n')
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


@needs_sandbox
def test_a_sandbox_that_runs_nothing_is_not_reported_as_working(monkeypatch):
    """The bug this pair of controls exists for.

    A backend that dies before executing anything blocks the forbidden write,
    because it blocks everything. Checking only for the forbidden write's
    absence therefore calls a completely broken sandbox healthy, which is
    what happened, in CI, on Linux, for four runs. Here the wrapper is
    replaced by a command that does nothing at all, and the self-test has to
    notice.
    """
    monkeypatch.setattr(Sandbox, "wrap",
                        lambda self, argv, folder=None: ["/bin/sh", "-c", ":"])
    working, detail = self_test()
    assert not working, "a sandbox that ran nothing was reported as confining"
    assert "not confining" in detail


@needs_sandbox
def test_the_self_test_passes_for_the_right_reason(tmp_path):
    """And the positive control is not merely always true: it fails when the
    boundary genuinely forbids the write."""
    working, detail = self_test()
    assert working, detail


def test_network_isolation_is_probed_rather_than_assumed(monkeypatch):
    """Whether a namespace can be entered is a fact about the machine."""
    S.network_isolation_works.cache_clear()
    calls = []
    monkeypatch.setattr(S.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or
                        type("R", (), {"returncode": 1})())
    monkeypatch.setattr(S, "detect_backend", lambda: S.BACKEND_LINUX)
    assert S.network_isolation_works() is False
    assert "--unshare-net" in calls[0]
    S.network_isolation_works.cache_clear()


def test_a_boundary_needing_isolation_is_refused_when_it_cannot_be_entered(
        monkeypatch, tmp_path):
    """Fail closed, not warn and continue. The alternative is a script that
    believes it has no network and does."""
    monkeypatch.setattr(S, "network_isolation_works", lambda backend=None: False)
    boundary = Boundary()
    boundary.declared = True
    boundary.network = False
    with pytest.raises(SandboxError) as e:
        Sandbox(boundary, str(tmp_path), S.BACKEND_LINUX)
    assert "cut off network access" in e.value.msg
    assert "may reach the network" in (e.value.hint or "")


def test_a_boundary_that_permits_the_network_needs_no_namespace(monkeypatch,
                                                                tmp_path):
    monkeypatch.setattr(S, "network_isolation_works", lambda backend=None: False)
    boundary = Boundary()
    boundary.declared = True
    boundary.network = True
    Sandbox(boundary, str(tmp_path), S.BACKEND_LINUX).close()      # no raise


def test_a_backend_that_does_not_confine_is_refused(monkeypatch, project,
                                                    tmp_path):
    """Present is not the same as working."""
    project("rules.policy", 'sandbox may run "echo"\n')
    project("s.frost", 'put "x"\n')
    import frostlang.sandbox as module
    # A backend has to look present, or require_backend refuses first and the
    # path being tested is never reached: which is what happened on Linux,
    # where the runners have no bubblewrap.
    monkeypatch.setattr(module, "require_backend",
                        lambda: module.BACKEND_MACOS)
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
    boundary.network = True          # see the note above POLICY
    if BACKEND == S.BACKEND_NONE:
        pytest.skip("no backend")
    guard = Sandbox(boundary, str(tmp_path))
    with pytest.raises(SandboxError) as e:
        guard.wrap(["curl", "https://example.com"])
    assert "does not allow running" in e.value.msg
    guard.close()


def test_the_macos_profile_denies_by_default(tmp_path):
    boundary = Boundary()
    boundary.writes = ["build/*"]
    profile = S.macos_profile(boundary, str(tmp_path))
    assert "(deny file-write*)" in profile
    assert "(deny network*)" in profile
    assert str(tmp_path / "build") in profile


def test_the_macos_profile_opens_the_network_only_when_asked(tmp_path):
    closed = S.macos_profile(Boundary(), str(tmp_path))
    assert "(allow network*)" not in closed

    boundary = Boundary()
    boundary.network = True
    assert "(allow network*)" in S.macos_profile(boundary, str(tmp_path))


def test_the_bubblewrap_argv_starts_in_the_right_directory(tmp_path):
    """bwrap starts the child in `/` whatever the parent's directory was, so
    without --chdir a command writing to a relative path writes somewhere
    else and the allowed write silently fails. Checked on every platform,
    because the behavioural test for it only runs where bwrap exists."""
    argv = S.bubblewrap_argv(Boundary(), str(tmp_path), str(tmp_path / "work"))
    assert "--chdir" in argv
    assert argv[argv.index("--chdir") + 1] == str(tmp_path / "work")


def test_the_bubblewrap_argv_falls_back_to_the_script_directory(tmp_path):
    argv = S.bubblewrap_argv(Boundary(), str(tmp_path), None)
    assert argv[argv.index("--chdir") + 1] == str(tmp_path)


def test_the_bubblewrap_argv_does_not_mask_the_working_directory(tmp_path):
    """A private /tmp is tempting and wrong: on a build machine the script's
    own directory is often under /tmp, and mounting over it makes every
    relative path resolve into an empty filesystem."""
    argv = S.bubblewrap_argv(Boundary(), str(tmp_path), None)
    assert "--tmpfs" not in argv


def test_the_bubblewrap_argv_binds_what_the_boundary_allows(tmp_path):
    (tmp_path / "build").mkdir()
    boundary = Boundary()
    boundary.writes = ["build/*"]
    argv = S.bubblewrap_argv(boundary, str(tmp_path), None)
    assert "--bind" in argv
    assert str(tmp_path / "build") in argv


@pytest.mark.skipif(BACKEND != S.BACKEND_LINUX, reason="Linux backend")
def test_the_bubblewrap_argv_unshares_the_network_by_default(tmp_path):
    argv = S.bubblewrap_argv(Boundary(), str(tmp_path))
    assert "--unshare-net" in argv
    boundary = Boundary()
    boundary.network = True
    assert "--unshare-net" not in S.bubblewrap_argv(boundary, str(tmp_path))


def test_a_boundary_pattern_is_resolved_through_symlinks(tmp_path):
    """macOS matches sandbox rules on the real path, and /tmp and /var are
    both symlinks there. An unresolved pattern names something the kernel
    never sees, so every write the boundary allows is denied, strict-looking
    and broken."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert S._anchor("out/*", str(link)) == str(real / "out" / "*")
    assert S._anchor(str(link / "out"), "/") == str(real / "out")


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

# The generators, checked on every platform. Each backend's text was only ever
# examined on the machine that could run it, so half of it was untested
# wherever the suite happened to be. These build strings and need no kernel.

def test_the_macos_profile_names_every_allowed_write(tmp_path):
    boundary = Boundary()
    boundary.writes = ["build/*", "/tmp/frost-scratch"]
    boundary.deletes = ["build/old/*"]
    profile = S.macos_profile(boundary, str(tmp_path))
    assert f'(subpath "{tmp_path}/build")' in profile
    assert '(literal "/private/tmp/frost-scratch")' in profile or \
           '(literal "/tmp/frost-scratch")' in profile
    assert f'(subpath "{tmp_path}/build/old")' in profile


def test_the_macos_profile_turns_a_glob_into_a_regex(tmp_path):
    boundary = Boundary()
    boundary.writes = ["log-????.txt"]
    profile = S.macos_profile(boundary, str(tmp_path))
    assert "(regex" in profile
    assert "[^/]" in profile


def test_the_glob_translation_escapes_what_a_regex_would_read(tmp_path):
    assert S._glob_to_regex("a.b") == "a\\.b"
    assert S._glob_to_regex("a*b") == "a[^/]*b"
    assert S._glob_to_regex("a?b") == "a[^/]b"
    assert S._glob_to_regex("a+b") == "a\\+b"


def test_the_macos_profile_lets_a_process_have_its_own_scratch(tmp_path):
    """A program that cannot open /dev/null fails in ways that read as frost
    being broken rather than as the sandbox working."""
    profile = S.macos_profile(Boundary(), str(tmp_path))
    assert '(allow file-write* (literal "/dev/null"))' in profile


def test_the_bubblewrap_argv_is_read_on_every_platform(tmp_path):
    """The twin of the above: the Linux wrapper was only examined on Linux."""
    boundary = Boundary()
    boundary.network = True
    argv = S.bubblewrap_argv(boundary, str(tmp_path))
    assert argv[0] == "bwrap"
    assert "--ro-bind" in argv and "--die-with-parent" in argv
    assert "--unshare-net" not in argv


def test_a_writable_root_stops_at_the_first_glob(tmp_path):
    assert S._writable_root("build/*", str(tmp_path)) == str(tmp_path / "build")
    assert S._writable_root("build/a/b.txt", str(tmp_path)) == \
        str(tmp_path / "build" / "a" / "b.txt")
    assert S._writable_root("*", str(tmp_path)) == str(tmp_path)
