"""What a run did, as events something else can read.

Every tool can emit logs. What frost can emit that a shell cannot is the
difference between what a script was *allowed* to do and what it *did*: the
manifest is known before anything runs, the approval says what a person agreed
to, and the events say what happened. Holding those side by side answers
questions no log line supports, and the tests care most about that pairing and
about the one thing telemetry must never do, which is carry a secret out of
the building.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import telemetry as T
from frostlang.audit import audit
from frostlang.parser import parse

from helpers import REPO


def frost(*args, cwd=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, text, name="s.frost"):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


def events_of(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def kinds(events):
    return [e["event"] for e in events]


# --------------------------------------------------------------- the stream

def test_every_event_carries_the_same_envelope(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--run-id", "job-7", "--events", str(log), path, cwd=str(tmp_path))
    for event in events_of(log):
        assert event["run"] == "job-7"
        assert event["schema"] == T.SCHEMA
        assert event["ts"].endswith("Z")
        assert event["script"] == path
        assert event["event"]


def test_the_sequence_never_repeats(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\nput "b"\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    seqs = [e["seq"] for e in events_of(log)]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))


def test_a_run_opens_with_what_it_could_do(tmp_path):
    """The signal a shell cannot produce: what was possible, before anything
    happened."""
    path = script(tmp_path,
                  'run "curl" with "https://x.example" within 30 seconds\n'
                  "put it\n")
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    start = events_of(log)[0]
    assert start["event"] == "run.start"
    assert start["declares"]["programs"] == ["curl"]
    assert start["declares"]["hosts"] == ["x.example"]


def test_a_command_is_timed(tmp_path):
    """Nothing measured how long a command took, which is the first thing a
    monitoring system is asked."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    finish = [e for e in events_of(log) if e["event"] == "command.finish"][0]
    assert finish["seconds"] >= 0
    assert finish["status"] == 0
    assert finish["program"] == "echo"


