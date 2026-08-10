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
from typing import List, NamedTuple, Optional

from . import ast as A
from .parser import TIME_UNITS


@dataclass
class Command:
    program: Optional[str]        # None when built at runtime
    args: List[Optional[str]]
    line: int
    checked: bool
    timeout: bool
    in_pipe: bool = False
    result_examined: bool = False
    stdin: bool = False           # text is fed to it with `reading`
    folder: Optional[str] = None  # `in folder`; None means the script's own
    # The deadline in seconds when it is a literal, so a policy can bound it.
    # None means either no timeout at all, or one computed at runtime.
    timeout_seconds: Optional[float] = None


@dataclass
class Capabilities:
    commands: List[Command] = field(default_factory=list)
    reads: List[tuple] = field(default_factory=list)     # (path, line)
    writes: List[tuple] = field(default_factory=list)
    deletes: List[tuple] = field(default_factory=list)
    env_reads: List[tuple] = field(default_factory=list)
    env_writes: List[tuple] = field(default_factory=list)   # (name, line)
    folder_changes: List[tuple] = field(default_factory=list)  # (path, line)
    # (name, source, line) — source is keystore | environment | file
    secret_reads: List[tuple] = field(default_factory=list)
    # (where, program-or-None, line) — where the plaintext leaves the process
    secret_releases: List[tuple] = field(default_factory=list)
    cleanups: List[int] = field(default_factory=list)       # ensure block lines
    exit_codes: List[tuple] = field(default_factory=list)
    handlers: List[str] = field(default_factory=list)
    dynamic: int = 0              # count of runtime-built names
    # (line, [literal fragments]) for every path expression, so a sensitive
    # tail hidden behind a variable prefix is still visible.
    read_fragments: List[tuple] = field(default_factory=list)
    write_fragments: List[tuple] = field(default_factory=list)


def literal(node, known=None):
    """The literal text of a node, or None if it is computed at runtime.

    `known` maps names proved to hold a single literal, so a program or path
    assembled from constants resolves instead of being reported as unknowable.
    """
    if isinstance(node, A.Lit):
        return str(node.value)
    if known and isinstance(node, (A.Var, A.GlobalRef)):
        return known.get(node.name)
    if isinstance(node, A.BinOp) and node.op in ("&", "&&"):
        left = literal(node.left, known)
        right = literal(node.right, known)
        if left is not None and right is not None:
            return left + ("" if node.op == "&" else " ") + right
    return None


def literal_number(node):
    """The numeric value of an expression made only of literals, else None.

    A timeout reaches the tree already scaled — `within 2 minutes` parses as
    2 * 60 — so a policy bound expressed in any unit can be compared against a
    script written in any other, without running anything.
    """
    if isinstance(node, A.Lit):
        if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)):
            return None
        return float(node.value)
    if isinstance(node, A.UnaryOp) and node.op == "-":
        inner = literal_number(node.operand)
        return None if inner is None else -inner
    if isinstance(node, A.BinOp):
        left = literal_number(node.left)
        right = literal_number(node.right)
        if left is None or right is None:
            return None
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right if right else None
        if node.op == "^":
            try:
                return float(left ** right)
            except (OverflowError, ValueError):
                return None
    return None


def constants(stmts):
    """Names whose value is the same literal everywhere, or nothing.

    `put "ls" into tool` then `run tool` is knowable, and reporting it as
    "built at runtime" understates the manifest as badly as guessing would
    overstate it. But the analysis only ever claims a value it is certain of,
    so the rule is deliberately blunt: a name is a constant when every
    definition of it anywhere in the file is the *same* literal, and it is
    never mutated afterwards.

    Anything else — two different literals, an append, an arithmetic
    statement, a loop variable, a handler parameter, a value that came from a
    command — makes the name unknown. Unknown is the safe answer, and the
    manifest already knows how to say it.
    """
    values = {}          # name -> literal text
    poisoned = set()     # names that cannot be trusted

    def poison(name):
        poisoned.add(name)
        values.pop(name, None)

    def note(name, expr):
        if name in poisoned:
            return
        text = literal(expr)
        if text is None or name in values and values[name] != text:
            poison(name)
            return
        values[name] = text

    def walk(node, in_loop=False):
        if isinstance(node, list):
            for item in node:
                walk(item, in_loop)
            return
        if isinstance(node, A.Put) and isinstance(
                node.target, (A.VarTarget, A.GlobalTarget)):
            if node.mode != "into" or in_loop:
                # An append changes the value; an assignment inside a loop
                # may run many times with different results.
                poison(node.target.name)
            else:
                note(node.target.name, node.expr)
        elif isinstance(node, A.Arith):
            poison(node.target.name)
        elif isinstance(node, A.Replace):
            poison(node.target.name)
        elif isinstance(node, A.RepeatWith):
            poison(node.var)
        elif isinstance(node, A.RepeatForEach):
            poison(node.var)
        elif isinstance(node, A.HandlerDef):
            for parameter in node.params:
                poison(parameter)

        nested = isinstance(node, (A.RepeatTimes, A.RepeatWith,
                                   A.RepeatForEach, A.RepeatWhile,
                                   A.RepeatForever))
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value, in_loop or nested)

    walk(stmts)
    return {name: text for name, text in values.items()
            if name not in poisoned}


