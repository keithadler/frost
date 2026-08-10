"""Sealed values: redaction that cannot be forgotten.

The claim being tested is strong, so it is tested as a property rather than
as a list of cases: for a script that touches a secret in every way the
language allows, the plaintext must not appear on stdout or stderr. A test
that only checked `put token` would pass while `put "x" && token` leaked.
"""

import io
import os
import sys

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import Interpreter, FrostError, to_text, to_argument
from frostlang.sealed import Sealed, reveal, is_sealed

from helpers import out, run, caps_for, dangers_for, titles

PLAINTEXT = "ghp_a1b2c3d4e5f6g7h8"


@pytest.fixture(autouse=True)
def token_in_the_environment():
    os.environ["FROST_TEST_TOKEN"] = PLAINTEXT
    os.environ["FROST_TEST_EMPTY"] = ""
    yield
    os.environ.pop("FROST_TEST_TOKEN", None)
    os.environ.pop("FROST_TEST_EMPTY", None)


TOKEN = 'put the secret environment variable "FROST_TEST_TOKEN" into token\n'


def streams(source):
    """Run a script; return everything it wrote to stdout and stderr."""
    tree = parse(TOKEN + source)
    held_out, held_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        Interpreter().run_program(tree)
        return sys.stdout.getvalue() + sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = held_out, held_err


# ------------------------------------------------------- the core property

LEAK_ATTEMPTS = [
    "put token",
    "put token into standard error",
    'put "prefix" && token',
    'put token & "suffix"',
    'put "a" & token & "b" && token',
    "put the uppercase token",
    "put the lowercase token",
    "put the trimmed token",
    "put the first character of token",
    "put the first word of token",
    "put characters 1 to 5 of token",
    "put the words of token",
    "put the lines of token",
    'put token split by "_"',
    'put (token split by "_") joined by "/"',
    "put the sorted the characters of token",
    "put the reversed the characters of token",
    "put the length of token & token",
    "put item 1 of (token split by \"_\")",
    'put "x" into holder\nput token after holder\nput holder',
    'put "x" into holder\nput token before holder\nput holder',
    "put 0 into n\nrepeat for each character in token as c\n    put c\nend repeat",
    "to echo back with value\n    return value\nend echo back\nput the echo back of token",
    "put token into the global kept\nput the global kept",
    "ensure\n    put token\nend ensure\nput \"body\"",
]


@pytest.mark.parametrize("attempt", LEAK_ATTEMPTS,
                         ids=[a.split("\n")[0][:44] for a in LEAK_ATTEMPTS])
def test_the_plaintext_never_reaches_a_stream(attempt):
    assert PLAINTEXT not in streams(attempt)


def test_the_attempts_actually_produce_output():
    """Guards the test above: redaction is not being 'proved' by silence."""
    for attempt in LEAK_ATTEMPTS:
        assert streams(attempt).strip(), f"produced nothing: {attempt}"


def test_the_marker_names_the_secret():
    assert streams("put token").strip() == "«secret FROST_TEST_TOKEN»"


def test_an_error_message_redacts():
    tree = parse(TOKEN + "put token + 1")
    with pytest.raises(FrostError) as e:
        Interpreter().run_program(tree)
    assert PLAINTEXT not in e.value.msg
    assert "secret" in e.value.msg


def test_a_traceback_repr_redacts():
    """repr is what lands in a debugger or a pprint of the tree."""
    sealed = Sealed(PLAINTEXT, "token")
    assert PLAINTEXT not in repr(sealed)
    assert PLAINTEXT not in str(sealed)
    assert PLAINTEXT not in f"{sealed}"
    assert PLAINTEXT not in "{}".format(sealed)
    assert PLAINTEXT not in to_text(sealed)


def test_trace_output_redacts():
    tree = parse(TOKEN + "put token")
    held = sys.stderr
    sys.stderr = io.StringIO()
    holdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        Interpreter(trace=True).run_program(tree)
        assert PLAINTEXT not in sys.stderr.getvalue()
    finally:
        sys.stderr, sys.stdout = held, holdout


# --------------------------------------------------------- keeping context

def test_the_parts_that_were_never_secret_still_print():
    """If the whole line redacted, people would route around the seal to keep
    their logs readable, and a mechanism people route around protects
    nothing."""
    src = 'put "deploy" into user\nput "connecting as" && user && "with" && token'
    assert streams(src).strip() \
        == "connecting as deploy with «secret FROST_TEST_TOKEN»"


def test_a_secret_in_the_middle_of_a_string():
    assert streams('put "url=https://api/" & token & "/v1"').strip() \
        == "url=https://api/«secret FROST_TEST_TOKEN»/v1"


