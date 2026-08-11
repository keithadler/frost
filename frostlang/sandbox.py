"""Capability boundaries the operating system enforces.

`--explain` and `--policy` answer a question about the *text* of a script.
They are sound about literals and honest about everything else: a path built
at runtime is reported as unknowable rather than guessed. That honesty is
also the gap. Once the script runs, an unknowable path is a real path, and
nothing was standing between it and the filesystem.

This closes that. A boundary is declared once, in the policy file, and the
runtime holds it:

    sandbox may run "git", "make"
    sandbox may read "*"
    sandbox may write "build/*", "/tmp/frost-*"

    frost --policy prod.policy --sandbox deploy.frost

A path the analyser could not resolve is still confined, because the
confinement does not depend on having resolved it.

## Two enforcers, and the difference matters

**Child processes are confined by the operating system.** On macOS through
`sandbox-exec`, on Linux through `bubblewrap`. Once a program is running
inside one of those, frost is not in the loop: the kernel refuses the write.
That holds even if the program is malicious, even if frost has a bug.

**frost's own file operations are confined by frost.** `put X into file
(path)` happens in this process, so the check is a check in the interpreter.
It is enforced by the same code that is being trusted to run the script at
all: which is a weaker claim, and this module says so rather than blurring
the two.

## What this cannot do

**Per-host network rules.** The obvious thing to want is *may reach
api.github.com and nothing else*. macOS's sandbox language filters on IP
literals, not names; Linux namespaces give you a network or no network. A
hostname allowlist needs a proxy, which is a different program. So network is
all-or-nothing here, `sandbox may reach the network` says exactly that, and a
per-host rule is **refused at parse time** rather than accepted and silently
under-enforced. A boundary that does not hold is worse than no boundary,
because someone relies on it.

**Machines that will not let a network namespace be entered.** bubblewrap
unshares the network and then configures a loopback interface inside it, and
where unprivileged user namespaces are restricted that second step is refused
bwrap exits before running anything. So `sandbox may reach the network` is
enforceable there and its absence is not. That is probed, not assumed, and a
boundary that needs isolation frost cannot enter is refused up front.

**Platforms without a backend.** If a boundary is declared and cannot be
enforced here, frost refuses to run. It does not warn and continue. The whole
value is that the guarantee is unconditional, and a guarantee with a
platform-shaped hole in it is a guarantee nobody can reason about.

**Anything a permitted program then does.** A sandbox that may run `git` may
run every `git` subcommand. Confinement bounds the blast radius; it does not
read intent.
"""
# SPDX-License-Identifier: MIT

import fnmatch
import functools
import os
import platform
import shutil
import subprocess
import tempfile

BACKEND_NONE = "none"
BACKEND_MACOS = "sandbox-exec"
BACKEND_LINUX = "bubblewrap"


class SandboxError(Exception):
    """A boundary was declared that cannot be held here."""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


class Boundary:
    """What a script may do, as an allow-list.

    Deny-shaped policy rules cannot become a sandbox: `forbid writing to
    "/etc/*"` says nothing about what writing *is* allowed, and a sandbox
    needs the positive form. So the boundary is declared separately, in the
    same file, with the same vocabulary as a module's ceiling.
    """

    def __init__(self):
        self.programs = []
        self.reads = []
        self.writes = []
        self.deletes = []
        self.network = False
        self.declared = False        # was a `sandbox may ...` rule seen?

    def allows_program(self, name):
        return name is not None and _matches(name, self.programs)

    def allows_read(self, path):
        return _matches(path, self.reads)

    def allows_write(self, path):
        return _matches(path, self.writes)

    def allows_delete(self, path):
        return _matches(path, self.deletes)

    def describe(self):
        parts = []
        for label, values in (("run", self.programs), ("read", self.reads),
                              ("write", self.writes), ("delete", self.deletes)):
            if values:
                parts.append(f"{label} " + ", ".join(f'"{v}"' for v in values))
        if self.network:
            parts.append("reach the network")
        return " and ".join(parts) if parts else "nothing"


def _matches(path, patterns):
    if path is None:
        return False
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


# ------------------------------------------------------------- the backend

def detect_backend():
    """Which enforcement is available here, if any."""
    system = platform.system()
    if system == "Darwin" and shutil.which("sandbox-exec"):
        return BACKEND_MACOS
    if system == "Linux" and shutil.which("bwrap"):
        return BACKEND_LINUX
    return BACKEND_NONE


def describe_backend(backend=None):
    backend = backend or detect_backend()
    return {
        BACKEND_MACOS: "macOS sandbox-exec",
        BACKEND_LINUX: "Linux bubblewrap",
        BACKEND_NONE: "none available on this platform",
    }[backend]


def require_backend():
    backend = detect_backend()
    if backend == BACKEND_NONE:
        system = platform.system()
        raise SandboxError(
            f"no way to enforce a sandbox on {system}",
            hint="frost confines child processes with sandbox-exec on macOS "
                 "and bubblewrap on Linux. Install bubblewrap, or run without "
                 "--sandbox and accept that the boundary is not enforced.")
    return backend


