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
all — which is a weaker claim, and this module says so rather than blurring
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
        ";; generated by frost — do not edit",
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
    absolute = pattern if os.path.isabs(pattern) else os.path.join(root,
                                                                  pattern)
    if absolute.endswith("/*"):
        return f'(subpath "{absolute[:-2]}")'
    if any(ch in absolute for ch in "*?["):
        return f'(regex #"^{_glob_to_regex(absolute)}$")'
    return f'(literal "{absolute}")'


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
    relative path writes somewhere else entirely — the boundary looked
    airtight while the allowed write silently failed.
    """
    # No `--tmpfs /tmp`. Giving the child a private /tmp is tempting, but it
    # masks whatever is really there — including, on a build machine, the
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
    absolute = pattern if os.path.isabs(pattern) else os.path.join(root,
                                                                   pattern)
    head = absolute
    while any(ch in head for ch in "*?[") and head not in ("/", ""):
        head = os.path.dirname(head)
    return head or None


# ---------------------------------------------------------------- the guard

class Sandbox:
    """Holds a boundary, and wraps the commands a script runs."""

    def __init__(self, boundary, root, backend=None):
        self.boundary = boundary
        self.root = os.path.abspath(root)
        self.backend = backend or require_backend()
        self._profile_path = None

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

    A sandbox nobody checked is a sandbox nobody has. This runs a real
    command that tries to write outside its boundary and returns whether the
    write was refused, so `--sandbox` can fail closed on a backend that is
    present but not working.
    """
    backend = backend or detect_backend()
    if backend == BACKEND_NONE:
        return False, "no backend"

    with tempfile.TemporaryDirectory() as scratch:
        boundary = Boundary()
        boundary.declared = True
        boundary.programs = ["sh", "/bin/sh"]
        boundary.reads = ["*"]
        boundary.writes = [os.path.join(scratch, "allowed", "*")]
        os.makedirs(os.path.join(scratch, "allowed"), exist_ok=True)
        forbidden = os.path.join(scratch, "forbidden.txt")

        try:
            sandbox = Sandbox(boundary, scratch, backend)
            argv = sandbox.wrap(["/bin/sh", "-c",
                                 f"echo x > {forbidden} 2>/dev/null"],
                                folder=scratch)
            subprocess.run(argv, capture_output=True, timeout=30)
            sandbox.close()
        except (SandboxError, OSError, subprocess.SubprocessError) as e:
            return False, str(e)

        if os.path.exists(forbidden):
            return False, "the backend did not block a write outside the "\
                          "boundary"
    return True, describe_backend(backend)
