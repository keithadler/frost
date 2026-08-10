"""Where a script reaches: checked twice, enforced elsewhere.

The static check reads the script. The runtime check reads a command's real
arguments in the moment before `execve`, which is the only place a computed
destination can be judged at all. Neither is a boundary, and the third piece
is a proxy that frost writes the configuration for rather than pretends to be.

The tests care about the seam between those three, and about the honesty of
each: what each one catches, and what it lets past.
"""

import os
import subprocess
import sys

import pytest

from frostlang import egress
from frostlang.audit import parse_policy, host_rules

from helpers import REPO


def frost(*args, cwd=None, stdin=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       input=stdin, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def project(tmp_path):
    def write(name, text):
        path = tmp_path / name
        path.write_text(text.lstrip("\n"))
        return str(path)
    write.root = tmp_path
    return write


# `echo` for the runtime tests: the host is read out of the argument whatever
# the program is, and echoing a URL reaches nothing. `curl` where the point is
# the static refusal, which happens before anything spawns.
DYNAMIC = ('put the standard input into url\n'
           'run "echo" with url\nput it\n')
DYNAMIC_NETWORK = ('put the standard input into url\n'
                   'run "curl" with "-fsS", url within 5 seconds\n')
ONLY = 'require reaching only "api.github.com"\n'


# ------------------------------------------------------- the runtime check

def test_a_computed_destination_cannot_run_under_an_allow_list(project):
    """The behaviour that made allow-lists unusable: statically unknowable is
    refused, so any dynamic URL means no allow-list at all."""
    policy = project("p.policy", ONLY)
    path = project("s.frost", DYNAMIC_NETWORK)
    status, _, err = frost("--policy", policy, path, cwd=str(project.root),
                           stdin="https://api.github.com/zen\n")
    assert status == 3
    assert "built at runtime" in err


def test_enforcing_hosts_lets_an_allowed_destination_through(project):
    """The point of moving the check: the URL is concrete at spawn, so it can
    be judged instead of assumed."""
    policy = project("p.policy", ONLY)
    path = project("s.frost", DYNAMIC)
    status, out, err = frost("--enforce-hosts", "--policy", policy, path,
                             cwd=str(project.root),
                             stdin="https://api.github.com/zen\n")
    assert status == 0, err
    assert "api.github.com" in out


def test_enforcing_hosts_refuses_one_that_is_not_allowed(project):
    policy = project("p.policy", ONLY)
    path = project("s.frost", DYNAMIC)
    status, out, err = frost("--enforce-hosts", "--policy", policy, path,
                             cwd=str(project.root),
                             stdin="https://telemetry.example/collect\n")
    assert status == 1
    assert "does not allow reaching telemetry.example" in err
    assert "api.github.com" in err, "the refusal should say what is allowed"
    assert "telemetry.example/collect" not in out


def test_a_forbidden_host_is_refused_with_the_rule_s_own_reason(project):
    policy = project("p.policy",
                     'forbid reaching "*.telemetry.example"  -- no reporting\n')
    path = project("s.frost", DYNAMIC)
    status, _, err = frost("--enforce-hosts", "--policy", policy, path,
                           cwd=str(project.root),
                           stdin="https://a.telemetry.example/x\n")
    assert status == 1
    assert "no reporting" in err


def test_a_network_command_with_no_readable_destination_fails_closed(project):
    """Same rule as the static check, now with the real arguments in hand.
    Cannot be shown to be on the list is not is."""
    policy = project("p.policy", ONLY)
    path = project("s.frost",
                   'run "curl" with "--config", "urls.txt" within 5 seconds\n')
    status, _, err = frost("--enforce-hosts", "--policy", policy, path,
                           cwd=str(project.root))
    assert status == 1
    assert "no destination frost can read" in err


def test_a_non_network_program_records_no_destination_statically():
    """`echo "https://x"` reaches nothing, so the analyser records nothing for
    an allow-list to object to. Only network programs get an unknowable
    destination recorded against them."""
    from frostlang.audit import audit
    from frostlang.parser import parse as _parse
    caps = audit(_parse('put the standard input into u\nrun "echo" with u\n'))
    assert caps.reaches == []


def test_a_non_network_command_is_not_second_guessed(project):
    """`git` takes a remote name, not a URL, and refusing it for having no
    readable destination would make the flag unusable."""
    policy = project("p.policy", ONLY)
    path = project("s.frost", 'try to run "git" with "status"\nput "ok"\n')
    status, out, err = frost("--enforce-hosts", "--policy", policy, path,
                             cwd=str(project.root))
    assert status == 0, err
    assert "ok" in out


def test_without_the_flag_nothing_is_checked_at_spawn(project):
    """The default is unchanged: the static check alone, failing closed."""
    policy = project("p.policy", 'forbid reaching "telemetry.example"\n')
    path = project("s.frost", 'run "echo" with "https://api.other/x"\nput it\n')
    status, out, _ = frost("--policy", policy, path, cwd=str(project.root))
    assert status == 0
    assert "api.other" in out


def test_the_rules_are_read_off_the_policy():
    forbidden, allowed = host_rules(parse_policy(
        'forbid reaching "*.telemetry.example"\n'
        'require reaching only "api.github.com", "*.internal"\n'))
    assert forbidden[0][0] == "*.telemetry.example"
    assert allowed == ["api.github.com", "*.internal"]


def test_two_allow_lists_intersect():
    """Same as the static composition, and for the same reason: both must
    pass, so the result can only narrow."""
    _, allowed = host_rules(parse_policy(
        'require reaching only "api.github.com", "*.internal"\n'
        'require reaching only "api.github.com"\n'))
    assert allowed == ["api.github.com"]


def test_no_rules_means_no_runtime_checking():
    forbidden, allowed = host_rules(parse_policy('forbid running "sudo"\n'))
    assert (forbidden, allowed) == ([], None)


# ----------------------------------------------------- rules for the proxy

def test_squid_states_the_allow_list_and_closes_the_default():
    rules = parse_policy('require reaching only "api.github.com", "*.internal"\n')
    config = egress.squid(rules, "p.policy")
    assert "acl frost_allowed dstdomain api.github.com" in config
    assert "acl frost_allowed dstdomain .internal" in config
    assert "http_access deny all" in config, (
        "without a closing deny the allow-list is a suggestion, because "
        "Squid falls through")


def test_a_forbidden_host_is_denied_before_anything_allows_it():
    rules = parse_policy('forbid reaching "*.telemetry.example"\n'
                         'require reaching only "*"\n')
    config = egress.squid(rules, "p.policy")
    assert config.index("http_access deny frost_denied") < \
        config.index("http_access allow frost_allowed")


def test_a_glob_in_the_middle_becomes_a_regex_rather_than_being_dropped():
    rules = parse_policy('require reaching only "api-*.example.com"\n')
    config = egress.squid(rules, "p.policy")
    assert "dstdom_regex" in config
    assert r"^api-.*\.example\.com$" in config


def test_a_policy_with_no_allow_list_says_so_rather_than_emitting_nothing():
    config = egress.squid(parse_policy('forbid running "sudo"\n'), "p.policy")
    assert "names no allow-list" in config
    assert "http_access allow" not in config


def test_the_plain_list_is_one_host_per_line():
    rules = parse_policy('require reaching only "a.example", "b.example"\n')
    lines = [l for l in egress.plain(rules, "p").split("\n")
             if l and not l.startswith("#")]
    assert lines == ["a.example", "b.example"]


def test_the_generated_config_says_what_it_does_not_do():
    """A file that looks like enforcement should say where enforcement
    actually is, or somebody will read it as the whole answer."""
    config = egress.squid(parse_policy(ONLY), "p.policy")
    assert "boundary" in config
    assert "by nothing else" in config


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError) as e:
        egress.render("nftables", [], "p")
    assert "squid" in str(e.value)


def test_the_command_writes_a_usable_config(project):
    policy = project("p.policy", ONLY)
    path = project("s.frost", 'put "x"\n')
    status, out, err = frost("--egress-rules", "squid", "--policy", policy,
                             path, cwd=str(project.root))
    assert status == 0, err
    assert "acl frost_allowed dstdomain api.github.com" in out


def test_the_command_can_write_a_plain_list(project):
    policy = project("p.policy", ONLY)
    path = project("s.frost", 'put "x"\n')
    _, out, _ = frost("--egress-rules", "list", "--policy", policy, path,
                      cwd=str(project.root))
    assert "api.github.com" in out