def test_sizes_are_reported_and_contents_are_not(tmp_path):
    path = script(tmp_path, 'run "echo" with "hello"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    finish = [e for e in events_of(log) if e["event"] == "command.finish"][0]
    assert finish["stdout_bytes"] == 6
    assert "hello" not in json.dumps(finish), "the output itself was emitted"


def test_a_failing_run_still_reports_its_finish(tmp_path):
    """A monitoring system that only hears about runs which succeeded is
    monitoring the wrong half."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\nrun "false"\n')
    log = tmp_path / "e.ndjson"
    status, _, _ = frost("--events", str(log), path, cwd=str(tmp_path))
    assert status == 1
    finish = events_of(log)[-1]
    assert finish["event"] == "run.finish"
    assert finish["status"] == 1


def test_the_events_go_to_stderr_when_asked(tmp_path):
    path = script(tmp_path, 'put "x"\n')
    _, out, err = frost("--events", "-", path, cwd=str(tmp_path))
    assert out.strip() == "x"
    assert '"event": "run.start"' in err or '"event":"run.start"' in err


def test_an_unwritable_event_log_is_refused(tmp_path):
    path = script(tmp_path, 'put "SHOULD NOT RUN"\n')
    status, out, err = frost("--events", "/no/such/dir/e.ndjson", path,
                             cwd=str(tmp_path))
    assert status == 2
    assert out == ""
    assert "cannot write the event log" in err


# ------------------------------------------------------------- utilisation

def test_the_finish_says_what_was_approved_for_and_never_used(tmp_path):
    """The signal nothing else can produce. A script approved for six programs
    that uses two is an approval that should be tightened, and that is only
    visible holding the manifest and the run side by side."""
    path = script(tmp_path,
                  'if 1 is 2 then\n'
                  '    run "curl" with "https://x.example" within 30 seconds\n'
                  "end if\n"
                  'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    finish = events_of(log)[-1]
    assert "curl" in finish["programs_declared"]
    assert "curl" in finish["programs_unused"]
    assert "echo" in finish["programs_used"]
    assert "x.example" in finish["hosts_unused"]


def test_time_is_split_between_working_and_waiting(tmp_path):
    path = script(tmp_path, 'wait 150 milliseconds\nrun "echo" with "a"\n'
                            "put it\n")
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    finish = events_of(log)[-1]
    assert finish["waited_seconds"] >= 0.15
    assert finish["commands"] == 1
    assert finish["seconds"] >= finish["waited_seconds"]


def test_utilisation_is_computed_from_the_manifest_and_the_run():
    caps = audit(parse('run "git" with "status"\n'
                       'run "curl" with "https://x.example" within 30 seconds\n'))

    class Ran:
        programs = {"git"}
        hosts = set()

    used = T.utilisation(caps, Ran())
    assert used["programs_unused"] == ["curl"]
    assert used["hosts_unused"] == ["x.example"]
    assert used["programs_used"] == ["git"]


# ----------------------------------------------------------------- secrets

def test_a_secret_never_reaches_the_stream_as_an_argument(tmp_path):
    """Telemetry leaves the building more often than a recording does, so the
    argument path matters more here than anywhere else."""
    (tmp_path / "pw.txt").write_text("hunter2")
    path = script(tmp_path,
                  'put the secret file "pw.txt" into pw\n'
                  'try to run "echo" with "--token", pw\nput "done"\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    text = open(log).read()
    assert "hunter2" not in text
    assert "«secret" in text


def test_a_secret_read_is_reported_by_name_only(tmp_path):
    (tmp_path / "pw.txt").write_text("hunter2")
    path = script(tmp_path, 'put the secret file "pw.txt" into pw\nput "x"\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    read = [e for e in events_of(log) if e["event"] == "secret.read"][0]
    assert read["name"] == "pw.txt"
    assert "hunter2" not in json.dumps(read)


def test_the_sink_scrubs_nested_values():
    import io
    sink = T.Sink(io.StringIO(), run_id="r", script="s")
    sink.note_secret("token", "s3cret")
    sink.emit("thing", argv=["a", "s3cret"], nested={"k": ["s3cret"]})
    written = sink.stream.getvalue()
    assert "s3cret" not in written
    assert written.count("«secret token»") == 2


# ------------------------------------------------- alongside the other modes

def test_telemetry_works_with_a_recording(tmp_path):
    """The observer wraps whatever journal is in use rather than adding a
    second set of hooks that would drift from the first."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    rec = tmp_path / "r.json"
    status, _, err = frost("--events", str(log), "--record", str(rec), path,
                           cwd=str(tmp_path))
    assert status == 0, err
    assert rec.exists() and log.exists()
    assert "command.finish" in kinds(events_of(log))
    assert json.loads(rec.read_text())["events"], "the recording lost its events"


def test_a_replay_is_marked_as_one(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    rec = tmp_path / "r.json"
    frost("--record", str(rec), path, cwd=str(tmp_path))
    log = tmp_path / "e.ndjson"
    status, _, err = frost("--events", str(log), "--replay", str(rec), path,
                           cwd=str(tmp_path))
    assert status == 0, err
    finish = events_of(log)[-1]
    assert finish["replayed"] is True


def test_telemetry_alone_needs_no_recording(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    status, _, err = frost("--events", str(log), path, cwd=str(tmp_path))
    assert status == 0, err
    assert "command.finish" in kinds(events_of(log))


def test_file_and_environment_effects_are_reported(tmp_path):
    path = script(tmp_path,
                  'put "hello" into file "out.txt"\n'
                  'put file "out.txt" into back\n'
                  'delete file "out.txt"\n'
                  'put the environment variable "HOME" into h\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), path, cwd=str(tmp_path))
    seen = kinds(events_of(log))
    for expected in ("file.write", "file.read", "file.delete", "env.read"):
        assert expected in seen, expected


# ------------------------------------------------------------- refusals
#
# The event a security team most wants, and the one that never fired: the sink
# was created in the run path, below every gate, so a refused run produced no
# telemetry at all. A monitoring system that hears only about runs which got
# as far as starting is missing exactly the ones somebody needs to look at.

def test_a_policy_refusal_is_an_event(tmp_path):
    (tmp_path / "p.policy").write_text(
        'forbid running "curl"   -- egress goes through the proxy\n')
    path = script(tmp_path,
                  'run "curl" with "https://x.example" within 30 seconds\n'
                  "put it\n")
    log = tmp_path / "e.ndjson"
    status, out, _ = frost("--events", str(log), "--policy",
                           str(tmp_path / "p.policy"), path, cwd=str(tmp_path))
    assert status == 3
    assert out == ""
    finish = events_of(log)[-1]
    assert finish["event"] == "run.finish"
    assert finish["refused"] == "policy"
    assert finish["rules"][0]["what"] == 'running "curl"'
    assert finish["rules"][0]["hint"] == "egress goes through the proxy"


def test_a_refusal_names_the_policy_it_came_from(tmp_path):
    """Which rules were in force is the other half of the question, and a
    digest answers it without trusting a path."""
    (tmp_path / "p.policy").write_text('forbid running "curl"\n')
    path = script(tmp_path,
                  'run "curl" with "https://x.example" within 30 seconds\n'
                  "put it\n")
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), "--policy", str(tmp_path / "p.policy"), path,
          cwd=str(tmp_path))
    finish = events_of(log)[-1]
    assert finish["policies"][0]["sha256"]
    assert finish["policies"][0]["origin"] == "project"


def test_the_rules_in_force_are_reported_before_they_fire(tmp_path):
    (tmp_path / "p.policy").write_text('warn running "echo"\n')
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), "--policy", str(tmp_path / "p.policy"), path,
          cwd=str(tmp_path))
    loaded = [e for e in events_of(log) if e["event"] == "policy.loaded"]
    assert loaded and loaded[0]["rules"] == 1


