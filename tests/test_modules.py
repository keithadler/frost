"""Modules, and the invariant they are most likely to break.

Everything frost is worth using for rests on one thing: the tree you audit is
the program you run, and the audit sees all of it. A module system is the
feature most likely to put capability outside the manifest, and if it does,
frost is worse than bash — bash never claimed to have audited anything.

So these tests are organised by the rule each one buys, and the two that
matter most are the last two sections: that a module cannot do anything when
it is imported, and that nothing it does can escape the manifest.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import modules as M
from frostlang.modules import ModuleError, Ceiling
from frostlang.parser import parse, ParseError
from frostlang.program_audit import (audit_program, check_all_ceilings,
                                     describe_program, cross_file_taint)

from helpers import REPO


@pytest.fixture
def workspace(tmp_path):
    """A directory to build little programs in."""
    def write(name, source):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source.lstrip("\n"))
        return str(path)
    write.root = tmp_path
    return write


def load(workspace, entry="entry.frost"):
    return M.load(str(workspace.root / entry))


def frost(*args, cwd=None):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


LIB = """
to shout with word
    return the uppercase word & "!"
end shout

to whisper with word
    return the lowercase word
end whisper
"""


# ------------------------------------------------ 1. declarations only

def test_a_module_with_a_top_level_statement_is_refused(workspace):
    """Import-time side effects are the most abused feature of every module
    system ever shipped. Refusing them means `use` can never do anything."""
    workspace("lib/bad.frost", 'run "curl" with "https://evil.example"\n'
                               'to helper\n    put 1\nend helper\n')
    workspace("entry.frost", 'use "lib/bad.frost" for the helper\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "only define handlers" in e.value.msg
    assert "imported" in e.value.msg


def test_even_a_harmless_top_level_statement_is_refused(workspace):
    """The rule is structural, not a judgement about which effects are bad."""
    workspace("lib/bad.frost", 'put "hello"\nto helper\n    put 1\nend helper\n')
    workspace("entry.frost", 'use "lib/bad.frost" for the helper\n')
    with pytest.raises(ModuleError):
        load(workspace)


def test_a_module_may_import_another_module(workspace):
    """`use` is a declaration: the file it names has no executable top level
    either, so reading the graph never runs any of it."""
    workspace("lib/upper.frost", "to shout with w\n    return the uppercase w\n"
                                 "end shout\n")
    workspace("lib/greet.frost",
              'use "upper.frost" for the shout\n'
              'to greet with n\n    return the shout of n\nend greet\n')
    workspace("entry.frost",
              'use "lib/greet.frost" for the greet\nput the greet of "hi"\n')
    program = load(workspace)
    assert set(program.modules) == {"entry.frost", "lib/greet.frost",
                                    "lib/upper.frost"}


def test_the_entry_script_may_of_course_have_statements(workspace):
    workspace("lib/text.frost", LIB)
    workspace("entry.frost",
              'use "lib/text.frost" for the shout\nput the shout of "x"\n')
    assert load(workspace).entry == "entry.frost"


# ---------------------------------------------------- 2. literal paths

@pytest.mark.parametrize("source", [
    'use (module name) for the a',
    'use module name for the a',
    'use "lib/" & name for the a',
    'use it for the a',
])
def test_a_computed_import_is_a_parse_error(source):
    """A dynamic import is eval wearing a hat: it would put the graph out of
    reach of the analysis every other guarantee depends on."""
    with pytest.raises(ParseError) as e:
        parse(source)
    assert "written out in full" in e.value.msg or "expected" in e.value.msg


def test_the_refusal_happens_when_the_file_is_parsed_not_when_it_runs():
    with pytest.raises(ParseError):
        parse('put "lib/x.frost" into chosen\nuse chosen for the a')


def test_an_import_must_name_what_it_brings_in():
    with pytest.raises(ParseError) as e:
        parse('use "lib/db.frost"')
    assert "for" in e.value.msg


def test_an_import_cannot_be_inside_a_block(workspace):
    """A conditional import would make the graph unknowable without running."""
    workspace("lib/text.frost", LIB)
    workspace("entry.frost",
              'if 1 is 1 then\n    use "lib/text.frost" for the shout\nend if\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "inside a block" in e.value.msg


# ------------------------------------------------ 3. relative resolution

def test_a_module_resolves_relative_to_the_file_that_imports_it(workspace):
    workspace("lib/upper.frost", "to shout with w\n    return w\nend shout\n")
    workspace("lib/greet.frost",
              'use "upper.frost" for the shout\n'
              'to greet with n\n    return the shout of n\nend greet\n')
    workspace("entry.frost",
              'use "lib/greet.frost" for the greet\nput the greet of "x"\n')
    program = load(workspace)
    assert "lib/upper.frost" in program.modules


@pytest.mark.parametrize("spec", ["/etc/passwd", "/tmp/x.frost"])
def test_an_absolute_path_is_refused(workspace, spec):
    workspace("entry.frost", f'use "{spec}" for the a\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "absolute" in e.value.msg


@pytest.mark.parametrize("spec", ["../outside.frost", "lib/../../outside.frost",
                                  "../../etc/passwd"])
def test_a_path_above_the_entry_directory_is_refused(workspace, spec):
    """Vendoring is the feature: reviewing the repository reviews the whole
    program."""
    workspace("entry.frost", f'use "{spec}" for the a\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "outside" in e.value.msg


def test_the_boundary_is_the_entry_scripts_own_directory(workspace):
    """Not the repository root — the entry script's directory. A script in
    tools/ cannot reach a sibling lib/, which is restrictive on purpose: the
    directory a reviewer opens is the directory the program lives in."""
    workspace("lib/text.frost", LIB)
    workspace("tools/entry.frost",
              'use "../lib/text.frost" for the shout\nput the shout of "x"\n')
    with pytest.raises(ModuleError) as e:
        M.load(str(workspace.root / "tools" / "entry.frost"))
    assert "outside" in e.value.msg


def test_a_module_may_sit_in_a_subdirectory_of_the_entry(workspace):
    workspace("lib/deep/text.frost", LIB)
    workspace("entry.frost",
              'use "lib/deep/text.frost" for the shout\nput the shout of "x"\n')
    assert "lib/deep/text.frost" in load(workspace).modules


def test_dot_dot_that_stays_inside_the_boundary_is_fine(workspace):
    workspace("lib/text.frost", LIB)
    workspace("lib/deep/mid.frost",
              'use "../text.frost" for the shout\n'
              "to mid with w\n    return the shout of w\nend mid\n")
    workspace("entry.frost",
              'use "lib/deep/mid.frost" for the mid\nput the mid of "x"\n')
    assert "lib/text.frost" in load(workspace).modules


def test_a_missing_module_is_refused_with_the_path(workspace):
    workspace("entry.frost", 'use "lib/nope.frost" for the a\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "lib/nope.frost" in e.value.msg


def test_there_is_no_search_path(workspace, monkeypatch):
    """An ambient search path is exactly how 'the script I reviewed loaded a
    different file in production' happens."""
    monkeypatch.setenv("FROST_PATH", str(workspace.root / "elsewhere"))
    workspace("elsewhere/text.frost", LIB)
    workspace("entry.frost", 'use "text.frost" for the shout\n')
    with pytest.raises(ModuleError):
        load(workspace)


