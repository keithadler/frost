"""Tree-walking interpreter for frost.

Two rules do most of the safety work here:

1. Arguments are a list, never a string. Nothing built at runtime is ever
   re-parsed as syntax, so there is no injection surface.
2. `run` aborts the script on a non-zero exit unless it was written as
   `try to run`. Pipes report failure if ANY stage fails, not just the last.
"""
# SPDX-License-Identifier: MIT

import fnmatch
import hmac
import os
import random
import re
import subprocess
import sys
import tempfile

from . import ast as A
from .sealed import Sealed, is_sealed, reveal, seal_like, first_sealed


class FrostError(Exception):
    def __init__(self, msg, line=None, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line
        self.hint = hint


TIMEOUT_STATUS = 124


class QuitSignal(Exception):
    def __init__(self, status):
        self.status = status


class ExitRepeatSignal(Exception):
    pass


class NextRepeatSignal(Exception):
    pass


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


# ------------------------------------------------------------------ chunking

def as_chunks(value, kind):
    if kind == "match":
        # Match groups are already a list; anything else is a single group.
        return list(value) if isinstance(value, list) else [to_text(value)]
    if isinstance(value, list):
        if kind == "item":
            return list(value)
        value = "\n".join(str(v) for v in value)
    text = to_text(value)
    if kind == "character":
        return list(text)
    if kind == "word":
        return text.split()
    if kind == "line":
        if text == "":
            return []
        return text.split("\n")
    if kind == "item":
        if text == "":
            return []
        return [p.strip() for p in text.split(",")]
    raise FrostError(f"unknown chunk kind {kind!r}")


def as_list(value):
    """Any value as a list. Text becomes its lines, which is the shape a
    command's output already has."""
    if isinstance(value, list):
        return list(value)
    text = to_text(value)
    return text.split("\n") if text else []


def sort_key(items):
    """Numeric order when everything is a number, alphabetical otherwise.

    Sorting ["10", "9"] lexically puts 10 first, which is never what anyone
    means when the values came out of a counter.
    """
    if items and all(is_numberish(i) for i in items):
        return to_number
    return to_text


def join_chunks(parts, kind):
    if kind == "character":
        return "".join(parts)
    if kind == "word":
        return " ".join(parts)
    if kind == "line":
        return "\n".join(parts)
    if kind == "item":
        return ", ".join(parts)
    if kind == "match":
        return ", ".join(parts)
    return "".join(parts)


# ------------------------------------------------------------------ coercion

def to_text(v):
    """Every path in the language reaches text through here.

    That is what makes redaction total rather than a list of places somebody
    remembered: a sealed value stringifies to its marker, so `put`, joining,
    `--trace`, error messages and the scratchpad all redact without knowing
    secrets exist. Use `reveal()` at a boundary where a program needs the
    plaintext; never here.
    """
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, Sealed):
        return v.marker
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return "\n".join(to_text(x) for x in v)
    return str(v)


def to_argument(v):
    """Text for a place a program genuinely needs the plaintext."""
    if isinstance(v, Sealed):
        return v.reveal()
    if isinstance(v, list):
        return "\n".join(to_argument(x) for x in v)
    return to_text(v)


def to_number(v, line=None):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    text = to_text(v).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        raise FrostError(f"{text!r} is not a number", line)


def is_numberish(v):
    if isinstance(v, (int, float, bool)):
        return True
    try:
        float(to_text(v).strip())
        return True
    except (ValueError, AttributeError):
        return False


def format_seconds(seconds):
    if seconds is None:
        return "no limit"
    if seconds < 1:
        return f"{int(seconds * 1000)} milliseconds"
    if seconds >= 60 and seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute" + ("s" if minutes != 1 else "")
    n = int(seconds) if float(seconds).is_integer() else seconds
    return f"{n} second" + ("s" if n != 1 else "")


def truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    text = to_text(v).strip().lower()
    if text in ("true", "yes"):
        return True
    if text in ("false", "no", ""):
        return False
    return True


# --------------------------------------------------------------- interpreter