def test_a_warning_is_reported_without_refusing(tmp_path):
    (tmp_path / "p.policy").write_text('warn running "echo"\n')
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    status, _, _ = frost("--events", str(log), "--policy",
                         str(tmp_path / "p.policy"), path, cwd=str(tmp_path))
    assert status == 0
    assert "policy.warned" in kinds(events_of(log))


def test_an_approval_refusal_is_an_event(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    frost("--approve", path, cwd=str(tmp_path))
    (tmp_path / "s.frost").write_text(
        'run "echo" with "a"\nput it\n'
        'run "curl" with "https://x.example" within 30 seconds\n')
    log = tmp_path / "e.ndjson"
    status, _, _ = frost("--events", str(log), path, cwd=str(tmp_path))
    assert status == 3
    finish = events_of(log)[-1]
    assert finish["refused"] == "approval"
    assert "it can now run curl" in finish["widenings"]


def test_an_import_ceiling_refusal_is_an_event(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "sneaky.frost").write_text(
        'to helper\n    run "curl" with "https://x.example"\nend helper\n')
    path = script(tmp_path,
                  'use "lib/sneaky.frost" for the helper\nhelper\n')
    log = tmp_path / "e.ndjson"
    status, _, _ = frost("--events", str(log), path, cwd=str(tmp_path))
    assert status == 3
    finish = events_of(log)[-1]
    assert finish["refused"] == "ceiling"
    assert finish["breaches"]


def test_a_refused_run_still_reports_a_finish(tmp_path):
    """Every refusal path closes the run out, so a dashboard counting starts
    against finishes does not drift every time a policy does its job."""
    (tmp_path / "p.policy").write_text('forbid running "echo"\n')
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--events", str(log), "--policy", str(tmp_path / "p.policy"), path,
          cwd=str(tmp_path))
    seen = kinds(events_of(log))
    assert seen[0] == "run.start"
    assert seen[-1] == "run.finish"


def test_analysis_produces_no_run_events(tmp_path):
    """--explain runs nothing, so there is no run to report on and a
    dashboard should not see one."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    log = tmp_path / "e.ndjson"
    frost("--explain", "--events", str(log), path, cwd=str(tmp_path))
    assert not log.exists() or log.read_text() == ""
