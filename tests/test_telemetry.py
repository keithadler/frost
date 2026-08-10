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


# ------------------------------------------------------------ OTLP traces
#
# NDJSON stays the default: every collector reads it and a line-oriented file
# survives a run killed halfway. OTLP is what New Relic and Datadog would
# rather have, and the instrumentation already existed, because a command has
# a start, an end and a status, which is a span with the labels changed.

def trace_of(path):
    with open(path) as fh:
        return json.load(fh)


def spans_of(path):
    return trace_of(path)["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attr(span, key):
    """One attribute, unwrapped from OTLP's typed-value envelope."""
    for a in span.get("attributes", []):
        if a["key"] != key:
            continue
        value = a["value"]
        if "arrayValue" in value:
            return [list(v.values())[0] for v in value["arrayValue"]["values"]]
        return list(value.values())[0]
    return None


def test_a_run_becomes_a_trace_with_a_span_per_command(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n'
                            'run "echo" with "b"\nput it\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    spans = spans_of(out)
    assert len(spans) == 3, "a root and one span per command"
    assert spans[0]["name"].startswith("frost ")
    assert all(s["parentSpanId"] == spans[0]["spanId"] for s in spans[1:])
    assert len({s["traceId"] for s in spans}) == 1


def test_a_span_carries_a_real_duration(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    command = spans_of(out)[1]
    started = int(command["startTimeUnixNano"])
    ended = int(command["endTimeUnixNano"])
    assert ended > started
    assert started > 1_700_000_000_000_000_000, "not nanoseconds since epoch"


def test_a_failing_command_is_an_error_span(tmp_path):
    path = script(tmp_path, 'try to run "false"\nput "done"\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    command = spans_of(out)[1]
    assert command["status"]["code"] == 2
    assert attr(command, "process.exit.code") == "1"


def test_the_trace_id_is_derived_from_the_run_id(tmp_path):
    """A replay of a recording produces the trace id of the run it replays,
    for the same reason the clock is recorded: a fixture whose identity moves
    is not one."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for out in (first, second):
        frost("--run-id", "job-7", "--events", str(out), "--events-format",
              "otel", path, cwd=str(tmp_path))
    assert spans_of(first)[0]["traceId"] == spans_of(second)[0]["traceId"]


def test_a_different_run_gets_a_different_trace(tmp_path):
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    frost("--run-id", "job-7", "--events", str(a), "--events-format", "otel",
          path, cwd=str(tmp_path))
    frost("--run-id", "job-8", "--events", str(b), "--events-format", "otel",
          path, cwd=str(tmp_path))
    assert spans_of(a)[0]["traceId"] != spans_of(b)[0]["traceId"]


def test_a_refusal_is_an_attribute_and_not_mistaken_for_a_crash(tmp_path):
    """A monitoring system that cannot tell a refused run from a broken one
    will page for the wrong thing."""
    (tmp_path / "p.policy").write_text('forbid running "echo"\n')
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", "--policy",
          str(tmp_path / "p.policy"), path, cwd=str(tmp_path))
    root = spans_of(out)[0]
    assert attr(root, "frost.refused") == "policy"
    assert attr(root, "frost.exit_code") == "3"


def test_the_resource_names_the_tool_and_version(tmp_path):
    from frostlang import __version__
    path = script(tmp_path, 'put "x"\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    resource = trace_of(out)["resourceSpans"][0]["resource"]["attributes"]
    names = {a["key"]: list(a["value"].values())[0] for a in resource}
    assert names["service.name"] == "frost"
    assert names["service.version"] == __version__


def test_unused_capabilities_reach_the_trace(tmp_path):
    """The signal worth keeping whichever format it arrives in."""
    path = script(tmp_path,
                  'if 1 is 2 then\n'
                  '    run "curl" with "https://x.example" within 30 seconds\n'
                  "end if\n"
                  'run "echo" with "a"\nput it\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    assert "curl" in attr(spans_of(out)[0], "frost.programs_unused")


def test_a_secret_never_reaches_a_span(tmp_path):
    (tmp_path / "pw.txt").write_text("hunter2")
    path = script(tmp_path,
                  'put the secret file "pw.txt" into pw\n'
                  'try to run "echo" with "--token", pw\nput "done"\n')
    out = tmp_path / "t.json"
    frost("--events", str(out), "--events-format", "otel", path,
          cwd=str(tmp_path))
    assert "hunter2" not in out.read_text()


def test_ndjson_remains_the_default(tmp_path):
    """It streams, and a run killed halfway still leaves every line up to the
    moment it died. OTLP is a batch format and cannot."""
    path = script(tmp_path, 'run "echo" with "a"\nput it\n')
    out = tmp_path / "e.ndjson"
    frost("--events", str(out), path, cwd=str(tmp_path))
    assert out.read_text().startswith('{"schema"')


# --------------------------- the sinks in process, so coverage sees them
#
# Driven through subprocesses these prove the wiring and measure nothing.
# telemetry.py read 37% while every branch in it ran, which is the fourth time
# in this project that the same shortcut has produced the same result.

import io as _io


class Kept(_io.StringIO):
    """A stream that survives being closed, so a test can read what a sink
    wrote after it finished writing."""

    def close(self):
        pass


class Recorded:
    """A journal that answers without doing anything, so the observer can be
    exercised without a filesystem or a process."""

    replaying = False

    def command(self, line, argv, stdin, folder, run):
        return "out", "err", 0

    def read_file(self, line, path, read):
        return "contents"

    def file_exists(self, line, path, check):
        return True

    def write_file(self, line, path, content, write):
        return None

    def delete_file(self, line, path, delete):
        return None

    def env_read(self, line, name, value):
        return value

    def env_write(self, line, name, value):
        return None

    def standard_input(self, read):
        return "piped"

    def clock(self, line, which, read):
        return "2026-01-01"

    def wait(self, line, seconds, sleep):
        return None

    def run_id(self, line, value):
        return value

    def note_secret(self, name, plaintext):
        return None


def sink_and_observer(cls=T.Sink, inner=None, **kw):
    stream = Kept()
    sink = cls(stream, run_id="r", script="s.frost", **kw)
    return sink, T.Observer(sink, inner), stream


def test_the_observer_forwards_every_effect_to_the_journal():
    """It wraps whatever journal is in use rather than adding a second set of
    hooks; if it stopped forwarding, a recording would silently lose events."""
    inner = Recorded()
    _, observer, stream = sink_and_observer(inner=inner)
    assert observer.command(1, ["echo", "a"], None, None,
                            lambda: ("x", "", 0)) == ("out", "err", 0)
    assert observer.read_file(2, "a.txt", lambda: "raw") == "contents"
    assert observer.file_exists(3, "a.txt", lambda: False) is True
    assert observer.standard_input(lambda: "raw") == "piped"
    assert observer.clock(4, "date", lambda w: "now") == "2026-01-01"
    assert observer.run_id(5, "given") == "given"
    observer.write_file(6, "o.txt", "body", lambda: None)
    observer.delete_file(7, "o.txt", lambda: None)
    observer.env_read(8, "HOME", "/root")
    observer.env_write(9, "CC", "clang")
    observer.wait(10, 0.0, lambda s: None)
    observer.note_secret("token", "s3cret")
    seen = [json.loads(l)["event"] for l in stream.getvalue().splitlines()]
    for kind in ("command.start", "command.finish", "file.read",
                 "file.checked", "stdin.read", "clock.read", "file.write",
                 "file.delete", "env.read", "env.write", "wait",
                 "secret.read"):
        assert kind in seen, kind


def test_the_observer_works_with_no_journal_at_all():
    _, observer, stream = sink_and_observer(inner=None)
    assert observer.command(1, ["echo"], None, None,
                            lambda: ("hi", "", 0))[0] == "hi"
    assert observer.read_file(2, "a", lambda: "body") == "body"
    assert observer.replaying is False


def test_a_command_that_raises_is_still_reported():
    """A command that never returned is the one worth knowing about."""
    _, observer, stream = sink_and_observer(inner=None)

    def boom():
        raise OSError("no such program")

    with pytest.raises(OSError):
        observer.command(1, ["nope"], None, None, boom)
    assert "command.failed" in stream.getvalue()


def test_the_observer_counts_what_it_saw():
    _, observer, _ = sink_and_observer(inner=None)
    observer.command(1, ["curl", "https://x.example/a"], None, None,
                     lambda: ("", "", 0))
    observer.wait(2, 1.5, lambda s: None)
    assert observer.commands == 1
    assert observer.programs == {"curl"}
    assert observer.hosts == {"x.example"}
    assert observer.waited == 1.5


def test_an_otel_document_is_built_without_a_run():
    """Nothing ran, so there is one span and it still names the tool."""
    sink, _, stream = sink_and_observer(T.OtelSink, version="9.9.9")
    sink.close()
    doc = json.loads(stream.getvalue())
    resource = doc["resourceSpans"][0]["resource"]["attributes"]
    assert {a["key"] for a in resource} == {"service.name", "service.version"}
    assert len(doc["resourceSpans"][0]["scopeSpans"][0]["spans"]) == 1


def test_otel_attributes_carry_their_types():
    sink = T.OtelSink(_io.StringIO(), run_id="r", script="s")
    out = {a["key"]: a["value"] for a in sink._attributes({
        "a": "text", "b": 7, "c": True, "d": ["x", "y"],
        "empty": "", "missing": None})}
    assert out["a"] == {"stringValue": "text"}
    assert out["b"] == {"intValue": "7"}
    assert out["c"] == {"boolValue": True}
    assert out["d"]["arrayValue"]["values"][0] == {"stringValue": "x"}
    assert "empty" not in out and "missing" not in out


def test_an_otel_span_pairs_the_start_with_the_finish():
    """The argument vector is on the start event only, so the sink has to hold
    it: repeating it on the finish put a command's arguments into an event
    whose contract is sizes rather than contents."""
    sink, observer, stream = sink_and_observer(T.OtelSink)
    sink.emit("run.start", declares={})
    observer.command(1, ["echo", "hello"], None, None, lambda: ("hi", "", 0))
    sink.emit("run.finish", status=0, commands=1)
    sink.close()
    spans = json.loads(stream.getvalue())["resourceSpans"][0]["scopeSpans"][0]["spans"]
    command = spans[1]
    args = [list(v.values())[0] for v in
            [a for a in command["attributes"]
             if a["key"] == "process.command_args"][0]["value"]["arrayValue"]["values"]]
    assert args == ["echo", "hello"]


def test_the_sink_closes_a_stream_it_was_given():
    stream = _io.StringIO()
    T.Sink(stream, run_id="r", script="s").close()
    assert stream.closed
