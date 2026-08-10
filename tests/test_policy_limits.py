"""Quantitative policy rules — the business-rule half of the policy language.

The original rules ask whether something appears at all. These ask how much of
it there is, which is what an organisation's rule usually says: no more than
three files written, at least one cleanup block, curl gets a deadline and no
more than thirty seconds of one.

All of it is still decided before anything runs, from the parse tree.
"""

import pytest

from frostlang.audit import (parse_policy, check, PolicyError, count_lines,
                             format_duration, literal_number)
from frostlang.parser import parse

from helpers import caps_for


def hits(src, policy):
    return check(caps_for(src), parse_policy(policy))


def blocked(src, policy):
    return [f.what for f in hits(src, policy) if f.severity == "forbid"]


TWO_COMMANDS = 'run "git" with "status"\nrun "make"'


# --------------------------------------------------------------- upper bounds

def test_more_than_blocks_when_the_count_is_exceeded():
    assert blocked(TWO_COMMANDS, "forbid more than 1 commands")


def test_more_than_allows_the_limit_itself():
    assert not blocked(TWO_COMMANDS, "forbid more than 2 commands")


def test_at_most_is_the_same_rule_said_the_other_way():
    assert blocked(TWO_COMMANDS, "require at most 1 command")
    assert not blocked(TWO_COMMANDS, "require at most 2 commands")


def test_the_message_names_the_actual_and_allowed_counts():
    [what] = blocked(TWO_COMMANDS, "forbid more than 1 command")
    assert what == "2 command, at most 1 allowed"


def test_the_violation_points_at_the_command_that_crossed_the_limit():
    src = 'run "a"\nrun "b"\nrun "c"\nrun "d"'
    [found] = hits(src, "forbid more than 2 commands")
    line = found.line
    assert line == 3, "should point at the third command, not the script"


def test_forbid_any_is_a_limit_of_zero():
    assert blocked('put "x" into file "/tmp/a"',
                   "forbid any files written")
    assert not blocked('put "x"', "forbid any files written")


# --------------------------------------------------------------- lower bounds

def test_at_least_blocks_when_there_are_too_few():
    assert blocked('put "x"', "require at least 1 cleanup")


def test_at_least_is_satisfied():
    src = 'ensure\n    put "tidy"\nend ensure\nput "x"'
    assert not blocked(src, "require at least 1 cleanup")


def test_fewer_than_is_the_same_rule_said_the_other_way():
    assert blocked('put "x"', "forbid fewer than 1 cleanup")


def test_a_shortfall_message_names_what_is_required():
    [what] = blocked('put "x"', "require at least 2 cleanups")
    assert what == "0 cleanups, at least 2 required"


def test_a_shortfall_has_no_line_because_it_is_about_the_whole_script():
    [found] = hits('put "x"', "require at least 1 cleanup")
    line = found.line
    assert line == 0


# --------------------------------------------------------------------- ranges

def test_a_range_accepts_a_count_inside_it():
    assert not blocked(TWO_COMMANDS, "require between 1 and 3 commands")


def test_a_range_rejects_a_count_below_it():
    assert blocked('put "x"', "require between 1 and 3 commands")


def test_a_range_rejects_a_count_above_it():
    src = 'run "a"\nrun "b"\nrun "c"\nrun "d"'
    assert blocked(src, "require between 1 and 3 commands")


def test_a_backwards_range_is_rejected_at_parse_time():
    with pytest.raises(PolicyError) as e:
        parse_policy("require between 5 and 2 commands")
    assert "greater than" in str(e.value)


# ------------------------------------------------------------ counting nouns

CASES = [
    ("commands", 'run "a"\nrun "b"', 2),
    ("network commands", 'run "curl" with "x"\nrun "ls"', 1),
    ("files read", 'put file "a.txt"\nput file "b.txt"', 2),
    ("files written", 'put "x" into file "a.txt"', 1),
    ("files deleted", 'delete file "a.txt"\ndelete file "b.txt"', 2),
    ("environment reads", 'put the environment variable "HOME"', 1),
    ("environment writes", 'put "x" into the environment variable "CC"', 1),
    ("folder changes", 'put "/tmp" into the current folder', 1),
    ("cleanups", 'ensure\n    put "x"\nend ensure\nput "y"', 1),
    ("unchecked commands", 'try to run "a"\nrun "b"', 1),
    ("commands without a timeout", 'run "a"\nrun "b" within 5 seconds', 1),
    ("runtime names", 'run "cat" with "n"\nput it into t\nrun t', 1),
    ("handlers", 'to helper\n    put "x"\nend helper\nhelper', 1),
]


