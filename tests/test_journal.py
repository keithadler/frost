"""Recording a run, replaying it, and streaming input.

Three things that were on the "still missing" list, and each is tested for
the property that makes it worth having rather than for the mechanism.

*Replay performs nothing.* Not "usually" and not "except writes" — a replayed
run spawns no process, writes no file and deletes nothing. A canary directory
proves it rather than a reading of the code.

*Divergence is a finding.* A reformat replays clean; a changed command is
reported with both sides named. That is what makes a recording a fixture.

*A stream that never ends can still be filtered.* The old behaviour read all
of standard input before the first line of the loop body ran, which made
frost useless for the shape every filter has.
"""

import io
import json
import os
import subprocess
import sys
import time

import pytest

from frostlang import journal as J
from frostlang.journal import Recorder, Player, Divergence
from frostlang.parser import parse
from frostlang.interp import Interpreter

from helpers import REPO, needs_coreutils


def frost(*args, stdin=None, cwd=None, timeout=60):
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, input=stdin,
                       env=env, cwd=cwd or REPO, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def script(tmp_path):
    def make(source, name="s.frost"):
        path = tmp_path / name
        path.write_text(source)
        return str(path)
    make.dir = tmp_path
    return make


# ------------------------------------------------------------- recording

@needs_coreutils
def test_a_recording_holds_what_the_run_did(script, tmp_path):
    path = script('run "echo" with "hello"\nput it\n')
    recording = str(tmp_path / "run.json")
    status, out, err = frost("--record", recording, path)
    assert (status, out.strip()) == (0, "hello"), err

    with open(recording) as fh:
        data = json.load(fh)
    assert data["schema"] == J.SCHEMA_VERSION
    commands = [e for e in data["events"] if e["kind"] == "command"]
    assert commands[0]["argv"] == ["echo", "hello"]
    assert commands[0]["stdout"].strip() == "hello"
    assert commands[0]["status"] == 0


