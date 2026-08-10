"""Structured diagnostics, repairs, and the loop they exist for.

frost's errors were written for a person reading at 3am. The thing that wrote
the script is not a person, and telling it `expected 'then' but found end of
line` makes it parse English and guess an edit. These tests are about the
other audience: same information, as data, with the edit attached where frost
already knew it.

The property that matters most is that a repair cannot make a script worse.
It is checked directly — every repair is applied and the result re-parsed —
rather than assumed from the fact that the code looks careful.
"""

import json
import os

import pytest

from frostlang import diagnostics
from frostlang.diagnostics import (Diagnostic, Repair, HIGH, LIKELY, GUESS,
                                   apply_repairs, slug, nearest,
                                   line_and_column)
from frostlang.parser import parse, ParseError
from frostlang.lexer import LexError
from frostlang.audit import parse_policy, check, audit
from frostlang import cli

from helpers import REPO


def diagnose(source):
    return cli.collect_diagnostics("s.frost", source)


def only(source):
    diags = diagnose(source)
    assert len(diags) == 1, [d.code for d in diags]
    return diags[0]


# ------------------------------------------------------------ the schema

def test_a_syntax_error_becomes_one_diagnostic():
    d = only("if x is 1\n    put 1\nend if")
    assert d.severity == "error"
    assert d.line == 1
    assert "then" in d.message


def test_a_diagnostic_serialises_to_stable_json():
    d = only('run "ls -la"')
    payload = d.as_dict()
    assert set(payload) >= {"severity", "code", "message", "line", "repairs"}
    json.dumps(payload)             # must be serialisable


def test_a_report_carries_the_schema_version():
    payload = diagnostics.report("s.frost", diagnose("if x is 1"), False, 2)
    assert payload["schema"] == diagnostics.SCHEMA_VERSION
    assert payload["ok"] is False
    assert payload["exit"] == 2


def test_a_column_is_reported_where_the_parser_knows_it():
    d = only("put 0 into count\nif count is 0\n    put 1\nend if")
    assert d.line == 2
    assert d.column and d.column > 1


@pytest.mark.parametrize("message,expected", [
    ("there is no variable named 'total cost'", "variable-named"),
    ("there is no variable named 'other'", "variable-named"),
    ("a timeout needs a unit", "timeout-needs-unit"),
    ("expected 'then' but found end of line", "expected-found-end-line"),
])
def test_a_code_is_stable_across_the_variable_parts(message, expected):
    assert slug(message) == expected


def test_two_errors_of_the_same_kind_share_a_code():
    a = only('run "ls -la"')
    b = only('run "wc -l"')
    assert a.code == b.code == "run-takes-a-program-name"


def test_a_runtime_error_also_becomes_a_diagnostic():
    """Undefined names are found when the line runs, not when it parses, so
    they arrive through a different door and must land in the same shape."""
    from frostlang.interp import Interpreter, FrostError
    source = "put missing thing"
    interpreter = Interpreter()
    with pytest.raises(FrostError) as e:
        interpreter.run_program(parse(source))
    e.value.candidates = sorted(interpreter.globals)
    d = diagnostics.from_error(e.value, source)
    assert d.severity == "error"
    assert d.line == 1
    assert "no variable named" in d.message


def test_line_and_column_from_an_offset():
    source = "abc\ndefgh\nij"
    assert line_and_column(source, 0) == (1, 1)
    assert line_and_column(source, 4) == (2, 1)
    assert line_and_column(source, 6) == (2, 3)
    assert line_and_column(source, None) == (None, None)


# ---------------------------------------------------------- the repairs

MECHANICAL = [
    ('run "ls -la"', 'run "ls" with "-la"', HIGH),
    ("if x is 1\n    put 1\nend if", "if x is 1 then", HIGH),
    ('run "sleep" with "1" within 5', 'within 5 seconds', LIKELY),
    ("put 5 into global total", "put 5 into the global total", HIGH),
]


@pytest.mark.parametrize("source,expected,confidence", MECHANICAL,
                         ids=[s.split("\n")[0][:28] for s, _, _ in MECHANICAL])
def test_the_repair_contains_the_corrected_line(source, expected, confidence):
    d = only(source)
    assert d.repairs, f"no repair offered for: {source}"
    repair = d.repairs[0]
    assert expected in repair.text
    assert repair.confidence == confidence


def test_a_repair_keeps_the_original_indentation():
    d = only('if 1 is 1 then\n    run "ls -la"\nend if')
    assert d.repairs[0].text.startswith("    ")


def test_every_repair_explains_itself():
    for source, _, _ in MECHANICAL:
        for repair in only(source).repairs:
            assert repair.why, f"a repair with no explanation: {source}"


def test_an_unknown_handler_suggests_a_near_name():
    source = ("to check outcome with n\n    return n\nend check outcome\n"
              "put the chek outcome of 1")
    with pytest.raises(ParseError) as e:
        parse(source)
    d = diagnostics.from_error(e.value, source)
    assert d.code == "no-handler-named"
    assert any("check outcome" in r.text for r in d.repairs)
    assert all(r.confidence == GUESS for r in d.repairs), (
        "a name that merely looks close must never be applied unattended")