@pytest.mark.parametrize("noun,src,expected", CASES)
def test_each_countable_noun_counts_the_right_thing(noun, src, expected):
    rules = parse_policy(f"forbid more than {expected} {noun}")
    assert not blocked(src, f"forbid more than {expected} {noun}"), \
        f"{noun}: {expected} should be allowed"
    assert blocked(src, f"forbid more than {expected - 1} {noun}"), \
        f"{noun}: {expected} should exceed a limit of {expected - 1}"


@pytest.mark.parametrize("noun,src,expected", CASES)
def test_the_count_matches_the_capability_list(noun, src, expected):
    [rule] = parse_policy(f"forbid more than 0 {noun}")
    assert len(count_lines(caps_for(src), rule.subject, rule.detail)) \
        == expected


def test_singular_and_plural_nouns_both_work():
    assert parse_policy("forbid more than 1 command")[0].subject == "commands"
    assert parse_policy("forbid more than 1 commands")[0].subject == "commands"


def test_pipes_are_counted_by_stage_line():
    src = 'pipe\n    run "a"\n    run "b"\nend pipe'
    assert not blocked(src, "forbid more than 2 pipes")
    assert blocked(src, "forbid more than 1 pipe")


# ------------------------------------------------------------- runs of a name

def test_runs_of_a_program_are_counted_separately():
    src = 'run "curl" with "a"\nrun "curl" with "b"\nrun "ls"'
    assert blocked(src, 'forbid more than 1 runs of "curl"')
    assert not blocked(src, 'forbid more than 2 runs of "curl"')


def test_runs_of_accepts_a_glob():
    src = 'run "git" with "status"\nrun "gitk"'
    assert blocked(src, 'forbid more than 1 runs of "git*"')


def test_runs_of_can_require_a_minimum():
    assert blocked('put "x"', 'require at least 1 run of "git"')
    assert not blocked('run "git"', 'require at least 1 run of "git"')


def test_a_program_built_at_runtime_is_not_counted_as_a_named_run():
    src = 'run "cat" with "n"\nput it into tool\nrun tool'
    assert not blocked(src, 'forbid any runs of "curl"')
    assert blocked(src, "forbid any runtime names")


# ------------------------------------------------------------ timeout bounds

def test_a_timeout_over_the_limit_is_blocked():
    assert blocked('run "curl" with "x" within 60 seconds',
                   'require timeout on "curl" of at most 30 seconds')


def test_a_timeout_at_the_limit_is_allowed():
    assert not blocked('run "curl" with "x" within 30 seconds',
                       'require timeout on "curl" of at most 30 seconds')


def test_units_are_reconciled_between_script_and_policy():
    """The script says minutes, the policy says seconds; both are compared."""
    assert blocked('run "curl" with "x" within 2 minutes',
                   'require timeout on "curl" of at most 30 seconds')
    assert not blocked('run "curl" with "x" within 500 milliseconds',
                       'require timeout on "curl" of at most 1 second')


def test_a_timeout_below_a_required_minimum_is_blocked():
    """Too short a deadline is its own failure mode: it kills healthy work."""
    assert blocked('run "curl" with "x" within 100 milliseconds',
                   'require timeout on "curl" of at least 1 second')


def test_a_timeout_range_accepts_the_middle():
    policy = 'require timeout on "curl" between 1 and 60 seconds'
    assert not blocked('run "curl" with "x" within 30 seconds', policy)
    assert blocked('run "curl" with "x" within 90 seconds', policy)
    assert blocked('run "curl" with "x" within 100 milliseconds', policy)


def test_a_bounded_rule_still_requires_a_timeout_at_all():
    assert blocked('run "curl" with "x"',
                   'require timeout on "curl" of at most 30 seconds')


def test_a_runtime_timeout_cannot_be_checked_and_is_refused():
    """A deadline computed at runtime is unknowable here, and saying so beats
    guessing that it is fine."""
    src = 'put 5 into limit\nrun "curl" within limit seconds'
    [what] = blocked(src, 'require timeout on "curl" of at most 30 seconds')
    assert "computed at runtime" in what


def test_the_message_reports_both_durations_readably():
    [what] = blocked('run "curl" with "x" within 2 minutes',
                     'require timeout on "curl" of at most 30 seconds')
    assert "waits up to 2 minutes" in what
    assert "limit is 30 seconds" in what


def test_a_bound_applies_through_a_glob():
    assert blocked('run "wget" with "x" within 5 minutes',
                   'require timeout on "*" of at most 1 minute')


def test_the_bare_timeout_rule_still_works():
    assert blocked('run "curl" with "x"', 'require timeout on "curl"')
    assert not blocked('run "curl" with "x" within 5 seconds',
                       'require timeout on "curl"')