# --------------------------------------------- 4. explicit lists, DAG

def test_two_modules_may_not_bring_in_the_same_name(workspace):
    """With a flat table one silently replaces the other, which is a hijack
    rather than a hygiene problem."""
    workspace("lib/a.frost", "to connect\n    put 1\nend connect\n")
    workspace("lib/b.frost", "to connect\n    put 2\nend connect\n")
    workspace("entry.frost", 'use "lib/a.frost" for the connect\n'
                             'use "lib/b.frost" for the connect\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "both" in e.value.msg


def test_an_import_may_not_shadow_a_local_handler(workspace):
    workspace("lib/a.frost", "to connect\n    put 1\nend connect\n")
    workspace("entry.frost", 'use "lib/a.frost" for the connect\n'
                             "to connect\n    put 2\nend connect\n")
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "already defines it" in e.value.msg


def test_importing_a_name_the_module_does_not_define_is_refused(workspace):
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the missing\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "does not define" in e.value.msg
    assert "shout" in (e.value.hint or "")


def test_only_the_named_handlers_arrive(workspace):
    """A module's other handlers are not in scope at the import site."""
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                             'put the shout of "x"\n')
    tables = M.handler_tables(load(workspace))
    assert sorted(tables["entry.frost"]) == ["shout"]
    assert sorted(tables["lib/text.frost"]) == ["shout", "whisper"]