SECRET_NODES = (A.SecretRef, A.SecretEnvRef, A.SecretFileRef)


def secret_sources(node):
    """Every secret-producing node anywhere in an expression."""
    found = []

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if isinstance(n, SECRET_NODES):
            found.append(n)
        if hasattr(n, "__dataclass_fields__"):
            for value in vars(n).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value)

    walk(node)
    return found


def tainted_names(stmts):
    """Variables that hold a secret, following assignment.

    `put the secret "x" into password` then `run "psql" with password` has to
    be reported as a release, or the manifest would only ever catch the case
    where somebody inlined the secret at the call site — which is the case
    nobody writes. Repeated to a fixed point so taint flows through a chain
    of assignments.

    This is a static approximation and says so: it follows names, not values,
    and does not attempt to be a proof.
    """
    tainted = set()

    def names_in(node):
        found = set()

        def walk(n):
            if isinstance(n, list):
                for item in n:
                    walk(item)
                return
            if isinstance(n, A.Var):
                found.add(n.name)
            elif isinstance(n, A.GlobalRef):
                found.add(n.name)
            if hasattr(n, "__dataclass_fields__"):
                for value in vars(n).values():
                    if isinstance(value, list) or hasattr(
                            value, "__dataclass_fields__"):
                        walk(value)

        walk(node)
        return found

    def assignments(node, out):
        if isinstance(node, list):
            for item in node:
                assignments(item, out)
            return
        if isinstance(node, A.Put) and isinstance(
                node.target, (A.VarTarget, A.GlobalTarget)):
            out.append((node.target.name, node.expr))
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    assignments(value, out)

    pairs = []
    assignments(stmts, pairs)

    for _ in range(len(pairs) + 1):          # to a fixed point
        grew = False
        for name, expr in pairs:
            if name in tainted:
                continue
            if secret_sources(expr) or (names_in(expr) & tainted):
                tainted.add(name)
                grew = True
        if not grew:
            break
    return tainted