# ----------------------------------------------------------------- severity

def test_a_warning_count_does_not_block():
    findings = hits(TWO_COMMANDS, "warn more than 1 command")
    assert [f[0] for f in findings] == ["warn"]


def test_a_warning_and_a_refusal_can_coexist():
    policy = "warn more than 1 command\nforbid more than 1 commands"
    severities = sorted(f[0] for f in hits(TWO_COMMANDS, policy))
    assert severities == ["forbid", "warn"]


# ------------------------------------------------------------------ errors

def test_an_uncountable_noun_is_rejected_with_the_vocabulary():
    with pytest.raises(PolicyError) as e:
        parse_policy("forbid more than 3 bananas")
    assert "not something that can be counted" in str(e.value)


def test_every_suggested_noun_actually_parses():
    """The suggestion list used to print internal keys like 'env_reads',
    which is advice that does not work when followed."""
    from frostlang.audit import COUNT_VOCABULARY
    for phrase in COUNT_VOCABULARY:
        parse_policy(f"forbid more than 1 {phrase}")


def test_an_unknown_time_unit_is_rejected():
    with pytest.raises(PolicyError) as e:
        parse_policy('require timeout on "curl" of at most 5 fortnights')
    assert "not a time unit" in str(e.value)


def test_a_policy_error_names_its_own_line():
    with pytest.raises(PolicyError) as e:
        parse_policy('forbid running "rm"\nforbid more than 3 bananas')
    assert "line 2" in str(e.value)


def test_comments_still_work_around_the_new_rules():
    rules = parse_policy("-- a note\nforbid more than 3 commands  # trailing")
    assert len(rules) == 1


# ------------------------------------------------------------ the helpers

@pytest.mark.parametrize("seconds,expected", [
    (1, "1 second"), (30, "30 seconds"), (60, "1 minute"),
    (120, "2 minutes"), (3600, "1 hour"), (7200, "2 hours"),
    (0.5, "500 milliseconds"), (0.001, "1 millisecond"),
])
def test_durations_read_in_the_largest_whole_unit(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("src,expected", [
    ('run "x" within 30 seconds', 30),
    ('run "x" within 2 minutes', 120),
    ('run "x" within 1 hour', 3600),
    ('run "x" within 500 milliseconds', 0.5),
])
def test_a_literal_timeout_folds_to_seconds(src, expected):
    assert literal_number(parse(src)[0].timeout) == expected


def test_a_computed_timeout_does_not_fold():
    src = 'put 5 into limit\nrun "x" within limit seconds'
    assert literal_number(parse(src)[1].timeout) is None


def test_a_timeout_amount_may_come_from_a_variable():
    """A time unit is not a reserved word, so the name used to swallow it and
    `within limit seconds` would not parse at all."""
    tree = parse('put 5 into limit\nrun "x" within limit minutes')
    assert tree[1].timeout is not None


def test_a_name_ending_in_a_time_unit_still_works_outside_a_timeout():
    assert parse("put 3 into wait seconds")[0].target.name == "wait seconds"


# ------------------------------------------------------- a realistic policy

BUSINESS_RULES = """
-- What a deployment script is allowed to be.
forbid running "sudo"
forbid writing to "/etc/*"
forbid setting "PATH"

require at most 12 commands
require at most 2 files written
require at most 1 files deleted
forbid any runs of "curl"
require at least 1 cleanup

require timeout on "*" between 1 and 120 seconds
require every command to be checked
"""


def test_a_conforming_script_passes_the_whole_policy():
    src = '''
    ensure
        delete file "/tmp/deploy.lock"
    end ensure
    put "held" into file "/tmp/deploy.lock"
    run "git" with "rev-parse", "HEAD" within 10 seconds
    run "make" with "build" within 60 seconds
    '''
    assert hits(src, BUSINESS_RULES) == []


def test_the_policy_catches_each_violation_in_a_bad_script():
    src = '''
    run "sudo" with "rm" within 5 seconds
    run "curl" with "https://x" within 300 seconds
    delete file "/tmp/a"
    delete file "/tmp/b"
    put "x" into file "/etc/thing"
    '''
    reasons = blocked(src, BUSINESS_RULES)
    assert any('running "sudo"' in r for r in reasons)
    assert any("writing to /etc/thing" in r for r in reasons)
    assert any("2 files deleted, at most 1 allowed" in r for r in reasons)
    assert any('runs of "curl"' in r for r in reasons)
    assert any("limit is 2 minutes" in r for r in reasons)
    assert any("0 cleanup, at least 1 required" in r for r in reasons)