@needs_coreutils
def test_a_recording_holds_file_reads_and_writes(script, tmp_path):
    source = tmp_path / "in.txt"
    source.write_text("contents\n")
    target = tmp_path / "out.txt"
    path = script(f'put file "{source}" into data\n'
                  f'put data into file "{target}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    kinds = [e["kind"] for e in json.load(open(recording))["events"]]
    assert "read" in kinds and "write" in kinds


def test_a_recording_holds_environment_reads(script, tmp_path, monkeypatch):
    path = script('put the environment variable "HOME" into home\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    events = json.load(open(recording))["events"]
    assert any(e["kind"] == "env-read" and e["name"] == "HOME"
               for e in events)


def test_a_recording_holds_the_standard_input(script, tmp_path):
    path = script("put the standard input\n")
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path, stdin="piped in\n")
    events = json.load(open(recording))["events"]
    assert any(e["kind"] == "stdin" and "piped in" in e["content"]
               for e in events)


# --------------------------------------------------------------- replay

@needs_coreutils
def test_replay_reproduces_the_output(script, tmp_path):
    path = script('run "echo" with "one"\nput it\n'
                  'run "echo" with "two"\nput it\n')
    recording = str(tmp_path / "run.json")
    _, recorded, _ = frost("--record", recording, path)
    status, replayed, err = frost("--replay", recording, path)
    assert (status, replayed) == (0, recorded), err


def test_replay_spawns_nothing(script, tmp_path):
    """The property, proved rather than assumed: the command it would run
    creates a file, and after replay that file does not exist."""
    canary = tmp_path / "canary.txt"
    path = script(f'run "touch" with "{canary}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    assert canary.exists(), "the recorded run should have created it"
    canary.unlink()

    status, _, err = frost("--replay", recording, path)
    assert status == 0, err
    assert not canary.exists(), "replay spawned the command"


def test_replay_writes_no_files(script, tmp_path):
    target = tmp_path / "written.txt"
    path = script(f'put "data" into file "{target}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    assert target.exists()
    target.unlink()

    frost("--replay", recording, path)
    assert not target.exists(), "replay wrote a file"


def test_replay_deletes_nothing(script, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("still here\n")
    path = script(f'delete file "{victim}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    assert not victim.exists()
    victim.write_text("still here\n")

    frost("--replay", recording, path)
    assert victim.exists(), "replay deleted a file"


def test_replay_serves_recorded_file_contents(script, tmp_path):
    source = tmp_path / "in.txt"
    source.write_text("original\n")
    path = script(f'put file "{source}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    source.write_text("changed on disk\n")
    status, out, _ = frost("--replay", recording, path)
    assert out.strip() == "original", "replay read the live file"


def test_replay_needs_a_recording(script):
    path = script('put "x"\n')
    status, _, err = frost("--replay", "/no/such/recording.json", path)
    assert (status, "cannot read" in err) == (2, True)


def test_record_and_replay_together_are_refused(script, tmp_path):
    path = script('put "x"\n')
    status, _, err = frost("--record", str(tmp_path / "a.json"),
                           "--replay", str(tmp_path / "b.json"), path)
    assert (status, "not both" in err) == (2, True)


# ------------------------------------------------------------ divergence

@needs_coreutils
def test_a_reformat_replays_clean(script, tmp_path):
    """The point of a fixture: a change that was meant to preserve behaviour
    either did or did not, and you find out without a real environment."""
    path = script('run "echo" with "one"\nput it\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    script('-- a new comment\n\nrun "echo" with "one"\n\nput it\n')
    status, _, err = frost("--replay", recording, path)
    assert status == 0, err


@needs_coreutils
def test_a_changed_command_diverges_and_names_both_sides(script, tmp_path):
    path = script('run "echo" with "one"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    script('run "echo" with "CHANGED"\n')
    status, _, err = frost("--replay", recording, path)
    assert status == 4
    assert "echo one" in err and "echo CHANGED" in err


@needs_coreutils
def test_a_dropped_command_is_caught_at_the_end(script, tmp_path):
    path = script('run "echo" with "one"\nrun "echo" with "two"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    script('run "echo" with "one"\n')
    status, _, err = frost("--replay", recording, path)
    assert status == 4
    assert "did not happen" in err


@needs_coreutils
def test_an_extra_command_diverges(script, tmp_path):
    path = script('run "echo" with "one"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    script('run "echo" with "one"\nrun "echo" with "extra"\n')
    status, _, err = frost("--replay", recording, path)
    assert status == 4
    assert "recording ended" in err


def test_a_changed_file_path_diverges(script, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a\n")
    b.write_text("b\n")
    path = script(f'put file "{a}"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)

    script(f'put file "{b}"\n')
    status, _, err = frost("--replay", recording, path)
    assert (status, "DIVERGED" in err) == (4, True)


def test_a_recording_from_the_future_is_refused(script, tmp_path):
    recording = tmp_path / "run.json"
    recording.write_text(json.dumps({"schema": 99, "events": []}))
    status, _, err = frost("--replay", str(recording), script('put "x"\n'))
    assert (status, "version 99" in err) == (2, True)


# ------------------------------------------------------------- secrets

def test_a_secret_value_is_never_recorded(script, tmp_path, monkeypatch):
    """A recording you cannot commit is not a fixture."""
    monkeypatch.setenv("FROST_JOURNAL_SECRET", "s3cr3t-canary-value")
    path = script('put the secret environment variable "FROST_JOURNAL_SECRET" '
                  "into pw\nput \"using\" && pw\n")
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    assert "s3cr3t-canary-value" not in open(recording).read()


@needs_coreutils
def test_a_revealed_secret_is_scrubbed_from_command_output(script, tmp_path,
                                                           monkeypatch):
    """A program handed a credential may well echo it back."""
    monkeypatch.setenv("FROST_JOURNAL_SECRET", "s3cr3t-canary-value")
    path = script('put the secret environment variable "FROST_JOURNAL_SECRET" '
                  'into pw\nrun "echo" with pw\nput "done"\n')
    recording = str(tmp_path / "run.json")
    frost("--record", recording, path)
    text = open(recording).read()
    assert "s3cr3t-canary-value" not in text
    assert "«secret" in text


# ------------------------------------------------- the pieces on their own

def test_the_recorder_scrubs_by_exact_match():
    recorder = Recorder()
    recorder.secrets["hunter2"] = "db password"
    recorder.command(1, ["echo"], None, None,
                     lambda: ("hunter2 and more", "", 0))
    assert "hunter2" not in json.dumps(recorder.events)
    assert "«secret db password»" in json.dumps(recorder.events,
                                              ensure_ascii=False)


def test_the_player_refuses_a_kind_it_did_not_expect():
    player = Player({"events": [{"kind": "read", "path": "a.txt"}]})
    with pytest.raises(Divergence) as e:
        player.command(1, ["echo"], None, None, lambda: ("", "", 0))
    assert "read a.txt" in e.value.msg


def test_the_player_reports_what_the_recording_had_left():
    player = Player({"events": [{"kind": "command", "argv": ["echo", "x"]}]})
    assert len(player.unconsumed()) == 1


def test_secret_events_carry_no_value():
    recorder = Recorder()
    recorder.note_secret("db password", "hunter2")
    assert recorder.events == [{"kind": "secret", "name": "db password"}]


# ------------------------------------------------------ streaming input

STREAM = ("repeat for each line in the standard input as row\n"
          '    put "saw:" && row\n'
          "end repeat\n")


def test_lines_arrive_as_they_are_produced(script):
    """The old behaviour read the whole stream before the first line of the
    body ran, which makes frost useless for the shape every filter has."""
    path = script(STREAM)
    producer = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time\n"
         "for w in ('one','two','three'):\n"
         "    print(w, flush=True); time.sleep(0.3)\n"],
        stdout=subprocess.PIPE)
    started = time.time()
    p = subprocess.Popen([sys.executable, os.path.join(REPO, "frost"), path],
                         stdin=producer.stdout, stdout=subprocess.PIPE,
                         text=True, env={**os.environ, "PYTHONPATH": REPO})
    first_line_at = None
    for line in p.stdout:
        if first_line_at is None:
            first_line_at = time.time() - started
    p.wait(timeout=30)
    producer.wait(timeout=30)
    assert first_line_at is not None
    assert first_line_at < 0.6, (
        f"the first line took {first_line_at:.2f}s, so the whole stream was "
        f"read before the loop body ran")


def test_a_stream_that_never_ends_can_be_filtered(script):
    """`exit repeat` gets out of an unbounded producer."""
    path = script("put 0 into seen\n"
                  "repeat for each line in the standard input as row\n"
                  "    add 1 to seen\n"
                  "    if seen is at least 3 then exit repeat\n"
                  "end repeat\n"
                  'put "stopped after" && seen\n')
    producer = subprocess.Popen(
        [sys.executable, "-c",
         "import sys,time,itertools\n"
         "for i in itertools.count():\n"
         "    print(i, flush=True); time.sleep(0.05)\n"],
        stdout=subprocess.PIPE)
    try:
        p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), path],
                           stdin=producer.stdout, capture_output=True,
                           text=True, timeout=20,
                           env={**os.environ, "PYTHONPATH": REPO})
    finally:
        producer.kill()
    assert p.stdout.strip() == "stopped after 3"


def test_every_line_is_seen(script):
    path = script(STREAM)
    status, out, err = frost(path, stdin="one\ntwo\nthree\n")
    assert (status, out) == (0, "saw: one\nsaw: two\nsaw: three\n"), err


def test_an_empty_stream_runs_the_body_no_times(script):
    path = script(STREAM + 'put "done"\n')
    status, out, _ = frost(path, stdin="")
    assert out.strip() == "done"


def test_a_trailing_newline_does_not_add_an_empty_line(script):
    path = script(STREAM)
    _, out, _ = frost(path, stdin="one\n")
    assert out == "saw: one\n"


def test_reading_the_whole_stream_still_works(script):
    path = script("put the number of lines in the standard input\n")
    _, out, _ = frost(path, stdin="a\nb\nc\n")
    assert out.strip() == "3"


def test_after_a_streaming_loop_the_input_is_consumed(script):
    path = script(STREAM + 'put "[" & the standard input & "]"\n')
    _, out, _ = frost(path, stdin="one\ntwo\n")
    assert out.endswith("[]\n")


def test_an_early_exit_leaves_the_rest_readable(script):
    """The lines not consumed are still in the pipe for whoever asks next."""
    path = script("repeat for each line in the standard input as row\n"
                  '    put "first:" && row\n'
                  "    exit repeat\n"
                  "end repeat\n"
                  'put "rest:" && the standard input\n')
    _, out, _ = frost(path, stdin="one\ntwo\nthree\n")
    assert "first: one" in out
    assert "two" in out and "three" in out


def test_a_streaming_run_can_be_recorded_and_replayed(script, tmp_path):
    path = script(STREAM)
    recording = str(tmp_path / "run.json")
    _, recorded, err = frost("--record", recording, path,
                             stdin="one\ntwo\n")
    assert recorded == "saw: one\nsaw: two\n", err
    status, replayed, err = frost("--replay", recording, path)
    assert (status, replayed) == (0, recorded), err


# ------------------------------------------- the recorder, in this process
#
# The tests above drive frost as a subprocess, which is what proves the CLI
# and the exit codes. A subprocess contributes nothing to coverage and cannot
# see inside, so the mechanism is also exercised directly.

def run_with(source, journal, stdin=None, cwd=None):
    """Run a script with a journal attached; return its stdout."""
    interp = Interpreter(cwd=cwd)
    interp.journal = journal
    held_out, held_in = sys.stdout, sys.stdin
    sys.stdout = io.StringIO()
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        interp.run_program(parse(source))
        return sys.stdout.getvalue()
    finally:
        sys.stdout, sys.stdin = held_out, held_in


@needs_coreutils
def test_the_recorder_captures_a_command_in_process():
    recorder = Recorder()
    out = run_with('run "echo" with "hi"\nput it\n', recorder)
    assert out.strip() == "hi"
    [event] = [e for e in recorder.events if e["kind"] == "command"]
    assert event["argv"] == ["echo", "hi"]


def test_the_recorder_captures_a_file_read(tmp_path):
    source = tmp_path / "in.txt"
    source.write_text("contents\n")
    recorder = Recorder()
    run_with(f'put file "{source}"\n', recorder)
    assert any(e["kind"] == "read" and e["content"] == "contents"
               for e in recorder.events)


def test_the_recorder_captures_an_existence_check(tmp_path):
    recorder = Recorder()
    run_with(f'if file "{tmp_path / "nope"}" exists then put "yes"\n',
             recorder)
    assert any(e["kind"] == "exists" and e["answer"] is False
               for e in recorder.events)


def test_the_recorder_captures_an_environment_write():
    recorder = Recorder()
    run_with('put "x" into the environment variable "FROST_J"\n', recorder)
    assert any(e["kind"] == "env-write" and e["name"] == "FROST_J"
               for e in recorder.events)


def test_the_recorder_captures_a_delete(tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("x\n")
    recorder = Recorder()
    run_with(f'delete file "{victim}"\n', recorder)
    assert any(e["kind"] == "delete" for e in recorder.events)
    assert not victim.exists()


def test_the_recorder_serialises_with_the_run(tmp_path):
    recorder = Recorder()
    recorder.status = 3
    path = tmp_path / "run.json"
    recorder.save(str(path), "s.frost", ["a", "b"])
    data = json.loads(path.read_text())
    assert data["exit"] == 3
    assert data["arguments"] == ["a", "b"]
    assert data["script"] == "s.frost"


# --------------------------------------------- the player, in this process

def test_the_player_serves_a_command_without_spawning(tmp_path):
    canary = tmp_path / "canary.txt"
    player = Player({"events": [
        {"kind": "command", "argv": ["touch", str(canary)],
         "stdout": "", "stderr": "", "status": 0}]})
    run_with(f'run "touch" with "{canary}"\n', player)
    assert not canary.exists()


def test_the_player_serves_recorded_output():
    player = Player({"events": [
        {"kind": "command", "argv": ["echo", "x"],
         "stdout": "recorded\n", "stderr": "", "status": 0}]})
    out = run_with('run "echo" with "x"\nput it\n', player)
    assert out.strip() == "recorded"


def test_the_player_suppresses_a_write(tmp_path):
    target = tmp_path / "out.txt"
    player = Player({"events": [
        {"kind": "write", "path": str(target), "content": "data"}]})
    run_with(f'put "data" into file "{target}"\n', player)
    assert not target.exists()
    assert player.performed == [("write", str(target))]


def test_the_player_suppresses_a_delete(tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("x\n")
    player = Player({"events": [{"kind": "delete", "path": str(victim)}]})
    run_with(f'delete file "{victim}"\n', player)
    assert victim.exists()


def test_the_player_serves_an_environment_read():
    player = Player({"events": [
        {"kind": "env-read", "name": "HOME", "value": "/recorded/home"}]})
    out = run_with('put the environment variable "HOME"\n', player)
    assert out.strip() == "/recorded/home"


def test_the_player_serves_an_existence_check(tmp_path):
    player = Player({"events": [
        {"kind": "exists", "path": str(tmp_path / "x"), "answer": True}]})
    out = run_with(f'if file "{tmp_path / "x"}" exists then put "yes"\n',
                   player)
    assert out.strip() == "yes"


def test_the_player_diverges_on_a_different_path(tmp_path):
    player = Player({"events": [
        {"kind": "read", "path": "/recorded/a.txt", "content": "x"}]})
    with pytest.raises(Divergence) as e:
        run_with('put file "/live/b.txt"\n', player)
    assert "/recorded/a.txt" in e.value.msg


def test_the_player_diverges_when_the_recording_runs_out():
    player = Player({"events": []})
    with pytest.raises(Divergence) as e:
        run_with('run "echo" with "x"\n', player)
    assert "recording ended" in e.value.msg


def test_the_player_skips_secret_events():
    """They carry no value to serve, so they must not be matched against."""
    player = Player({"events": [
        {"kind": "secret", "name": "db password"},
        {"kind": "env-read", "name": "HOME", "value": "/x"}]})
    assert run_with('put the environment variable "HOME"\n',
                    player).strip() == "/x"


def test_describing_every_event_kind():
    for event, expected in (
            ({"kind": "command", "argv": ["ls", "-l"]}, "run ls -l"),
            ({"kind": "read", "path": "a.txt"}, "read a.txt"),
            ({"kind": "env-read", "name": "HOME"}, "env-read HOME"),
            ({"kind": "stdin"}, "stdin")):
        assert J._describe(event) == expected


# ------------------------------------------- the run worth recording failed

def frost_cli(*args, cwd=None, timeout=60):
    import subprocess
    import sys as _sys
    env = {**os.environ, "PYTHONPATH": REPO}
    p = subprocess.run([_sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=env, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def write_script(tmp_path, text):
    path = tmp_path / "s.frost"
    path.write_text(text.lstrip("\n"))
    return str(path)


def test_a_failing_run_is_still_recorded(tmp_path):
    """The recording only survived success, which threw away the case it is
    most wanted for. A run that failed is exactly the one somebody needs to
    read afterwards."""
    script = write_script(tmp_path, 'run "echo" with "one"\nrun "false"\n')
    rec = tmp_path / "run.json"
    status, _, err = frost_cli("--record", str(rec), script, cwd=str(tmp_path))
    assert status == 1
    assert rec.exists(), "the failing run left no recording"

    payload = json.loads(rec.read_text())
    assert payload["exit"] == 1
    kinds = [e["kind"] for e in payload["events"]]
    assert kinds == ["command", "command"]
    assert payload["events"][-1]["status"] == 1


def test_a_successful_run_is_still_recorded(tmp_path):
    script = write_script(tmp_path, 'run "echo" with "one"\n')
    rec = tmp_path / "run.json"
    status, _, _ = frost_cli("--record", str(rec), script, cwd=str(tmp_path))
    assert status == 0
    assert json.loads(rec.read_text())["exit"] == 0


def test_the_recorded_status_is_the_one_the_script_exited_with(tmp_path):
    script = write_script(tmp_path, 'quit with status 3\n')
    rec = tmp_path / "run.json"
    frost_cli("--record", str(rec), script, cwd=str(tmp_path))
    assert json.loads(rec.read_text())["exit"] == 3


# ------------------------------------------------------------- the trace

def test_the_trace_goes_to_a_file(tmp_path):
    script = write_script(tmp_path, 'put "one"\nput "two"\n')
    log = tmp_path / "t.log"
    status, _, _ = frost_cli("--trace-to-file", str(log), script,
                             cwd=str(tmp_path))
    assert status == 0
    assert log.exists()
    assert len(log.read_text().strip().split("\n")) == 2


def test_the_trace_shows_the_line_somebody_wrote(tmp_path):
    """`[frost] line 5: Run` names the interpreter's internals. The line the
    author wrote is what they are looking for."""
    script = write_script(tmp_path, 'run "echo" with "hello"\n')
    log = tmp_path / "t.log"
    frost_cli("--trace-to-file", str(log), script, cwd=str(tmp_path))
    text = log.read_text()
    assert 'run "echo" with "hello"' in text
    assert "Run\n" not in text, "the trace still names AST classes"


def test_the_trace_survives_a_run_that_fails(tmp_path):
    """Flushed as it goes, because the run worth tracing is often the one that
    never finishes, and a buffered trace of a wedged script is an empty file."""
    script = write_script(tmp_path, 'put "before"\nrun "false"\nput "after"\n')
    log = tmp_path / "t.log"
    status, _, _ = frost_cli("--trace-to-file", str(log), script,
                             cwd=str(tmp_path))
    assert status == 1
    assert 'put "before"' in log.read_text()


def test_tracing_to_a_file_needs_no_second_flag(tmp_path):
    script = write_script(tmp_path, 'put "x"\n')
    log = tmp_path / "t.log"
    frost_cli("--trace-to-file", str(log), script, cwd=str(tmp_path))
    assert log.read_text().strip()


def test_an_unwritable_trace_is_refused_before_the_script_runs(tmp_path):
    script = write_script(tmp_path, 'put "x"\n')
    status, out, err = frost_cli("--trace-to-file",
                                 str(tmp_path / "nope" / "t.log"), script,
                                 cwd=str(tmp_path))
    assert status == 2
    assert "cannot write the trace" in err
    assert out == "", "the script ran despite the trace being unusable"


def test_the_trace_does_not_reveal_a_secret(tmp_path):
    """It prints source text, never runtime values, so a credential cannot
    reach it. Worth pinning: a trace is a file people paste into tickets."""
    creds = tmp_path / "c.txt"
    creds.write_text("hunter2")
    script = write_script(
        tmp_path,
        f'put the secret file "{creds}" into pw\nput "using" && pw\n')
    log = tmp_path / "t.log"
    frost_cli("--trace-to-file", str(log), script, cwd=str(tmp_path))
    assert "hunter2" not in log.read_text()


# ------------------------------------------------- flags that take a value

def test_every_value_taking_flag_is_known_to_the_splitter():
    """The list of value-taking options used to sit beside the parser instead
    of being read off it, and went stale the moment a flag was added:
    `--trace-to-file out.log s.frost` silently treated out.log as the script.
    """
    import argparse
    from frostlang import cli

    parser = argparse.ArgumentParser()
    for flag in ("--policy", "--record", "--trace-to-file"):
        parser.add_argument(flag, metavar="FILE")
    derived = cli.value_options(parser)
    assert {"--policy", "--record", "--trace-to-file"} <= derived


def test_a_value_flag_before_the_script_does_not_eat_it(tmp_path):
    script = write_script(tmp_path, 'put item 1 of the arguments\n')
    log = tmp_path / "t.log"
    status, out, err = frost_cli("--trace-to-file", str(log), script,
                                 "an-argument", cwd=str(tmp_path))
    assert status == 0, err
    assert out.strip() == "an-argument"
