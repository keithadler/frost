"""Static analysis over a frost script, before it runs.

Because a frost script is parsed rather than string-substituted, everything it
can do is visible in the tree. That makes two things possible that a shell
script cannot offer:

  * a capability manifest — a plain reading of what the script will touch
  * policy enforcement — rules checked before a single process is spawned

Both work on literals only. A program name or path built at runtime is reported
as unknown rather than guessed at, because a manifest that quietly omits things
is worse than no manifest.
"""
# SPDX-License-Identifier: MIT

import fnmatch
import re
from dataclasses import dataclass, field
from typing import List, Optional

from . import ast as A


@dataclass
class Command:
    program: Optional[str]        # None when built at runtime
    args: List[Optional[str]]
    line: int
    checked: bool
    timeout: bool
    in_pipe: bool = False
    result_examined: bool = False


@dataclass
class Capabilities:
    commands: List[Command] = field(default_factory=list)
    reads: List[tuple] = field(default_factory=list)     # (path, line)
    writes: List[tuple] = field(default_factory=list)
    deletes: List[tuple] = field(default_factory=list)
    env_reads: List[tuple] = field(default_factory=list)
    exit_codes: List[tuple] = field(default_factory=list)
    handlers: List[str] = field(default_factory=list)
    dynamic: int = 0              # count of runtime-built names
    # (line, [literal fragments]) for every path expression, so a sensitive
    # tail hidden behind a variable prefix is still visible.
    read_fragments: List[tuple] = field(default_factory=list)
    write_fragments: List[tuple] = field(default_factory=list)


def literal(node):
    """The literal text of a node, or None if it is computed at runtime."""
    if isinstance(node, A.Lit):
        return str(node.value)
    if isinstance(node, A.BinOp) and node.op in ("&", "&&"):
        left = literal(node.left)
        right = literal(node.right)
        if left is not None and right is not None:
            return left + ("" if node.op == "&" else " ") + right
    return None


def literal_fragments(node):
    """Every literal string anywhere in an expression.

    `home & "/.ssh/id_rsa"` has no whole-string literal, but the fragment
    "/.ssh/id_rsa" is right there in the tree. Secret-path detection works on
    fragments, so a path assembled at runtime cannot hide a known-sensitive
    tail behind a variable prefix.
    """
    found = []

    def walk(n):
        if isinstance(n, A.Lit) and isinstance(n.value, str):
            found.append(n.value)
        elif hasattr(n, "__dataclass_fields__"):
            for v in vars(n).values():
                if isinstance(v, list):
                    for item in v:
                        walk(item)
                elif hasattr(v, "__dataclass_fields__"):
                    walk(v)

    walk(node)
    return found


