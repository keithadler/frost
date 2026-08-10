"""Loops that cannot end, and runs that will not.

`within` bounds one command and a policy can bound how many there are.
Neither touches a loop doing arithmetic, which spawns nothing, reads nothing
and writes nothing — so it has no capabilities, and a manifest describing
capabilities called it clean. The cheapest way for a generated script to wedge
a runner was also the one thing frost reported as harmless.
"""

import os
import subprocess
import sys
import time

import pytest

from frostlang.audit import audit, find_dangers, verdict, parse_policy
from frostlang.parser import parse

from helpers import REPO


def frost(*args, cwd=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, text, name="s.frost"):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


SPIN = "put 0 into n\nrepeat forever\n    add 1 to n\nend repeat\n"


def loops_in(source):
    return audit(parse(source)).loops


# ------------------------------------------------- a loop that cannot end

def test_a_loop_with_no_way_out_is_dangerous():
    caps = audit(parse(SPIN))
    assert caps.loops == [(2, "forever", False)]
    findings = find_dangers(caps)
    assert verdict(findings) == "dangerous"
    assert "cannot end" in findings[0].title


def test_it_used_to_report_as_doing_nothing_observable():
    """The reason this check exists. A loop touches nothing, so a manifest
    about capabilities had nothing to say and said so approvingly."""
    from frostlang.audit import describe
    caps = audit(parse(SPIN))
    assert describe(caps) == "This script does nothing observable."
    assert find_dangers(caps), "and yet it never terminates"


@pytest.mark.parametrize("escape", [
    "exit repeat", "quit with status 0", "return 1",
])
def test_anything_that_could_end_it_counts(escape):
    body = f"repeat forever\n    if n is 10 then {escape}\nend repeat\n"
    source = ("to go\n    put 0 into n\n    " + body.replace("\n", "\n    ")
              + "\nend go\n") if escape == "return 1" else \
        "put 0 into n\n" + body
    assert loops_in(source)[0][2] is True


def test_presence_counts_rather_than_reachability():
    """An `exit repeat` behind a condition that never fires still counts.
    That understates and never overstates, which is the right way round for a
    check that would otherwise flag working code and be switched off."""
    caps = audit(parse("put 0 into n\nrepeat forever\n"
                       "    if 1 is 2 then exit repeat\nend repeat\n"))
    assert caps.loops[0][2] is True
    assert not [f for f in find_dangers(caps) if "loop" in f.title]


@pytest.mark.parametrize("source,kind", [
    ("repeat while true\n    add 1 to n\nend repeat\n", "while true"),
    ("repeat until false\n    add 1 to n\nend repeat\n", "until false"),
])
def test_the_other_ways_of_writing_forever(source, kind):
    loops = loops_in("put 0 into n\n" + source)
    assert loops[0][1] == kind
    assert loops[0][2] is False


def test_a_real_condition_is_not_second_guessed():
    """`repeat while n is less than 10` may well terminate, and guessing is
    how a check earns a reputation for crying wolf."""
    assert loops_in("put 0 into n\nrepeat while n is less than 10\n"
                    "    add 1 to n\nend repeat\n") == []


def test_a_counted_loop_is_not_a_finding():
    assert loops_in("repeat 10 times\n    put 1\nend repeat\n") == []


def test_a_policy_can_count_unbounded_loops():
    from frostlang.audit import check
    caps = audit(parse(SPIN))
    findings = check(caps, parse_policy("require at most 0 unbounded loops\n"))
    assert findings


# --------------------------------------------------------- the run budget

def test_a_deadline_stops_a_script_that_will_not_stop(tmp_path):
    path = script(tmp_path, SPIN)
    started = time.monotonic()
    status, _, err = frost("--deadline", "1", path, cwd=str(tmp_path))
    elapsed = time.monotonic() - started
    assert status == 124, err
    assert elapsed < 10, f"took {elapsed:.1f}s to honour a 1s deadline"
    assert "whole time budget" in err


def test_cleanup_still_runs_when_the_budget_is_spent(tmp_path):
    """Raised rather than killed. A deadline that skipped cleanup would leave
    exactly the mess it was meant to bound."""
    path = script(tmp_path,
                  'ensure\n    put "cleanup ran"\nend ensure\n' + SPIN)
    status, out, _ = frost("--deadline", "1", path, cwd=str(tmp_path))
    assert status == 124
    assert "cleanup ran" in out


def test_the_exit_code_is_the_one_a_shell_uses_for_a_timeout(tmp_path):
    """124, the same answer frost already gives when one command runs too
    long: the same question at a different scale."""
    from frostlang.interp import TIMEOUT_STATUS
    assert TIMEOUT_STATUS == 124
    path = script(tmp_path, SPIN)
    assert frost("--deadline", "1", path, cwd=str(tmp_path))[0] == 124


