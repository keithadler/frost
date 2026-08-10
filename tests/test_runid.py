"""One identity per execution.

These scripts are run by agents and pipelines, so the question afterwards is
never "what happened" but "what did *that* run do": the one in the incident,
the one whose fixture is on disk, the one an API saw a duplicate request from.
Without an identity every run looks alike and a recording is a file with no
join key.
"""

import json
import os
import re
import subprocess
import sys

import pytest

from frostlang import runid as R

from helpers import REPO, out as run_source


def frost(*args, cwd=None, env=None, timeout=60):
    environ = {**os.environ, "PYTHONPATH": REPO}
    environ.pop(R.ENV_NAME, None)
    environ.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=environ, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def script(tmp_path, text, name="s.frost"):
    path = tmp_path / name
    path.write_text(text.lstrip("\n"))
    return str(path)


# ------------------------------------------------------------ where it comes from

def test_a_supplied_id_wins():
    """An outside id always wins: joining frost's record to the pipeline's is
    the entire point, and a job id is more useful than anything frost could
    invent."""
    assert R.resolve("ci-job-42", {R.ENV_NAME: "from-env"}) == \
        ("ci-job-42", "--run-id")


def test_the_environment_is_used_when_nothing_is_supplied():
    assert R.resolve(None, {R.ENV_NAME: "agent:task:9"}) == \
        ("agent:task:9", R.ENV_NAME)


def test_one_is_generated_when_nothing_says_otherwise():
    value, source = R.resolve(None, {})
    assert source == "generated"
    assert re.fullmatch(r"[0-9a-f-]{36}", value)


def test_generated_ids_do_not_repeat():
    assert len({R.generate() for _ in range(200)}) == 200


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("bad", [
    "with space", "has/slash", "line\nbreak", "tab\there", "", "-leading",
    "semi;colon", "quote\"mark", "back\\slash", "x" * 129,
])
def test_a_dangerous_id_is_refused(bad):
    """It reaches log lines, child environments and any path a script builds
    from it. A newline forges a log entry and a slash moves a file, which is
    the familiar shape of trusting text from elsewhere."""
    with pytest.raises(R.RunIdError):
        R.validate(bad, "--run-id")


@pytest.mark.parametrize("good", [
    "ci-job-42", "agent:task:9", "run_1", "a.b.c", "0123456789abcdef",
    "x" * 128,
])
def test_an_ordinary_id_is_accepted(good):
    assert R.validate(good, "--run-id") == good


def test_a_refusal_says_where_the_id_came_from():
    with pytest.raises(R.RunIdError) as e:
        R.validate("bad value", R.ENV_NAME)
    assert R.ENV_NAME in e.value.msg


def test_a_bad_id_is_refused_before_the_script_runs(tmp_path):
    path = script(tmp_path, 'put "ran"\n')
    status, out, err = frost("--run-id", "has/slash", path, cwd=str(tmp_path))
    assert status == 2
    assert out == "", "the script ran despite an unusable run id"
    assert "not allowed" in err


# --------------------------------------------------------------- in a script

def test_the_id_is_readable_and_stable_within_a_run():
    first, second = run_source("put the run id\nput the run id\n").split("\n")
    assert first == second and first


def test_the_supplied_id_reaches_the_script(tmp_path):
    path = script(tmp_path, "put the run id\n")
    _, out, _ = frost("--run-id", "ci-job-42", path, cwd=str(tmp_path))
    assert out.strip() == "ci-job-42"


def test_the_environment_id_reaches_the_script(tmp_path):
    path = script(tmp_path, "put the run id\n")
    _, out, _ = frost(path, cwd=str(tmp_path), env={R.ENV_NAME: "from-ci"})
    assert out.strip() == "from-ci"


def test_children_inherit_the_id(tmp_path):
    """A log line from a program three layers down has to be tied back to the
    frost run that caused it."""
    path = script(tmp_path,
                  'run "sh" with "-c", "echo $FROST_RUN_ID"\nput it\n')
    _, out, _ = frost("--run-id", "ci-job-42", path, cwd=str(tmp_path))
    assert out.strip() == "ci-job-42"


def test_the_id_makes_an_isolated_scratch_path(tmp_path):
    """The use it exists for: a path built from the id cannot collide with a
    concurrent run."""
    path = script(tmp_path,
                  'put "build/" & the run id & "/out.txt" into scratch\n'
                  "put scratch\n")
    _, out, _ = frost("--run-id", "job-7", path, cwd=str(tmp_path))
    assert out.strip() == "build/job-7/out.txt"


# ------------------------------------------------------- recording and replay

def test_the_recording_carries_the_id_at_the_top_level(tmp_path):
    """So a recording can be joined to an audit log without being parsed."""
    path = script(tmp_path, 'run "echo" with "x"\n')
    rec = tmp_path / "run.json"
    frost("--run-id", "job-7", "--record", str(rec), path, cwd=str(tmp_path))
    assert json.loads(rec.read_text())["run"] == "job-7"


def test_a_replay_reports_the_run_it_is_replaying(tmp_path):
    """Serving a fresh id would make every replay of a script that stamps one
    differ from its recording, which is what a fixture exists to prevent."""
    path = script(tmp_path, "put the run id\n")
    rec = tmp_path / "run.json"
    frost("--run-id", "first", "--record", str(rec), path, cwd=str(tmp_path))
    status, out, err = frost("--run-id", "second", "--replay", str(rec), path,
                             cwd=str(tmp_path))
    assert status == 0, err
    assert out.strip() == "first"


def test_two_replays_agree(tmp_path):
    path = script(tmp_path, "put the run id\n")
    rec = tmp_path / "run.json"
    frost("--record", str(rec), path, cwd=str(tmp_path))
    _, first, _ = frost("--replay", str(rec), path, cwd=str(tmp_path))
    _, again, _ = frost("--replay", str(rec), path, cwd=str(tmp_path))
    assert first == again and first.strip()


def test_the_trace_file_opens_with_the_run(tmp_path):
    path = script(tmp_path, 'put "x"\n')
    log = tmp_path / "t.log"
    frost("--run-id", "job-7", "--trace-to-file", str(log), path,
          cwd=str(tmp_path))
    assert log.read_text().splitlines()[0] == "[frost] run job-7 (--run-id)"


# ---------------------------------------------------------- a closed pipe

def test_a_closed_reader_is_not_a_crash(tmp_path):
    """`frost s.frost | head` is an ordinary thing to do. A traceback there
    says frost broke when the shell did exactly what it was asked."""
    path = script(tmp_path, "repeat 5000 times\n    put the run id\nend repeat\n")
    proc = subprocess.run(
        f'"{sys.executable}" "{os.path.join(REPO, "frost")}" "{path}" | head -1',
        shell=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": REPO}, cwd=str(tmp_path), timeout=60)
    assert "Traceback" not in proc.stderr
    assert "BrokenPipeError" not in proc.stderr
    assert proc.stdout.strip()
