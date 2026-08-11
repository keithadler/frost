"""The one invariant everything else rests on.

`--explain` may overstate what a script can do. It must never understate.

A manifest that overstates costs a reviewer a question. A manifest that
understates is worse than having no manifest at all, because the reviewer
stops looking. Every other feature here, the policy engine, approvals, the
sandbox, the MCP server, is built on the assumption that this holds.

Until now it was checked by example: a list of scripts somebody thought of,
each asserting a thing that somebody expected. `tests/test_secret_differential`
does better for one capability, running the script and observing what actually
escaped. This does it for the rest, over scripts nobody chose.

## How it works

Generate a script from a grammar of effectful constructs. Run it with the real
interpreter, with the boundaries instrumented: every process spawned, every
file opened, written or removed, every environment variable read. Then compare
what *happened* against what the manifest *predicted*.

The comparison is one-directional on purpose. Anything that happened must
appear in the manifest. Things in the manifest that did not happen are fine:
a branch not taken is still a capability the script has.

## Why the effects are safe

Programs come from a fixed harmless set, paths are inside pytest's temporary
directory, and loops are bounded by construction. Nothing generated here can
touch anything outside the folder it was given. The scripts are still real:
they spawn real processes and write real files, because a manifest checked
against simulated effects is a manifest checked against an assumption.
"""

import os
import random
import subprocess

import pytest

from frostlang.audit import audit, RUNTIME_HOST
from frostlang.interp import Interpreter
from frostlang.parser import parse

from helpers import needs_coreutils

ROUNDS = int(os.environ.get("FROST_PROPERTY_ROUNDS", "120"))

# Harmless, present everywhere, and each does something observable.
PROGRAMS = ["echo", "true", "printf"]


class Watcher:
    """What actually happened, recorded at the boundary."""

    def __init__(self):
        self.commands = []
        self.reads = []
        self.writes = []
        self.deletes = []
        self.env_reads = []


def instrument(monkeypatch, watcher):
    """Record real effects without changing them.

    Patched into the interpreter's own module namespace rather than over the
    builtins, so pytest, coverage and everything else keep the real ones.
    Every wrapper calls through: the effect happens, and is noted.
    """
    from frostlang import interp as I

    real_popen = subprocess.Popen
    real_open = open
    real_remove = os.remove

    def popen(argv, *a, **kw):
        # Popen only. `subprocess.run` builds one internally, so recording at
        # both counted every spawn twice, which the self-test below caught by
        # expecting exactly one.
        watcher.commands.append(list(argv))
        return real_popen(argv, *a, **kw)

    def opener(path, mode="r", *a, **kw):
        if any(m in mode for m in ("w", "a", "+", "x")):
            watcher.writes.append(str(path))
        else:
            watcher.reads.append(str(path))
        return real_open(path, mode, *a, **kw)

    def remove(path, *a, **kw):
        watcher.deletes.append(str(path))
        return real_remove(path, *a, **kw)

    monkeypatch.setattr(I.subprocess, "Popen", popen)
    monkeypatch.setattr(I, "open", opener, raising=False)
    monkeypatch.setattr(I.os, "remove", remove)


# ------------------------------------------------------------- the grammar