def test_a_cycle_is_refused_with_the_path(workspace):
    workspace("lib/a.frost", 'use "b.frost" for the b\n'
                             "to a\n    b\nend a\n")
    workspace("lib/b.frost", 'use "a.frost" for the a\n'
                             "to b\n    a\nend b\n")
    workspace("entry.frost", 'use "lib/a.frost" for the a\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "import each other" in e.value.msg
    assert "lib/a.frost" in e.value.msg and "lib/b.frost" in e.value.msg


def test_a_module_importing_itself_is_refused(workspace):
    workspace("lib/a.frost", 'use "a.frost" for the a\n'
                             "to a\n    put 1\nend a\n")
    workspace("entry.frost", 'use "lib/a.frost" for the a\n')
    with pytest.raises(ModuleError) as e:
        load(workspace)
    assert "itself" in e.value.msg


def test_the_same_module_imported_twice_is_read_once(workspace):
    workspace("lib/text.frost", LIB)
    workspace("lib/mid.frost", 'use "text.frost" for the shout\n'
                               "to mid with w\n    return the shout of w\n"
                               "end mid\n")
    workspace("entry.frost", 'use "lib/text.frost" for the whisper\n'
                             'use "lib/mid.frost" for the mid\n'
                             'put the mid of "x"\n')
    program = load(workspace)
    assert list(program.modules).count("lib/text.frost") == 1
    assert program.order.index("lib/text.frost") < program.order.index(
        "lib/mid.frost")


# ------------------------------------- HAZARD 1: handler table shadowing

def test_a_module_calls_its_own_handler_not_the_entry_scripts(workspace):
    """The hijack this prevents: a flat name table lets an entry script
    capture a call made inside a module."""
    workspace("lib/a.frost",
              'to inner\n    return "module"\nend inner\n'
              "to outer\n    return the inner\nend outer\n")
    workspace("entry.frost",
              'use "lib/a.frost" for the outer\n'
              'to inner\n    return "entry"\nend inner\n'
              "put the outer\nput the inner\n")
    status, out, err = frost(str(workspace.root / "entry.frost"))
    assert status == 0, err
    assert out.split("\n")[:2] == ["module", "entry"]


def test_each_file_gets_its_own_table(workspace):
    workspace("lib/a.frost", "to only mine\n    put 1\nend only mine\n"
                             "to shared\n    put 2\nend shared\n")
    workspace("entry.frost", 'use "lib/a.frost" for the shared\n'
                             "to local only\n    put 3\nend local only\n")
    tables = M.handler_tables(load(workspace))
    assert sorted(tables["entry.frost"]) == ["local only", "shared"]
    assert sorted(tables["lib/a.frost"]) == ["only mine", "shared"]


def test_a_module_cannot_call_a_handler_it_did_not_import(workspace):
    workspace("lib/a.frost", "to helper\n    return the entry only\nend helper\n")
    workspace("entry.frost", 'use "lib/a.frost" for the helper\n'
                             "to entry only\n    return 1\nend entry only\n"
                             "put the helper\n")
    with pytest.raises(ParseError) as e:
        load(workspace)
    # The module's table does not contain it, so the name does not resolve.
    assert "entry only" in e.value.msg


# ------------------------------------------- HAZARD 2: taint scoping

def test_an_unrelated_name_in_another_file_is_not_tainted(workspace):
    """Name-based taint over a concatenated tree makes a module's `token` and
    an entry script's secret `token` one node, which is a false positive."""
    workspace("lib/pub.frost",
              "to publish\n"
              '    put "public-config" into token\n'
              '    run "echo" with token\n'
              "end publish\n")
    workspace("entry.frost",
              'use "lib/pub.frost" for the publish which may run "echo"\n'
              'put the secret environment variable "REAL" into token\n'
              "publish\n")
    program = load(workspace)
    caps = audit_program(program)
    assert caps.merged.secret_releases == []


def test_taint_does_cross_where_the_data_does(workspace):
    """A secret passed as an argument taints the callee's parameter."""
    workspace("lib/send.frost",
              "to send with value\n"
              '    run "curl" with "--data", value\n'
              "end send\n")
    workspace("entry.frost",
              'use "lib/send.frost" for the send which may run "curl"\n'
              'put the secret environment variable "REAL" into token\n'
              "send with token\n")
    caps = audit_program(load(workspace))
    assert any(where == "argument"
               for where, _, _ in caps.merged.secret_releases)


def test_taint_is_computed_per_file(workspace):
    workspace("lib/a.frost", 'to a\n    put "x" into token\nend a\n')
    workspace("entry.frost",
              'use "lib/a.frost" for the a\n'
              'put the secret environment variable "REAL" into token\n')
    program = load(workspace)
    tainted = cross_file_taint(program, M.handler_tables(program))
    assert "token" in tainted["entry.frost"]
    assert "token" not in tainted["lib/a.frost"]


# ------------------------------------ 5. closure audit with provenance

def test_capabilities_are_gathered_over_the_closure(workspace):
    workspace("lib/db.frost",
              'to connect\n    run "psql"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "psql"\n'
              "connect\n")
    caps = audit_program(load(workspace))
    assert [c.program for c in caps.merged.commands] == ["psql"]


def test_a_module_is_audited_even_if_nothing_calls_it(workspace):
    """The sound direction, and the existing traversal gives it for free."""
    workspace("lib/db.frost", 'to never used\n    run "psql"\nend never used\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the never used which may run "psql"\n'
              'put "the handler is never called"\n')
    caps = audit_program(load(workspace))
    assert [c.program for c in caps.merged.commands] == ["psql"]


def test_each_capability_is_attributed_to_its_file(workspace):
    workspace("lib/db.frost", 'to connect\n    run "psql"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "psql"\n'
              'run "git" with "status"\nconnect\n')
    program = load(workspace)
    caps = audit_program(program)
    by_file = {f.path: [c.program for c in f.caps.commands]
               for f in caps.files}
    assert by_file["lib/db.frost"] == ["psql"]
    assert by_file["entry.frost"] == ["git"]


def test_the_manifest_names_the_import_a_capability_arrived_through(workspace):
    workspace("lib/db.frost", 'to connect\n    run "psql"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "psql"\n'
              "connect\n")
    program = load(workspace)
    text = describe_program(program, audit_program(program))
    assert "lib/db.frost" in text
    assert "imported by entry.frost:1" in text


def test_a_single_file_manifest_is_unchanged(workspace):
    """Adding modules must not change the output for scripts that have none."""
    from frostlang.audit import describe, audit
    workspace("entry.frost", 'run "git" with "status"\n')
    program = load(workspace)
    assert describe_program(program, audit_program(program)) == describe(
        audit(program.tree))


# ------------------------------------------- 6. the import-site ceiling

def test_a_module_that_exceeds_its_ceiling_is_refused(workspace):
    workspace("lib/sneaky.frost",
              'to helper\n    run "curl" with "https://collect.example"\n'
              "end helper\n")
    workspace("entry.frost",
              'use "lib/sneaky.frost" for the helper\nhelper\n')
    program = load(workspace)
    breaches = check_all_ceilings(program, audit_program(program))
    assert any("may not run curl" in f.title for f in breaches)


def test_the_default_ceiling_is_nothing_but_computation(workspace):
    workspace("lib/pure.frost",
              "to double with n\n    return n * 2\nend double\n")
    workspace("entry.frost",
              'use "lib/pure.frost" for the double\nput the double of 2\n')
    program = load(workspace)
    assert check_all_ceilings(program, audit_program(program)) == []


def test_a_declared_capability_is_allowed(workspace):
    workspace("lib/db.frost", 'to connect\n    run "psql"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "psql"\n'
              "connect\n")
    program = load(workspace)
    assert check_all_ceilings(program, audit_program(program)) == []


def test_a_ceiling_covers_only_what_it_names(workspace):
    workspace("lib/db.frost",
              'to connect\n    run "psql"\n    run "pg_dump"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "psql"\n'
              "connect\n")
    program = load(workspace)
    breaches = check_all_ceilings(program, audit_program(program))
    assert len(breaches) == 1
    assert "pg_dump" in breaches[0].title


def test_a_ceiling_accepts_a_glob(workspace):
    workspace("lib/db.frost",
              'to connect\n    run "psql"\n    run "pg_dump"\nend connect\n')
    workspace("entry.frost",
              'use "lib/db.frost" for the connect which may run "p*"\n'
              "connect\n")
    program = load(workspace)
    assert check_all_ceilings(program, audit_program(program)) == []


@pytest.mark.parametrize("body,clause,breach", [
    ('put file "x.txt"', 'read "x.txt"', 'read "y.txt"'),
    ('put "a" into file "out.txt"', 'write "out.txt"', 'write "other.txt"'),
    ('delete file "gone.txt"', 'delete "gone.txt"', 'delete "other.txt"'),
    ('put "a" into the environment variable "CC"', 'set "CC"', 'set "OTHER"'),
    ('put "/tmp" into the current folder', 'change folder', 'run "x"'),
])
def test_every_kind_of_capability_is_covered(workspace, body, clause, breach):
    workspace("lib/m.frost", f"to act\n    {body}\nend act\n")
    workspace("entry.frost",
              f'use "lib/m.frost" for the act which may {clause}\nact\n')
    program = load(workspace)
    assert check_all_ceilings(program, audit_program(program)) == [], clause

    workspace("entry.frost",
              f'use "lib/m.frost" for the act which may {breach}\nact\n')
    program = load(workspace)
    assert check_all_ceilings(program, audit_program(program)), breach


def test_a_runtime_built_program_name_always_exceeds_a_ceiling(workspace):
    """A limit that cannot be checked is not a limit."""
    workspace("lib/m.frost",
              'to act\n    run "cat" with "n.txt"\n    put it into tool\n'
              "    run tool\nend act\n")
    workspace("entry.frost",
              'use "lib/m.frost" for the act which may run "psql"\nact\n')
    program = load(workspace)
    breaches = check_all_ceilings(program, audit_program(program))
    assert any("chosen at runtime" in f.title for f in breaches)


def test_a_transitive_module_is_bounded_by_its_own_import(workspace):
    workspace("lib/deep.frost", 'to deep\n    run "curl"\nend deep\n')
    workspace("lib/mid.frost",
              'use "deep.frost" for the deep\n'
              "to mid\n    deep\nend mid\n")
    workspace("entry.frost",
              'use "lib/mid.frost" for the mid which may run "curl"\nmid\n')
    program = load(workspace)
    breaches = check_all_ceilings(program, audit_program(program))
    assert any("lib/deep.frost may not run curl" in f.title for f in breaches)


# ----------------------------------------------------- 7. content pinning

def test_the_lockfile_records_every_module(workspace):
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                             'put the shout of "x"\n')
    entry = str(workspace.root / "entry.frost")
    status, out, err = frost("--lock", entry)
    assert status == 0, err
    with open(entry + ".lock") as fh:
        recorded = json.load(fh)
    assert set(recorded["modules"]) == {"entry.frost", "lib/text.frost"}
    assert all(len(d) == 64 for d in recorded["modules"].values())


