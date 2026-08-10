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
