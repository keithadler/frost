"""What a run did, as events something else can read.

Every tool can emit logs. What frost can emit that a shell cannot is the
*difference between what a script was allowed to do and what it did*. The
manifest is known before anything runs, the approval says what a person agreed
to, the policy says what the host permits, and the events say what actually
happened. A monitoring system given all four can answer questions no log line
supports:

  * which scripts are approved for capabilities they never use, so the
    approval could be tightened before it is abused
  * which hosts are reached in practice, against the allow-list somebody wrote
    six months ago
  * how long each command took, which run was slow, and whether the slowness
    was a command or a `wait`
  * every refusal, as a security event, with the rule that fired and the
    digest of the policy it came from

## The format

One JSON object per line, written as things happen and flushed. NDJSON is what
Splunk's HTTP collector, New Relic's log API, Datadog, Vector and Fluent Bit
all ingest without a translator, and a line-oriented file survives a run that
is killed halfway, which a single JSON document does not.

Every event carries the same envelope: a timestamp, the run id, the script,
a sequence number and an event name. A collector can route on `event` and
group on `run` without knowing anything else about frost.

## Secrets

The same rule as recordings, for the same reason but with sharper teeth:
telemetry usually leaves the building. A sealed value is redacted before it
reaches an event, arguments are scrubbed of any plaintext the run revealed,
and file contents are never emitted at all. Sizes are, because "wrote 4kb" is
useful and the 4kb is not.
"""
# SPDX-License-Identifier: MIT

import datetime
import json
import time

SCHEMA = 1


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


class Sink:
    """Where events go. One line per event, flushed as it happens."""

    def __init__(self, stream, run_id="", script="", scrub=None):
        self.stream = stream
        self.run_id = run_id
        self.script = script
        self.seq = 0
        # plaintext -> name, exactly as a Recorder keeps it. Telemetry leaves
        # the building more often than a recording does, so this matters more.
        self.secrets = dict(scrub or {})

    def note_secret(self, name, plaintext):
        if plaintext:
            self.secrets[plaintext] = name

    def _clean(self, value):
        if isinstance(value, str):
            for plaintext, name in self.secrets.items():
                if plaintext and plaintext in value:
                    value = value.replace(plaintext, f"«secret {name}»")
            return value
        if isinstance(value, dict):
            return {k: self._clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._clean(v) for v in value]
        return value

    def emit(self, event, **fields):
        self.seq += 1
        payload = {
            "schema": SCHEMA,
            "ts": _now(),
            "run": self.run_id,
            "script": self.script,
            "seq": self.seq,
            "event": event,
        }
        payload.update(self._clean(fields))
        # ensure_ascii=False for the same reason a recording uses it: a
        # redaction marker written as \u00absecret\u00bb is one nobody greps
        # for, and the whole point of the marker is being noticed.
        self.stream.write(
            json.dumps(payload, sort_keys=False, ensure_ascii=False) + "\n")
        self.stream.flush()

    def close(self):
        if hasattr(self.stream, "close") and self.stream not in (None,):
            try:
                self.stream.close()
            except Exception:                     # pragma: no cover
                pass