def test_repeated_markers_collapse():
    """Splitting and rejoining should not produce a wall of markers."""
    printed = streams('put (token split by "_") joined by ""').strip()
    assert printed.count("«secret") == 1


# ------------------------------------------------------------ comparisons

def test_equality_sees_through_the_seal():
    assert out(TOKEN + f'if token is "{PLAINTEXT}" then put "match"') == "match"


def test_inequality_works():
    assert out(TOKEN + 'if token is not "wrong" then put "differs"') == "differs"


def test_prefix_and_contains_work():
    assert out(TOKEN + 'if token starts with "ghp_" then put "prefix"') \
        == "prefix"
    assert out(TOKEN + 'if token contains "b2c3" then put "inside"') == "inside"


def test_a_non_empty_secret_is_not_empty():
    assert out(TOKEN + 'if token is not empty then put "present"') == "present"


def test_an_empty_secret_is_empty():
    src = ('put the secret environment variable "FROST_TEST_EMPTY" into blank\n'
           'if blank is empty then put "blank"')
    assert out(src) == "blank"


def test_the_length_is_the_real_length():
    assert out(TOKEN + "put the length of token") == str(len(PLAINTEXT))


def test_comparison_is_constant_time():
    """Not a timing measurement — that it routes through compare_digest."""
    import frostlang.interp as interp
    source = open(interp.__file__).read()
    assert "hmac.compare_digest" in source


# ---------------------------------------------------------- the boundaries

def test_an_argument_gets_the_plaintext():
    assert to_argument(Sealed(PLAINTEXT, "t")) == PLAINTEXT


def test_a_list_of_arguments_gets_the_plaintext():
    assert to_argument([Sealed("a", "t"), "b"]) == "a\nb"


# These used to read the value back through `put it`. frost now re-seals a
# secret a child prints back, so that route redacts — which is the leak being
# closed, and would have turned these into tests of the redaction rather than
# of the release. The child writes what it received to a file instead, which
# is the one record frost does not edit on the way past.

def test_a_command_receives_the_real_value(tmp_path):
    seen = tmp_path / "seen.txt"
    run(TOKEN + f'run "sh" with "-c", "printf %s \\"$1\\" > {seen}", '
                f'"sh", token')
    assert seen.read_text() == PLAINTEXT


def test_a_command_receives_the_real_value_on_stdin(tmp_path):
    seen = tmp_path / "seen.txt"
    run(TOKEN + f'run "sh" with "-c", "cat > {seen}" reading token')
    assert seen.read_text().strip() == PLAINTEXT


def test_a_file_write_receives_the_real_value(tmp_path):
    target = tmp_path / "out.txt"
    run(TOKEN + f'put token into file "{target}"')
    assert target.read_text().strip() == PLAINTEXT


def test_the_child_environment_receives_the_real_value(tmp_path):
    seen = tmp_path / "seen.txt"
    run(TOKEN + 'put token into the environment variable "FROST_CHILD_SECRET"\n'
        f'run "sh" with "-c", '
        f'"printf %s \\"$FROST_CHILD_SECRET\\" > {seen}"')
    assert seen.read_text() == PLAINTEXT


# ------------------------------------------------------------ the sources

def test_a_secret_file_is_sealed(tmp_path):
    key = tmp_path / "id_rsa"
    key.write_text("PRIVATE-KEY-MATERIAL\n")
    src = f'put the secret file "{key}" into key\nput "read:" && key'
    printed = out(src)
    assert "PRIVATE-KEY-MATERIAL" not in printed
    assert "read: «secret" in printed


