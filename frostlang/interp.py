"""Tree-walking interpreter for frost.

Two rules do most of the safety work here:

1. Arguments are a list, never a string. Nothing built at runtime is ever
   re-parsed as syntax, so there is no injection surface.
2. `run` aborts the script on a non-zero exit unless it was written as
   `try to run`. Pipes report failure if ANY stage fails, not just the last.
"""
# SPDX-License-Identifier: MIT

import fnmatch
import os
import random
import re
import subprocess
import sys
import tempfile

from . import ast as A


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
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return "\n".join(to_text(x) for x in v)
    return str(v)


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
    def __init__(self, argv=None, trace=False, cwd=None):
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

    def run_program(self, stmts):
        for s in stmts:
            if isinstance(s, A.HandlerDef):
                self.handlers[s.name] = s
        body = [s for s in stmts if not isinstance(s, A.HandlerDef)]

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
        for block in reversed(pending):
            try:
                self.exec_block(block)
            except (QuitSignal, ReturnSignal, ExitRepeatSignal,
                    NextRepeatSignal):
                pass
            except FrostError as e:
                where = f" at line {e.line}" if e.line else ""
                sys.stderr.write(f"frost: cleanup failed{where}: {e.msg}\n")
                sys.stderr.flush()

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
            if node.mode == "before":
                existing = ""
                if os.path.exists(path):
                    with open(path) as fh:
                        existing = fh.read()
                with open(path, "w") as fh:
                    fh.write(to_text(value) + "\n" + existing)
                return
            with open(path, mode) as fh:
                fh.write(to_text(value) + "\n")
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
            addition = to_text(value)
            if node.mode == "into":
                self.env[name] = addition
            else:
                current = self.env.get(name, "")
                self.env[name] = (current + addition if node.mode == "after"
                                  else addition + current)
            return

        if node.mode == "into":
            self.write_target(target, value)
        else:
            current = to_text(self.read_target(target, node.line))
            addition = to_text(value)
            self.write_target(
                target,
                current + addition if node.mode == "after"
                else addition + current)

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
                args.extend(to_text(x) for x in v)
            else:
                args.append(to_text(v))

        seconds = self.eval_timeout(node)
        stdin_text = None
        if node.stdin is not None:
            stdin_text = to_text(self.eval(node.stdin))
            if not stdin_text.endswith("\n"):
                stdin_text += "\n"

        try:
            proc = subprocess.run([program] + args, capture_output=True,
                                  text=True, cwd=self.child_folder(node),
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

        self.it = proc.stdout.rstrip("\n")
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
                    args.extend(to_text(x) for x in v)
                else:
                    args.append(to_text(v))
            commands.append(([program] + args, stage.line))

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
        parts = as_chunks(self.eval(node.source), node.kind)
        for part in parts:
            self.set_var(node.var, part)
            try:
                self.exec_block(node.block)
            except NextRepeatSignal:
                continue
            except ExitRepeatSignal:
                break

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
        self.cleanups.append(node.block)

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
        try:
            os.remove(path)
        except FileNotFoundError:
            raise FrostError(f"there is no file at {path!r}", node.line)

    def exec_Call(self, node):
        handler = self.handlers.get(node.name)
        if handler is None:
            raise FrostError(
                f"there is no handler named {node.name!r}", node.line,
                hint="define it with:  to " + node.name + " ... end "
                     + node.name)
        args = [self.eval(a) for a in node.args]
        if len(args) != len(handler.params):
            raise FrostError(
                f"{node.name!r} expects {len(handler.params)} value(s) "
                f"but got {len(args)}", node.line)
        frame = dict(zip(handler.params, args))
        self.scopes.append(frame)
        try:
            self.exec_block(handler.block)
            self.it = ""
        except ReturnSignal as r:
            self.it = r.value
        finally:
            self.scopes.pop()

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
        return self.env.get(to_text(self.eval(node.name)), "")

    def eval_GlobalRef(self, node):
        if node.name not in self.globals:
            raise FrostError(
                f"there is no global named {node.name!r}", node.line,
                hint="assign it first with:  put ... into the global "
                     + node.name)
        return self.globals[node.name]

    def eval_FileRef(self, node):
        path = self.resolve_path(to_text(self.eval(node.path)))
        try:
            with open(path) as fh:
                return fh.read().rstrip("\n")
        except FileNotFoundError:
            raise FrostError(f"there is no file at {path!r}", node.line)
        except IsADirectoryError:
            raise FrostError(f"{path!r} is a folder, not a file", node.line)

    def eval_FileExists(self, node):
        inner = node.path
        if isinstance(inner, A.FileRef):
            path = to_text(self.eval(inner.path))
        else:
            path = to_text(self.eval(inner))
        return os.path.exists(self.resolve_path(path))

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
        if node.op == "&":
            return to_text(left) + to_text(right)
        if node.op == "&&":
            return to_text(left) + " " + to_text(right)
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

        if node.op == "is empty":
            return to_text(left).strip() == ""
        if node.op == "is not empty":
            return to_text(left).strip() != ""

        right = self.eval(node.right)

        if node.op == "contains":
            if isinstance(left, list):
                return to_text(right) in [to_text(x) for x in left]
            return to_text(right) in to_text(left)
        if node.op == "in":
            if isinstance(right, list):
                return to_text(left) in [to_text(x) for x in right]
            return to_text(left) in to_text(right)
        if node.op == "starts with":
            return to_text(left).startswith(to_text(right))
        if node.op == "ends with":
            return to_text(left).endswith(to_text(right))

        if is_numberish(left) and is_numberish(right):
            a, b = to_number(left), to_number(right)
        else:
            a, b = to_text(left), to_text(right)

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

    def eval_LengthOf(self, node):
        v = self.eval(node.source)
        if isinstance(v, list):
            return len(v)
        return len(to_text(v))

    def eval_CountOf(self, node):
        return len(as_chunks(self.eval(node.source), node.kind))

    def eval_Chunk(self, node):
        parts = as_chunks(self.eval(node.source), node.kind)
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