def test_a_name_that_is_nothing_like_one_that_exists_gets_no_guess():
    source = ("to helper\n    put 1\nend helper\n"
              "put the zzzzzzzz of 1")
    with pytest.raises(ParseError) as e:
        parse(source)
    assert diagnostics.from_error(e.value, source).repairs == []


def test_nearest_only_suggests_close_matches():
    assert nearest("chek", ["check", "other"]) == ["check"]
    assert nearest("zzzz", ["check", "other"]) == []
    assert nearest("check", []) == []


def test_an_error_with_no_mechanical_fix_offers_no_repair():
    """Silence is correct here. A wrong repair costs an agent a round trip
    and teaches it the wrong grammar."""
    d = only("put the frobnitz")
    assert d.repairs == []


# ------------------------------------------------- applying them safely

def test_repairs_apply_back_to_front():
    source = "line one\nline two\nline three"
    diags = [
        Diagnostic("error", "x", "m", 1, repairs=[
            Repair("replace-line", 1, "ONE", HIGH)]),
        Diagnostic("error", "x", "m", 3, repairs=[
            Repair("replace-line", 3, "THREE", HIGH)]),
    ]
    out, applied = apply_repairs(source, diags, HIGH)
    assert out == "ONE\nline two\nTHREE"
    assert len(applied) == 2


def test_only_repairs_at_or_above_the_threshold_are_applied():
    diags = [Diagnostic("error", "x", "m", 1, repairs=[
        Repair("replace-line", 1, "NEW", GUESS)])]
    out, applied = apply_repairs("old", diags, HIGH)
    assert (out, applied) == ("old", [])
    out, applied = apply_repairs("old", diags, GUESS)
    assert out == "NEW"


def test_two_repairs_on_one_line_take_the_first():
    """Two edits to the same line cannot both be right, and applying either
    blindly would be guessing."""
    diags = [Diagnostic("error", "x", "m", 1, repairs=[
        Repair("replace-line", 1, "FIRST", HIGH),
        Repair("replace-line", 1, "SECOND", HIGH)])]
    out, applied = apply_repairs("old", diags, HIGH)
    assert (out, len(applied)) == ("FIRST", 1)


def test_an_insert_adds_a_line():
    diags = [Diagnostic("error", "x", "m", 2, repairs=[
        Repair("insert-line", 2, "end if", LIKELY)])]
    out, _ = apply_repairs("a\nb", diags, LIKELY)
    assert out == "a\nend if\nb"


def test_nothing_to_apply_leaves_the_source_untouched():
    out, applied = apply_repairs("unchanged", [], HIGH)
    assert (out, applied) == ("unchanged", [])


# -------------------------------------------------------------- the loop

def test_the_loop_fixes_several_errors_in_one_go():
    """A recursive-descent parser stops at the first error, so fixing it
    reveals the next. One pass would fail on any script with two mistakes."""
    source = ('put 0 into error count\n'
              'if error count is 0\n'
              '    run "ls -la"\n'
              'end if\n'
              'put 5 into global total\n')
    repaired, applied = cli.repair_until_stuck(source)
    assert len(applied) == 3
    parse(repaired)                     # raises if it did not work


def test_the_loop_preserves_everything_it_did_not_touch():
    source = ('-- a comment\nput 0 into count\nif count is 0\n'
              '    put "x"\nend if\n')
    repaired, _ = cli.repair_until_stuck(source)
    assert repaired.startswith("-- a comment\n")
    assert 'put "x"' in repaired


def test_the_loop_stops_when_it_stops_making_progress():
    """Bounded, and it gives up rather than looping on an error it cannot
    move."""
    source = "put the frobnitz\n"
    repaired, applied = cli.repair_until_stuck(source)
    assert (repaired, applied) == (source, [])


def test_a_script_that_already_parses_is_left_alone():
    source = 'put "fine"\n'
    assert cli.repair_until_stuck(source) == (source, [])


@pytest.mark.parametrize("source", [
    'run "ls -la"',
    "if x is 1\n    put 1\nend if",
    "put 5 into global total",
    'put 0 into n\nif n is 0\n    run "git status"\nend if',
    'run "ls -la"\nrun "wc -l"',
])
def test_a_repair_never_leaves_a_script_worse(source):
    """The property the whole feature rests on. Either the repaired script
    parses, or its first error is strictly later than before."""
    before = cli.first_error_line(source)
    repaired, applied = cli.repair_until_stuck(source)
    after = cli.first_error_line(repaired)
    if applied:
        assert after is None or after > before, (
            f"repair moved the error backwards: {before} -> {after}")


def test_the_loop_is_bounded():
    assert cli.MAX_REPAIR_PASSES <= 20


# ------------------------------------------------------- policy hints

def test_a_rule_carries_its_trailing_comment_as_a_hint():
    """Policy authors already write the explanation as a comment. Making it
    the hint means every policy that exists gets better output for free."""
    [rule] = parse_policy(
        'forbid running "sudo"    -- the deploy role already has what it needs')
    assert rule.hint == "the deploy role already has what it needs"