def a_script(rng, folder):
    """A random script whose every effect lands inside `folder`."""
    lines, expected_env = [], []
    for index in range(rng.randint(1, 6)):
        shape = rng.choice(["run", "write", "append", "read", "delete",
                            "getenv", "branch", "loop", "handler", "quiet",
                            "computed_path", "computed_program",
                            "path_from_env", "handler_loop"])
        name = f"f{index}"
        path = os.path.join(folder, f"{name}.txt")

        if shape == "run":
            program = rng.choice(PROGRAMS)
            lines.append(f'run "{program}" with "{name}"')
        elif shape == "write":
            lines.append(f'put "{name}" into file "{path}"')
        elif shape == "append":
            lines.append(f'put "{name}" into file "{path}"')
            lines.append(f'put "more" after file "{path}"')
        elif shape == "read":
            lines.append(f'put "{name}" into file "{path}"')
            lines.append(f'put file "{path}" into {name}')
        elif shape == "delete":
            lines.append(f'put "{name}" into file "{path}"')
            lines.append(f'delete file "{path}"')
        elif shape == "getenv":
            variable = f"FROST_PROP_{index}"
            expected_env.append(variable)
            lines.append(f'put the environment variable "{variable}" '
                         f'into {name}')
        elif shape == "branch":
            # Both arms have effects, and only one runs. The manifest has to
            # report the arm that did not, which is the whole point of a
            # static manifest, so the comparison must not require it.
            lines.append(f'if "{index}" is "{index}" then')
            lines.append(f'    put "taken" into file "{path}"')
            lines.append("else")
            lines.append(f'    run "echo" with "not taken"')
            lines.append("end if")
        elif shape == "loop":
            lines.append(f"repeat {rng.randint(1, 3)} times")
            lines.append(f'    put "{name}" after file "{path}"')
            lines.append("end repeat")
        elif shape == "handler":
            lines.append(f"to make {name} with where")
            lines.append('    put "made" into file where')
            lines.append(f"end make {name}")
            lines.append(f'put the make {name} of "{path}"')
        elif shape == "computed_path":
            # Built from a literal and a name. The manifest may resolve it by
            # constant propagation or report it as unknowable; either is
            # honest, and inventing a different path would not be.
            lines.append(f'put "{name}.txt" into leaf{index}')
            lines.append(f'put "{name}" into file ("{folder}/" & leaf{index})')
        elif shape == "computed_program":
            lines.append(f'put "echo" into tool{index}')
            lines.append(f'run tool{index} with "{name}"')
        elif shape == "path_from_env":
            variable = f"FROST_PROP_DIR_{index}"
            expected_env.append(variable)
            lines.append(f'put the environment variable "{variable}" '
                         f'into base{index}')
            lines.append(f'put "{name}" into file (base{index} & "/{name}.txt")')
        elif shape == "handler_loop":
            lines.append(f"to touch {name} with where")
            lines.append('    put "x" after file where')
            lines.append(f"end touch {name}")
            lines.append("repeat 2 times")
            lines.append(f'    put the touch {name} of "{path}"')
            lines.append("end repeat")
        else:
            lines.append(f'put "{name}"')
    return "\n".join(lines) + "\n", expected_env


# --------------------------------------------------------------- the check

def predicted(caps):
    """What the manifest says, as comparable sets.

    A None anywhere means "built at runtime", which the manifest reports as
    unknowable rather than guessing. Unknowable is not an understatement, so
    it stands in for everything.
    """
    return {
        "commands": {c.program for c in caps.commands},
        "runtime_command": any(c.program is None for c in caps.commands),
        "reads": {p for p, _ in caps.reads if p},
        "runtime_read": any(p is None for p, _ in caps.reads),
        "writes": {p for p, _ in caps.writes if p},
        "runtime_write": any(p is None for p, _ in caps.writes),
        "deletes": {p for p, _ in caps.deletes if p},
        "runtime_delete": any(p is None for p, _ in caps.deletes),
        "env": {n for n, _ in caps.env_reads if n},
        "runtime_env": any(n is None for n, _ in caps.env_reads),
    }


def relative(paths, folder):
    """Real paths, as the script wrote them, so they compare with the tree."""
    return {os.path.abspath(p) for p in paths}


@needs_coreutils
@pytest.mark.parametrize("seed", range(ROUNDS))
def test_the_manifest_never_understates_what_a_script_did(seed, tmp_path,
                                                          monkeypatch):
    """Generated, run, and compared against what actually happened."""
    rng = random.Random(seed)
    folder = str(tmp_path)
    source, wanted_env = a_script(rng, folder)

    for variable in wanted_env:
        # A folder variable points at the same temporary folder, so a path
        # built from the environment still lands somewhere harmless.
        monkeypatch.setenv(variable,
                           folder if "DIR" in variable else "set-for-the-test")

    tree = parse(source)
    caps = audit(tree)
    manifest = predicted(caps)

    watcher = Watcher()
    instrument(monkeypatch, watcher)

    interp = Interpreter()
    interp.run_program(tree)

    # 1. Every program that ran is named, or the manifest says a name was
    #    built at runtime.
    for argv in watcher.commands:
        program = os.path.basename(argv[0])
        assert program in manifest["commands"] or manifest["runtime_command"], (
            f"ran {program!r} and the manifest does not mention it\n{source}")

    # 2. Every file written is named. Compared as absolute paths, because the
    #    script writes the absolute path the generator gave it.
    for path in relative(watcher.writes, folder):
        if not path.startswith(folder):
            continue                     # pytest's own bookkeeping
        named = {os.path.abspath(p) for p in manifest["writes"]}
        assert path in named or manifest["runtime_write"], (
            f"wrote {path} and the manifest does not mention it\n{source}")

    # 3. Every file deleted is named.
    for path in relative(watcher.deletes, folder):
        if not path.startswith(folder):
            continue
        named = {os.path.abspath(p) for p in manifest["deletes"]}
        assert path in named or manifest["runtime_delete"], (
            f"deleted {path} and the manifest does not mention it\n{source}")

    # 4. Every file read is named.
    for path in relative(watcher.reads, folder):
        if not path.startswith(folder):
            continue
        named = {os.path.abspath(p) for p in manifest["reads"]}
        assert path in named or manifest["runtime_read"], (
            f"read {path} and the manifest does not mention it\n{source}")


