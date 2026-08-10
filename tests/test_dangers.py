"""The standing danger checks that apply with no policy at all."""

from frostlang.audit import (find_dangers, summarise, verdict, classify_path,
                             Finding, parse_policy, check)

from helpers import caps_for, dangers_for, titles, example


def test_rm_rf_is_a_danger():
    f = dangers_for('run "rm" with "-rf", "/tmp/x"')
    assert any(x.severity == "danger" and "Recursive forced" in x.title
               for x in f)


def test_rm_r_alone_is_only_a_caution():
    f = dangers_for('run "rm" with "-r", "/tmp/x"')
    assert [x.severity for x in f if "Recursive" in x.title] == ["caution"]


def test_plain_rm_is_not_flagged():
    assert not [x for x in dangers_for('run "rm" with "one file.txt"')
                if x.severity == "danger"]


def test_wildcard_delete_is_flagged():
    assert any("wildcard" in t for t in titles('run "rm" with "*.tmp"'))


def test_sudo_is_a_danger():
    assert any("Elevated" in t for t in titles('run "sudo" with "ls"'))


def test_chmod_777_is_a_danger():
    assert any("permission" in t for t in titles('run "chmod" with "777", "/x"'))


def test_shell_escape_is_flagged():
    assert any("Shell escape" in t
               for t in titles('run "sh" with "-c", "echo hi"'))


def test_network_program_is_noted():
    f = dangers_for('run "curl" with "https://example.com" within 5 seconds')
    assert any(x.severity == "note" and "network" in x.title for x in f)


def test_network_without_timeout_is_a_caution():
    f = dangers_for('run "curl" with "https://example.com"')
    assert any("No timeout" in x.title and x.severity == "caution" for x in f)


def test_network_with_timeout_has_no_timeout_caution():
    f = dangers_for('run "curl" with "https://x" within 5 seconds')
    assert not any("No timeout" in x.title for x in f)


def test_curl_piped_into_shell_is_the_worst_case():
    src = '''
    pipe
        run "curl" with "https://install.example.com/x.sh"
        run "sh"
    end pipe
    '''
    assert any("piped into a shell" in t for t in titles(src))


def test_write_to_system_location_is_a_danger():
    assert any("system location" in t
               for t in titles('put "x" into file "/etc/thing.conf"'))


def test_write_to_tmp_is_not_a_danger():
    assert not [x for x in dangers_for('put "x" into file "/tmp/thing"')
                if x.severity == "danger"]


def test_reading_credentials_is_flagged():
    assert any("credentials" in t
               for t in titles('put file "~/.ssh/id_rsa" into key'))


def test_a_literal_reaching_a_name_is_resolved_not_guessed():
    """`put "ls" into tool` then `run tool` is knowable, and calling it
    unknowable understates the manifest as badly as guessing would
    overstate it."""
    assert not any("built at runtime" in t
                   for t in titles('put "ls" into tool\nrun tool with "-l"'))


def test_runtime_program_name_is_a_caution():
    """Genuinely unknowable: the name comes out of another program."""
    src = 'run "cat" with "which.txt"\nput it into tool\nrun tool'
    assert any("built at runtime" in t for t in titles(src))


def test_path_classification():
    assert classify_path("/etc/passwd") == "system"
    assert classify_path("/tmp/x") == "temporary"
    assert classify_path("~/notes.txt") == "home"
    assert classify_path("notes.txt") == "relative"
    assert classify_path(None) == "runtime"


# -- verdicts and summary

def test_clean_script_has_a_clean_verdict():
    assert verdict(dangers_for('run "echo" with "hello"')) == "clean"


def test_dangerous_script_has_a_dangerous_verdict():
    assert verdict(dangers_for('run "rm" with "-rf", "/"')) == "dangerous"


def test_policy_refusal_outranks_everything():
    caps = caps_for('run "rm" with "-rf", "/tmp/x"')
    hits = check(caps, parse_policy('forbid running "rm" with "-rf"'))
    assert verdict(find_dangers(caps), hits) == "blocked"


def test_summary_reads_as_a_sentence():
    src = '''
    put file "in.txt" into data
    run "curl" with "https://x" within 5 seconds
    put data into file "/tmp/out.txt"
    quit with status 1
    '''
    text = summarise(caps_for(src))
    assert text.startswith("This script ")
    assert text.endswith(".")
    assert "curl" in text and "internet" in text
    assert "reads 1 file" in text and "writes 1 file" in text


def test_summary_of_an_inert_script():
    assert "nothing observable" in summarise(caps_for('put "hi"'))


def test_the_three_demo_scripts_land_as_intended():
    """The audit page depends on these verdicts staying put."""
    rules = parse_policy(example("production.policy"))
    expected = {"healthcheck.frost": "clean",
                "logreport.frost": "clean",
                "danger.frost": "blocked"}
    for name, want in expected.items():
        caps = caps_for(example(name))
        got = verdict(find_dangers(caps), check(caps, rules))
        assert got == want, f"{name}: expected {want}, got {got}"