@functools.lru_cache(maxsize=None)
def network_isolation_works(backend=None):
    """Whether a network namespace can actually be entered here.

    Asked as a question rather than assumed, because the failure is invisible
    from the outside: bwrap dies while configuring loopback, so the command
    never runs, so nothing it was forbidden to do happens. That reads as
    flawless confinement. It is a sandbox that confines by not working.
    """
    backend = backend or detect_backend()
    if backend == BACKEND_MACOS:
        return True             # SBPL filters syscalls; no namespace to enter
    if backend != BACKEND_LINUX:
        return False
    try:
        done = subprocess.run(
            ["bwrap", "--ro-bind", "/", "/", "--unshare-net",
             "--die-with-parent", "/bin/sh", "-c", ":"],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


# ------------------------------------------------------- macOS: sandbox-exec

def macos_profile(boundary, root):
    """A sandbox profile in SBPL.

    Reads are allowed broadly and writes are not: a script that cannot read
    its own interpreter, libraries or locale data does not start, and the
    interesting boundary is what it can change. Writes, deletes and network
    are denied unless the boundary named them.
    """
    lines = [
        "(version 1)",
        ";; generated by frost: do not edit",
        "(allow default)",
        "(deny file-write*)",
        "(deny network*)",
    ]

    # A process needs somewhere to put its own temporary files, or ordinary
    # programs fail in ways that look like frost is broken.
    for pattern in ("/dev/null", "/dev/dtracehelper", "/dev/tty",
                    "/dev/urandom", "/dev/random"):
        lines.append(f'(allow file-write* (literal "{pattern}"))')

    for pattern in boundary.writes + boundary.deletes:
        lines.append(f"(allow file-write* {_sbpl_target(pattern, root)})")

    if boundary.network:
        lines.append("(allow network*)")

    return "\n".join(lines) + "\n"


def _sbpl_target(pattern, root):
    """One allow-list entry, as an SBPL filter.

    A glob becomes a subpath or a regex; a plain path becomes a literal.
    Relative patterns are anchored to the script's directory, so `build/*`
    means this project's build directory and not any other.
    """
    absolute = _anchor(pattern, root)
    if absolute.endswith("/*"):
        return f'(subpath "{absolute[:-2]}")'
    if any(ch in absolute for ch in "*?["):
        return f'(regex #"^{_glob_to_regex(absolute)}$")'
    return f'(literal "{absolute}")'


def _anchor(pattern, root):
    """A boundary pattern as one absolute, symlink-free path.

    Resolving matters more than it looks. macOS matches sandbox rules against
    the real path, and `/tmp` and `/var` are both symlinks there, so a
    boundary written as `/tmp/build/*` produced a rule that could never match
    anything, and every write the boundary *allowed* was denied. The sandbox
    looked strict. It was broken.
    """
    absolute = pattern if os.path.isabs(pattern) else os.path.join(root,
                                                                   pattern)
    return os.path.realpath(absolute)


def _glob_to_regex(pattern):
    out = []
    for ch in pattern:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch in ".^$+(){}|\\[]":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


# ------------------------------------------------------ Linux: bubblewrap

def bubblewrap_argv(boundary, root, folder=None):
    """The bwrap wrapper for one command.

    The filesystem is mounted read-only and the paths the boundary names are
    re-bound writable on top. Network is a namespace: present or absent.

    `--chdir` is not optional. bwrap starts the child in `/` regardless of
    the parent's working directory, so without it a command writing to a
    relative path writes somewhere else entirely, the boundary looked
    airtight while the allowed write silently failed.
    """
    # No `--tmpfs /tmp`. Giving the child a private /tmp is tempting, but it
    # masks whatever is really there: including, on a build machine, the
    # directory the script itself is running in. The read-only bind of / is
    # stricter anyway: /tmp is writable only if the boundary names it.
    argv = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    argv += ["--chdir", folder or root]
    for pattern in boundary.writes + boundary.deletes:
        base = _writable_root(pattern, root)
        if base and os.path.exists(base):
            argv += ["--bind", base, base]
    if not boundary.network:
        argv.append("--unshare-net")
    argv.append("--die-with-parent")
    return argv


def _writable_root(pattern, root):
    """The deepest directory a glob is rooted at, since bwrap binds paths."""
    head = _anchor(pattern, root)
    while any(ch in head for ch in "*?[") and head not in ("/", ""):
        head = os.path.dirname(head)
    return head or None


# ---------------------------------------------------------------- the guard

class Sandbox:
    """Holds a boundary, and wraps the commands a script runs."""

    def __init__(self, boundary, root, backend=None):
        self.boundary = boundary
        self.root = os.path.realpath(root)
        self.backend = backend or require_backend()
        self._profile_path = None

        if not boundary.network and not network_isolation_works(self.backend):
            raise SandboxError(
                "this machine will not let frost cut off network access",
                hint="bubblewrap has to enter a new network namespace and "
                     "bring up loopback inside it; here that is refused, and "
                     "bwrap exits before running anything. Restricted "
                     "unprivileged user namespaces are the usual reason. Add "
                     "`sandbox may reach the network` if the script is "
                     "allowed to use it, or run somewhere namespaces are "
                     "permitted, frost will not hold part of a boundary and "
                     "call it the boundary.")

    def close(self):
        if self._profile_path and os.path.exists(self._profile_path):
            os.unlink(self._profile_path)
            self._profile_path = None

    # -- children

    def wrap(self, argv, folder=None):
        """The command line that runs `argv` inside the boundary.

        `folder` is where the command should start, which some backends have
        to be told explicitly.
        """
        program = argv[0] if argv else ""
        if not self.boundary.allows_program(os.path.basename(program)) \
                and not self.boundary.allows_program(program):
            raise SandboxError(
                f"the sandbox does not allow running {program!r}",
                hint=f"the boundary allows: {self.boundary.describe()}")

        if self.backend == BACKEND_MACOS:
            if self._profile_path is None:
                handle, path = tempfile.mkstemp(suffix=".sb", prefix="frost-")
                with os.fdopen(handle, "w") as fh:
                    fh.write(macos_profile(self.boundary, self.root))
                self._profile_path = path
            return ["sandbox-exec", "-f", self._profile_path] + list(argv)

        if self.backend == BACKEND_LINUX:
            return bubblewrap_argv(self.boundary, self.root,
                                   folder) + list(argv)

        raise SandboxError("no sandbox backend")     # pragma: no cover

    # -- frost's own operations
    #
    # These are enforced by the interpreter rather than by the kernel, which
    # is a weaker claim and is documented as one. They exist because `put X
    # into file (path)` never becomes a child process, so the OS never sees
    # it, and leaving that unguarded would be a hole exactly where the static
    # analyser already admitted it could not see.

    def check_read(self, path, line):
        if not self.boundary.allows_read(path):
            raise SandboxError(
                f"the sandbox does not allow reading {path!r}",
                hint=f"the boundary allows: {self.boundary.describe()}")

    def check_write(self, path, line):
        if not self.boundary.allows_write(path):
            raise SandboxError(
                f"the sandbox does not allow writing {path!r}",
                hint=f"the boundary allows: {self.boundary.describe()}")

    def check_delete(self, path, line):
        if not self.boundary.allows_delete(path):
            raise SandboxError(
                f"the sandbox does not allow deleting {path!r}",
                hint=f"the boundary allows: {self.boundary.describe()}")


def self_test(backend=None):
    """Prove the backend actually confines, right now, on this machine.

    Two controls, because one is worthless. A **negative** control, a write
    outside the boundary must be refused, and a **positive** control, a
    write inside it must succeed.

    The negative control alone cannot tell confinement from collapse. A
    sandbox that fails to start blocks the forbidden write too, and then
    reports itself healthy; that is exactly how a Linux backend passed this
    check while every command it wrapped was dying before it ran. An absence
    is only evidence if something was there to be absent.
    """
    backend = backend or detect_backend()
    if backend == BACKEND_NONE:
        return False, "no backend"

    with tempfile.TemporaryDirectory() as raw:
        # Not the path tempfile handed back: on macOS that is under /var,
        # which is a symlink, and the profile would name something the kernel
        # never sees.
        scratch = os.path.realpath(raw)
        boundary = Boundary()
        boundary.declared = True
        boundary.programs = ["sh", "/bin/sh"]
        boundary.reads = ["*"]
        boundary.writes = [os.path.join(scratch, "allowed", "*")]
        # The namespace is a separate question with its own probe. Asking for
        # it here would let a loopback failure masquerade as a verdict about
        # the filesystem.
        boundary.network = True
        os.makedirs(os.path.join(scratch, "allowed"), exist_ok=True)
        forbidden = os.path.join(scratch, "forbidden.txt")
        permitted = os.path.join(scratch, "allowed", "ok.txt")

        try:
            sandbox = Sandbox(boundary, scratch, backend)
            argv = sandbox.wrap(
                ["/bin/sh", "-c", f"echo x > {forbidden} 2>/dev/null; "
                                  f"echo y > {permitted} 2>/dev/null"],
                folder=scratch)
            done = subprocess.run(argv, capture_output=True, timeout=30)
            sandbox.close()
        except (SandboxError, OSError, subprocess.SubprocessError) as e:
            return False, str(e)

        if os.path.exists(forbidden):
            return False, "the backend did not block a write outside the "\
                          "boundary"
        if not os.path.exists(permitted):
            noise = (done.stderr or b"").decode("utf-8", "replace").strip()
            return False, ("the backend blocked a write the boundary allows, "
                           "so it is not confining. It is failing"
                           + (f": {noise}" if noise else ""))
    return True, describe_backend(backend)