def test_frozen_accepts_an_unchanged_program(workspace):
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                             'put the shout of "x"\n')
    entry = str(workspace.root / "entry.frost")
    frost("--lock", entry)
    status, out, err = frost("--frozen", entry)
    assert (status, out.strip()) == (0, "X!")


def test_frozen_refuses_a_changed_module(workspace):
    """Modules open a window between the audit and the run. This closes it."""
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                             'put the shout of "x"\n')
    entry = str(workspace.root / "entry.frost")
    frost("--lock", entry)
    workspace("lib/text.frost", LIB + '\nto extra\n    run "curl"\nend extra\n')
    status, _, err = frost("--frozen", entry)
    assert status == 3
    assert "has changed" in err
    assert "was not run" in err


def test_frozen_without_a_lockfile_is_refused(workspace):
    workspace("entry.frost", 'put "x"\n')
    status, _, err = frost("--frozen", str(workspace.root / "entry.frost"))
    assert status == 2
    assert "no lockfile" in err


def test_frozen_notices_a_module_that_was_added(workspace):
    workspace("lib/a.frost", "to a\n    put 1\nend a\n")
    workspace("entry.frost", 'use "lib/a.frost" for the a\na\n')
    entry = str(workspace.root / "entry.frost")
    frost("--lock", entry)
    workspace("lib/b.frost", "to b\n    put 2\nend b\n")
    workspace("entry.frost", 'use "lib/a.frost" for the a\n'
                             'use "lib/b.frost" for the b\na\nb\n')
    status, _, err = frost("--frozen", entry)
    assert status == 3
    assert "not in the lockfile" in err


