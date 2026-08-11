"""Records, JSON, captured standard error, and the clock.

These four landed together because they share one cause: without them a real
script has to leave frost. JSON meant shelling out to `jq`, an unreadable
error meant `sh -c "... 2>&1"`, and a timestamp meant `run "date"`: each one
handing capability to a program the auditor can describe but not see into.

So the tests here care most about the seams: that a secret is still a secret
after a round trip through a parser, that `the error output` belongs to the
command that just ran and not the one before it, and that a recording of a
script which reads the clock replays to the same answer.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import structured as S
from frostlang.sealed import Sealed, is_sealed
from frostlang.parser import parse, ParseError
from frostlang.audit import audit, summarise, describe

from helpers import REPO, out as run_source, run_failing


PAYLOAD = json.dumps({
    "status": "green",
    "user": {"name": "ada", "id": 7},
    "tags": ["alpha", "beta"],
    "ratio": 1.5,
    "ok": True,
    "nothing": None,
})


def frost_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return path


def frost(*args, cwd=None, timeout=90):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------- reading JSON

def test_a_field_comes_out_by_name():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "status" of r\n')
    assert out.strip() == "green"


def test_fields_nest():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "name" of the "user" of r\n')
    assert out.strip() == "ada"


def test_an_array_is_a_list_the_language_already_understands():
    """The point of mapping arrays onto lists rather than inventing a second
    sequence: `item 2 of` and `repeat for each` keep working."""
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put item 2 of the "tags" of r\n'
                     f'put the number of items in the "tags" of r\n')
    assert out.split() == ["beta", "2"]


def test_a_number_stays_a_number():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "id" of the "user" of r + 1\n'
                     f'put the "ratio" of r * 2\n')
    assert out.split() == ["8", "3"]


def test_a_missing_key_is_empty_not_an_error():
    """Same rule as `word 99 of`, for the same reason: an optional field in an
    API response should not need a guard around every access."""
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "absent" of r is empty\n')
    assert out.strip() == "true"


def test_a_missing_path_can_be_walked_through():
    """`the "a" of the "b" of r` where `b` is absent must not explode, or
    every nested read needs a guard and people stop using records."""
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "name" of the "nobody" of r is empty\n')
    assert out.strip() == "true"


def test_a_field_of_plain_text_is_an_error():
    """The other half of the rule. Empty here would hide a real mistake, the
    value is not the shape the script thinks it is, at the only moment when
    finding it is cheap."""
    _, error = run_failing('put the "status" of "not a record"\n')
    assert "only a record has named fields" in error.hint


def test_a_field_of_a_list_says_what_to_do_instead():
    _, error = run_failing('put the "status" of the empty list\n')
    assert "numbered, not named" in error.hint


def test_invalid_json_fails_loudly_and_says_where_to_look():
    _, error = run_failing('put the json of "not json at all"\n')
    assert "not valid JSON" in error.msg
    assert "the error output" in error.hint


def test_null_is_the_empty_the_language_already_has():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the "nothing" of r is empty\n')
    assert out.strip() == "true"


def test_a_boolean_survives():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'if the "ok" of r then put "yes"\n')
    assert out.strip() == "yes"


# ------------------------------------------------------------- writing JSON

def test_a_record_is_built_a_field_at_a_time():
    out = run_source('put the empty record into summary\n'
                     'put "green" into the "status" of summary\n'
                     'put 2 into the "count" of summary\n'
                     'put the json text of summary\n')
    assert json.loads(out) == {"status": "green", "count": 2}


def test_the_first_field_creates_the_record():
    """Requiring `put the empty record into r` first is ceremony nobody would
    remember; the assignment makes one."""
    out = run_source('put "green" into the "status" of fresh\n'
                     'put the json text of fresh\n')
    assert json.loads(out) == {"status": "green"}


def test_a_record_prints_as_json_rather_than_as_a_type_name():
    """A record printing as `<record>` would send everyone straight back to
    jq to look at their own data."""
    out = run_source('put "x" into the "a" of r\nput r\n')
    assert json.loads(out) == {"a": "x"}


def test_keys_and_values_are_lists():
    out = run_source('put "1" into the "a" of r\n'
                     'put "2" into the "b" of r\n'
                     'put the keys of r joined by ","\n'
                     'put the values of r joined by ","\n')
    assert out.split() == ["a,b", "1,2"]


def test_a_round_trip_preserves_the_document():
    out = run_source(f'put the json of {json.dumps(PAYLOAD)} into r\n'
                     f'put the json text of r\n')
    assert json.loads(out) == json.loads(PAYLOAD)


# ------------------------------------------------------------------ secrets

def test_parsing_a_secret_seals_every_field():
    """A parser is not a laundry. The credentials do not stop being secret
    because they went through JSON."""
    value = S.from_json(Sealed('{"password": "hunter2"}', "db"))
    assert is_sealed(value["password"])
    assert value["password"].reveal() == "hunter2"


def test_a_sealed_number_is_sealed_too():
    value = S.from_json(Sealed('{"port": 5432}', "db"))
    assert is_sealed(value["port"])


def test_a_field_pulled_from_a_secret_still_redacts(tmp_path):
    creds = tmp_path / "creds.json"
    creds.write_text('{"password": "hunter2"}')
    out = run_source(f'put the json of the secret file "{creds}" into c\n'
                     f'put "pw is" && the "password" of c\n')
    assert "hunter2" not in out
    assert "«secret" in out


def test_serialising_redacts_one_field_and_keeps_the_rest_readable():
    """All-or-nothing redaction would make people avoid the seal to keep
    their output legible, and a mechanism people route around protects
    nothing."""
    record = {"user": "deploy", "password": Sealed("hunter2", "db password")}
    text = S.to_json(record)
    assert is_sealed(text)
    assert "hunter2" not in text.marker
    assert "deploy" in text.marker
    assert "«secret db password»" in text.marker


def test_the_auditor_sees_a_secret_through_a_parse_and_a_field(tmp_path):
    """The seam most likely to leak: taint has to survive `the json of` and
    `the "k" of`, or the manifest under-reports."""
    caps = audit(parse(
        'put the json of the secret file "c.json" into config\n'
        'run "psql" with the "password" of config\n'))
    assert caps.secret_reads
    assert caps.secret_releases, "a secret reached a program unreported"
    assert "secret" in summarise(caps)


# ------------------------------------------------------- standard error

def test_the_error_output_is_captured():
    out = run_source('try to run "sh" with "-c", "echo boom >&2; exit 3"\n'
                     'put "saw:" && the error output\n')
    assert "saw: boom" in out


def test_the_error_output_belongs_to_the_command_that_just_ran():
    """A stale value would be read as this command's, which is the same shape
    of mistake as a check decided by something other than the thing under
    test."""
    out = run_source('try to run "sh" with "-c", "echo old >&2"\n'
                     'run "echo" with "fresh"\n'
                     'put "after:" && the error output\n')
    assert "old" not in out.split("after:")[1]


def test_the_status_and_the_error_output_are_both_available():
    out = run_source('try to run "sh" with "-c", "echo why >&2; exit 7"\n'
                     'put the result\n'
                     'put the error output\n')
    assert out.split() == ["7", "why"]


def test_the_error_output_survives_a_replay(tmp_path):
    """A recording that dropped stderr would replay a script whose error
    handling never fires."""
    script = frost_file(tmp_path, "s.frost",
                        'try to run "sh" with "-c", "echo why >&2; exit 1"\n'
                        'put the error output\n')
    rec = tmp_path / "run.json"
    frost("--record", str(rec), str(script), cwd=str(tmp_path))
    status, out, err = frost("--replay", str(rec), str(script),
                             cwd=str(tmp_path))
    assert status == 0, err
    assert "why" in out


# --------------------------------------------------------- clock and waiting

def test_the_clock_reads_look_like_what_they_are():
    import re
    out = run_source('put the current date\nput the current time\n'
                     'put the current timestamp\n')
    date, time_, stamp = out.strip().split("\n")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", time_)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", stamp)


def test_a_wait_needs_a_unit():
    """Same argument as timeouts: a bare 3 means seconds to one reader and
    milliseconds to another."""
    with pytest.raises(ParseError) as e:
        parse("wait 3\n")
    assert "needs a unit" in e.value.msg


def test_wait_is_not_a_reserved_word():
    """It is recognised at the start of a statement, which costs nothing from
    the identifier vocabulary, `wait time` stays a usable name."""
    out = run_source('put 5 into wait time\nput wait time\n')
    assert out.strip() == "5"


def test_a_wait_actually_waits():
    import time
    started = time.monotonic()
    run_source("wait 300 milliseconds\n")
    assert time.monotonic() - started >= 0.25


def test_a_negative_wait_is_refused():
    _, error = run_failing("wait -1 seconds\n")
    assert "cannot be negative" in error.msg


def test_a_replay_serves_the_recorded_clock(tmp_path):
    """A recording whose timestamp moves every replay is not a fixture, it is
    a diff generator."""
    script = frost_file(tmp_path, "s.frost", "put the current timestamp\n")
    rec = tmp_path / "run.json"
    frost("--record", str(rec), str(script), cwd=str(tmp_path))
    recorded = json.loads(rec.read_text())
    stamp = [e for e in recorded["events"] if e["kind"] == "clock"][0]["value"]

    _, first, _ = frost("--replay", str(rec), str(script), cwd=str(tmp_path))
    _, again, _ = frost("--replay", str(rec), str(script), cwd=str(tmp_path))
    assert first.strip() == stamp
    assert first == again


def test_a_replay_does_not_actually_sleep(tmp_path):
    """Replaying a script that backs off for thirty seconds should take no
    longer than replaying anything else."""
    import time
    script = frost_file(tmp_path, "s.frost",
                        'wait 2 seconds\nput "done"\n')
    rec = tmp_path / "run.json"
    frost("--record", str(rec), str(script), cwd=str(tmp_path))

    started = time.monotonic()
    status, out, err = frost("--replay", str(rec), str(script),
                             cwd=str(tmp_path))
    elapsed = time.monotonic() - started
    assert status == 0, err
    assert "done" in out
    assert elapsed < 1.5, f"the replay slept for real ({elapsed:.1f}s)"


# ----------------------------------------------------------------- manifest

def test_a_wait_appears_in_the_manifest():
    """Not a capability. It touches nothing, but a reviewer approving a CI
    job wants to know it sleeps for ten minutes."""
    caps = audit(parse('wait 90 seconds\nrun "echo" with "x"\n'))
    assert caps.waits == [(90.0, 1, False)]
    assert "waits" in summarise(caps)
    assert "Waits:" in describe(caps)


def test_a_wait_in_a_loop_is_not_reported_as_happening_once():
    """`waits 2 seconds` for a retry that sleeps between five attempts
    understates it by the loop count, and the manifest may overstate a risk
    but must never understate one."""
    caps = audit(parse('repeat 5 times\n    wait 2 seconds\nend repeat\n'))
    assert caps.waits == [(2.0, 2, True)]
    assert "at least" in summarise(caps)
    assert "each time round a loop" in describe(caps)


def test_a_wait_built_at_runtime_is_reported_as_unknowable():
    caps = audit(parse('put 5 into n\nwait n seconds\n'))
    assert "Waits:" in describe(caps)
    assert "runtime" in describe(caps)


def test_a_policy_can_bound_the_waiting():
    from frostlang.audit import parse_policy, check
    caps = audit(parse("wait 30 seconds\nwait 40 seconds\n"))
    findings = check(caps, parse_policy("require at most 1 wait\n"))
    assert findings, "a policy could not constrain how much a script sleeps"


def test_the_merge_covers_every_capability_field():
    """The manifest is the product. A field that a single-file audit collects
    and the closure audit drops is a manifest that lies by omission, and the
    hand-written field list this replaced did exactly that to `waits`."""
    import dataclasses
    from frostlang.audit import Capabilities
    from frostlang.program_audit import merge

    left, right = Capabilities(), Capabilities()
    for f in dataclasses.fields(right):
        value = getattr(right, f.name)
        if isinstance(value, list):
            value.append(("sentinel", 1))
        elif isinstance(value, int):
            setattr(right, f.name, 1)

    merged = merge(left, right)
    for f in dataclasses.fields(merged):
        assert getattr(merged, f.name), \
            f"merge() dropped {f.name}; --explain would omit it silently"


# ---------------------------------------------------- declared record shapes

DECLARE = ('put the json of {payload} into build '
           'with fields "status", "number"\n')
# Not named PAYLOAD: this module already has one, and rebinding it at the
# bottom of the file silently changed what every test above was parsing.
SHAPED = json.dumps(json.dumps({"status": "green", "number": 7}))


def test_a_declared_shape_that_matches_just_works():
    out = run_source(DECLARE.format(payload=SHAPED) +
                     'put the "status" of build\n')
    assert out.strip() == "green"


def test_a_missing_declared_field_fails_at_the_line_that_read_it():
    """Not three screens later. A missing key is empty by design, so a payload
    that quietly stopped carrying `status` would make every downstream test of
    it go the wrong way with nothing to point at."""
    thin = json.dumps(json.dumps({"status": "green"}))
    _, error = run_failing(DECLARE.format(payload=thin) + 'put "unreached"\n')
    assert "missing 'number'" in error.msg
    assert "it has: status" in error.hint


def test_declaring_a_shape_on_something_that_is_not_a_record_is_refused():
    _, error = run_failing('put "plain text" into b with fields "status"\n')
    assert "not a record" in error.msg


def test_a_mistyped_field_is_caught_before_anything_runs():
    """The reason this feature exists. `the "staus" of build` reads as empty,
    the comparison against it quietly goes the wrong way, and nothing anywhere
    says that `staus` was never a field."""
    with pytest.raises(ParseError) as e:
        parse(DECLARE.format(payload=SHAPED) + 'put the "staus" of build\n')
    assert e.value.msg == "build has no field 'staus'"
    assert "status, number" in e.value.hint
    assert e.value.code == "no-such-field"


def test_the_mistyped_field_carries_a_repair():
    from frostlang.diagnostics import from_error
    source = DECLARE.format(payload=SHAPED) + 'put the "staus" of build\n'
    try:
        parse(source)
    except ParseError as e:
        diagnostic = from_error(e, source)
    assert diagnostic.repairs
    assert '"status"' in diagnostic.repairs[0].text
    assert diagnostic.repairs[0].confidence == "guess"


def test_an_undeclared_record_is_not_second_guessed():
    """Only a shape the author claimed is checked. Inferring one from whatever
    JSON turned up during development would reject correct scripts."""
    parse('put the json of it into build\nput the "anything" of build\n')


def test_reassigning_without_a_claim_drops_the_shape():
    """Otherwise a name reused for something else reports a mistake in code
    that is perfectly correct."""
    parse(DECLARE.format(payload=SHAPED) +
          'put the json of it into build\n'
          'put the "whatever" of build\n')


def test_a_shape_declared_inside_a_block_does_not_leak_out():
    parse('if 1 is 1 then\n'
          '    put the json of it into b with fields "a"\n'
          'end if\n'
          'put the json of it into b\n'
          'put the "z" of b\n')


def test_a_field_name_has_to_be_written_out():
    """A name built at runtime could not be checked, which is the entire point
    of declaring one."""
    with pytest.raises(ParseError) as e:
        parse('put the json of it into b with fields (name)\n')
    assert e.value.code == "field-must-be-literal"


def test_a_field_declared_twice_is_a_mistake():
    with pytest.raises(ParseError) as e:
        parse('put the json of it into b with fields "a", "a"\n')
    assert "declared twice" in e.value.msg


def test_check_refuses_the_typo_without_running(tmp_path):
    """`--check` is where an agent finds this, before a process starts."""
    script = frost_file(tmp_path, "s.frost",
                        DECLARE.format(payload=SHAPED) +
                        'run "echo" with the "staus" of build\n')
    status, _, err = frost("--check", str(script), cwd=str(tmp_path))
    assert status == 2
    assert "has no field 'staus'" in err


def test_the_declaration_survives_formatting():
    from frostlang.formatter import format_source
    source = 'put the json of it into b with fields "a",   "c"\n'
    once = format_source(source)
    assert once == 'put the json of it into b with fields "a", "c"\n'
    assert parse(once, resolve=False) == parse(source, resolve=False)