class Auditor:
    def __init__(self):
        self.caps = Capabilities()
        self.seen = set()   # Run nodes already recorded, so pipe stages are
                            # not counted twice by the generic walk

    def scan(self, stmts):
        self.visit_block(stmts)
        return self.caps

    @staticmethod
    def mentions_result(node):
        """Does this subtree read `the result`?"""
        if node is None:
            return False
        if isinstance(node, A.ResultRef):
            return True
        if isinstance(node, list):
            return any(Auditor.mentions_result(n) for n in node)
        if not hasattr(node, "__dataclass_fields__"):
            return False
        return any(Auditor.mentions_result(v) for v in vars(node).values())

    def visit_block(self, stmts):
        """Visit a statement list, noting whether a `try to` result is read.

        A `try to run` followed by a check of `the result` is correct code, not
        an unchecked failure. Looking one step ahead in the same block is
        enough to tell them apart.
        """
        for idx, stmt in enumerate(stmts):
            before = len(self.caps.commands)
            self.visit(stmt)
            new = self.caps.commands[before:]
            if not new or all(c.checked for c in new):
                continue
            following = stmts[idx + 1:idx + 3]
            if any(self.mentions_result(f) for f in following):
                for c in new:
                    c.result_examined = True

    # -- traversal

    def visit(self, node):
        if node is None:
            return
        if isinstance(node, list):
            self.visit_block(node)
            return

        name = type(node).__name__
        handler = getattr(self, "on_" + name, None)
        if handler:
            handler(node)

        # Walk every child, so nested expressions are not missed.
        for value in vars(node).values():
            if isinstance(value, (A.Lit, A.Var)) or value is None:
                continue
            if isinstance(value, list):
                self.visit_block([v for v in value
                                  if hasattr(v, "__dataclass_fields__")])
            elif hasattr(value, "__dataclass_fields__"):
                self.visit(value)

    # -- collectors

    def record_run(self, node, in_pipe=False):
        if id(node) in self.seen:
            return
        self.seen.add(id(node))
        program = literal(node.program)
        if program is None:
            self.caps.dynamic += 1
        self.caps.commands.append(Command(
            program=program,
            args=[literal(a) for a in node.args],
            line=node.line,
            checked=node.checked,
            timeout=node.timeout is not None,
            in_pipe=in_pipe,
        ))

    def on_Run(self, node):
        self.record_run(node)

    def on_Pipe(self, node):
        for stage in node.stages:
            self.record_run(stage, in_pipe=True)

    def on_FileRef(self, node):
        path = literal(node.path)
        self.caps.reads.append((path, node.line))
        self.caps.read_fragments.append((node.line,
                                         literal_fragments(node.path)))
        if path is None:
            self.caps.dynamic += 1

    def on_FileExists(self, node):
        # A `file "x" exists` test wraps a FileRef, which the generic walk will
        # visit on its own. Only record the bare-expression form here.
        if isinstance(node.path, A.FileRef):
            return
        path = literal(node.path)
        self.caps.reads.append((path, node.line))

    def on_Put(self, node):
        if isinstance(node.target, A.FileTarget):
            path = literal(node.target.path)
            self.caps.writes.append((path, node.line))
            self.caps.write_fragments.append(
                (node.line, literal_fragments(node.target.path)))
            if path is None:
                self.caps.dynamic += 1

    def on_DeleteFile(self, node):
        path = literal(node.path)
        self.caps.deletes.append((path, node.line))
        if path is None:
            self.caps.dynamic += 1

    def on_EnvRef(self, node):
        self.caps.env_reads.append((literal(node.name), node.line))

    def on_Quit(self, node):
        code = literal(node.status) if node.status else "0"
        self.caps.exit_codes.append((code, node.line))

    def on_HandlerDef(self, node):
        self.caps.handlers.append(node.name)


def audit(stmts):
    # Handlers are declarations; their bodies still count as capabilities.
    return Auditor().scan(stmts)


# --------------------------------------------------------------- manifest

def describe(caps):
    """A plain reading of what a script can do.

    Columns are aligned within a section. This is a manifest someone approves
    under time pressure, and a ragged left column makes it read as prose when
    the point is that it should read as a table.
    """
    out = []

    def section(title, rows):
        """`rows` is a list of (subject, trailer) pairs."""
        if not rows:
            return
        width = max(len(subject) for subject, _ in rows)
        out.append(title)
        out.extend(f"  {subject.ljust(width)}  {trailer}".rstrip()
                   for subject, trailer in rows)
        out.append("")

    def where(path, line):
        return (path or "(path built at runtime)", f"— line {line}")

    programs = {}
    for c in caps.commands:
        key = c.program or "(built at runtime)"
        programs.setdefault(key, []).append(c)

    rows = []
    for name in sorted(programs):
        uses = programs[name]
        detail = []
        unchecked = sum(1 for u in uses
                        if not u.checked and not u.result_examined)
        untimed = sum(1 for u in uses if not u.timeout)
        if unchecked:
            detail.append(f"{unchecked} allowed to fail")
        if untimed == len(uses):
            detail.append("no timeout")
        note = f"  ({', '.join(detail)})" if detail else ""
        at = ", ".join(str(u.line) for u in uses)
        rows.append((name, f"— line {at}{note}"))
    section("Runs these programs:", rows)

    section("Reads these files:",
            [where(p, ln) for p, ln in caps.reads])
    section("Writes these files:",
            [where(p, ln) for p, ln in caps.writes])
    section("Deletes these files:",
            [where(p, ln) for p, ln in caps.deletes])
    section("Reads these environment variables:",
            [(n, f"— line {ln}") for n, ln in caps.env_reads])
    section("Can exit with:",
            [(f"status {c}", "") for c in sorted({c for c, _ in
                                                  caps.exit_codes})])

    if caps.dynamic:
        out.append(f"Note: {caps.dynamic} name(s) are built at runtime and "
                   f"cannot be checked ahead of time.")
        out.append("")

    return "\n".join(out).rstrip() or "This script does nothing observable."


