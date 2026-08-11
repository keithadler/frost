"""Modules that cannot put capability outside the manifest.

Everything frost is worth using for rests on one invariant: **the tree you
audit is the program you run, and the audit sees all of it.** A module system
is the feature most likely to break that. If a module can contribute a `run`
that `--explain` does not print, frost becomes worse than bash, bash never
claimed to have audited anything.

So the goal here is not "safe modules". It is *modules that cannot put
capability outside the manifest*, and every rule below is chosen for that.

**A module is declarations only.** Handler definitions and nothing else; a
top-level statement in a module is a parse error. Import-time side effects are
the most abused feature of every module system ever shipped, Python's
`__init__.py`, npm's `postinstall`: and they turn `use` into "run this file".
Refusing them means `use` can never do anything.

**The path is a literal.** `use (module name)` fails when the file is parsed,
not when it runs. A computed import is `eval` wearing a hat: it would put the
import graph out of reach of the static analysis that every other guarantee
depends on.

**Resolution is relative to the importing file.** No search path, no
environment variable, no absolute paths, nothing above the entry script's
directory, no network, no registry. One path string resolves to one file, the
same way on every machine. Vendoring is the feature: if a module has to live
in the repo, then the review that covered the repo covered the module. An
ambient search path is exactly how "the script I reviewed loaded a different
file in production" happens.

**The closure is read once.** Resolve, read, hash, parse, audit, run, all
from the same bytes. Re-opening a module at execution time would open a
window between the audit and the run, which is the one thing single-file
frost never had.

**A ceiling constrains the whole subtree, not one file.** `which may run
"psql"` bounds everything that arrives because of that import, including what
the imported module imports in turn. Checking only the named file left the
guarantee holed exactly one level down, a module allowed to run `psql` could
import a second one that ran `curl`, and nothing objected, so the upper bound
a reviewer took from the entry file was not an upper bound at all. A breach
names the file that actually holds the capability and the import that forbade
it, since blaming the module the import names would send a reader to a file
that does not contain the offending line.

**Imports are explicit and the graph is a DAG.** `for the connect, the
migrate` names what arrives, so a collision between two modules is an error
here rather than one silently shadowing the other. Cycles are refused rather
than resolved.

What is deliberately absent: conditional imports, re-export, module-level
state, versions, namespacing beyond the file path. Each of those exists to
make a large dependency tree tractable, and a large dependency tree is the
thing this review model cannot survive. This is for sharing five handlers
across four scripts in one repository.
"""
# SPDX-License-Identifier: MIT

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ast as A
from .lexer import LexError
from .parser import parse, ParseError

LOCK_SUFFIX = ".lock"


class ModuleError(Exception):
    """A module could not be resolved, read, or accepted.

    Carries a line in the importing file where there is one, so the failure
    reports like every other refusal rather than as a bare exception.
    """

    def __init__(self, msg, path=None, line=None, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.path = path
        self.line = line
        self.hint = hint


@dataclass
class Ceiling:
    """What an import allows a module to do. Empty means: compute, nothing more.

    Default-deny is the whole point. A reviewer who reads only the entry file
    gets a sound upper bound on what the entire program can do, and a module
    that later grows a network call breaks the build at the import site
    instead of quietly widening the manifest.
    """
    programs: List[str] = field(default_factory=list)
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    deletes: List[str] = field(default_factory=list)
    env: List[str] = field(default_factory=list)
    secrets: List[str] = field(default_factory=list)
    change_folder: bool = False

    def is_empty(self):
        return not (self.programs or self.reads or self.writes or self.deletes
                    or self.env or self.secrets or self.change_folder)

    def describe(self):
        parts = []
        for label, values in (("run", self.programs), ("read", self.reads),
                              ("write", self.writes), ("delete", self.deletes),
                              ("set", self.env), ("read secret", self.secrets)):
            if values:
                parts.append(f"{label} " +
                             ", ".join(f'"{v}"' for v in values))
        if self.change_folder:
            parts.append("change folder")
        return " and ".join(parts) if parts else "nothing but compute"


@dataclass
class Module:
    """One file in the program, and the bytes it was read from."""
    path: str                       # relative to the entry directory
    absolute: str
    source: str
    digest: str
    tree: list
    is_entry: bool = False
    # (imported path, line, names, ceiling) for each `use` in this file.
    imports: List[tuple] = field(default_factory=list)


@dataclass
class Program:
    """An entry script and the transitive closure of what it imports."""
    entry: str
    root: str                       # the entry's directory; nothing above it
    modules: Dict[str, Module] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)   # dependencies first

    @property
    def entry_module(self):
        return self.modules[self.entry]

    @property
    def tree(self):
        return self.entry_module.tree

    def digests(self):
        return {path: self.modules[path].digest for path in sorted(self.modules)}

    def imported_by(self, path):
        """Every (importer, line) that pulls in `path`."""
        return [(m.path, line) for m in self.modules.values()
                for imported, line, _, _ in m.imports if imported == path]