def carries_secret(node, tainted):
    """Does this expression carry a secret, directly or through a name?"""
    if secret_sources(node):
        return True

    hit = [False]

    def walk(n):
        if hit[0]:
            return
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if isinstance(n, (A.Var, A.GlobalRef)) and n.name in tainted:
            hit[0] = True
            return
        if hasattr(n, "__dataclass_fields__"):
            for value in vars(n).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value)

    walk(node)
    return hit[0]


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
    def __init__(self, handlers=None, tainted=None, known=None):
        self.caps = Capabilities()
        self.seen = set()   # Run nodes already recorded, so pipe stages are
                            # not counted twice by the generic walk
        self.handlers = handlers or {}
        self.tainted = tainted or set()
        self.known = known or {}

    def literal(self, node):
        """The literal text of a node, resolving names that are constants."""
        return literal(node, self.known)

    def scan(self, stmts):
        self.visit_block(stmts)
        return self.caps

    def mentions_result(self, node, _visiting=None):
        """Does this subtree read `the result`?

        Follows a handler call into the handler, because factoring the check
        into a helper — `check outcome with "build"` — is good practice, and
        flagging it as an ignored failure would punish exactly that.
        """
        if node is None:
            return False
        if isinstance(node, A.ResultRef):
            return True
        if isinstance(node, list):
            return any(self.mentions_result(n, _visiting) for n in node)
        if not hasattr(node, "__dataclass_fields__"):
            return False

        if isinstance(node, (A.Call, A.FuncCall)):
            visiting = _visiting or set()
            handler = self.handlers.get(node.name)
            if handler is not None and node.name not in visiting:
                if self.mentions_result(handler.block, visiting | {node.name}):
                    return True

        return any(self.mentions_result(v, _visiting)
                   for v in vars(node).values())

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
        self.note_releases(node)

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

    def record_run(self, node, in_pipe=False, stdin=None, folder=None):
        if id(node) in self.seen:
            return
        self.seen.add(id(node))
        program = self.literal(node.program)
        if program is None:
            self.caps.dynamic += 1
        stdin = node.stdin if node.stdin is not None else stdin
        folder = node.folder if node.folder is not None else folder
        self.caps.commands.append(Command(
            program=program,
            args=[self.literal(a) for a in node.args],
            line=node.line,
            checked=node.checked,
            timeout=node.timeout is not None,
            in_pipe=in_pipe,
            stdin=stdin is not None,
            folder=self.literal(folder) if folder is not None else None,
            timeout_seconds=(literal_number(node.timeout)
                             if node.timeout is not None else None),
        ))

    def on_Run(self, node):
        self.record_run(node)

    def on_Pipe(self, node):
        # A pipe's own clauses belong to its stages: the input reaches the
        # first, the folder applies to all of them. Passed down rather than
        # written onto the stages, because this pass must not touch the tree.
        for idx, stage in enumerate(node.stages):
            self.record_run(stage, in_pipe=True,
                            stdin=node.stdin if idx == 0 else None,
                            folder=node.folder)

    def on_FileRef(self, node):
        path = self.literal(node.path)
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
        path = self.literal(node.path)
        self.caps.reads.append((path, node.line))

    def on_Put(self, node):
        if isinstance(node.target, A.FileTarget):
            path = self.literal(node.target.path)
            self.caps.writes.append((path, node.line))
            self.caps.write_fragments.append(
                (node.line, literal_fragments(node.target.path)))
            if path is None:
                self.caps.dynamic += 1
        elif isinstance(node.target, A.EnvTarget):
            name = self.literal(node.target.name)
            self.caps.env_writes.append((name, node.line))
            if name is None:
                self.caps.dynamic += 1
        elif isinstance(node.target, A.FolderTarget):
            path = self.literal(node.expr)
            self.caps.folder_changes.append((path, node.line))
            if path is None:
                self.caps.dynamic += 1

    def on_Ensure(self, node):
        self.caps.cleanups.append(node.line)

    def on_SecretRef(self, node):
        self.caps.secret_reads.append((self.literal(node.name), "keystore",
                                       node.line))

    def on_SecretEnvRef(self, node):
        self.caps.secret_reads.append((self.literal(node.name), "environment",
                                       node.line))

    def on_SecretFileRef(self, node):
        self.caps.secret_reads.append((self.literal(node.path), "file", node.line))

    def note_releases(self, node):
        """Where a secret's plaintext leaves the process.

        These are the points a reviewer needs, because once the plaintext is
        handed to another program frost cannot follow it — the output of that
        program is ordinary text again, and the manifest should say so rather
        than imply a seal that does not hold.
        """
        if isinstance(node, A.Run):
            program = self.literal(node.program)
            if any(carries_secret(a, self.tainted) for a in node.args):
                self.caps.secret_releases.append(("argument", program,
                                                  node.line))
            if node.stdin is not None and carries_secret(node.stdin,
                                                         self.tainted):
                self.caps.secret_releases.append(("input", program, node.line))
        elif isinstance(node, A.Put) and carries_secret(node.expr,
                                                        self.tainted):
            if isinstance(node.target, A.FileTarget):
                self.caps.secret_releases.append(
                    ("file", self.literal(node.target.path), node.line))
            elif isinstance(node.target, A.EnvTarget):
                self.caps.secret_releases.append(
                    ("environment", self.literal(node.target.name), node.line))

    def on_DeleteFile(self, node):
        path = self.literal(node.path)
        self.caps.deletes.append((path, node.line))
        if path is None:
            self.caps.dynamic += 1

    def on_EnvRef(self, node):
        self.caps.env_reads.append((self.literal(node.name), node.line))

    def on_Quit(self, node):
        code = self.literal(node.status) if node.status else "0"
        self.caps.exit_codes.append((code, node.line))

    def on_HandlerDef(self, node):
        self.caps.handlers.append(node.name)


