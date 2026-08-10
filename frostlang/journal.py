"""Recording a run, and replaying it without touching anything.

A frost script's capabilities are knowable before it runs. What it actually
*did* was not knowable at all — you ran it and watched. That gap is what this
closes:

    frost --record run.json deploy.frost      # run it, write down everything
    frost --replay run.json deploy.frost      # run it again, spawn nothing

A recording holds every input the run consumed and every effect it produced:
each command with its arguments, standard input, output and exit status; each
file read and its contents; each environment variable read; whatever was piped
in. Replay serves those recorded answers back and performs no effects at all —
no process is spawned, no file is written, nothing is deleted.

Two things fall out.

**Snapshot testing for shell scripts.** A recording is a fixture. Change the
script, replay it, and every command it would run is compared against what it
ran before. A refactor that was meant to be behaviour-preserving either is or
is not, and you find out without a database or a network.

**Divergence is a finding, not a crash.** If the script asks for something the
recording does not have, that is reported as a difference — the recorded run
did X here, this one wants Y — because that is the useful output. A stack
trace would only tell you it happened.

## Secrets

Values are never recorded. A secret read is written down as its *name*, and
the plaintext is replaced on replay by a marker that carries the same seal, so
the redaction rules still hold. Any plaintext the run revealed is also scrubbed
from recorded command output, since a program handed a credential may well
echo it back.

That makes a recording safe to commit, which is the point — a fixture you
cannot check in is not a fixture. The scrubbing is exact-match and says so: a
program that transforms a secret before printing it will defeat it, and the
manifest already reports that the secret was released there.
"""
# SPDX-License-Identifier: MIT

import json

SCHEMA_VERSION = 1


