"""Auditing a whole program, and enforcing what each import allows.

The invariant single-file frost had for free: the tree you audit is the
program you run, and the audit sees all of it. With modules that has to be
maintained deliberately, which is what this does.

Two properties matter, and they pull in opposite directions.

**Nothing hides.** Capabilities are gathered over the transitive closure, not
the entry file, and each one is attributed to the file it came from and the
import it arrived through. A reviewer must be able to see that a `psql` in
the manifest belongs to `lib/db.frost` and got there via line 8. The existing
traversal already does the hard part: it recurses into handler bodies, so a
module's handlers are audited whether or not anything calls them, which is
the sound direction.

**Nothing widens quietly.** An import declares a ceiling, and the module is
refused if it exceeds it. That is `--policy` pointed inward, and it buys the
property that makes single-file review survive multi-file code: reading only
the entry script gives a sound upper bound on what the whole program can do.
It also means a shared module that later grows a `curl` call breaks the build
at the import site rather than silently appearing in someone's manifest —
which is the supply-chain shape you become exposed to the moment modules can
be shared at all.

Taint is per file. A `token` in one file and an unrelated `token` in another
are different variables, and treating them as one node produces both false
positives and, through a shadowed handler, false negatives. Secrets cross a
file boundary only where data does: through the arguments of a handler call.
"""
# SPDX-License-Identifier: MIT

import fnmatch
from dataclasses import dataclass, field
from typing import List, Optional

from . import ast as A
from .audit import (Capabilities, Auditor, audit, collect_handlers,
                    secret_sources, tainted_names, Finding, literal)
from . import modules as M


@dataclass
class FileCapabilities:
    """One file's capabilities, and how the program reached it."""
    path: str
    caps: Capabilities
    # (importer, line) chain from the entry script. Empty for the entry.
    via: List[tuple] = field(default_factory=list)

    @property
    def arrival(self):
        if not self.via:
            return ""
        importer, line = self.via[-1]
        return f"via {importer}:{line}"


@dataclass
class ProgramCapabilities:
    """Every file's capabilities, plus one merged view for the old consumers."""
    files: List[FileCapabilities] = field(default_factory=list)
    merged: Capabilities = field(default_factory=Capabilities)

    def by_path(self, path):
        for entry in self.files:
            if entry.path == path:
                return entry
        return None

    def origin_of_command(self, command):
        for entry in self.files:
            if any(c is command for c in entry.caps.commands):
                return entry
        return None


# ------------------------------------------------------------------ taint

def cross_file_taint(program, tables):
    """Per-file tainted names, propagated across handler calls only.

    A secret leaves its file exactly where data does: as an argument to a
    handler defined elsewhere. Everything else stays local, so two variables
    that merely share a name stay separate.

    Deliberately over-approximate at the boundary — if any argument carries a
    secret, every parameter of the callee is treated as carrying one. Erring
    towards reporting is the right direction for a manifest.
    """
    tainted = {path: tainted_names(module.tree)
               for path, module in program.modules.items()}

    def calls_in(node, out):
        if isinstance(node, list):
            for item in node:
                calls_in(item, out)
            return
        if isinstance(node, (A.Call, A.FuncCall)):
            out.append(node)
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    calls_in(value, out)

    home = M.owning_file(program)

    for _ in range(len(program.modules) + 2):        # to a fixed point
        grew = False
        for path, module in program.modules.items():
            calls = []
            calls_in(module.tree, calls)
            for call in calls:
                handler = tables[path].get(call.name)
                if handler is None:
                    continue
                callee = home.get(id(handler))
                if callee is None:
                    continue
                carries = any(
                    secret_sources(arg) or _names(arg) & tainted[path]
                    for arg in call.args)
                if not carries:
                    continue
                for parameter in handler.params:
                    if parameter not in tainted[callee]:
                        tainted[callee].add(parameter)
                        grew = True
        if not grew:
            break
    return tainted


def _names(node):
    found = set()

    def walk(n):
        if isinstance(n, list):
            for item in n:
                walk(item)
            return
        if isinstance(n, (A.Var, A.GlobalRef)):
            found.add(n.name)
        if hasattr(n, "__dataclass_fields__"):
            for value in vars(n).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value)

    walk(node)
    return found


# ------------------------------------------------------------- the audit

def merge(into, other):
    for name in ("commands", "reads", "writes", "deletes", "env_reads",
                 "env_writes", "folder_changes", "secret_reads",
                 "secret_releases", "exit_codes", "handlers",
                 "read_fragments", "write_fragments"):
        getattr(into, name).extend(getattr(other, name))
    into.cleanups.extend(other.cleanups)
    into.dynamic += other.dynamic
    return into