def test_the_closure_is_read_once(workspace):
    """Audit and run come from the same bytes, so there is no window to
    change a module in between."""
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n')
    program = load(workspace)
    source_at_load = program.modules["lib/text.frost"].source
    workspace("lib/text.frost", "to shout with w\n    return \"changed\"\n"
                                "end shout\n")
    assert program.modules["lib/text.frost"].source == source_at_load


# ------------------------------------------------------ fail closed

def test_explain_fails_closed_on_an_unresolvable_module(workspace):
    """A manifest with a hole in it is the one output that actively misleads,
    and it would contradict frost's own rule about naming the unknowable."""
    workspace("entry.frost", 'use "lib/missing.frost" for the a\n')
    status, out, err = frost("--explain", str(workspace.root / "entry.frost"))
    assert status == 2
    assert "Verdict" not in out
    assert "Runs these programs" not in out
    assert "no module at" in err


def test_check_fails_closed_too(workspace):
    workspace("entry.frost", 'use "lib/missing.frost" for the a\n')
    status, out, err = frost("--check", str(workspace.root / "entry.frost"))
    assert (status, "ok" in out) == (2, False)


def test_a_module_error_is_available_as_json(workspace):
    workspace("entry.frost", 'use "lib/missing.frost" for the a\n')
    status, out, _ = frost("--check", "--json",
                           str(workspace.root / "entry.frost"))
    payload = json.loads(out)
    assert (status, payload["ok"]) == (2, False)
    assert payload["diagnostics"][0]["code"] == "module-error"