# ------------------------------------------------------------- resolution

def resolve(importing_path, spec, root, line):
    """Turn one import string into one file, or refuse.

    Relative to the importing file, never above `root`, never absolute.
    """
    if not spec:
        raise ModuleError("an import path cannot be empty", line=line)
    if os.path.isabs(spec) or (len(spec) > 1 and spec[1] == ":"):
        raise ModuleError(
            f"{spec!r} is an absolute path", line=line,
            hint="modules are found relative to the file that imports them, "
                 "so that the same script resolves the same way everywhere")
    if "\\" in spec:
        raise ModuleError(
            f"{spec!r} uses backslashes", line=line,
            hint="write module paths with forward slashes on every platform")

    base = os.path.dirname(os.path.join(root, importing_path))
    absolute = os.path.normpath(os.path.join(base, spec))
    root_absolute = os.path.normpath(root)

    if not (absolute == root_absolute or
            absolute.startswith(root_absolute + os.sep)):
        raise ModuleError(
            f"{spec!r} is outside the script's own directory", line=line,
            hint="a module must live with the script that uses it, so that "
                 "reviewing the repository reviews the whole program")

    relative = os.path.relpath(absolute, root_absolute)
    return relative.replace(os.sep, "/"), absolute


# ------------------------------------------------------------- validation

def check_declarations_only(tree, path):
    """A module may define handlers, and import, and nothing else.

    `use` counts as a declaration because it cannot do anything: by this same
    rule the file it names has no executable top level either. That is what
    makes the closure safe to load, reading the graph never runs any of it.
    """
    for statement in tree:
        if not isinstance(statement, (A.HandlerDef, A.Use)):
            raise ModuleError(
                f"a module may only define handlers, and {path} has a "
                f"statement that would run when it is imported",
                path=path, line=getattr(statement, "line", None),
                hint="move it into a handler. A module that can do something "
                     "when it is imported turns 'use' into 'run this file', "
                     "which is the thing this design refuses")


def imports_of(tree):
    """The `use` statements of a file, in order.

    Only at the top level: an import inside a handler or a loop would be a
    conditional import, and the graph has to be knowable without running.
    """
    found = []
    for statement in tree:
        if isinstance(statement, A.Use):
            found.append(statement)

    def refuse_nested(node, depth=0):
        if isinstance(node, list):
            for item in node:
                refuse_nested(item, depth)
            return
        if isinstance(node, A.Use) and depth > 0:
            raise ModuleError(
                "an import cannot be inside a block", line=node.line,
                hint="every import sits at the top level, so the whole graph "
                     "is known before anything runs")
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    refuse_nested(value, depth + 1)

    refuse_nested(tree)
    return found


# ----------------------------------------------------------------- loading

def read_source(absolute, spec, line):
    try:
        with open(absolute) as fh:
            return fh.read()
    except FileNotFoundError:
        raise ModuleError(f"there is no module at {spec!r}", line=line,
                          hint="the path is relative to the file that "
                               "imports it")
    except IsADirectoryError:
        raise ModuleError(f"{spec!r} is a folder, not a module", line=line)
    except OSError as e:
        raise ModuleError(f"cannot read {spec!r}: {e}", line=line)


def load(entry_path, source=None):
    """Read, parse and validate the whole closure, once.

    `source` supplies the entry file's text instead of reading it, for a
    script arriving on standard input. Imports still resolve against the
    working directory, because a module path in a script from a pipe can mean
    nothing else.

    Returns a Program. Raises ModuleError, ParseError or LexError, every one
    of which the caller must treat as fatal, because a manifest built from a
    partial closure would be a manifest with a hole in it.
    """
    entry_absolute = os.path.abspath(entry_path)
    root = os.path.dirname(entry_absolute) or "."
    entry_name = os.path.basename(entry_absolute)

    program = Program(entry=entry_name, root=root)
    _load_file(program, entry_name, entry_absolute, is_entry=True,
               source=source)
    _order(program)
    _check_name_collisions(program)
    _resolve_names(program)
    return program


def _resolve_names(program):
    """Check every handler call against the calling file's own table.

    Done here rather than at parse time because a file's legal names are its
    own handlers plus exactly what it imported, which is only known once the
    closure is loaded.
    """
    from .parser import resolve_calls
    tables = handler_tables(program)
    for path, module in program.modules.items():
        resolve_calls(module.tree, extra_names=tables[path].keys())


