"""The analysis surface a browser gets.

`play.html` used to run a second implementation of the language in JavaScript,
which could evaluate expressions and nothing else. With CPython compiled to
WebAssembly the page runs this module, so the demo is the tool rather than a
model of it.

Tested here in plain Python rather than through a headless browser: the module
deliberately has no browser dependency, and a test that needed one would be
slower and would prove less.
"""

import json

import pytest

from frostlang import browser
from frostlang.audit import audit
from frostlang.parser import parse


CLEAN = 'run "echo" with "hello"\nput it\n'
REACHES = 'run "curl" with "https://api.example/x" within 30 seconds\n'
BROKEN = 'if 1 is 1\n    put "x"\nend if\n'


def test_check_reports_a_parse():
    assert "ok (2 top-level statements)" in browser.run("check", CLEAN)


def test_a_syntax_error_is_a_sentence_not_a_traceback():
    """A traceback in a demo teaches nothing and looks broken."""
    answer = browser.run("check", BROKEN)
    assert "Syntax error on line 1" in answer
    assert "Traceback" not in answer
    assert "then" in answer


def test_explain_produces_the_manifest():
    answer = browser.run("explain", REACHES)
    assert "Runs these programs:" in answer
    assert "Reaches these hosts:" in answer
    assert "api.example" in answer
    assert "Verdict:" in answer


def test_policy_refuses_and_says_why():
    answer = browser.run("policy", REACHES,
                         'forbid running "curl"   -- ask before adding one\n')
    assert "REFUSED" in answer
    assert "ask before adding one" in answer
    assert "would not run" in answer


def test_a_policy_that_passes_says_so():
    answer = browser.run("policy", CLEAN, 'forbid running "sudo"\n')
    assert "satisfies every rule" in answer


def test_a_broken_policy_blames_the_policy():
    answer = browser.run("policy", CLEAN, "forbid flying to the moon\n")
    assert "policy itself does not parse" in answer


def test_approve_shows_the_capability_set():
    payload = json.loads(browser.run("approve", REACHES))
    assert payload["programs"] == ["curl"]
    assert payload["reaches"] == ["api.example"]


def test_compare_takes_the_current_script_first():
    """Reversing these reported a poisoned script as a list of narrowings,
    which reads as reassuring and is exactly backwards."""
    poisoned = REACHES + ('run "curl" with "https://telemetry.example" '
                          "within 30 seconds\n")
    answer = browser.compare(poisoned, REACHES)
    assert "REFUSED: it can now reach telemetry.example" in answer

    reverse = browser.compare(REACHES, poisoned)
    assert "Nothing widened" in reverse


def test_diagnose_carries_the_repair():
    answer = browser.run("diagnose", BROKEN)
    assert "missing-then" in answer
    assert "repair (high)" in answer
    assert "if 1 is 1 then" in answer


def test_diagnose_says_when_there_is_no_repair():
    answer = browser.run("diagnose", 'use (name) for the go\n')
    assert "no repair" in answer


def test_repair_returns_the_fixed_source():
    payload = json.loads(browser.run("repair", BROKEN))
    assert payload["source"].startswith("if 1 is 1 then")
    assert "1 repair" in payload["note"]


def test_repair_leaves_alone_what_it_cannot_be_sure_of():
    payload = json.loads(browser.run("repair", 'use (name) for the go\n'))
    assert payload["source"] == 'use (name) for the go\n'
    assert "Nothing frost was sure enough about" in payload["note"]


def test_the_repair_loop_is_the_command_line_one():
    """Imported, not reimplemented. A second copy of the repair rules would
    drift, which is the whole reason the page runs real Python."""
    from frostlang import diagnostics, cli
    assert cli.repair_until_stuck is diagnostics.repair_until_stuck


def test_an_unknown_action_says_so_rather_than_raising():
    assert "unknown action" in browser.run("teleport", CLEAN)


@pytest.mark.parametrize("action", sorted(browser.ACTIONS))
def test_every_action_survives_an_empty_script(action):
    """A page loads with an empty box more often than anyone plans for."""
    answer = browser.run(action, "", "")
    assert isinstance(answer, str)
    assert "Traceback" not in answer


def test_the_browser_module_needs_no_operating_system():
    """The claim the whole panel rests on: everything worth demonstrating is
    static analysis, and static analysis needs no processes, filesystem or
    network."""
    import ast as pyast
    import os
    source = open(os.path.join(os.path.dirname(browser.__file__),
                               "browser.py")).read()
    imported = set()
    for node in pyast.walk(pyast.parse(source)):
        if isinstance(node, pyast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, pyast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert not (imported & {"os", "subprocess", "socket", "shutil",
                            "tempfile", "pathlib"}), imported