class Divergence(Exception):
    """The script asked for something the recording does not have."""

    def __init__(self, msg, line=None, expected=None, actual=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line
        self.expected = expected
        self.actual = actual


def _scrub(text, secrets):
    """Remove any revealed plaintext from text about to be written down."""
    if not text or not secrets:
        return text
    for value, name in secrets.items():
        if value and value in text:
            text = text.replace(value, f"«secret {name}»")
    return text


def _scrub_event(event, secrets):
    """Scrub every string anywhere in an event.

    Field by field is how a leak gets in: the first version of this scrubbed
    output and forgot the argument list, so `run "psql" with password` wrote
    the credential straight into the recording. Walking the whole structure
    means a field added later is covered without anyone remembering to.
    """
    if not secrets:
        return event
    if isinstance(event, str):
        return _scrub(event, secrets)
    if isinstance(event, dict):
        return {k: _scrub_event(v, secrets) for k, v in event.items()}
    if isinstance(event, list):
        return [_scrub_event(v, secrets) for v in event]
    return event


class Recorder:
    """Runs for real, and writes down what happened."""

    replaying = False

    def __init__(self, identity="", policies=()):
        self.identity = identity
        # Which rules governed this run, by path and digest. A recording that
        # cannot say what constrained it proves a policy existed, never that
        # this run was subject to it.
        self.policies = list(policies)
        self.events = []
        self.secrets = {}          # plaintext -> name, for scrubbing
        self.stdout = ""
        self.stderr = ""
        self.status = 0

    def _append(self, event):
        self.events.append(_scrub_event(event, self.secrets))

    # -- effects

    def note_secret(self, name, plaintext):
        if plaintext:
            self.secrets[plaintext] = name
        self._append({"kind": "secret", "name": name})

    def command(self, line, argv, stdin, folder, run):
        """`run` performs the real thing and returns (stdout, stderr, status)."""
        stdout, stderr, status = run()
        self._append({
            "kind": "command", "line": line, "argv": list(argv),
            "stdin": _scrub(stdin, self.secrets),
            "folder": folder,
            "stdout": _scrub(stdout, self.secrets),
            "stderr": _scrub(stderr, self.secrets),
            "status": status,
        })
        return stdout, stderr, status

    def read_file(self, line, path, read):
        content = read()
        self._append({"kind": "read", "line": line, "path": path,
                            "content": _scrub(content, self.secrets)})
        return content

    def file_exists(self, line, path, check):
        answer = check()
        self._append({"kind": "exists", "line": line, "path": path,
                            "answer": answer})
        return answer

    def write_file(self, line, path, content, write):
        self._append({"kind": "write", "line": line, "path": path,
                            "content": _scrub(content, self.secrets)})
        return write()

    def delete_file(self, line, path, delete):
        self._append({"kind": "delete", "line": line, "path": path})
        return delete()

    def env_read(self, line, name, value):
        self._append({"kind": "env-read", "line": line, "name": name,
                            "value": _scrub(value, self.secrets)})
        return value

    def env_write(self, line, name, value):
        self._append({"kind": "env-write", "line": line, "name": name,
                            "value": _scrub(value, self.secrets)})

    def standard_input(self, read):
        content = read()
        self._append({"kind": "stdin", "content": content})
        return content

    def run_id(self, line, value):
        self._append({"kind": "run-id", "line": line, "value": value})
        return value

    def clock(self, line, which, read):
        value = read(which)
        self._append({"kind": "clock", "line": line, "which": which,
                      "value": value})
        return value

    def wait(self, line, seconds, sleep):
        self._append({"kind": "wait", "line": line, "seconds": seconds})
        sleep(seconds)

    # -- the file

    def as_dict(self, script, argv):
        return {
            "schema": SCHEMA_VERSION,
            "script": script,
            # Top level as well as in the events, so a recording can be joined
            # to an audit log without being parsed.
            "run": self.identity,
            "policies": self.policies,
            "arguments": list(argv),
            "exit": self.status,
            "stdout": _scrub(self.stdout, self.secrets),
            "stderr": _scrub(self.stderr, self.secrets),
            "events": self.events,
        }

    def save(self, path, script, argv):
        # Readable on purpose: a recording is meant to be committed and read
        # in a diff, and `\u00absecret db password\u00bb` is not.
        with open(path, "w") as fh:
            json.dump(self.as_dict(script, argv), fh, indent=2,
                      ensure_ascii=False)
            fh.write("\n")


class Player:
    """Serves a recording back, and performs nothing.

    Events are consumed in order. Matching is on what a reader would call the
    identity of the effect — which program with which arguments, which path —
    and not on the line number, so a script may be reformatted or have
    comments added without every event failing to match.
    """

    replaying = True

    def __init__(self, recording):
        self.recording = recording
        self.events = list(recording.get("events", []))
        self.position = 0
        self.performed = []          # effects suppressed during replay
        self.divergences = []

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            recording = json.load(fh)
        if recording.get("schema") != SCHEMA_VERSION:
            raise Divergence(
                f"{path} is a version {recording.get('schema')} recording; "
                f"this frost understands version {SCHEMA_VERSION}")
        return cls(recording)

    # -- consuming

    def _next(self, kind, line, described):
        while self.position < len(self.events):
            event = self.events[self.position]
            self.position += 1
            if event["kind"] == "secret":
                continue                        # carries no value to serve
            if event["kind"] != kind:
                raise Divergence(
                    f"the recording does next: {_describe(event)}\n"
                    f"    this run wants:      {described}",
                    line, _describe(event), described)
            return event
        raise Divergence(
            f"the recording ended, but this run still wants: {described}",
            line, None, described)

    def note_secret(self, name, plaintext):
        pass

    def command(self, line, argv, stdin, folder, run):
        described = "run " + " ".join(argv)
        event = self._next("command", line, described)
        if list(event["argv"]) != list(argv):
            raise Divergence(
                f"the recording ran: {' '.join(event['argv'])}\n"
                f"    this run wants:  {' '.join(argv)}",
                line, " ".join(event["argv"]), " ".join(argv))
        return event.get("stdout", ""), event.get("stderr", ""), \
            event.get("status", 0)

    def read_file(self, line, path, read):
        event = self._next("read", line, f"read {path}")
        self._compare(event, "path", path, line, "read")
        return event.get("content", "")

    def file_exists(self, line, path, check):
        event = self._next("exists", line, f"check whether {path} exists")
        self._compare(event, "path", path, line, "check")
        return event.get("answer", False)

    def write_file(self, line, path, content, write):
        event = self._next("write", line, f"write {path}")
        self._compare(event, "path", path, line, "write")
        self.performed.append(("write", path))
        return None                       # deliberately not performed

    def delete_file(self, line, path, delete):
        event = self._next("delete", line, f"delete {path}")
        self._compare(event, "path", path, line, "delete")
        self.performed.append(("delete", path))
        return None

    def env_read(self, line, name, value):
        event = self._next("env-read", line, f"read the environment {name}")
        self._compare(event, "name", name, line, "read the environment")
        return event.get("value", "")

    def env_write(self, line, name, value):
        event = self._next("env-write", line, f"set the environment {name}")
        self._compare(event, "name", name, line, "set the environment")

    def standard_input(self, read):
        event = self._next("stdin", None, "read the standard input")
        return event.get("content", "")

    def run_id(self, line, value):
        """The recorded run's id, not this one's.

        A replay that reported its own id would differ from the recording
        everywhere the script stamped it, which is exactly what a fixture is
        supposed to hold still.
        """
        event = self._next("run-id", line, "read the run id")
        return event.get("value", "")

    def clock(self, line, which, read):
        """The recorded reading, not a fresh one.

        A replay that re-read the clock would produce a different answer every
        time and no recording of a script that stamps a timestamp would ever
        replay clean.
        """
        event = self._next("clock", line, f"read the current {which}")
        self._compare(event, "which", which, line, "read the current")
        return event.get("value", "")

    def wait(self, line, seconds, sleep):
        """Recorded, and not performed. Replaying a script that backs off for
        thirty seconds should take no longer than the rest of the replay."""
        self._next("wait", line, f"wait {seconds} seconds")
        self.performed.append(("wait", seconds))

    def _compare(self, event, field, actual, line, verb):
        if event.get(field) != actual:
            raise Divergence(
                f"the recording would {verb} {event.get(field)!r}\n"
                f"    this run wants to {verb} {actual!r}",
                line, event.get(field), actual)

    # -- afterwards

    def unconsumed(self):
        """Events the recording had that this run never asked for."""
        return [e for e in self.events[self.position:] if e["kind"] != "secret"]


def _describe(event):
    kind = event["kind"]
    if kind == "command":
        return "run " + " ".join(event.get("argv", []))
    if kind in ("read", "write", "delete", "exists"):
        return f"{kind} {event.get('path')}"
    if kind in ("env-read", "env-write"):
        return f"{kind} {event.get('name')}"
    if kind == "run-id":
        return "read the run id"
    if kind == "clock":
        return f"read the current {event.get('which')}"
    if kind == "wait":
        return f"wait {event.get('seconds')} seconds"
    return kind