# ----------------------------------------------------------------- policy

RULE_PATTERNS = [
    (re.compile(r'^(forbid|warn)\s+running\s+"([^"]+)"'
                r'(?:\s+with\s+"([^"]+)")?\s*$'), "run"),
    (re.compile(r'^(forbid|warn)\s+writing\s+to\s+"([^"]+)"\s*$'), "write"),
    (re.compile(r'^(forbid|warn)\s+reading\s+"([^"]+)"\s*$'), "read"),
    (re.compile(r'^(forbid|warn)\s+deleting\s+"([^"]+)"\s*$'), "delete"),
    (re.compile(r'^require\s+timeout\s+on\s+"([^"]+)"\s*$'), "timeout"),
    (re.compile(r'^require\s+every\s+command\s+to\s+be\s+checked\s*$'),
     "checked"),
]


class PolicyError(Exception):
    pass


@dataclass
class Rule:
    kind: str
    severity: str
    subject: str
    detail: Optional[str] = None
    source_line: int = 0


def parse_policy(text):
    rules = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("--")[0].split("#")[0].strip()
        if not line:
            continue
        for rx, kind in RULE_PATTERNS:
            m = rx.match(line)
            if not m:
                continue
            groups = m.groups()
            if kind == "checked":
                rules.append(Rule(kind, "forbid", "*", None, n))
            elif kind == "timeout":
                rules.append(Rule(kind, "forbid", groups[0], None, n))
            elif kind == "run":
                rules.append(Rule(kind, groups[0], groups[1], groups[2], n))
            else:
                rules.append(Rule(kind, groups[0], groups[1], None, n))
            break
        else:
            raise PolicyError(f"policy line {n}: cannot read {line!r}")
    return rules


def check(caps, rules):
    """Return a list of (severity, message, script_line)."""
    findings = []

    for rule in rules:
        if rule.kind == "run":
            for c in caps.commands:
                if c.program is None:
                    continue
                if not fnmatch.fnmatchcase(c.program, rule.subject):
                    continue
                if rule.detail is not None:
                    if not any(a is not None
                               and fnmatch.fnmatchcase(a, rule.detail)
                               for a in c.args):
                        continue
                    what = f'running "{c.program}" with "{rule.detail}"'
                else:
                    what = f'running "{c.program}"'
                findings.append((rule.severity, what, c.line))

        elif rule.kind in ("write", "read", "delete"):
            source = {"write": caps.writes, "read": caps.reads,
                      "delete": caps.deletes}[rule.kind]
            verb = {"write": "writing to", "read": "reading",
                    "delete": "deleting"}[rule.kind]
            for path, line in source:
                if path is None:
                    continue
                if fnmatch.fnmatchcase(path, rule.subject):
                    findings.append((rule.severity, f"{verb} {path}", line))

        elif rule.kind == "timeout":
            for c in caps.commands:
                if c.program and fnmatch.fnmatchcase(c.program, rule.subject) \
                        and not c.timeout:
                    findings.append(
                        ("forbid",
                         f'"{c.program}" has no timeout', c.line))

        elif rule.kind == "checked":
            for c in caps.commands:
                if not c.checked and not c.in_pipe and not c.result_examined:
                    findings.append(
                        ("forbid",
                         f'"{c.program or "?"}" may fail without being checked',
                         c.line))

    findings.sort(key=lambda f: f[2])
    return findings


# ------------------------------------------------- built-in danger checks

NETWORK_PROGRAMS = {
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat",
    "ftp", "telnet", "pip", "pip3", "npm", "yarn", "brew", "apt", "apt-get",
    "gem", "cargo", "go",
}

SHELL_PROGRAMS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "python",
                  "python3", "perl", "ruby", "node", "osascript"}

SYSTEM_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/var/lib",
                   "/System", "/Library", "/private/etc", "/opt")