def test_the_generator_produces_scripts_with_effects():
    """Guards every property above from passing on empty scripts, which is
    how a check that has stopped looking reports success."""
    rng = random.Random(0)
    seen = {"commands": 0, "writes": 0, "reads": 0, "deletes": 0, "env": 0}
    for seed in range(ROUNDS):
        source, _ = a_script(random.Random(seed), "/tmp/x")
        caps = audit(parse(source))
        seen["commands"] += len(caps.commands)
        seen["writes"] += len(caps.writes)
        seen["reads"] += len(caps.reads)
        seen["deletes"] += len(caps.deletes)
        seen["env"] += len(caps.env_reads)
    for kind, count in seen.items():
        assert count > 5, f"the generator almost never produces a {kind}: {seen}"


def test_every_generated_script_parses():
    """A generator that emits nonsense would make the properties vacuous in
    the most flattering way: no effects, nothing to contradict."""
    for seed in range(ROUNDS):
        source, _ = a_script(random.Random(seed), "/tmp/x")
        parse(source)


@needs_coreutils
def test_the_harness_would_notice_an_understated_manifest(tmp_path,
                                                          monkeypatch):
    """The harness, pointed at a manifest that is missing something.

    Without this the properties above prove only that nothing raised. The
    audit is stubbed to forget one command, and the same comparison the
    properties make has to fail.
    """
    source = 'run "echo" with "hello"\n'
    tree = parse(source)
    caps = audit(tree)
    caps.commands = []                    # the understatement, by hand
    manifest = predicted(caps)

    watcher = Watcher()
    instrument(monkeypatch, watcher)
    Interpreter().run_program(tree)

    assert watcher.commands, "nothing was recorded; the instrument is broken"
    missed = [os.path.basename(argv[0]) for argv in watcher.commands
              if os.path.basename(argv[0]) not in manifest["commands"]
              and not manifest["runtime_command"]]
    assert missed == ["echo"], \
        "the comparison does not notice a command missing from the manifest"


@needs_coreutils
def test_the_instrument_actually_sees_every_kind_of_effect(tmp_path,
                                                           monkeypatch):
    """The properties above loop over what the instrument recorded.

    If it recorded nothing they would pass by iterating over an empty list,
    which is the most flattering way for a check to fail. This runs the same
    generated scripts and asserts each kind of effect was observed at least
    once across the batch.
    """
    totals = {"commands": 0, "writes": 0, "reads": 0, "deletes": 0}
    for seed in range(40):
        folder = tmp_path / f"run{seed}"
        folder.mkdir()
        source, wanted = a_script(random.Random(seed), str(folder))
        for variable in wanted:
            monkeypatch.setenv(variable,
                               str(folder) if "DIR" in variable else "x")

        watcher = Watcher()
        with monkeypatch.context() as patch:
            instrument(patch, watcher)
            Interpreter().run_program(parse(source))

        totals["commands"] += len(watcher.commands)
        inside = lambda paths: [p for p in paths if str(folder) in str(p)]
        totals["writes"] += len(inside(watcher.writes))
        totals["reads"] += len(inside(watcher.reads))
        totals["deletes"] += len(inside(watcher.deletes))

    for kind, count in totals.items():
        assert count > 3, (
            f"the instrument saw almost no {kind} across 40 scripts, so the "
            f"properties are checking an empty list: {totals}")