def collect_handlers(node, into):
    if isinstance(node, list):
        for item in node:
            collect_handlers(item, into)
        return
    if isinstance(node, A.HandlerDef):
        into[node.name] = node
    if hasattr(node, "__dataclass_fields__"):
        for value in vars(node).values():
            if isinstance(value, list) or hasattr(value,
                                                  "__dataclass_fields__"):
                collect_handlers(value, into)


def audit(stmts, handlers=None, tainted=None):
    """Capabilities of one file.

    `handlers` and `tainted` are supplied when this file is part of a larger
    program: name resolution and taint are both per-file, and computing
    either from a concatenated tree would let an unrelated `token` in one
    file join the taint node of a secret in another.
    """
    if handlers is None:
        handlers = {}
        collect_handlers(stmts, handlers)
    if tainted is None:
        tainted = tainted_names(stmts)
    return Auditor(handlers, tainted, constants(stmts)).scan(stmts)


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
        if any(u.stdin for u in uses):
            detail.append("given input")
        folders = sorted({u.folder for u in uses if u.folder})
        if folders:
            detail.append("in " + ", ".join(folders))
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
            [(n or "(name built at runtime)", f"— line {ln}")
             for n, ln in caps.env_reads])
    section("Sets these environment variables:",
            [(n or "(name built at runtime)", f"— line {ln}")
             for n, ln in caps.env_writes])
    section("Changes the working folder to:",
            [where(p, ln) for p, ln in caps.folder_changes])
    section("Reads these secrets:",
            [(n or "(name built at runtime)", f"— line {ln}  (from the {src})")
             for n, src, ln in caps.secret_reads])
    section("Lets a secret leave the process:",
            [({"argument": f"as an argument to {what or 'a program'}",
               "input": f"on the standard input of {what or 'a program'}",
               "file": f"written to {what or 'a file'}",
               "environment": f"in the environment variable {what}"}[where],
              f"— line {ln}")
             for where, what, ln in caps.secret_releases])
    section("Cleans up on exit:",
            [(f"ensure block", f"— line {ln}") for ln in caps.cleanups])
    section("Can exit with:",
            [(f"status {c}", "") for c in sorted({c for c, _ in
                                                  caps.exit_codes})])

    if caps.dynamic:
        out.append(f"Note: {caps.dynamic} name(s) are built at runtime and "
                   f"cannot be checked ahead of time.")
        out.append("")

    return "\n".join(out).rstrip() or "This script does nothing observable."


# ----------------------------------------------------------------- policy
#
# Two kinds of rule. The original ones ask whether something appears at all;
# the counting ones ask how much of it there is, which is what a business rule
# usually needs — "no more than three files written", "at least one cleanup
# block", "curl gets a deadline, and no more than thirty seconds of one".

# Every countable noun. The first phrase of each row is the one suggested
# back to a policy author who mistypes, so it has to be a phrase that parses —
# not the internal key.
COUNT_NOUNS = {}
COUNT_VOCABULARY = []


def _noun(key, *phrases):
    COUNT_VOCABULARY.append(phrases[0])
    for phrase in phrases:
        COUNT_NOUNS[phrase] = key


_noun("commands", "commands", "command")
_noun("network commands", "network commands", "network command")
_noun("reads", "files read", "file read", "file reads")
_noun("writes", "files written", "file written", "file writes")
_noun("deletes", "files deleted", "file deleted", "file deletes")
_noun("env_reads", "environment reads", "environment read")
_noun("env_writes", "environment writes", "environment write")
_noun("folder_changes", "folder changes", "folder change")
_noun("cleanups", "cleanups", "cleanup", "ensure block", "ensure blocks")
_noun("unchecked", "unchecked commands", "unchecked command")
_noun("untimed", "commands without a timeout", "command without a timeout")
_noun("dynamic", "runtime names", "runtime name")
_noun("handlers", "handlers", "handler")
_noun("pipes", "pipes", "pipe")
_noun("secret_reads", "secrets read", "secret read")
_noun("secret_releases", "secret releases", "secret release")