def test_an_ordinary_file_read_is_not_sealed(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("nothing secret\n")
    assert out(f'put file "{notes}"') == "nothing secret"


def test_an_ordinary_environment_read_is_not_sealed():
    assert out('put the environment variable "FROST_TEST_TOKEN"') == PLAINTEXT


def test_a_missing_secret_file_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run('put the secret file "/no/such/key" into k')
    assert "no file at" in e.value.msg


def test_reading_a_keystore_secret_without_a_keystore_is_a_clear_error():
    with pytest.raises(FrostError) as e:
        run('put the secret "db password" into p')
    assert "no keystore is open" in e.value.msg
    assert "--keystore" in (e.value.hint or "")


# ---------------------------------------------------------------- parsing

def test_secret_is_not_a_reserved_word():
    """Gated by `the`, so it costs nothing from the name vocabulary."""
    assert out("put 3 into secret count\nput secret count") == "3"


@pytest.mark.parametrize("src", [
    "put the secret",
    "put the secret environment",
    "put the secret environment variable",
    "put the secret file",
])
def test_malformed_secret_expressions_are_rejected(src):
    with pytest.raises(ParseError):
        parse(src)


def test_the_three_forms_parse():
    for src in ['put the secret "name"',
                'put the secret environment variable "NAME"',
                'put the secret file "path"']:
        assert parse(src)


# ----------------------------------------------------------- the manifest

def test_the_manifest_records_a_keystore_read():
    caps = caps_for('put the secret "db password" into p')
    assert caps.secret_reads == [("db password", "keystore", 1)]


def test_the_manifest_records_the_source():
    caps = caps_for('put the secret environment variable "T" into p\n'
                    'put the secret file "k" into q')
    assert [s for _, s, _ in caps.secret_reads] == ["environment", "file"]


def test_a_release_through_a_variable_is_found():
    """The case everyone actually writes: assign, then use."""
    caps = caps_for('put the secret "p" into password\n'
                    'run "psql" with "--password", password')
    assert ("argument", "psql", 2) in caps.secret_releases


def test_taint_follows_a_chain_of_assignments():
    caps = caps_for('put the secret "p" into a\nput a into b\nput b into c\n'
                    'run "psql" with c')
    assert ("argument", "psql", 4) in caps.secret_releases


def test_taint_follows_concatenation():
    caps = caps_for('put the secret "p" into pw\n'
                    'put "postgres://user:" & pw into url\n'
                    'run "psql" with url')
    assert ("argument", "psql", 3) in caps.secret_releases


def test_an_untainted_variable_is_not_reported():
    caps = caps_for('put "public" into name\nrun "echo" with name')
    assert caps.secret_releases == []


def test_the_four_release_kinds_are_distinguished():
    caps = caps_for('''
    put the secret "p" into pw
    run "a" with pw
    run "b" reading pw
    put pw into file "out.txt"
    put pw into the environment variable "PW"
    ''')
    assert sorted(where for where, _, _ in caps.secret_releases) == \
        ["argument", "environment", "file", "input"]


def test_describe_lists_secrets_and_releases():
    from frostlang.audit import describe
    text = describe(caps_for('put the secret "db password" into p\n'
                             'run "psql" with p'))
    assert "Reads these secrets:" in text
    assert "db password" in text
    assert "Lets a secret leave the process:" in text


def test_the_summary_mentions_secrets():
    from frostlang.audit import summarise
    text = summarise(caps_for('put the secret "db password" into p'))
    assert "reads 1 secret (db password)" in text


# ------------------------------------------------------------- the checks

def test_writing_a_secret_to_a_file_is_a_danger():
    f = dangers_for('put the secret "p" into pw\nput pw into file "creds.txt"')
    assert any(x.severity == "danger" and "written to" in x.title for x in f)


def test_handing_a_secret_to_a_network_program_is_a_danger():
    f = dangers_for('put the secret "p" into pw\n'
                    'run "curl" with "--data", pw within 5 seconds')
    assert any(x.severity == "danger" and "handed to curl" in x.title
               for x in f)


def test_a_secret_as_an_argument_is_a_caution():
    f = dangers_for('put the secret "p" into pw\nrun "psql" with pw')
    assert any(x.severity == "caution" and "as an argument" in x.title
               for x in f)


def test_a_secret_on_standard_input_is_not_flagged():
    """It is the recommended way, so flagging it would train people to ignore
    the finding."""
    titles_found = titles('put the secret "p" into pw\nrun "psql" reading pw')
    assert not any("secret" in t.lower() for t in titles_found)


def test_a_secret_in_the_environment_is_a_caution():
    f = dangers_for('put the secret "p" into pw\n'
                    'put pw into the environment variable "PGPASSWORD"')
    assert any("into the environment" in x.title for x in f)


# ------------------------------------------------------------ the policy

def test_a_policy_can_forbid_reading_a_secret():
    from frostlang.audit import parse_policy, check
    rules = parse_policy('forbid reading secret "prod/*"')
    hits = check(caps_for('put the secret "prod/db" into p'), rules)
    assert [f for f in hits if f[0] == "forbid"]


def test_a_policy_glob_leaves_other_secrets_alone():
    from frostlang.audit import parse_policy, check
    rules = parse_policy('forbid reading secret "prod/*"')
    assert check(caps_for('put the secret "staging/db" into p'), rules) == []


def test_secrets_read_is_countable():
    from frostlang.audit import parse_policy, check
    rules = parse_policy("forbid more than 1 secrets read")
    src = 'put the secret "a" into x\nput the secret "b" into y'
    assert [f for f in check(caps_for(src), rules) if f[0] == "forbid"]


def test_secret_releases_are_countable():
    from frostlang.audit import parse_policy, check
    rules = parse_policy("forbid any secret releases")
    src = 'put the secret "a" into x\nrun "psql" with x'
    assert [f for f in check(caps_for(src), rules) if f[0] == "forbid"]