SECRET_PATHS = ("*/.ssh/*", "*/.aws/*", "*/.gnupg/*", "*/.netrc",
                "*/.env", "*/credentials*", "*/id_rsa*", "*/id_ed25519*",
                "*.pem", "*.key")

SECRET_ENV = {"GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY",
              "AWS_SESSION_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "NPM_TOKEN", "SLACK_TOKEN", "STRIPE_SECRET_KEY",
              "DATABASE_URL", "SECRET_KEY", "PRIVATE_KEY"}


@dataclass
class Finding:
    severity: str          # danger | caution | note
    title: str
    detail: str
    line: int
    source: str = "built-in"


def classify_path(path):
    if path is None:
        return "runtime"
    if path.startswith(SYSTEM_PREFIXES):
        return "system"
    if path.startswith(("/tmp", "/var/tmp", "/private/tmp")):
        return "temporary"
    if path.startswith("~") or path.startswith("/Users") or \
            path.startswith("/home"):
        return "home"
    if path.startswith("/dev"):
        return "device"
    if path.startswith("/"):
        return "absolute"
    return "relative"


def has_glob(text):
    return text is not None and any(ch in text for ch in "*?[")


def find_dangers(caps):
    """Checks that run with no policy file, on every script."""
    out = []

    for c in caps.commands:
        prog = c.program
        args = [a for a in c.args if a is not None]
        lowered = [a.lower() for a in args]

        if prog in ("rm", "rmdir"):
            flags = "".join(a for a in lowered if a.startswith("-"))
            recursive = "r" in flags
            forced = "f" in flags
            if recursive and forced:
                out.append(Finding(
                    "danger", "Recursive forced delete",
                    "rm -rf removes a whole tree without confirming and "
                    "without reporting what it removed.", c.line))
            elif recursive:
                out.append(Finding(
                    "caution", "Recursive delete",
                    "rm -r removes a directory and everything under it.",
                    c.line))
            if any(has_glob(a) for a in args):
                out.append(Finding(
                    "danger", "Delete with a wildcard",
                    "The set of files removed depends on what happens to be "
                    "on disk at the time, so it cannot be checked here.",
                    c.line))

        if prog in ("sudo", "doas", "su"):
            out.append(Finding(
                "danger", "Elevated privileges",
                "Everything after this point runs as another user, outside "
                "anything this audit can see.", c.line))

        if prog == "chmod" and any(a in ("777", "-R", "a+rwx") for a in args):
            out.append(Finding(
                "danger", "Permissive or recursive permission change",
                "Making files world-writable lets any local user modify them.",
                c.line))

        if prog in ("dd", "mkfs", "fdisk", "diskutil", "shutdown", "reboot"):
            out.append(Finding(
                "danger", f"Destructive system command ({prog})",
                "This can overwrite or unmount storage devices.", c.line))

        if prog in SHELL_PROGRAMS and "-c" in args:
            out.append(Finding(
                "danger", f"Shell escape via {prog} -c",
                "The text handed to -c is re-parsed as a command line, which "
                "reintroduces exactly the injection risk frost removes.",
                c.line))

        if prog in NETWORK_PROGRAMS:
            targets = [a for a in args if "://" in a or a.count(".") >= 2]
            where = f" ({targets[0]})" if targets else ""
            out.append(Finding(
                "note", f"Reaches the network via {prog}{where}",
                "Data can leave this machine, and the result depends on a "
                "system you do not control.", c.line))
            if not c.timeout:
                out.append(Finding(
                    "caution", f"No timeout on {prog}",
                    "A network command with no deadline can hang the script "
                    "indefinitely.", c.line))

        if prog is None:
            out.append(Finding(
                "caution", "Program name built at runtime",
                "Which program runs cannot be determined before the script "
                "starts, so it cannot be checked here.", c.line))

        if not c.checked and not c.in_pipe and not c.result_examined:
            out.append(Finding(
                "caution", f"Failure ignored ({c.program or 'runtime name'})",
                "This was written as 'try to run' but the result is never "
                "examined, so a failure passes silently.", c.line))

    # remote code execution: a network fetch piped into an interpreter
    pipe_progs = [c.program for c in caps.commands if c.in_pipe]
    if any(p in NETWORK_PROGRAMS for p in pipe_progs) and \
            any(p in SHELL_PROGRAMS for p in pipe_progs):
        line = next(c.line for c in caps.commands
                    if c.in_pipe and c.program in SHELL_PROGRAMS)
        out.append(Finding(
            "danger", "Downloaded code piped into a shell",
            "Whatever the server returns is executed. The script's behaviour "
            "is decided by a remote host.", line))

    for path, line in caps.writes:
        if classify_path(path) == "system":
            out.append(Finding(
                "danger", f"Writes to a system location ({path})",
                "Changes here affect every user and survive the script.", line))

    for path, line in caps.deletes:
        if classify_path(path) == "system":
            out.append(Finding(
                "danger", f"Deletes from a system location ({path})",
                "Removing system files can leave the machine unbootable.",
                line))

    for name, line in caps.env_reads:
        if name in SECRET_ENV:
            out.append(Finding(
                "caution", f"Reads a secret from the environment ({name})",
                "This environment variable normally holds a token or key.",
                line))

    flagged_secret_lines = set()
    for line, fragments in caps.read_fragments:
        joined = "".join(fragments)
        if any(fnmatch.fnmatchcase(joined, p) or
               any(fnmatch.fnmatchcase(f, p) for f in fragments)
               for p in SECRET_PATHS):
            flagged_secret_lines.add(line)
            out.append(Finding(
                "danger", "Reads credentials",
                "This path points at a file that usually holds keys or "
                "tokens.", line))

    # Exfiltration: the script reads secrets (files or sensitive env vars)
    # AND reaches the network. Either alone is normal; together, on a script
    # you did not write, is the shape of a supply-chain attack.
    reads_secrets = bool(flagged_secret_lines) or any(
        n in SECRET_ENV for n, _ in caps.env_reads)
    net_lines = [c.line for c in caps.commands
                 if c.program in NETWORK_PROGRAMS]
    if reads_secrets and net_lines:
        out.append(Finding(
            "danger", "Secrets read, then the network is contacted",
            "This script reads credentials and also sends data over the "
            "network. On a script you did not write yourself, that is the "
            "pattern of data theft.", min(net_lines)))

    out.sort(key=lambda f: (f.line,
                            {"danger": 0, "caution": 1, "note": 2}[f.severity]))
    return out