# `runs of "curl"` is a countable noun with a subject attached.
RUNS_OF = re.compile(r'^runs?\s+of\s+"([^"]+)"$')

NUM = r"(\d+(?:\.\d+)?)"

RULE_PATTERNS = [
    (re.compile(r'^(forbid|warn)\s+running\s+"([^"]+)"'
                r'(?:\s+with\s+"([^"]+)")?\s*$'), "run"),
    (re.compile(r'^(forbid|warn)\s+writing\s+to\s+"([^"]+)"\s*$'), "write"),
    (re.compile(r'^(forbid|warn)\s+reading\s+"([^"]+)"\s*$'), "read"),
    (re.compile(r'^(forbid|warn)\s+deleting\s+"([^"]+)"\s*$'), "delete"),
    (re.compile(r'^(forbid|warn)\s+setting\s+"([^"]+)"\s*$'), "setenv"),
    (re.compile(r'^(forbid|warn)\s+reading\s+secret\s+"([^"]+)"\s*$'),
     "readsecret"),
    (re.compile(r'^(forbid|warn)\s+changing\s+folder\s*$'), "chfolder"),

    # The sandbox boundary. Allow-shaped, because a deny-list cannot become
    # one: `forbid writing to "/etc/*"` says nothing about what writing is
    # permitted, and confinement needs the positive form.
    (re.compile(r'^sandbox\s+may\s+reach\s+the\s+network\s*$'),
     "sandbox_network"),
    (re.compile(r'^sandbox\s+may\s+(run|read|write|delete)\s+(.+?)\s*$'),
     "sandbox"),
    (re.compile(r'^sandbox\s+may\s+reach\s+(.+?)\s*$'), "sandbox_host"),

    # Bounded timeouts. These must precede the bare `require timeout on`
    # pattern only for clarity; that one anchors at end of line and so cannot
    # match these anyway.
    (re.compile(r'^require\s+timeout\s+on\s+"([^"]+)"\s+between\s+' + NUM +
                r'\s+and\s+' + NUM + r'\s+(\w+)\s*$'), "timeout_range"),
    (re.compile(r'^require\s+timeout\s+on\s+"([^"]+)"\s+of\s+at\s+most\s+' +
                NUM + r'\s+(\w+)\s*$'), "timeout_max"),
    (re.compile(r'^require\s+timeout\s+on\s+"([^"]+)"\s+of\s+at\s+least\s+' +
                NUM + r'\s+(\w+)\s*$'), "timeout_min"),
    (re.compile(r'^require\s+timeout\s+on\s+"([^"]+)"\s*$'), "timeout"),
    (re.compile(r'^require\s+every\s+command\s+to\s+be\s+checked\s*$'),
     "checked"),

    # Counts. `forbid more than N X` and `require at most N X` are the same
    # rule said two ways; both read naturally depending on the noun.
    (re.compile(r'^(forbid|warn)\s+more\s+than\s+' + NUM + r'\s+(.+?)\s*$'),
     "count_max"),
    (re.compile(r'^(forbid|warn)\s+fewer\s+than\s+' + NUM + r'\s+(.+?)\s*$'),
     "count_min"),
    (re.compile(r'^(require|warn)\s+at\s+most\s+' + NUM + r'\s+(.+?)\s*$'),
     "count_max"),
    (re.compile(r'^(require|warn)\s+at\s+least\s+' + NUM + r'\s+(.+?)\s*$'),
     "count_min"),
    (re.compile(r'^(require|warn)\s+between\s+' + NUM + r'\s+and\s+' + NUM +
                r'\s+(.+?)\s*$'), "count_range"),
    (re.compile(r'^(forbid|warn)\s+any\s+(.+?)\s*$'), "count_none"),
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
    # Counting and bounding rules: the inclusive range that is allowed.
    low: Optional[float] = None
    high: Optional[float] = None
    noun: Optional[str] = None       # as the policy author wrote it
    # The trailing comment on the rule's own line. A policy that refuses a
    # script should be able to say why and what to do instead, and the
    # comment authors already write is exactly that text.
    hint: str = ""


class PolicyFinding(NamedTuple):
    severity: str                    # forbid | warn
    what: str
    line: int
    hint: str = ""


def _resolve_noun(phrase, policy_line):
    """Map a countable phrase to (key, subject). Raises on an unknown noun."""
    phrase = " ".join(phrase.split()).lower()
    runs = RUNS_OF.match(phrase)
    if runs:
        return "runs", runs.group(1)
    if phrase in COUNT_NOUNS:
        return COUNT_NOUNS[phrase], None
    known = ", ".join(COUNT_VOCABULARY)
    raise PolicyError(
        f"policy line {policy_line}: {phrase!r} is not something that can be "
        f'counted. Countable: {known}, and runs of "program"')


def _seconds(amount, unit, policy_line):
    if unit not in TIME_UNITS:
        raise PolicyError(
            f"policy line {policy_line}: {unit!r} is not a time unit. Try "
            f"milliseconds, seconds, minutes or hours")
    return float(amount) * TIME_UNITS[unit]


def boundary_from(rules):
    """The sandbox boundary a policy declares, if it declares one."""
    from .sandbox import Boundary
    boundary = Boundary()
    for rule in rules:
        if rule.kind == "sandbox_network":
            boundary.network = True
            boundary.declared = True
        elif rule.kind == "sandbox":
            boundary.declared = True
            getattr(boundary, {"run": "programs", "read": "reads",
                               "write": "writes",
                               "delete": "deletes"}[rule.subject]).extend(
                rule.detail)
    return boundary


def parse_policy(text):
    rules = []
    for n, raw in enumerate(text.splitlines(), start=1):
        code, _, comment = _split_comment(raw)
        line = code.strip()
        if not line:
            continue
        for rx, kind in RULE_PATTERNS:
            m = rx.match(line)
            if not m:
                continue
            g = m.groups()
            if kind == "checked":
                rules.append(Rule(kind, "forbid", "*", None, n))
            elif kind == "timeout":
                rules.append(Rule(kind, "forbid", g[0], None, n))
            elif kind == "run":
                rules.append(Rule(kind, g[0], g[1], g[2], n))
            elif kind == "chfolder":
                rules.append(Rule(kind, g[0], "*", None, n))
            elif kind == "sandbox_network":
                rules.append(Rule(kind, "allow", "network", None, n))
            elif kind == "sandbox_host":
                raise PolicyError(
                    f"policy line {n}: a sandbox cannot allow one host.\n"
                    f"  macOS filters on addresses, not names, and a Linux "
                    f"namespace is all-or-nothing, so a host allow-list would "
                    f"be accepted here and not enforced.\n"
                    f'  Write `sandbox may reach the network` and mean it, or '
                    f"leave it out and have no network at all.")
            elif kind == "sandbox":
                subjects = re.findall(r'"([^"]*)"', g[1])
                if not subjects:
                    raise PolicyError(
                        f"policy line {n}: name what the sandbox may "
                        f'{g[0]}, in quotes')
                rules.append(Rule(kind, "allow", g[0], None, n,
                                  noun=" ".join(subjects)))
                rules[-1].detail = subjects
            elif kind == "timeout_max":
                rules.append(Rule("timeout_bound", "forbid", g[0], None, n,
                                  high=_seconds(g[1], g[2], n)))
            elif kind == "timeout_min":
                rules.append(Rule("timeout_bound", "forbid", g[0], None, n,
                                  low=_seconds(g[1], g[2], n)))
            elif kind == "timeout_range":
                low = _seconds(g[1], g[3], n)
                high = _seconds(g[2], g[3], n)
                if low > high:
                    raise PolicyError(
                        f"policy line {n}: {g[1]} is greater than {g[2]}")
                rules.append(Rule("timeout_bound", "forbid", g[0], None, n,
                                  low=low, high=high))
            elif kind in ("count_max", "count_min", "count_none",
                          "count_range"):
                severity = "warn" if g[0] == "warn" else "forbid"
                phrase = g[-1]
                key, subject = _resolve_noun(phrase, n)
                low = high = None
                if kind == "count_max":
                    high = float(g[1])
                elif kind == "count_min":
                    low = float(g[1])
                elif kind == "count_none":
                    high = 0.0
                else:
                    low, high = float(g[1]), float(g[2])
                    if low > high:
                        raise PolicyError(
                            f"policy line {n}: {g[1]} is greater than {g[2]}")
                rules.append(Rule("count", severity, key, subject, n,
                                  low=low, high=high, noun=phrase.strip()))
            else:
                rules.append(Rule(kind, g[0], g[1], None, n))
            if comment:
                rules[-1].hint = comment
            break
        else:
            raise PolicyError(f"policy line {n}: cannot read {line!r}")
    return rules


def _split_comment(raw):
    """Split a policy line into code, marker and trailing comment."""
    for marker in ("--", "#"):
        if marker in raw:
            code, _, comment = raw.partition(marker)
            return code, marker, comment.strip()
    return raw, "", ""


def _plain(n):
    """Whole numbers without a trailing .0, which is how people write limits."""
    return str(int(n)) if float(n).is_integer() else str(n)


def format_duration(seconds):
    """A duration in the largest unit that keeps it a whole number."""
    for scale, unit in ((3600, "hour"), (60, "minute"), (1, "second"),
                        (0.001, "millisecond")):
        if seconds >= scale and (seconds / scale).is_integer():
            n = int(seconds / scale)
            return f"{n} {unit}" + ("s" if n != 1 else "")
    return f"{_plain(seconds)} seconds"


def count_lines(caps, key, subject=None):
    """Every source line contributing to a countable noun.

    Returned as lines rather than a bare number so a violation can point at
    the occurrence that crossed the limit instead of at the whole script.
    Things with no line of their own contribute a 0.
    """
    if key == "commands":
        return [c.line for c in caps.commands]
    if key == "network commands":
        return [c.line for c in caps.commands
                if c.program in NETWORK_PROGRAMS]
    if key == "runs":
        return [c.line for c in caps.commands
                if c.program and fnmatch.fnmatchcase(c.program, subject)]
    if key == "unchecked":
        return [c.line for c in caps.commands
                if not c.checked and not c.result_examined]
    if key == "untimed":
        return [c.line for c in caps.commands if not c.timeout]
    if key == "pipes":
        return sorted({c.line for c in caps.commands if c.in_pipe})
    if key == "dynamic":
        return [0] * caps.dynamic
    if key == "handlers":
        return [0] * len(caps.handlers)
    if key == "cleanups":
        return list(caps.cleanups)
    if key == "secret_reads":
        return [ln for _, _, ln in caps.secret_reads]
    if key == "secret_releases":
        return [ln for _, _, ln in caps.secret_releases]
    pairs = {"reads": caps.reads, "writes": caps.writes,
             "deletes": caps.deletes, "env_reads": caps.env_reads,
             "env_writes": caps.env_writes,
             "folder_changes": caps.folder_changes}[key]
    return [ln for _, ln in pairs]


def _check_rules(caps, rules):
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

        elif rule.kind == "setenv":
            for name, line in caps.env_writes:
                if name is None:
                    findings.append(
                        (rule.severity,
                         "setting an environment variable named at runtime",
                         line))
                elif fnmatch.fnmatchcase(name, rule.subject):
                    findings.append(
                        (rule.severity, f'setting "{name}"', line))

        elif rule.kind == "readsecret":
            for name, source, line in caps.secret_reads:
                if name is None:
                    findings.append(
                        (rule.severity,
                         "reading a secret named at runtime", line))
                elif fnmatch.fnmatchcase(name, rule.subject):
                    findings.append(
                        (rule.severity, f'reading the secret "{name}"', line))

        elif rule.kind == "chfolder":
            for path, line in caps.folder_changes:
                findings.append(
                    (rule.severity,
                     f"changing the working folder to {path or '(runtime)'}",
                     line))

        elif rule.kind == "count":
            lines = sorted(count_lines(caps, rule.subject, rule.detail))
            n = len(lines)
            noun = rule.noun
            if rule.high is not None and n > rule.high:
                limit = _plain(rule.high)
                # Point at the occurrence that crossed the line, not the file.
                at = lines[int(rule.high)] if int(rule.high) < n else lines[-1]
                findings.append((
                    rule.severity,
                    f"{n} {noun}, at most {limit} allowed", at))
            elif rule.low is not None and n < rule.low:
                findings.append((
                    rule.severity,
                    f"{n} {noun}, at least {_plain(rule.low)} required", 0))

        elif rule.kind == "timeout_bound":
            for c in caps.commands:
                if not (c.program
                        and fnmatch.fnmatchcase(c.program, rule.subject)):
                    continue
                if c.timeout_seconds is None:
                    what = ("has no timeout" if not c.timeout
                            else "has a timeout computed at runtime, which "
                                 "cannot be checked here")
                    findings.append(
                        ("forbid", f'"{c.program}" {what}', c.line))
                    continue
                seconds = c.timeout_seconds
                if rule.high is not None and seconds > rule.high:
                    findings.append((
                        rule.severity,
                        f'"{c.program}" waits up to {format_duration(seconds)}'
                        f", the limit is {format_duration(rule.high)}", c.line))
                elif rule.low is not None and seconds < rule.low:
                    findings.append((
                        rule.severity,
                        f'"{c.program}" waits only {format_duration(seconds)}'
                        f", at least {format_duration(rule.low)} is required",
                        c.line))

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


def check(caps, rules):
    """Every rule violation, each carrying the rule's own explanation.

    Wraps the plain checker so the hint is attached once, at the boundary,
    rather than at each of the fourteen places a violation is constructed —
    which would be fourteen chances to forget.
    """
    out = []
    for rule in rules:
        start = len(out)
        out.extend(_check_rules(caps, [rule]))
        for i in range(start, len(out)):
            severity, what, line = out[i][:3]
            out[i] = PolicyFinding(severity, what, line, rule.hint)
    out.sort(key=lambda f: f.line)
    return out


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

# Variables that decide which binary or library a later command actually gets.
# Setting one of these turns every subsequent `run` into something the reader
# cannot resolve by reading the program name.
LOADER_ENV = {"PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
              "DYLD_LIBRARY_PATH", "PYTHONPATH", "NODE_OPTIONS", "PERL5LIB",
              "RUBYOPT", "GEM_PATH", "CLASSPATH", "IFS", "BASH_ENV", "ENV"}


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

    for name, line in caps.env_writes:
        if name in LOADER_ENV:
            out.append(Finding(
                "danger", f"Changes how programs are found or loaded ({name})",
                "Every command run after this resolves through the new value, "
                "so a program named later in the script need not be the one "
                "the reader expects.", line))
        elif name is None:
            out.append(Finding(
                "caution", "Sets an environment variable named at runtime",
                "Which variable is set cannot be determined before the "
                "script runs.", line))
        elif name in SECRET_ENV:
            out.append(Finding(
                "caution", f"Sets a credential in the environment ({name})",
                "Every child process started afterwards inherits it.", line))

    for where, what, line in caps.secret_releases:
        if where == "argument":
            out.append(Finding(
                "caution", f"A secret is passed to {what or 'a program'} as "
                           f"an argument",
                "Command-line arguments are visible to every other process on "
                "the machine while it runs. Prefer 'reading <secret>', which "
                "puts it on the program's standard input.", line))
        elif where == "file":
            out.append(Finding(
                "danger", f"A secret is written to {what or 'a file'}",
                "The value leaves the process in the clear and stays on disk "
                "after the script ends.", line))
        elif where == "environment":
            out.append(Finding(
                "caution", f"A secret is put into the environment ({what})",
                "Every child process started afterwards inherits it, "
                "including ones started for unrelated reasons.", line))
        if what in NETWORK_PROGRAMS:
            out.append(Finding(
                "danger", f"A secret is handed to {what}",
                "This program reaches the network, so the credential can "
                "leave the machine.", line))

    for path, line in caps.folder_changes:
        if path is None:
            out.append(Finding(
                "caution", "Changes the working folder to a runtime path",
                "Every relative path after this points somewhere that cannot "
                "be determined ahead of time.", line))

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
    if caps.env_writes:
        names = sorted({n for n, _ in caps.env_writes if n})
        shown = f" ({', '.join(names)})" if names else ""
        parts.append(
            f"sets {_count(caps.env_writes, 'environment variable')}{shown}")
    if caps.folder_changes:
        parts.append("changes the working folder")
    if caps.secret_reads:
        named = sorted({n for n, _, _ in caps.secret_reads if n})
        shown = f" ({', '.join(named)})" if named else ""
        parts.append(f"reads {_count(caps.secret_reads, 'secret')}{shown}")
    if caps.secret_releases:
        parts.append(
            f"lets a secret leave the process in "
            f"{_count(caps.secret_releases, 'place')}")

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
