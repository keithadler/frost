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
    assert any(x.severity == "forbid" and "rm" in x.what for x in f)


def test_policy_allows_the_same_program_with_other_arguments():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "rm" with "one file.txt"'), rules)
    assert not [x for x in f if x[0] == "forbid"]


def test_policy_blocks_protected_paths():
    rules = parse_policy(POLICY)
    f = check(caps_for('put "x" into file "/etc/passwd"'), rules)
    assert any("writing to /etc/passwd" in x.what for x in f)


def test_policy_warning_does_not_block():
    rules = parse_policy(POLICY)
    f = check(caps_for('run "curl" with "-s", "http://x" within 5 seconds'),
              rules)
    assert [x for x in f if x[0] == "warn"]
    assert not [x for x in f if x[0] == "forbid"]


def test_missing_timeout_is_caught():
    rules = parse_policy('require timeout on "curl"')
    f = check(caps_for('run "curl" with "http://x"'), rules)
    assert any("no timeout" in x.what for x in f)


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
    assert any("without being checked" in x.what for x in f)


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


# ------------------------------------------------ reading the environment
#
# Setting a variable had a rule and reading one did not, which is the wrong
# way round: what a script *takes* from the environment is where the
# credentials are.

def test_reading_an_environment_variable_can_be_forbidden():
    caps = caps_for('put the environment variable "AWS_SECRET_ACCESS_KEY" '
                    "into k\nput k\n")
    findings = check(caps, parse_policy(
        'forbid reading the environment "AWS_*"\n'))
    assert findings
    assert "AWS_SECRET_ACCESS_KEY" in findings[0].what


def test_an_unrelated_variable_is_left_alone():
    caps = caps_for('put the environment variable "PATH" into p\nput p\n')
    assert check(caps, parse_policy(
        'forbid reading the environment "AWS_*"\n')) == []


def test_the_environment_can_have_an_allow_list():
    caps = caps_for('put the environment variable "PATH" into p\n'
                    'put the environment variable "AWS_SECRET" into k\n'
                    "put p & k\n")
    findings = check(caps, parse_policy(
        'require reading only the environment "PATH", "HOME"\n'))
    assert len(findings) == 1
    assert "AWS_SECRET" in findings[0].what
    assert "allow-list" in findings[0].what


def test_a_name_built_at_runtime_fails_the_allow_list_closed():
    """Same rule as everywhere else: cannot be shown to be allowed is not
    allowed."""
    caps = caps_for('put the standard input into wanted\n'
                    "put the environment variable wanted into v\nput v\n")
    findings = check(caps, parse_policy(
        'require reading only the environment "PATH"\n'))
    assert findings
    assert "named at runtime" in findings[0].what


def test_a_runtime_name_also_trips_a_deny_rule():
    caps = caps_for('put the standard input into wanted\n'
                    "put the environment variable wanted into v\nput v\n")
    assert check(caps, parse_policy(
        'forbid reading the environment "AWS_*"\n'))


def test_an_environment_allow_list_needs_names():
    with pytest.raises(PolicyError) as e:
        parse_policy("require reading only the environment everything\n")
    assert "name the variables in quotes" in str(e.value)


def test_the_rule_carries_its_comment():
    caps = caps_for('put the environment variable "AWS_KEY" into k\nput k\n')
    findings = check(caps, parse_policy(
        'forbid reading the environment "AWS_*"  -- use the keystore\n'))
    assert findings[0].hint == "use the keystore"


def test_reading_and_setting_are_separate_rules():
    """`forbid setting "X"` never covered reads, and a policy that looked
    like it did would be worse than one that plainly does not."""
    caps = caps_for('put the environment variable "TOKEN" into t\nput t\n')
    assert check(caps, parse_policy('forbid setting "TOKEN"\n')) == []
    assert check(caps, parse_policy(
        'forbid reading the environment "TOKEN"\n'))