# ------------------------------------------------------- effect summary

def _count(items, noun):
    n = len(items)
    return f"{n} {noun}" + ("" if n == 1 else "s")


def summarise(caps):
    """One paragraph: what will this script actually do?"""
    parts = []

    programs = sorted({c.program for c in caps.commands if c.program})
    if programs:
        shown = ", ".join(programs[:4])
        more = f" and {len(programs) - 4} more" if len(programs) > 4 else ""
        parts.append(f"runs {shown}{more}")

    net = sorted({c.program for c in caps.commands
                  if c.program in NETWORK_PROGRAMS})
    if net:
        parts.append(f"reaches the internet using {', '.join(net)}")

    if caps.reads:
        scopes = sorted({classify_path(p) for p, _ in caps.reads})
        parts.append(f"reads {_count(caps.reads, 'file')} "
                     f"({', '.join(scopes)})")
    if caps.writes:
        scopes = sorted({classify_path(p) for p, _ in caps.writes})
        parts.append(f"writes {_count(caps.writes, 'file')} "
                     f"({', '.join(scopes)})")
    if caps.deletes:
        parts.append(f"deletes {_count(caps.deletes, 'file')}")
    if caps.env_reads:
        parts.append(f"reads {_count(caps.env_reads, 'environment variable')}")

    if not parts:
        return "This script does nothing observable outside itself."

    codes = sorted({c for c, _ in caps.exit_codes})
    tail = ""
    if codes:
        tail = ". It can exit with status " + ", ".join(codes)

    body = parts[0] if len(parts) == 1 else \
        ", ".join(parts[:-1]) + ", and " + parts[-1]
    return f"This script {body}{tail}."


def verdict(findings, policy_findings=()):
    blocked = [f for f in policy_findings if f[0] == "forbid"]
    if blocked:
        return "blocked"
    if any(f.severity == "danger" for f in findings):
        return "dangerous"
    if any(f.severity == "caution" for f in findings):
        return "caution"
    return "clean"