def test_a_policy_can_impose_a_budget(tmp_path):
    """So a datacenter can bound what a wedged script costs without every
    author remembering to."""
    (tmp_path / "p.policy").write_text(
        "require the run to finish within 1 seconds\n")
    path = script(tmp_path, SPIN)
    status, _, err = frost("--policy", str(tmp_path / "p.policy"), path,
                           cwd=str(tmp_path))
    assert status == 124
    assert "time budget" in err


def test_the_tightest_budget_wins(tmp_path):
    """A flag cannot widen what the policy imposed, for the same reason no
    other rule can be loosened from the command line."""
    (tmp_path / "p.policy").write_text(
        "require the run to finish within 1 seconds\n")
    path = script(tmp_path, SPIN)
    started = time.monotonic()
    status, _, _ = frost("--policy", str(tmp_path / "p.policy"),
                         "--deadline", "300", path, cwd=str(tmp_path))
    assert status == 124
    assert time.monotonic() - started < 10


def test_a_deadline_units_are_read_from_the_policy():
    rule = parse_policy("require the run to finish within 2 minutes\n")[0]
    assert rule.kind == "deadline"
    assert rule.detail == 120


def test_an_ordinary_script_is_not_affected(tmp_path):
    path = script(tmp_path, 'put "quick"\n')
    status, out, err = frost("--deadline", "30", path, cwd=str(tmp_path))
    assert (status, out.strip()) == (0, "quick"), err


def test_the_budget_survives_a_loop_that_does_nothing(tmp_path):
    """An empty body still executes the loop, so the check cannot live only
    in the statements a body happens to contain."""
    path = script(tmp_path, "repeat forever\n    put 1 into n\nend repeat\n")
    status, _, _ = frost("--deadline", "1", path, cwd=str(tmp_path))
    assert status == 124


# ------------------------------------------------------------- dead code
#
# A script written by a machine has a shape. Invented helpers, statements
# after a return, values computed and dropped: each is harmless alone and
# together they are the clearest sign that what is on the page is not what
# anybody intended.

def dead_in(source):
    return audit(parse(source)).dead


def findings_in(source):
    return [f.title for f in find_dangers(audit(parse(source)))]


def test_a_statement_after_the_script_stops_is_reported():
    titles = findings_in('put "a"\nquit with status 0\nput "never"\n')
    assert any("already stopped" in t for t in titles)


@pytest.mark.parametrize("terminator", [
    "quit with status 0", "exit repeat", "next repeat",
])
def test_every_terminator_ends_its_block(terminator):
    body = f"repeat forever\n    {terminator}\n    put \"never\"\nend repeat\n"
    assert any(k == "unreachable" for _, k, _ in dead_in(body))


def test_only_the_first_unreachable_statement_is_reported():
    """One report per block. A run of ten dead lines is one mistake."""
    source = 'quit with status 0\nput "a"\nput "b"\nput "c"\n'
    assert len([k for _, k, _ in dead_in(source) if k == "unreachable"]) == 1


def test_a_handler_nobody_calls_is_reported():
    assert any("never called" in t for t in
               findings_in('to helper\n    put 1\nend helper\nput "x"\n'))


def test_a_handler_that_is_called_is_not():
    assert not any("never called" in t for t in findings_in(
        "to double with n\n    return n * 2\nend double\n"
        "put the double of 2\n"))


def test_a_handler_defined_in_a_module_and_called_from_the_entry_is_used():
    """The bug this replaced. Findings were computed per file and merged, so
    every handler a module exported read as dead. Defined here and called
    there is the normal shape of an import."""
    import subprocess as sp
    out = sp.run([sys.executable, os.path.join(REPO, "frost"), "--explain",
                  os.path.join(REPO, "examples", "summarise.frost")],
                 capture_output=True, text=True,
                 env={**os.environ, "PYTHONPATH": REPO})
    assert "never called" not in out.stdout


def test_a_value_computed_and_dropped_is_reported():
    assert any("never read" in t for t in
               findings_in('put 1 into dropped\nput "x"\n'))


@pytest.mark.parametrize("source", [
    "put 0 into n\nadd 1 to n\nput n\n",
    "put 0 into n\nrepeat 3 times\n    add 1 to n\nend repeat\nput n\n",
    'put "a" into s\nput "b" after s\nput s\n',
    'put "a" into the "k" of rec\nput rec\n',
])
def test_a_name_that_is_read_by_any_route_is_not_dead(source):
    """`add 1 to n` reads n before writing it. Counting only plain reads
    called every counter in the language unused, which is the shape of false
    positive that gets a check switched off in a week."""
    assert not any("never read" in t for t in findings_in(source))


def test_dead_code_does_not_make_a_script_dangerous():
    """It is a smell, not a hazard. A verdict that shouted at unused code
    would be a verdict people stop reading."""
    from frostlang.audit import verdict
    caps = audit(parse('to helper\n    put 1\nend helper\nput "x"\n'))
    assert verdict(find_dangers(caps)) == "clean"


def test_a_policy_can_refuse_dead_code():
    from frostlang.audit import check
    caps = audit(parse('put "a"\nquit with status 0\nput "never"\n'))
    assert check(caps, parse_policy("require at most 0 dead code\n"))