class Observer:
    """A journal that watches, forwards, and times.

    frost already routes every effect through one interface so a recording can
    be made without the interpreter knowing. Telemetry is the same shape, so
    this wraps whatever journal is in use — a Recorder, a Player, or nothing —
    rather than adding a second set of hooks to the interpreter that would
    drift from the first.
    """

    def __init__(self, sink, inner=None):
        self.sink = sink
        self.inner = inner
        self.commands = 0
        self.programs = set()
        self.hosts = set()
        self.waited = 0.0
        self.busy = 0.0

    @property
    def replaying(self):
        return getattr(self.inner, "replaying", False)

    # -- effects

    def note_secret(self, name, plaintext):
        self.sink.note_secret(name, plaintext)
        self.sink.emit("secret.read", line=None, name=name)
        if self.inner is not None:
            return self.inner.note_secret(name, plaintext)

    def command(self, line, argv, stdin, folder, run):
        from .audit import Command, hosts_in

        program = argv[0] if argv else ""
        self.sink.emit("command.start", line=line, program=program,
                       argv=list(argv), folder=folder,
                       stdin=bool(stdin))
        started = time.monotonic()
        try:
            if self.inner is not None:
                out, err, status = self.inner.command(line, argv, stdin,
                                                      folder, run)
            else:
                out, err, status = run()
        except Exception as e:
            self.sink.emit("command.failed", line=line, program=program,
                           seconds=round(time.monotonic() - started, 6),
                           error=type(e).__name__)
            raise
        seconds = round(time.monotonic() - started, 6)
        self.busy += seconds
        self.commands += 1
        self.programs.add(program)
        reached = hosts_in(Command(program=program, args=list(argv[1:]),
                                   line=line, checked=True, timeout=False))
        self.hosts.update(reached)
        # Sizes, not contents. "wrote 4kb" is useful and the 4kb is not.
        self.sink.emit("command.finish", line=line, program=program,
                       status=status, seconds=seconds,
                       stdout_bytes=len(out or ""), stderr_bytes=len(err or ""),
                       hosts=reached, replayed=self.replaying)
        return out, err, status

    def read_file(self, line, path, read):
        content = (self.inner.read_file(line, path, read)
                   if self.inner is not None else read())
        self.sink.emit("file.read", line=line, path=path,
                       bytes=len(content or ""))
        return content

    def file_exists(self, line, path, check):
        answer = (self.inner.file_exists(line, path, check)
                  if self.inner is not None else check())
        self.sink.emit("file.checked", line=line, path=path, exists=answer)
        return answer

    def write_file(self, line, path, content, write):
        self.sink.emit("file.write", line=line, path=path,
                       bytes=len(content or ""))
        if self.inner is not None:
            return self.inner.write_file(line, path, content, write)
        return write()

    def delete_file(self, line, path, delete):
        self.sink.emit("file.delete", line=line, path=path)
        if self.inner is not None:
            return self.inner.delete_file(line, path, delete)
        return delete()

    def env_read(self, line, name, value):
        self.sink.emit("env.read", line=line, name=name)
        if self.inner is not None:
            return self.inner.env_read(line, name, value)
        return value

    def env_write(self, line, name, value):
        self.sink.emit("env.write", line=line, name=name)
        if self.inner is not None:
            return self.inner.env_write(line, name, value)

    def standard_input(self, read):
        content = (self.inner.standard_input(read)
                   if self.inner is not None else read())
        self.sink.emit("stdin.read", bytes=len(content or ""))
        return content

    def clock(self, line, which, read):
        value = (self.inner.clock(line, which, read)
                 if self.inner is not None else read(which))
        self.sink.emit("clock.read", line=line, which=which)
        return value

    def wait(self, line, seconds, sleep):
        self.waited += seconds
        self.sink.emit("wait", line=line, seconds=seconds)
        if self.inner is not None:
            return self.inner.wait(line, seconds, sleep)
        return sleep(seconds)

    def run_id(self, line, value):
        if self.inner is not None:
            return self.inner.run_id(line, value)
        return value

    # -- what the recorder needs back

    def __getattr__(self, name):
        # Anything else the driver asks of a journal goes to the real one.
        if self.inner is None:
            raise AttributeError(name)
        return getattr(self.inner, name)


def utilisation(caps, observer):
    """Approved for, and actually used.

    The signal nothing else can produce. A script approved to run six programs
    and reaching four hosts, which in practice uses two and reaches one, is an
    approval that should be tightened — and that is visible only by holding
    the manifest and the run side by side.
    """
    from .audit import RUNTIME_HOST

    declared_programs = sorted({c.program for c in caps.commands if c.program})
    declared_hosts = sorted({h for h, _ in caps.reaches if h != RUNTIME_HOST})
    used_programs = sorted(p for p in observer.programs if p)
    used_hosts = sorted(observer.hosts)
    return {
        "programs_declared": declared_programs,
        "programs_used": used_programs,
        "programs_unused": [p for p in declared_programs
                            if p not in set(used_programs)],
        "hosts_declared": declared_hosts,
        "hosts_used": used_hosts,
        "hosts_unused": [h for h in declared_hosts
                         if h not in set(used_hosts)],
        "unknowable_names": caps.dynamic,
    }