def _load_file(program, relative, absolute, is_entry=False, spec=None,
               line=None, source=None):
    if relative in program.modules:
        return program.modules[relative]

    if source is None:
        source = read_source(absolute, spec or relative, line)
    # Without resolution: the names this file may call are its own plus
    # what it imports, and the imports are not loaded yet.
    tree = parse(source, resolve=False)
    if not is_entry:
        check_declarations_only(tree, relative)

    module = Module(
        path=relative,
        absolute=absolute,
        source=source,
        digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        tree=tree,
        is_entry=is_entry,
    )
    program.modules[relative] = module

    for use in imports_of(tree):
        child_relative, child_absolute = resolve(
            relative, use.path, program.root, use.line)
        if child_relative == relative:
            raise ModuleError(f"{relative} imports itself", path=relative,
                              line=use.line)
        module.imports.append(
            (child_relative, use.line, list(use.names), use.ceiling))
        _load_file(program, child_relative, child_absolute, spec=use.path,
                   line=use.line)
    return module


def _order(program):
    """Topological order, dependencies first. Refuses cycles."""
    order, state = [], {}

    def visit(path, trail):
        mark = state.get(path)
        if mark == "done":
            return
        if mark == "visiting":
            cycle = " -> ".join(trail + [path])
            raise ModuleError(
                f"these modules import each other: {cycle}", path=path,
                hint="the import graph has to be a DAG, so that every file "
                     "can be understood without already understanding itself")
        state[path] = "visiting"
        for child, _, _, _ in program.modules[path].imports:
            visit(child, trail + [path])
        state[path] = "done"
        order.append(path)

    visit(program.entry, [])
    program.order = order


def _check_name_collisions(program):
    """Two imports may not bring in the same name, and none may shadow a local.

    With a flat handler table one module silently replaces another's handler,
    which is a hijack rather than a hygiene problem. Naming imports explicitly
    turns it into an error here.
    """
    for module in program.modules.values():
        local = {s.name for s in module.tree if isinstance(s, A.HandlerDef)}
        seen = {}
        for imported, line, names, _ in module.imports:
            exported = {s.name for s in program.modules[imported].tree
                        if isinstance(s, A.HandlerDef)}
            for name in names:
                if name not in exported:
                    raise ModuleError(
                        f"{imported} does not define a handler named "
                        f"{name!r}", path=module.path, line=line,
                        hint="it defines: " + (", ".join(sorted(exported))
                                               or "no handlers at all"))
                if name in local:
                    raise ModuleError(
                        f"{name!r} is imported from {imported} but this file "
                        f"already defines it", path=module.path, line=line,
                        hint="rename one of them; frost will not choose")
                if name in seen:
                    raise ModuleError(
                        f"{name!r} is imported from both {seen[name]} and "
                        f"{imported}", path=module.path, line=line,
                        hint="rename one of them; frost will not choose")
                seen[name] = imported


# -------------------------------------------------------- handler binding

def handler_tables(program):
    """name -> HandlerDef for each file, resolved lexically.

    A handler defined in a module calls its own file's handlers, whether or
    not the entry script imported them. A flat table would let an entry
    script's handler capture a call made inside a module.
    """
    tables = {}
    for path, module in program.modules.items():
        table = {s.name: s for s in module.tree if isinstance(s, A.HandlerDef)}
        for imported, _, names, _ in module.imports:
            source_table = {s.name: s
                            for s in program.modules[imported].tree
                            if isinstance(s, A.HandlerDef)}
            for name in names:
                table[name] = source_table[name]
        tables[path] = table
    return tables


def owning_file(program):
    """id(HandlerDef) -> the file that defines it, for lexical lookup."""
    owner = {}
    for path, module in program.modules.items():
        for statement in module.tree:
            if isinstance(statement, A.HandlerDef):
                owner[id(statement)] = path
    return owner


# ---------------------------------------------------------------- locking

def lock_path(entry_path):
    return entry_path + LOCK_SUFFIX


def write_lock(program, path):
    import json
    payload = {"schema": 1, "entry": program.entry,
               "modules": program.digests()}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return payload


def check_lock(program, path):
    """Every difference between the recorded digests and what was just read."""
    import json
    try:
        with open(path) as fh:
            recorded = json.load(fh)["modules"]
    except FileNotFoundError:
        raise ModuleError(
            f"there is no lockfile at {path}",
            hint="write one with --lock, then commit it")
    except (ValueError, KeyError) as e:
        raise ModuleError(f"{path} is not a usable lockfile: {e}")

    now = program.digests()
    drift = []
    for name in sorted(set(recorded) | set(now)):
        if name not in now:
            drift.append(f"{name} is in the lockfile but not imported")
        elif name not in recorded:
            drift.append(f"{name} is imported but not in the lockfile")
        elif recorded[name] != now[name]:
            drift.append(f"{name} has changed since the lockfile was written")
    return drift
