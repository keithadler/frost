"""Policy files: rules checked against the tree before anything runs."""

import pytest

from frostlang.audit import parse_policy, check, PolicyError

from helpers import caps_for


POLICY = '''
forbid running "rm" with "-rf"
warn running "curl"
forbid writing to "/etc/*"
require timeout on "curl"
require every command to be checked
'''


def test_policy_blocks_dangerous_command():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "rm" with "-rf", "/tmp/x"'), rules)
    assert any(sev == "forbid" and "rm" in what for sev, what, _ in f)


def test_policy_allows_the_same_program_with_other_arguments():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "rm" with "one file.txt"'), rules)
    assert not [x for x in f if x[0] == "forbid"]


def test_policy_blocks_protected_paths():
    rules = parse_policy(POLICY)
    f = check(caps_for('put "x" into file "/etc/passwd"'), rules)
    assert any("writing to /etc/passwd" in what for _, what, _ in f)


def test_policy_warning_does_not_block():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "curl" with "-s", "http://x" within 5 seconds'),
              rules)
    assert [x for x in f if x[0] == "warn"]
    assert not [x for x in f if x[0] == "forbid"]


def test_missing_timeout_is_caught():
    rules = parse_policy('require timeout on "curl"')
    f = check(caps_for('run "curl" with "http://x"'), rules)
    assert any("no timeout" in what for _, what, _ in f)


def test_checked_rule_understands_a_result_check():
    """`try to run` followed by reading the result is correct, not a violation."""
    rules = parse_policy("require every command to be checked")
    src = '''
    try to run "ping" with "-c", "1", "example.com"
    if the result is not 0 then
        put "unreachable"
    end if
    '''
    assert check(caps_for(src), rules) == []


def test_checked_rule_catches_a_genuinely_ignored_failure():
    rules = parse_policy("require every command to be checked")
    src = '''
    try to run "ping" with "-c", "1", "example.com"
    put "carrying on regardless"
    '''
    f = check(caps_for(src), rules)
    assert any("without being checked" in what for _, what, _ in f)


def test_policy_line_numbers_point_at_the_script():
    rules = parse_policy('forbid running "rm" with "-rf"')
    src = 'put "a"\nput "b"\nrun "rm" with "-rf", "x"'
    f = check(caps_for(src), rules)
    assert f[0][2] == 3


def test_unreadable_policy_line_is_reported():
    with pytest.raises(PolicyError):
        parse_policy("please do not delete anything")


def test_a_result_check_inside_a_handler_counts_as_checking_it():
    """Factoring the check into a helper is good practice; flagging it as an
    ignored failure would punish exactly that."""
    rules = parse_policy("require every command to be checked")
    src = '''
    to check outcome with label
        if the result is 0 then return "ok"
        return "failed"
    end check outcome

    try to run "make" with "build"
    check outcome with "build"
    '''
    assert check(caps_for(src), rules) == []


def test_the_same_holds_for_the_expression_form():
    rules = parse_policy("require every command to be checked")
    src = '''
    to check outcome with label
        if the result is 0 then return "ok"
        return "failed"
    end check outcome

    try to run "make" with "build"
    put the check outcome of "build" into outcome
    '''
    assert check(caps_for(src), rules) == []


def test_a_handler_that_ignores_the_result_is_still_caught():
    """The check must follow the call, not merely notice that one happened."""
    rules = parse_policy("require every command to be checked")
    src = '''
    to announce with label
        put label
    end announce

    try to run "make" with "build"
    announce with "build"
    '''
    assert [f for f in check(caps_for(src), rules) if f[0] == "forbid"]


def test_a_mutually_recursive_pair_does_not_hang_the_check():
    rules = parse_policy("require every command to be checked")
    src = '''
    to ping with n
        pong with n
    end ping

    to pong with n
        ping with n
    end pong

    try to run "make"
    ping with 1
    '''
    assert [f for f in check(caps_for(src), rules) if f[0] == "forbid"]


def test_policy_comments_are_ignored():
    rules = parse_policy('-- a note\n# another\nforbid running "dd"')
    assert len(rules) == 1