# --------------------------------------------------------- running it

def test_a_program_runs_across_files(workspace):
    workspace("lib/text.frost", LIB)
    workspace("entry.frost", 'use "lib/text.frost" for the shout\n'
                             'put the shout of "deploying"\n')
    status, out, err = frost(str(workspace.root / "entry.frost"))
    assert (status, out.strip()) == (0, "DEPLOYING!"), err


def test_a_breached_ceiling_stops_the_run(workspace):
    workspace("lib/sneaky.frost",
              'to helper\n    run "echo" with "ran anyway"\nend helper\n')
    workspace("entry.frost",
              'use "lib/sneaky.frost" for the helper\nhelper\n')
    status, out, err = frost(str(workspace.root / "entry.frost"))
    assert status == 3
    assert "ran anyway" not in out
    assert "was not run" in err


def test_a_handler_from_a_module_can_return_a_value(workspace):
    workspace("lib/math.frost",
              "to double with n\n    return n * 2\nend double\n")
    workspace("entry.frost",
              'use "lib/math.frost" for the double\n'
              "put the double of 21\n")
    status, out, err = frost(str(workspace.root / "entry.frost"))
    assert (status, out.strip()) == (0, "42"), err


def test_a_single_file_script_still_runs_unchanged(workspace):
    workspace("entry.frost", 'put "unchanged"\n')
    status, out, _ = frost(str(workspace.root / "entry.frost"))
    assert (status, out.strip()) == (0, "unchanged")


# ------------------------------------------------------ the ceiling type

def test_an_empty_ceiling_describes_itself_honestly():
    assert Ceiling().describe() == "nothing but compute"
    assert Ceiling().is_empty()


def test_a_ceiling_describes_what_it_allows():
    c = Ceiling(programs=["psql"], writes=["/tmp/*"])
    assert 'run "psql"' in c.describe()
    assert 'write "/tmp/*"' in c.describe()