def test_a_hash_comment_works_too():
    [rule] = parse_policy('forbid running "sudo"   # use the deploy role')
    assert rule.hint == "use the deploy role"


def test_a_rule_without_a_comment_has_no_hint():
    [rule] = parse_policy('forbid running "sudo"')
    assert rule.hint == ""


def test_a_comment_on_its_own_line_is_not_a_rule_hint():
    rules = parse_policy('-- a section header\nforbid running "sudo"')
    assert len(rules) == 1
    assert rules[0].hint == ""


def test_the_hint_reaches_the_finding():
    rules = parse_policy('forbid running "sudo"  -- use the deploy role')
    [finding] = check(audit(parse('run "sudo" with "make"')), rules)
    assert finding.hint == "use the deploy role"


def test_the_hint_survives_a_counting_rule():
    rules = parse_policy("require at least 1 cleanup  -- always release locks")
    [finding] = check(audit(parse('put "x"')), rules)
    assert finding.hint == "always release locks"


def test_a_policy_finding_still_unpacks_in_order():
    rules = parse_policy('forbid running "sudo"')
    [finding] = check(audit(parse('run "sudo"')), rules)
    severity, what, line, hint = finding
    assert severity == "forbid"
    assert finding.severity == severity and finding.what == what


def test_the_hint_becomes_the_diagnostic_hint():
    rules = parse_policy('forbid running "sudo"  -- use the deploy role')
    source = 'run "sudo" with "make"'
    [finding] = check(audit(parse(source)), rules)
    d = diagnostics.from_policy_finding(finding, source)
    assert d.hint == "use the deploy role"
    assert d.severity == "error"


def test_a_rule_without_a_hint_gets_a_useful_default():
    rules = parse_policy('forbid running "sudo"')
    source = 'run "sudo"'
    [finding] = check(audit(parse(source)), rules)
    assert "refused by the policy" in diagnostics.from_policy_finding(
        finding, source).hint


def test_the_shipped_policy_still_parses():
    with open(os.path.join(REPO, "examples", "production.policy")) as fh:
        assert parse_policy(fh.read())


# ------------------------------------------------- every code is accounted for

# A code with a derivable fix and no repair is a silent gap: the diagnostic
# still reports, so nothing looks broken, and an agent that could have been
# handed the edit gets prose instead. `wait-needs-a-unit` shipped that way
# despite `timeout-needs-a-unit` — its exact twin — having had a repair for
# two releases. Anything genuinely underivable belongs in the list below, with
# the reason, so the decision is recorded rather than forgotten.
UNDERIVABLE = {
    # The path has to come from a person: the parser knows the import is not a
    # literal, never which file was meant.
    "module-path-must-be-literal",
    # Which handlers to import is the author's intent, not a fact about text.
    "import-needs-a-name-list",
    # A misspelled capability could be any of the verbs; suggesting one would
    # be a guess dressed as a fix.
    "unknown-capability",
}


def parser_codes():
    """Every `code=` the front end can emit."""
    import re
    codes = set()
    for name in ("parser.py", "lexer.py", "interp.py"):
        path = os.path.join(REPO, "frostlang", name)
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            codes.update(re.findall(r'code="([a-z-]+)"', fh.read()))
    return codes


def test_the_scanner_finds_the_codes():
    """Without this, a broken regex would empty the check below."""
    found = parser_codes()
    assert len(found) >= 6, f"only found {sorted(found)}"
    assert "missing-then" in found


@pytest.mark.parametrize("code", sorted(parser_codes()))
def test_every_diagnostic_code_offers_a_repair_or_says_why_not(code):
    import re
    with open(os.path.join(REPO, "frostlang", "diagnostics.py")) as fh:
        source = fh.read()
    handled = set(re.findall(r'code == "([a-z-]+)"', source))
    handled.update(re.findall(r'"([a-z-]+)"', 
                              "".join(re.findall(r'code in \(([^)]*)\)',
                                                 source))))
    assert code in handled or code in UNDERIVABLE, (
        f"{code!r} has no repair and is not listed as underivable. Either add "
        f"one to repairs_for(), or add it to UNDERIVABLE with the reason.")


def test_the_wait_repair_supplies_the_unit():
    """The twin of the timeout repair, and derived the same way."""
    from frostlang.diagnostics import from_error
    try:
        parse("wait 3\n")
    except ParseError as e:
        diagnostic = from_error(e, "wait 3\n")
    assert diagnostic.code == "wait-needs-a-unit"
    assert diagnostic.repairs, "no repair for a fix the parser already knew"
    assert diagnostic.repairs[0].text == "wait 3 seconds"
    assert diagnostic.repairs[0].confidence == LIKELY


def test_the_wait_repair_keeps_the_indentation():
    from frostlang.diagnostics import from_error
    source = "repeat 2 times\n    wait 3\nend repeat\n"
    try:
        parse(source)
    except ParseError as e:
        diagnostic = from_error(e, source)
    assert diagnostic.repairs[0].text == "    wait 3 seconds"
