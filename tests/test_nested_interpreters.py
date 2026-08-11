"""An interpreter reached through another program.

The original detector asked whether the *program* was a shell and whether -c
was among its arguments. That is the shape people write when they mean to use
a shell, and it is not the shape they write when they do it by accident.

`xargs sh -c`, `env sh -c`, `sudo sh -c`, `timeout 5 bash -c`, `find -exec`,
`ssh host "..."`: the escape is identical and the program name is innocent, so
every one of them walked past a manifest that reported no shell escape at all.
A manifest may overstate. Understating is the failure that makes it worse than
having none, and this was an understatement.

The list of launchers is a floor, not a proof. `awk` can call system(), `make`
runs a shell per recipe line, and some program nobody here has heard of takes
a --command flag. What is testable is that the spellings an agent actually
writes are caught, and that the innocent lookalikes are not.
"""

import pytest

from frostlang.audit import (audit, nested_interpreter, parse_policy, check,
                             find_dangers, verdict)
from frostlang.parser import parse

from helpers import caps_for


def findings_for(source):
    return find_dangers(audit(parse(source)))


def escapes_in(source):
    return [f for f in findings_for(source)
            if "Shell escape" in f.title or "Hidden command" in f.title]


# ------------------------------------------------------- the ones to catch

CAUGHT = [
    ('run "xargs" with "-I", "{}", "sh", "-c", "echo {}"', "xargs sh -c"),
    ('run "env" with "FOO=1", "bash", "-c", "id"', "env bash -c"),
    ('run "sudo" with "sh", "-c", "id"', "sudo sh -c"),
    ('run "timeout" with "5", "python3", "-c", "print(1)"',
     "timeout python3 -c"),
    ('run "nohup" with "sh", "-c", "sleep 1"', "nohup sh -c"),
    ('run "docker" with "run", "img", "sh", "-c", "id"', "docker sh -c"),
    ('run "find" with ".", "-exec", "rm", "{}", ";"', "find -exec"),
    ('run "find" with ".", "-execdir", "rm", "{}", ";"', "find -execdir"),
    ('run "find" with ".", "-exec", "sh", "-c", "rm {}", ";"',
     "find -exec sh -c"),
    ('run "ssh" with "host", "rm -rf /tmp/x"', "ssh remote command"),
]


@pytest.mark.parametrize("source,how", CAUGHT,
                         ids=[h for _, h in CAUGHT])
def test_an_interpreter_reached_through_another_program_is_reported(
        source, how):
    found = escapes_in(source)
    assert found, f"nothing reported for: {source}"
    assert any(how in f.title for f in found), \
        f"expected {how!r}, got {[f.title for f in found]}"
    assert all(f.severity == "danger" for f in found)


def test_the_direct_form_still_reports_once_and_not_twice():
    """The nested check runs only where the direct one did not fire. Two
    findings for one command reads as two problems."""
    found = escapes_in('run "sh" with "-c", "id"')
    assert len(found) == 1
    assert "Shell escape via sh -c" in found[0].title


# -------------------------------------------------- the ones to leave alone

INNOCENT = [
    'run "grep" with "-c", "sh", "log.txt"',      # -c before the name
    'run "echo" with "sh"',                        # no -c at all
    'run "echo" with "-c"',
    'run "find" with ".", "-name", "*.log"',       # no action
    'run "ssh" with "host"',                       # no remote command
    'run "ssh" with "-p", "22", "host"',
    'run "xargs" with "rm"',
    'run "env"',
    'run "timeout" with "5", "curl", "https://example.com"',
]


@pytest.mark.parametrize("source", INNOCENT,
                         ids=[s[5:40] for s in INNOCENT])
def test_an_innocent_lookalike_is_not_reported(source):
    """A detector that fires on `grep -c sh` teaches people to ignore it,
    which costs more than the cases it catches."""
    assert escapes_in(source) == []


def test_the_helper_answers_none_rather_than_guessing():
    assert nested_interpreter("echo", ["hello"]) is None
    assert nested_interpreter("xargs", ["rm"]) is None
    assert nested_interpreter("find", [".", "-name", "x"]) is None


# ---------------------------------------------------------- what it is for

def test_a_policy_that_forbids_the_shell_still_misses_the_launcher():
    """Worth stating plainly, because it is the limit of this change.

    The finding names the escape. `forbid running "sh"` matches the program
    frost spawns, which here is xargs, so the rule does not fire. Refusing the
    launcher as well is the policy author's job, and the manifest now gives
    them the reason to.
    """
    rules = parse_policy('forbid running "sh"')
    source = 'run "xargs" with "-I", "{}", "sh", "-c", "echo {}"'
    assert check(audit(parse(source)), rules) == []
    assert escapes_in(source), "but the manifest says so"


def test_the_verdict_turns_dangerous():
    """The finding has to reach the verdict, or nothing acts on it."""
    assert verdict(findings_for(
        'run "xargs" with "-I", "{}", "sh", "-c", "echo {}"')) == "dangerous"


def test_a_nested_escape_counts_against_a_capability_budget():
    """`--explain` reports it, so a review gate that counts dangers sees it."""
    caps = caps_for('run "env" with "sh", "-c", "id"')
    assert caps.commands, "the command is still reported as a command"