class Interpreter:
    def __init__(self, argv=None, trace=False, cwd=None, keystore=None,
                 role=None):
        self.globals = {}
        self.scopes = [self.globals]
        self.handlers = {}
        self.it = ""
        self.result = 0
        self.match_groups = []
        self.whole_match = ""
        self.argv = argv or []
        self.trace = trace
        self.cwd = cwd or os.getcwd()
        self.env = dict(os.environ)
        self.cleanups = []       # ensure blocks, run in reverse at exit
        self._stdin_text = None  # `the standard input`, read once and kept
        self.keystore = keystore
        self.role = role
        # Handler names resolve in the file that defines the code doing the
        # calling, not in one flat table. A flat table lets an entry script's
        # handler capture a call made inside a module, which is a hijack
        # rather than a hygiene problem.
        # A Recorder writes down every effect; a Player serves them back and
        # performs none. None means run normally.
        self.journal = None
        # A capability boundary the operating system holds for children, and
        # this interpreter holds for its own file operations. None means the
        # script runs with whatever permissions frost itself has.
        self.sandbox = None
        self.handler_tables = {}      # file -> {name: HandlerDef}
        self.handler_home = {}        # id(HandlerDef) -> defining file
        self.current_file = None      # whose table calls resolve in

    @staticmethod
    def compile_pattern(pattern, line):
        try:
            return re.compile(pattern)
        except re.error as e:
            raise FrostError(
                f"that is not a valid pattern: {e}", line,
                hint="patterns use standard regular expression syntax; "
                     'for simple filename matching use: is like "*.txt"')

    def record_match(self, m):
        if m is None:
            self.match_groups = []
            self.whole_match = ""
            return False
        self.match_groups = [g if g is not None else "" for g in m.groups()]
        self.whole_match = m.group(0)
        return True

    # -- scope helpers

    @property
    def scope(self):
        return self.scopes[-1]

    def get_var(self, name, line):
        if name in self.scope:
            return self.scope[name]
        if name in self.globals:
            return self.globals[name]
        raise FrostError(f"there is no variable named {name!r}", line,
                         hint="assign it first with:  put ... into " + name)

    def set_var(self, name, value):
        self.scope[name] = value

    def read_target(self, target, line):
        """Current value of an assignment target."""
        if isinstance(target, A.GlobalTarget):
            if target.name not in self.globals:
                raise FrostError(
                    f"there is no global named {target.name!r}", line,
                    hint="a global is created by assigning to it at the top "
                         "level, or with:  put ... into the global "
                         + target.name)
            return self.globals[target.name]
        return self.get_var(target.name, line)

    def write_target(self, target, value):
        if isinstance(target, A.GlobalTarget):
            self.globals[target.name] = value
        else:
            self.set_var(target.name, value)

    # -- entry points

    def install(self, program):
        """Bind a loaded multi-file program before running its entry script.

        Each file gets its own name table, and every handler remembers where
        it was defined, so a call inside a module resolves against that
        module rather than against whatever the entry script happens to have.
        """
        from . import modules as M
        self.handler_tables = M.handler_tables(program)
        self.handler_home = M.owning_file(program)
        self.current_file = program.entry
        self.handlers = dict(self.handler_tables[program.entry])
        return self

    def run_program(self, stmts):
        for s in stmts:
            if isinstance(s, A.HandlerDef):
                self.handlers[s.name] = s
        body = [s for s in stmts
                if not isinstance(s, (A.HandlerDef, A.Use))]

        status, failure = 0, None
        try:
            self.exec_block(body)
        except QuitSignal as q:
            status = q.status
        except ReturnSignal:
            status = 0
        except FrostError as e:
            failure = e
        except KeyboardInterrupt:
            # Cleanup has to survive Ctrl-C too, or the lock file it was
            # written to release outlives the script that took it.
            self.run_cleanups()
            raise
        self.run_cleanups()
        if failure is not None:
            raise failure
        return status

    def run_cleanups(self):
        """Run every registered `ensure` block, most recent first.

        A failure in one block is reported but does not stop the others, and
        never replaces the error that ended the script — that error is what
        the reader needs to see first.
        """
        pending, self.cleanups = self.cleanups, []
        for block, home in reversed(pending):
            previous, self.current_file = self.current_file, home
            try:
                self.exec_block(block)
            except (QuitSignal, ReturnSignal, ExitRepeatSignal,
                    NextRepeatSignal):
                pass
            except FrostError as e:
                where = f" at line {e.line}" if e.line else ""
                sys.stderr.write(f"frost: cleanup failed{where}: {e.msg}\n")
                sys.stderr.flush()
            finally:
                self.current_file = previous

    def exec_block(self, stmts):
        for s in stmts:
            self.exec_statement(s)

    # -- statements

    def exec_statement(self, node):
        method = getattr(self, "exec_" + type(node).__name__, None)
        if method is None:
            raise FrostError(
                f"cannot execute {type(node).__name__}",
                getattr(node, "line", None))
        if self.trace:
            sys.stderr.write(
                f"[frost] line {getattr(node, 'line', '?')}: "
                f"{type(node).__name__}\n")
        return method(node)

    def exec_Put(self, node):
        value = self.eval(node.expr)
        target = node.target

        if target is None:
            sys.stdout.write(to_text(value) + "\n")
            sys.stdout.flush()
            return

        if isinstance(target, A.StreamTarget):
            stream = sys.stderr if target.name == "error" else sys.stdout
            stream.write(to_text(value) + "\n")
            stream.flush()
            return

        if isinstance(target, A.FileTarget):
            path = self.resolve_path(to_text(self.eval(target.path)))
            mode = "a" if node.mode == "after" else "w"
            # A deliberate release: writing a config file is a real need, and
            # --explain reports it as a secret leaving the process.
            written = to_argument(value)
            self.guard("write", path, node.line)

            def do_write():
                if node.mode == "before":
                    existing = ""
                    if os.path.exists(path):
                        with open(path) as fh:
                            existing = fh.read()
                    with open(path, "w") as fh:
                        fh.write(written + "\n" + existing)
                    return
                with open(path, mode) as fh:
                    fh.write(written + "\n")

            if self.journal is not None:
                # Replay performs nothing: that is what makes it safe to run
                # a script against a recording with no consequences.
                self.journal.write_file(node.line, path, written, do_write)
                return
            do_write()
            return

        if isinstance(target, A.FolderTarget):
            if node.mode != "into":
                raise FrostError(
                    f"the current folder can only be set with 'into', "
                    f"not {node.mode!r}", node.line)
            path = self.resolve_path(to_text(value))
            if not os.path.isdir(path):
                raise FrostError(f"there is no folder at {path!r}", node.line,
                                 hint="the folder must already exist")
            self.cwd = os.path.abspath(path)
            return

        if isinstance(target, A.EnvTarget):
            name = to_text(self.eval(target.name))
            if name == "" or "=" in name or "\0" in name:
                raise FrostError(
                    f"{name!r} is not a usable environment variable name",
                    node.line)
            addition = to_argument(value)   # children need the real value
            if node.mode == "into":
                self.env[name] = addition
            else:
                current = self.env.get(name, "")
                self.env[name] = (current + addition if node.mode == "after"
                                  else addition + current)
            if self.journal is not None:
                self.journal.env_write(node.line, name, self.env[name])
            return

        if node.mode == "into":
            self.write_target(target, value)
            return

        current = self.read_target(target, node.line)
        if isinstance(current, list):
            # Appending to a list adds an element. Appending to text joins it.
            addition = value if isinstance(value, list) else [value]
            self.write_target(
                target,
                current + addition if node.mode == "after"
                else addition + current)
            return
        addition = to_text(value)
        current = to_text(current)
        self.write_target(
            target,
            current + addition if node.mode == "after"
            else addition + current)

    def journal_run(self, node, program, argv, stdin_text, folder, seconds):
        """A command, recorded or replayed. Replay spawns nothing."""
        def spawn():
            done = subprocess.run(argv, capture_output=not node.streaming,
                                  text=True, cwd=folder, env=self.env,
                                  input=stdin_text, timeout=seconds)
            return (done.stdout or "", done.stderr or "", done.returncode)

        try:
            out, err, code = self.journal.command(
                node.line, argv, stdin_text, folder, spawn)
        except subprocess.TimeoutExpired:
            self.it = ""
            self.result = TIMEOUT_STATUS
            if node.checked:
                raise FrostError(
                    f"{program!r} ran longer than {format_seconds(seconds)} "
                    f"and was stopped", node.line)
            return
        except FileNotFoundError:
            raise FrostError(f"there is no program named {program!r}",
                             node.line,
                             hint="check the name, or that it is on your PATH")

        if err:
            sys.stderr.write(err)
            sys.stderr.flush()
        self.it = "" if node.streaming else out.rstrip("\n")
        self.result = code
        if node.checked and code != 0:
            raise FrostError(
                f"{program!r} failed with status {code}", node.line,
                hint="if this failure is expected, write 'try to run ...' "
                     "and check 'the result'")

    def confine(self, argv, line):
        """The command line that runs `argv` inside the boundary."""
        from .sandbox import SandboxError
        try:
            return self.sandbox.wrap(argv)
        except SandboxError as e:
            raise FrostError(e.msg, line, hint=e.hint)

    def guard(self, action, path, line):
        """Check one of frost's own file operations against the boundary.

        Enforced here rather than by the kernel, because this never becomes a
        child process. A weaker guarantee than the one covering commands, and
        named differently in the documentation for that reason.
        """
        if self.sandbox is None:
            return
        from .sandbox import SandboxError
        try:
            getattr(self.sandbox, "check_" + action)(path, line)
        except SandboxError as e:
            raise FrostError(e.msg, line, hint=e.hint)

    def eval_timeout(self, node):
        if node.timeout is None:
            return None
        seconds = to_number(self.eval(node.timeout), node.line)
        if seconds <= 0:
            raise FrostError("a timeout must be greater than zero", node.line)
        return seconds

    def child_folder(self, node):
        """Where a child process should run: its own folder, or the script's."""
        if node.folder is None:
            return self.cwd
        path = self.resolve_path(to_text(self.eval(node.folder)))
        if not os.path.isdir(path):
            raise FrostError(f"there is no folder at {path!r}", node.line,
                             hint="the folder must exist before a command "
                                  "can run in it")
        return path

    def exec_Run(self, node):
        program = to_text(self.eval(node.program))
        args = []
        for a in node.args:
            v = self.eval(a)
            if isinstance(v, list):
                args.extend(to_argument(x) for x in v)
            else:
                args.append(to_argument(v))

        seconds = self.eval_timeout(node)
        stdin_text = None
        if node.stdin is not None:
            stdin_text = to_argument(self.eval(node.stdin))
            if not stdin_text.endswith("\n"):
                stdin_text += "\n"

        argv = [program] + args
        folder = self.child_folder(node)
        if self.sandbox is not None:
            argv = self.confine(argv, node.line)

        if self.journal is not None:
            return self.journal_run(node, program, argv, stdin_text, folder,
                                    seconds)

        try:
            # `showing output` lets the child write straight to the terminal:
            # the only way to see a long build as it happens, or to run
            # anything interactive at all. Nothing is captured, so `it` is
            # empty afterwards rather than stale.
            proc = subprocess.run(argv,
                                  capture_output=not node.streaming,
                                  text=True, cwd=folder,
                                  env=self.env, input=stdin_text,
                                  timeout=seconds)
        except subprocess.TimeoutExpired as e:
            partial = e.stdout or ""
            if isinstance(partial, bytes):
                partial = partial.decode(errors="replace")
            self.it = partial.rstrip("\n")
            self.result = TIMEOUT_STATUS
            if node.checked:
                raise FrostError(
                    f"{program!r} ran longer than "
                    f"{format_seconds(seconds)} and was stopped",
                    node.line,
                    hint="if a slow run is acceptable, write 'try to run ...' "
                         "and check 'the result' for 124")
            return
        except FileNotFoundError:
            raise FrostError(f"there is no program named {program!r}",
                             node.line,
                             hint="check the name, or that it is on your PATH")
        except PermissionError:
            raise FrostError(f"{program!r} is not executable", node.line)

        if proc.stderr:
            sys.stderr.write(proc.stderr)
            sys.stderr.flush()

        self.it = "" if node.streaming else proc.stdout.rstrip("\n")
        self.result = proc.returncode

        if node.checked and proc.returncode != 0:
            raise FrostError(
                f"{program!r} failed with status {proc.returncode}",
                node.line,
                hint="if this failure is expected, write 'try to run ...' "
                     "and check 'the result'")

    def exec_Pipe(self, node):
        commands = []
        for stage in node.stages:
            program = to_text(self.eval(stage.program))
            args = []
            for a in stage.args:
                v = self.eval(a)
                if isinstance(v, list):
                    args.extend(to_argument(x) for x in v)
                else:
                    args.append(to_argument(v))
            argv = [program] + args
            if self.sandbox is not None:
                argv = self.confine(argv, stage.line)
            commands.append((argv, stage.line))

        folder = self.child_folder(node)
        seconds = self.eval_timeout(node)

        # `pipe reading X` feeds the first stage. The text goes through a
        # temporary file rather than a pipe we write to ourselves: writing
        # into stage one while waiting on the last stage's output is the
        # classic way to deadlock a pipeline on a large input.
        feed = None
        if node.stdin is not None:
            text = to_text(self.eval(node.stdin))
            if not text.endswith("\n"):
                text += "\n"
            feed = tempfile.TemporaryFile(mode="w+")
            feed.write(text)
            feed.seek(0)

        procs = []
        prev_stdout = feed
        try:
            for idx, (cmd, line) in enumerate(commands):
                last = idx == len(commands) - 1
                try:
                    p = subprocess.Popen(
                        cmd,
                        stdin=prev_stdout,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE if last else None,
                        text=True,
                        cwd=folder,
                        env=self.env,
                    )
                except FileNotFoundError:
                    raise FrostError(
                        f"there is no program named {cmd[0]!r}", line)
                if prev_stdout is not None:
                    prev_stdout.close()
                prev_stdout = p.stdout
                procs.append((p, cmd[0], line))
        except FrostError:
            for p, _, _ in procs:
                p.kill()
            if feed is not None and not feed.closed:
                feed.close()
            raise

        final = procs[-1][0]
        try:
            out, err = final.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            for p, _, _ in procs:
                p.kill()
            for p, _, _ in procs:
                p.wait()
            self.it = ""
            self.result = TIMEOUT_STATUS
            if node.checked:
                raise FrostError(
                    f"the pipe ran longer than {format_seconds(seconds)} "
                    "and was stopped", node.line,
                    hint="if a slow pipe is acceptable, write 'try to pipe' "
                         "and check 'the result' for 124")
            return
        for p, _, _ in procs[:-1]:
            p.wait()

        if err:
            sys.stderr.write(err)
            sys.stderr.flush()

        self.it = (out or "").rstrip("\n")

        # pipefail by default: the first failing stage wins.
        failed = None
        for p, name, line in procs:
            if p.returncode != 0:
                failed = (name, p.returncode, line)
                break
        self.result = failed[1] if failed else 0

        if node.checked and failed:
            name, code, line = failed
            raise FrostError(
                f"stage {name!r} of the pipe failed with status {code}",
                line,
                hint="if this failure is expected, write 'try to pipe'")

    def exec_If(self, node):
        if truthy(self.eval(node.cond)):
            self.exec_block(node.then_block)
        elif node.else_block is not None:
            self.exec_block(node.else_block)

    def exec_RepeatTimes(self, node):
        count = int(to_number(self.eval(node.count), node.line))
        for _ in range(count):
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                continue
            except ExitRepeatSignal:
                break

    def exec_RepeatWith(self, node):
        start = to_number(self.eval(node.start), node.line)
        stop = to_number(self.eval(node.stop), node.line)
        step = to_number(self.eval(node.step), node.line) if node.step else None
        if step is None:
            step = 1 if stop >= start else -1
        if step == 0:
            raise FrostError("a repeat step of 0 would never finish", node.line)
        current = start
        while (current <= stop) if step > 0 else (current >= stop):
            self.set_var(node.var, current)
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                pass
            except ExitRepeatSignal:
                break
            current += step

    def exec_RepeatForEach(self, node):
        # `repeat for each line in the standard input` is the shape of every
        # filter, and reading the whole stream first makes frost useless for
        # one that never ends — a tail, a log follow, anything piped from a
        # long-running command. Lines are consumed as they arrive.
        if (node.kind == "line" and isinstance(node.source, A.StdInRef)
                and self._stdin_text is None):
            return self.stream_standard_input(node)

        parts = as_chunks(self.eval(node.source), node.kind)
        for part in parts:
            self.set_var(node.var, part)
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                continue
            except ExitRepeatSignal:
                break

    def stream_standard_input(self, node):
        """Run the loop body per line, as the lines arrive.

        Under a recording the text is still consumed lazily, but the whole of
        what was read is written down at the end, so a replay of a streaming
        filter is deterministic.
        """
        if self.journal is not None and self.journal.replaying:
            recorded = self.journal.standard_input(lambda: "")
            lines = recorded.split("\n") if recorded else []
            self._stdin_text = ""
            return self.walk_lines(node, iter(lines))

        consumed = []

        def source():
            stream = sys.stdin
            if stream is None:
                return
            for line in stream:
                text = line.rstrip("\n")
                consumed.append(text)
                yield text

        try:
            exhausted = self.walk_lines(node, source())
        finally:
            if self.journal is not None:
                self.journal.standard_input(lambda: "\n".join(consumed))
        # Everything was consumed, so a later read finds nothing. An early
        # `exit repeat` leaves the rest in the pipe for whoever asks next.
        if exhausted:
            self._stdin_text = ""

    def walk_lines(self, node, lines):
        """The loop body over an iterator of lines. True if it ran to the end."""
        for line in lines:
            self.set_var(node.var, line)
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                continue
            except ExitRepeatSignal:
                return False
        return True

    def exec_RepeatWhile(self, node):
        guard = 0
        while True:
            cond = truthy(self.eval(node.cond))
            if node.until:
                cond = not cond
            if not cond:
                break
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                pass
            except ExitRepeatSignal:
                break
            guard += 1
            if guard > 10_000_000:
                raise FrostError("loop ran away", node.line)

    def exec_RepeatForever(self, node):
        while True:
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                continue
            except ExitRepeatSignal:
                break

    def exec_ExitRepeat(self, node):
        raise ExitRepeatSignal()

    def exec_NextRepeat(self, node):
        raise NextRepeatSignal()

    def exec_Quit(self, node):
        status = 0
        if node.status is not None:
            status = int(to_number(self.eval(node.status), node.line))
        raise QuitSignal(status)

    def exec_HandlerDef(self, node):
        self.handlers[node.name] = node

    def exec_Return(self, node):
        raise ReturnSignal(self.eval(node.expr) if node.expr else "")

    def exec_Ensure(self, node):
        # The cleanup runs later, by which point the current file may be a
        # different one, so it remembers where it came from.
        self.cleanups.append((node.block, self.current_file))

    def exec_Use(self, node):
        """Imports are resolved before anything runs; nothing happens here."""

    def exec_Arith(self, node):
        amount = to_number(self.eval(node.amount), node.line)
        current = to_number(self.read_target(node.target, node.line),
                            node.line)
        if node.op == "add":
            current += amount
        elif node.op == "subtract":
            current -= amount
        elif node.op == "multiply":
            current *= amount
        else:
            if amount == 0:
                raise FrostError("cannot divide by zero", node.line)
            current /= amount
        self.write_target(node.target, current)

    def exec_DeleteFile(self, node):
        path = self.resolve_path(to_text(self.eval(node.path)))
        self.guard("delete", path, node.line)

        def remove():
            try:
                os.remove(path)
            except FileNotFoundError:
                raise FrostError(f"there is no file at {path!r}", node.line)

        if self.journal is not None:
            return self.journal.delete_file(node.line, path, remove)
        return remove()

    def visible_handlers(self):
        """The names the code currently running is allowed to call."""
        if self.current_file is not None:
            return self.handler_tables.get(self.current_file, self.handlers)
        return self.handlers

    def call_handler(self, name, args, line):
        """Run a handler and return what it returned.

        Shared by the statement form, which lands the value in `it`, and the
        expression form, which does not — an expression buried inside another
        expression must not quietly replace the last command's output.
        """
        handler = self.visible_handlers().get(name)
        if handler is None:
            raise FrostError(
                f"there is no handler named {name!r}", line,
                hint="define it with:  to " + name + " ... end " + name)
        if len(args) != len(handler.params):
            raise FrostError(
                f"{name!r} expects {len(handler.params)} value(s) "
                f"but got {len(args)}", line)
        self.scopes.append(dict(zip(handler.params, args)))
        # While the handler runs, names resolve in the file that defined it.
        home = self.handler_home.get(id(handler), self.current_file)
        previous, self.current_file = self.current_file, home
        try:
            self.exec_block(handler.block)
            return ""
        except ReturnSignal as r:
            return r.value
        finally:
            self.scopes.pop()
            self.current_file = previous

    def exec_Call(self, node):
        self.it = self.call_handler(
            node.name, [self.eval(a) for a in node.args], node.line)

    # -- expressions

    def eval(self, node):
        method = getattr(self, "eval_" + type(node).__name__, None)
        if method is None:
            raise FrostError(f"cannot evaluate {type(node).__name__}",
                             getattr(node, "line", None))
        return method(node)

    def eval_Lit(self, node):
        return node.value

    def eval_Var(self, node):
        return self.get_var(node.name, node.line)

    def eval_ItRef(self, node):
        return self.it

    def eval_ResultRef(self, node):
        return self.result

    def eval_ArgList(self, node):
        return list(self.argv)

    def eval_CurrentFolder(self, node):
        return self.cwd

    def eval_EnvRef(self, node):
        name = to_text(self.eval(node.name))
        value = self.env.get(name, "")
        if self.journal is not None:
            return self.journal.env_read(node.line, name, value)
        return value

    def eval_SecretEnvRef(self, node):
        name = to_text(self.eval(node.name))
        plaintext = self.env.get(name, "")
        if self.journal is not None:
            self.journal.note_secret(name, plaintext)
        return Sealed(plaintext, name)

    def eval_SecretFileRef(self, node):
        path = to_text(self.eval(node.path))
        resolved = self.resolve_path(path)
        try:
            with open(resolved) as fh:
                plaintext = fh.read().rstrip("\n")
            if self.journal is not None:
                self.journal.note_secret(path, plaintext)
            return Sealed(plaintext, path)
        except FileNotFoundError:
            raise FrostError(f"there is no file at {resolved!r}", node.line)
        except IsADirectoryError:
            raise FrostError(f"{resolved!r} is a folder, not a file", node.line)

    def eval_SecretRef(self, node):
        name = to_text(self.eval(node.name))
        if self.keystore is None:
            raise FrostError(
                f"no keystore is open, so the secret {name!r} cannot be read",
                node.line,
                hint="run with:  frost --keystore <file> --role <role> "
                     "script.frost")
        try:
            plaintext = self.keystore.open_secret(name, self.role)
            if self.journal is not None:
                self.journal.note_secret(name, plaintext)
            return Sealed(plaintext, name)
        except KeyError:
            raise FrostError(
                f"the keystore has no secret named {name!r}", node.line,
                hint="list what it holds with:  frost keystore list <file>")
        except PermissionError as e:
            raise FrostError(str(e), node.line)

    def eval_GlobalRef(self, node):
        if node.name not in self.globals:
            raise FrostError(
                f"there is no global named {node.name!r}", node.line,
                hint="assign it first with:  put ... into the global "
                     + node.name)
        return self.globals[node.name]

    def eval_FileRef(self, node):
        path = self.resolve_path(to_text(self.eval(node.path)))
        self.guard("read", path, node.line)

        def read():
            try:
                with open(path) as fh:
                    return fh.read().rstrip("\n")
            except FileNotFoundError:
                raise FrostError(f"there is no file at {path!r}", node.line)
            except IsADirectoryError:
                raise FrostError(f"{path!r} is a folder, not a file",
                                 node.line)

        if self.journal is not None:
            return self.journal.read_file(node.line, path, read)
        return read()

    def eval_FileExists(self, node):
        inner = node.path
        if isinstance(inner, A.FileRef):
            path = to_text(self.eval(inner.path))
        else:
            path = to_text(self.eval(inner))
        resolved = self.resolve_path(path)
        if self.journal is not None:
            return self.journal.file_exists(
                node.line, resolved, lambda: os.path.exists(resolved))
        return os.path.exists(resolved)

    def eval_UnaryOp(self, node):
        if node.op == "not":
            return not truthy(self.eval(node.operand))
        return -to_number(self.eval(node.operand), node.line)

    def eval_Logical(self, node):
        left = truthy(self.eval(node.left))
        if node.op == "and":
            return left and truthy(self.eval(node.right))
        return left or truthy(self.eval(node.right))

    def eval_BinOp(self, node):
        left = self.eval(node.left)
        right = self.eval(node.right)
        if node.op in ("&", "&&"):
            separator = " " if node.op == "&&" else ""
            # A connection string built from a password is a password. Without
            # this, one concatenation would launder a secret into plain text
            # and the redaction would be worth nothing.
            if is_sealed(left):
                return left.joined(right, separator)
            if is_sealed(right):
                return right.preceded_by(left, separator)
            return to_text(left) + separator + to_text(right)
        a = to_number(left, node.line)
        b = to_number(right, node.line)
        if node.op == "+":
            return a + b
        if node.op == "-":
            return a - b
        if node.op == "*":
            return a * b
        if node.op == "^":
            return a ** b
        if node.op == "/":
            if b == 0:
                raise FrostError("cannot divide by zero", node.line)
            return a / b
        raise FrostError(f"unknown operator {node.op!r}", node.line)

    def eval_Compare(self, node):
        left = self.eval(node.left)

        # Comparisons see through the seal. A comparison yields one bit, and
        # checking that a credential is not the placeholder from the example
        # config is exactly the kind of check a careful script does. Comparing
        # the markers instead would silently answer the wrong question — every
        # secret would look equal to every other.
        def text(value):
            return to_text(reveal(value))

        if node.op == "is empty":
            return text(left).strip() == ""
        if node.op == "is not empty":
            return text(left).strip() != ""

        right = self.eval(node.right)

        if node.op == "contains":
            if isinstance(left, list):
                return text(right) in [text(x) for x in left]
            return text(right) in text(left)
        if node.op == "in":
            if isinstance(right, list):
                return text(left) in [text(x) for x in right]
            return text(left) in text(right)
        if node.op == "starts with":
            return text(left).startswith(text(right))
        if node.op == "ends with":
            return text(left).endswith(text(right))

        if is_sealed(left) or is_sealed(right):
            # Constant time, so a comparison cannot be turned into an oracle
            # that recovers the value one character at a time.
            if node.op in ("=", "is"):
                return hmac.compare_digest(text(left), text(right))
            if node.op == "is not":
                return not hmac.compare_digest(text(left), text(right))

        if is_numberish(left) and is_numberish(right):
            a, b = to_number(left), to_number(right)
        else:
            a, b = text(left), text(right)

        if node.op in ("=", "is"):
            return a == b
        if node.op == "is not":
            return a != b
        if node.op == ">":
            return a > b
        if node.op == "<":
            return a < b
        if node.op == ">=":
            return a >= b
        if node.op == "<=":
            return a <= b
        raise FrostError(f"unknown comparison {node.op!r}", node.line)

    def eval_Matches(self, node):
        subject = to_text(self.eval(node.subject))
        pattern = to_text(self.eval(node.pattern))
        rx = self.compile_pattern(pattern, node.line)
        return self.record_match(rx.search(subject))

    def eval_IsLike(self, node):
        subject = to_text(self.eval(node.subject))
        pattern = to_text(self.eval(node.pattern))
        return fnmatch.fnmatchcase(subject, pattern)

    def eval_MatchGroups(self, node):
        return list(self.match_groups)

    def eval_WholeMatch(self, node):
        return self.whole_match

    def eval_EveryMatch(self, node):
        pattern = to_text(self.eval(node.pattern))
        source = to_text(self.eval(node.source))
        rx = self.compile_pattern(pattern, node.line)
        return [m.group(0) for m in rx.finditer(source)]

    def exec_Replace(self, node):
        pattern = to_text(self.eval(node.pattern))
        replacement = to_text(self.eval(node.replacement))
        current = to_text(self.read_target(node.target, node.line))
        rx = self.compile_pattern(pattern, node.line)
        try:
            self.write_target(node.target, rx.sub(replacement, current))
        except re.error as e:
            raise FrostError(f"that replacement is not valid: {e}", node.line)

    # Measurements see through the seal, for the same reason comparisons do:
    # the marker's length is not the answer to any question anyone asked, and
    # returning it would be silently wrong rather than safely refusing. A
    # length is a far smaller leak than a wrong answer.

    def eval_LengthOf(self, node):
        v = reveal(self.eval(node.source))
        if isinstance(v, list):
            return len(v)
        return len(to_text(v))

    def eval_CountOf(self, node):
        return len(as_chunks(reveal(self.eval(node.source)), node.kind))

    def eval_StdInRef(self, node):
        if self._stdin_text is None:
            def read():
                try:
                    return ("" if sys.stdin is None
                            else sys.stdin.read()).rstrip("\n")
                except (OSError, ValueError):
                    return ""
            self._stdin_text = (self.journal.standard_input(read)
                                if self.journal is not None else read())
        return self._stdin_text

    def eval_EmptyList(self, node):
        return []

    def eval_SplitBy(self, node):
        separator = to_text(self.eval(node.separator))
        if separator == "":
            raise FrostError("cannot split on an empty separator", node.line,
                             hint='to split into characters, write: '
                                  'the characters of X')
        source = self.eval(node.source)
        parts = to_text(reveal(source)).split(separator)
        return [seal_like(source, p) for p in parts]

    def eval_JoinedBy(self, node):
        separator = to_text(self.eval(node.separator))
        source = self.eval(node.source)
        items = as_list(reveal(source))
        sealed = first_sealed([source] + list(items))
        joined = separator.join(to_text(reveal(p)) for p in items)
        return seal_like(sealed, joined) if sealed is not None else joined

    def eval_ChunkList(self, node):
        source = self.eval(node.source)
        parts = as_chunks(reveal(source), node.kind)
        return [seal_like(source, p) for p in parts]

    def eval_Transform(self, node):
        source = self.eval(node.source)
        value = reveal(source)          # transformed text keeps the seal
        op = node.op
        if op == "uppercase":
            return seal_like(source, to_text(value).upper())
        if op == "lowercase":
            return seal_like(source, to_text(value).lower())
        if op == "trimmed":
            return seal_like(source, to_text(value).strip())
        if op == "rounded":
            return int(round(to_number(value, node.line)))
        if op == "absolute":
            return abs(to_number(value, node.line))

        items = as_list(value)
        if op == "sorted":
            try:
                return sorted(items, key=sort_key(items))
            except (TypeError, FrostError):
                return sorted(to_text(i) for i in items)
        if op == "reversed":
            return list(reversed(items))
        if op == "unique":
            seen, out = set(), []
            for item in items:
                text = to_text(item)
                if text not in seen:
                    seen.add(text)
                    out.append(item)
            return out
        raise FrostError(f"unknown transformation {op!r}", node.line)

    def eval_Aggregate(self, node):
        items = as_list(self.eval(node.source))
        if not items:
            raise FrostError(
                f"the {node.op} of nothing is undefined", node.line,
                hint="check the list is not empty before asking for its "
                     + node.op)
        numbers = [to_number(i, node.line) for i in items]
        if node.op == "sum":
            return sum(numbers)
        if node.op == "largest":
            return max(numbers)
        if node.op == "smallest":
            return min(numbers)
        if node.op == "average":
            return sum(numbers) / len(numbers)
        raise FrostError(f"unknown aggregate {node.op!r}", node.line)

    def eval_FuncCall(self, node):
        if not node.args and node.name not in self.visible_handlers():
            # `the frobnitz` with no handler of that name was never a call;
            # it was a mistyped property. Say so, as the parser would.
            raise FrostError(
                f"'the' must be followed by a property or chunk, found "
                f"{node.name!r}", node.line,
                hint="try: the result / the first line of X / "
                     "the number of words in X / the length of X")
        return self.call_handler(node.name, [self.eval(a) for a in node.args],
                                 node.line)

    def eval_Chunk(self, node):
        source = self.eval(node.source)
        # A word of a password is still part of a password.
        return seal_like(source, self.chunk_of(node, reveal(source)))

    def chunk_of(self, node, source):
        parts = as_chunks(source, node.kind)
        n = len(parts)

        if isinstance(node.start, str):
            if n == 0:
                return ""
            if node.start == "last":
                return parts[-1]
            if node.start == "middle":
                return parts[(n - 1) // 2]
            if node.start == "any":
                return random.choice(parts)

        start = int(to_number(self.eval(node.start), node.line))
        if start < 0:
            start = n + start + 1

        if node.end is None:
            if start < 1 or start > n:
                return ""
            return parts[start - 1]

        end = int(to_number(self.eval(node.end), node.line))
        if end < 0:
            end = n + end + 1
        lo = max(1, start)
        hi = min(n, end)
        if lo > hi:
            return ""
        return join_chunks(parts[lo - 1:hi], node.kind)

    # -- helpers

    def resolve_path(self, path):
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded):
            return expanded
        return os.path.join(self.cwd, expanded)