# ------------------------------------------------------- the allow-list forms
#
# Hosts had `require reaching only` and environment reads had `require reading
# only the environment`. Programs, the capability that matters most, were
# deny-shaped only: a policy could name what it feared and never what it
# trusted. A deny-list cannot be completed, because it is a list of the
# programs somebody thought of.

def test_a_program_allow_list_permits_what_it_names():
    rules = parse_policy('require running only "git", "npm"')
    assert check(caps_for('run "git" with "status"'), rules) == []
    assert check(caps_for('run "npm" with "ci"'), rules) == []


def test_a_program_allow_list_refuses_everything_else():
    rules = parse_policy('require running only "git"')
    [finding] = check(caps_for('run "curl" with "https://x.example"'), rules)
    assert finding.severity == "forbid"
    assert "running curl" in finding.what
    assert "allow-list" in finding.what


def test_a_program_allow_list_takes_patterns():
    rules = parse_policy('require running only "git", "docker-*"')
    assert check(caps_for('run "docker-compose" with "up"'), rules) == []
    assert check(caps_for('run "dockerd"'), rules)


def test_a_program_named_at_runtime_is_refused_rather_than_assumed():
    """The same answer the host allow-list gives. A name that does not exist
    until the run cannot be shown to be on the list, and assuming it is on the
    list is the failure that makes an allow-list decorative."""
    rules = parse_policy('require running only "git"')
    [finding] = check(caps_for('put the environment variable "T" into tool\n'
                               "run tool"), rules)
    assert finding.severity == "forbid"
    assert "named at runtime" in finding.what


def test_a_program_allow_list_without_quotes_is_refused_at_parse_time():
    with pytest.raises(PolicyError) as caught:
        parse_policy("require running only git")
    assert "in quotes" in str(caught.value)


def test_the_allow_list_composes_by_intersection():
    """Two policies concatenated must narrow, never widen. Both rules apply,
    so a program has to satisfy each of them."""
    rules = parse_policy('require running only "git", "npm"\n'
                         'require running only "git"')
    assert check(caps_for('run "git" with "status"'), rules) == []
    assert check(caps_for('run "npm" with "ci"'), rules)


# ------------------------------------------------- escapes a rule can act on

def test_a_shell_escape_can_be_refused_and_not_only_reported():
    """The gap this closes: `forbid running "sh"` matches the program frost
    spawns, which for `xargs sh -c` is xargs, so the direct rule never fired
    on the indirect form. The manifest named an escape that no rule could
    count."""
    named = parse_policy('forbid running "sh"')
    indirect = 'run "xargs" with "-I", "{}", "sh", "-c", "echo {}"'
    assert check(caps_for(indirect), named) == [], \
        "the program rule still does not see through the launcher"

    counted = parse_policy("require at most 0 shell escapes")
    [finding] = check(caps_for(indirect), counted)
    assert finding.severity == "forbid"
    assert "shell escapes" in finding.what


def test_the_direct_escape_is_counted_too():
    rules = parse_policy("require at most 0 shell escapes")
    assert check(caps_for('run "sh" with "-c", "id"'), rules)


def test_a_script_with_no_escape_passes_the_count():
    rules = parse_policy("require at most 0 shell escapes")
    assert check(caps_for('run "git" with "status"'), rules) == []


def test_the_count_and_the_findings_cannot_disagree():
    """Both read the same detector. Two answers to one question is how a
    manifest ends up describing something no rule can refuse."""
    from frostlang.audit import shell_escapes, find_dangers

    source = ('run "sh" with "-c", "id"\n'
              'run "xargs" with "sh", "-c", "id"\n'
              'run "find" with ".", "-exec", "rm", "{}", ";"\n')
    caps = caps_for(source)
    escapes = shell_escapes(caps)
    titled = [f.title for f in find_dangers(caps)]
    assert len(escapes) == 3
    for _, title, _ in escapes:
        assert title in titled