def audit_program(program):
    """Capabilities over the transitive closure, attributed to their file."""
    tables = M.handler_tables(program)
    tainted = cross_file_taint(program, tables)

    result = ProgramCapabilities()
    for path in program.order:                      # dependencies first
        module = program.modules[path]
        caps = audit(module.tree, handlers=tables[path],
                     tainted=tainted[path])
        via = _shortest_import_chain(program, path)
        result.files.append(FileCapabilities(path, caps, via))
        merge(result.merged, caps)
    return result


def _shortest_import_chain(program, path):
    """How the entry script reaches `path`, as (importer, line) steps."""
    if path == program.entry:
        return []
    queue = [(program.entry, [])]
    seen = {program.entry}
    while queue:
        current, chain = queue.pop(0)
        for imported, line, _, _ in program.modules[current].imports:
            step = chain + [(current, line)]
            if imported == path:
                return step
            if imported not in seen:
                seen.add(imported)
                queue.append((imported, step))
    return []


# ---------------------------------------------------------- the ceiling

def _allowed(subject, patterns):
    if subject is None:
        return False                      # unknowable is never within a limit
    return any(fnmatch.fnmatchcase(subject, p) for p in patterns)


def check_ceiling(path, caps, ceiling, line, importer):
    """Everything a module does that its import did not allow.

    Default-deny: with no `which may`, a module may compute and nothing else.
    A capability built at runtime always exceeds a ceiling, because a limit
    that cannot be checked is not a limit.
    """
    ceiling = ceiling or M.Ceiling()
    out = []

    def refuse(what, detail, at):
        out.append(Finding(
            "danger",
            f"{path} may not {what}",
            f"{detail} The import at {importer}:{line} allows: "
            f"{ceiling.describe()}.",
            at))

    for command in caps.commands:
        if not _allowed(command.program, ceiling.programs):
            named = command.program or "a program chosen at runtime"
            refuse(f"run {named}",
                   "The module runs it, but the import does not allow it.",
                   command.line)

    for kind, entries, patterns, verb in (
            ("read", caps.reads, ceiling.reads, "read"),
            ("write", caps.writes, ceiling.writes, "write to"),
            ("delete", caps.deletes, ceiling.deletes, "delete")):
        for target, at in entries:
            if not _allowed(target, patterns):
                named = target or "a path built at runtime"
                refuse(f"{verb} {named}",
                       "The module touches it, but the import does not "
                       "allow it.", at)

    for name, at in caps.env_writes:
        if not _allowed(name, ceiling.env):
            refuse(f"set {name or 'a variable named at runtime'}",
                   "The module sets it, and every child process started "
                   "afterwards inherits it.", at)

    for name, source, at in caps.secret_reads:
        if not _allowed(name, ceiling.secrets):
            refuse(f"read the secret {name or '(named at runtime)'}",
                   "The module reads a credential the import did not grant.",
                   at)

    if caps.folder_changes and not ceiling.change_folder:
        refuse("change the working folder",
               "Every relative path afterwards means something else.",
               caps.folder_changes[0][1])

    return out


def check_all_ceilings(program, program_caps):
    """Every ceiling violation in the program, in file order."""
    out = []
    for module in program.modules.values():
        for imported, line, _, ceiling in module.imports:
            entry = program_caps.by_path(imported)
            if entry is None:                       # pragma: no cover
                continue
            out.extend(check_ceiling(imported, entry.caps, ceiling, line,
                                     module.path))
    return sorted(out, key=lambda f: (f.line, f.title))


# ------------------------------------------------------------- provenance

def describe_program(program, program_caps):
    """The manifest, with each capability attributed to where it came from.

    A capability that arrived through an import has to say so. Otherwise a
    reviewer reading the manifest cannot tell which file to go and read.
    """
    from .audit import describe

    if len(program_caps.files) == 1:
        return describe(program_caps.merged)

    sections = []
    for entry in program_caps.files:
        text = describe(entry.caps)
        if text == "This script does nothing observable.":
            continue
        header = entry.path
        if entry.via:
            importer, line = entry.via[-1]
            header += f"   (imported by {importer}:{line})"
        sections.append(header + "\n" + "\n".join(
            "  " + line if line else "" for line in text.split("\n")))

    if not sections:
        return "This program does nothing observable."
    return "\n\n".join(sections)
