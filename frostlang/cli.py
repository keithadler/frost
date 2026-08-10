"""Command-line driver for frost."""
# SPDX-License-Identifier: MIT

import argparse
import os
import sys

from . import __version__
from .lexer import LexError
from .parser import parse, ParseError
from .interp import Interpreter, FrostError
from . import diagnostics
from . import modules as M
from .program_audit import (audit_program, check_all_ceilings,
                            describe_program)
from .audit import (audit, describe, parse_policy, check, PolicyError,
                    find_dangers, summarise, verdict)


def emit_json(payload):
    import json
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    sys.stdout.flush()


def repair_script(opts, source):
    """Apply the repairs frost is sure about.

    The loop this exists for is: generate, check, repair, re-check. That is
    only safe if a repair can never make things worse, which is what
    `repair_until_stuck` guarantees — a pass that does not move the first
    error later is thrown away rather than left on disk for a human to
    unpick.
    """
    repaired, applied = repair_until_stuck(source)

    if opts.json:
        emit_json({
            "schema": diagnostics.SCHEMA_VERSION,
            "script": opts.script,
            "ok": first_error_line(repaired) is None,
            "applied": [r.as_dict() for r in applied],
            "remaining": [d.as_dict() for d in
                          collect_diagnostics(opts.script, repaired)],
            "source": repaired,
        })
        return 0 if applied else 1

    if not applied:
        sys.stderr.write("frost: nothing to repair with confidence\n")
        for diagnostic in collect_diagnostics(opts.script, source):
            if diagnostic.repairs:
                sys.stderr.write(
                    f"  line {diagnostic.line}: {diagnostic.message}\n"
                    f"    suggestion ({diagnostic.repairs[0].confidence}): "
                    f"{diagnostic.repairs[0].text.strip()}\n")
        return 1

    if opts.write:
        with open(opts.script, "w") as fh:
            fh.write(repaired)
        print(f"repaired {opts.script} ({len(applied)} change(s))")
        for r in applied:
            print(f"  line {r.line}: {r.why}")
    else:
        sys.stdout.write(repaired)
    return 0


MAX_REPAIR_PASSES = 10


def first_error_line(source):
    """Where the parser gives up, or None if it does not."""
    try:
        parse(source)
        return None
    except (LexError, ParseError) as e:
        return e.line or 0


def repair_until_stuck(source):
    """Apply repairs until nothing is left that frost is sure about.

    One pass is not enough. A recursive-descent parser stops at the first
    error, so fixing it reveals the next one — a single round would look like
    it had failed whenever a script had two mistakes, which is most of them.

    A pass is kept only if it made progress: either the script now parses, or
    the first error moved strictly later. That is what stops a repair which
    merely rearranges the problem from being written to disk, without
    demanding that one edit fix everything.
    """
    applied = []
    for _ in range(MAX_REPAIR_PASSES):
        before = first_error_line(source)
        if before is None:
            break
        candidate, just_applied = diagnostics.apply_repairs(
            source, collect_diagnostics(None, source),
            minimum=diagnostics.HIGH)
        if not just_applied:
            break
        after = first_error_line(candidate)
        if after is not None and after <= before:
            break                     # no progress; leave it for a human
        source = candidate
        applied.extend(just_applied)
    return source, applied


def format_script(opts, source, source_lines):
    """Canonical layout for one file, with no reference to its imports."""
    from .formatter import format_source
    try:
        formatted = format_source(source)
    except (LexError, ParseError) as e:
        if opts.json:
            emit_json(diagnostics.report(
                opts.script, [diagnostics.from_error(e, source)], False, 2))
            return 2
        report("Syntax error", e.msg, e.line, getattr(e, "hint", None),
               source_lines, opts.script)
        sys.stderr.write("frost: refusing to format a script that does "
                         "not parse\n")
        return 2

    if opts.write:
        if formatted != source:
            with open(opts.script, "w") as fh:
                fh.write(formatted)
            print(f"formatted {opts.script}")
        else:
            print(f"{opts.script} already formatted")
    else:
        sys.stdout.write(formatted)
    return 0


def open_sandbox(opts):
    """Build the boundary, and prove it can be held before running anything.

    Fails closed at every step. A sandbox that is declared but not enforced
    is worse than none, because somebody will rely on it.
    """
    from . import sandbox as S
    from .audit import parse_policy, boundary_from, PolicyError

    if not opts.policy:
        return None, ("--sandbox needs a policy to say what is allowed.\n"
                      "  Declare one:  sandbox may run \"git\"")
    try:
        with open(opts.policy) as fh:
            rules = parse_policy(fh.read())
    except OSError as e:
        return None, f"cannot read policy: {e}"
    except PolicyError as e:
        return None, str(e)

    boundary = boundary_from(rules)
    if not boundary.declared:
        return None, (f"{opts.policy} declares no sandbox boundary.\n"
                      f"  Add one, for example:  sandbox may run \"git\"\n"
                      f"  A policy says what is forbidden; a sandbox has to "
                      f"say what is allowed.")

    try:
        S.require_backend()
    except S.SandboxError as e:
        return None, f"{e.msg}\n  hint: {e.hint}"

    working, detail = S.self_test()
    if not working:
        return None, (f"the sandbox backend is present but did not confine a "
                      f"test command: {detail}\n"
                      f"  Refusing to run, because a boundary that does not "
                      f"hold is worse than none.")

    root = os.path.dirname(os.path.abspath(opts.script)) or "."
    return S.Sandbox(boundary, root), None


def collect_diagnostics(script, source):
    """Every diagnostic frost can produce for this source, without running."""
    try:
        tree = parse(source)
    except (LexError, ParseError) as e:
        return [diagnostics.from_error(e, source)]
    return [diagnostics.from_finding(f, source)
            for f in find_dangers(audit(tree))]


def report(kind, msg, line, hint, source_lines, path):
    out = sys.stderr
    where = f"{path}:{line}" if line else path
    out.write(f"\n{kind} at {where}\n")
    if line and 0 < line <= len(source_lines):
        out.write(f"  {line:>4} | {source_lines[line - 1].rstrip()}\n")
    out.write(f"       {msg}\n")
    if hint:
        out.write(f"       hint: {hint}\n")
    out.write("\n")


def audit_json(path, caps, findings, source_lines):
    def src(n):
        return source_lines[n - 1].strip() if 0 < n <= len(source_lines) else ""
    return {
        "script": path,
        "summary": summarise(caps),
        "verdict": verdict(findings),
        "commands": [
            {"program": c.program, "args": c.args, "line": c.line,
             "source": src(c.line), "checked": c.checked or c.result_examined,
             "timeout": c.timeout, "in_pipe": c.in_pipe}
            for c in caps.commands],
        "reads": [{"path": p, "line": n, "source": src(n)}
                  for p, n in caps.reads],
        "writes": [{"path": p, "line": n, "source": src(n)}
                   for p, n in caps.writes],
        "deletes": [{"path": p, "line": n, "source": src(n)}
                    for p, n in caps.deletes],
        "environment": [{"name": p, "line": n} for p, n in caps.env_reads],
        "exits": sorted({c for c, _ in caps.exit_codes}),
        "findings": [
            {"severity": f.severity, "title": f.title, "detail": f.detail,
             "line": f.line, "source": src(f.line)}
            for f in findings],
    }


# Options that take a separate value token, so the splitter below does not
# mistake that value for the script path.
VALUE_OPTIONS = {"--policy", "--keystore", "--role", "--record", "--replay"}


def split_argv(argv):
    """Separate frost's own options from the script's arguments.

    frost's options end at the script path; everything after it belongs to the
    script, exactly as with `python script.py --flag`. Without this, a script
    that takes `--check` as its own argument could never be given one, because
    argparse would claim the flag for frost.
    """
    own = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-") and arg != "-":
            own.append(arg)
            if arg in VALUE_OPTIONS and i + 1 < len(argv):
                own.append(argv[i + 1])
                i += 1
            i += 1
            continue
        own.append(arg)                  # the script path
        return own, list(argv[i + 1:])
    return own, []


def open_keystore(opts):
    """Load and unlock the keystore, if one was named.

    Returns (keystore, error message). Unlocking here rather than at the
    first `the secret ...` means a wrong passphrase is reported before the
    script has done anything.
    """
    if not opts.keystore:
        return None, None
    from .keystore import Keystore, KeystoreError
    from .keystore_cli import read_passphrase
    try:
        store = Keystore.load(opts.keystore)
    except KeystoreError as e:
        return None, str(e)
    if opts.role:
        try:
            store.unlock(opts.role, read_passphrase(opts.role))
        except (KeystoreError, PermissionError) as e:
            return None, str(e)
    return store, None


def find_secret_names(tree):
    """Every `the secret "..."` in the tree, as (name-or-None, line)."""
    from . import ast as A
    from .audit import literal

    found = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, A.SecretRef):
            found.append((literal(node.name), node.line))
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value)

    walk(tree)
    return found


def check_secret_access(tree, store, role):
    """Which secrets this script names that the role cannot read.

    Answerable from the tree and the keystore's plaintext metadata, with no
    passphrase — which is what lets the script be refused before it runs
    rather than failing part way through, having already done something.
    """
    from . import ast as A
    from .audit import literal

    denials = []
    seen = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, A.SecretRef):
            name = literal(node.name)
            if name is not None and (name, node.line) not in seen:
                seen.add((name, node.line))
                if store is None:
                    denials.append((name, node.line, "no keystore is open"))
                elif name not in store.names:
                    denials.append((name, node.line,
                                    "the keystore has no such secret"))
                elif role is None:
                    denials.append((name, node.line, "no role was given"))
                elif not store.may_read(name, role):
                    allowed = ", ".join(store.roles_for(name))
                    denials.append((name, node.line,
                                    f"{role!r} may not read it "
                                    f"(allowed: {allowed})"))
        if hasattr(node, "__dataclass_fields__"):
            for value in vars(node).values():
                if isinstance(value, list) or hasattr(
                        value, "__dataclass_fields__"):
                    walk(value)

    walk(tree)
    return denials


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    # `frost keystore ...` administers a keystore rather than running a
    # script. Checked before the main parser so its own flags do not collide.
    if raw and raw[0] == "keystore":
        from .keystore_cli import main as keystore_main
        return keystore_main(raw[1:])

    ap = argparse.ArgumentParser(
        prog="frost",
        description="Run a frost script.")
    ap.add_argument("--version", action="version",
                    version=f"frost {__version__}")
    ap.add_argument("script", nargs="?", help="path to a .frost file")
    ap.add_argument("--try", dest="try_mode", action="store_true",
                    help="open a scratchpad for trying chunk expressions")
    ap.add_argument("args", nargs="*",
                    help="arguments passed to the script, not to frost")
    ap.add_argument("--format", dest="fmt", action="store_true",
                    help="print the script in canonical layout")
    ap.add_argument("--write", action="store_true",
                    help="with --format, rewrite the file in place")
    ap.add_argument("--check", action="store_true",
                    help="parse only; do not run")
    ap.add_argument("--ast", action="store_true",
                    help="parse and dump the syntax tree")
    ap.add_argument("--trace", action="store_true",
                    help="print each statement as it runs")
    ap.add_argument("--explain", action="store_true",
                    help="describe what the script can do, without running it")
    ap.add_argument("--json", action="store_true",
                    help="emit the result as JSON: the manifest with "
                         "--explain, and otherwise the diagnostics, with a "
                         "repair for each one that has a mechanical fix")
    ap.add_argument("--repair", action="store_true",
                    help="apply every high-confidence repair and print the "
                         "result; refuses to write unless it then parses")
    ap.add_argument("--policy", metavar="FILE",
                    help="check the script against a policy file, "
                         "then run it only if it passes")
    ap.add_argument("--keystore", metavar="FILE",
                    help="the keystore that 'the secret ...' reads from")
    ap.add_argument("--role", metavar="ROLE",
                    help="the role this run acts as; decides which secrets "
                         "it may read")
    ap.add_argument("--lock", action="store_true",
                    help="record the sha256 of every module in <script>.lock")
    ap.add_argument("--frozen", action="store_true",
                    help="refuse to run if any module differs from the "
                         "lockfile")
    ap.add_argument("--approve", action="store_true",
                    help="record what the script may do, in "
                         "<script>.approved")
    ap.add_argument("--as-approved", action="store_true",
                    dest="as_approved",
                    help="refuse to run if it gained a capability since "
                         "--approve")
    ap.add_argument("--record", metavar="FILE",
                    help="run the script and write down everything it did")
    ap.add_argument("--replay", metavar="FILE",
                    help="run the script against a recording, spawning "
                         "nothing and changing nothing")
    ap.add_argument("--sandbox", action="store_true",
                    help="hold the boundary the policy declares; refuses to "
                         "run if it cannot be enforced here")
    own, script_args = split_argv(raw)
    opts = ap.parse_args(own)
    opts.args = script_args

    if opts.try_mode:
        from .repl import main as repl_main
        return repl_main(opts.script)

    if not opts.script:
        ap.print_help()
        return 2

    try:
        with open(opts.script) as fh:
            source = fh.read()
    except OSError as e:
        sys.stderr.write(f"frost: cannot read {opts.script}: {e}\n")
        return 2

    source_lines = source.splitlines()

    if opts.repair:
        return repair_script(opts, source)

    # Before the modules load: laying out one file is a lexical job, and
    # somebody fixing a broken import should still be able to format it.
    if opts.fmt:
        return format_script(opts, source, source_lines)

    # The whole closure, read once. Everything below audits and runs these
    # same bytes; nothing re-opens a module later.
    try:
        program = M.load(opts.script)
    except (LexError, ParseError) as e:
        if opts.json:
            emit_json(diagnostics.report(
                opts.script, [diagnostics.from_error(e, source)], False, 2))
            return 2
        report("Syntax error", e.msg, e.line, getattr(e, "hint", None),
               source_lines, opts.script)
        if opts.fmt:
            sys.stderr.write("frost: refusing to format a script that does "
                             "not parse\n")
        return 2
    except M.ModuleError as e:
        # Fail closed. A manifest built from a partial closure would be a
        # manifest with a hole in it, which is the one output that actively
        # misleads a reviewer.
        if opts.json:
            emit_json(diagnostics.report(
                opts.script,
                [diagnostics.Diagnostic("error", "module-error", e.msg,
                                        line=e.line, hint=e.hint or "")],
                False, 2))
            return 2
        report("Module error", e.msg, e.line, e.hint, source_lines,
               opts.script)
        return 2
    tree = program.tree

    if opts.lock:
        path = M.lock_path(opts.script)
        M.write_lock(program, path)
        print(f"wrote {path} ({len(program.modules)} file(s))")
        return 0

    if opts.frozen:
        try:
            drift = M.check_lock(program, M.lock_path(opts.script))
        except M.ModuleError as e:
            sys.stderr.write(f"frost: {e.msg}\n")
            if e.hint:
                sys.stderr.write(f"  hint: {e.hint}\n")
            return 2
        if drift:
            for item in drift:
                sys.stderr.write(f"REFUSED: {item}\n")
            sys.stderr.write(f"\nthe program does not match its lockfile; "
                             f"it was not run.\n")
            return 3

    if opts.approve or opts.as_approved:
        from . import baseline as B
        caps = audit_program(program).merged
        path = B.path_for(opts.script)

        if opts.approve:
            previous = None
            try:
                previous = B.read(path)
            except B.BaselineError:
                pass                       # nothing approved yet is not a fault
            B.write(opts.script, caps, path)
            print(f"wrote {path}")
            if previous is not None:
                for item in B.widenings(previous, B.capability_set(caps)):
                    print(f"  wider:    {item}")
                for item in B.narrowings(previous, B.capability_set(caps)):
                    print(f"  narrower: {item}")
            return 0

        try:
            approved = B.read(path)
        except B.BaselineError as e:
            sys.stderr.write(f"frost: {e.msg}\n")
            if e.hint:
                sys.stderr.write(f"  hint: {e.hint}\n")
            return 2
        gained = B.widenings(approved, B.capability_set(caps))
        if gained:
            for item in gained:
                sys.stderr.write(f"REFUSED: {item}\n")
            sys.stderr.write(
                f"\n{len(gained)} capability change(s) since {path}; "
                f"it was not run.\n"
                f"  Read what changed, then re-approve with --approve.\n")
            return 3

    if opts.ast:
        import pprint
        pprint.pprint(tree)
        return 0

    if opts.explain:
        program_caps = audit_program(program)
        caps = program_caps.merged
        findings = find_dangers(caps) + check_all_ceilings(program,
                                                           program_caps)
        findings.sort(key=lambda f: f.line)
        if opts.json:
            import json
            print(json.dumps(audit_json(opts.script, caps, findings,
                                        source_lines), indent=2))
            return 0
        print(f"{opts.script}\n" + "=" * len(opts.script))
        print(summarise(caps))
        if len(program.modules) > 1:
            print(f"\nBuilt from {len(program.modules)} files: "
                  + ", ".join(sorted(program.modules)))
        print()
        print(describe_program(program, program_caps))
        if findings:
            print()
            label = {"danger": "DANGER ", "caution": "caution", "note": "note   "}
            print("Findings:")
            for f in findings:
                print(f"  [{label[f.severity]}] line {f.line}  {f.title}")
                print(f"             {f.detail}")
        print()
        print(f"Verdict: {verdict(findings)}")
        return 0 if verdict(findings) in ("clean", "caution") else 1

    if opts.policy:
        try:
            with open(opts.policy) as fh:
                rules = parse_policy(fh.read())
        except OSError as e:
            sys.stderr.write(f"frost: cannot read policy: {e}\n")
            return 2
        except PolicyError as e:
            sys.stderr.write(f"frost: {e}\n")
            return 2

        findings = check(audit_program(program).merged, rules)
        blocked = [f for f in findings if f.severity == "forbid"]
        if opts.json:
            emit_json(diagnostics.report(
                opts.script,
                [diagnostics.from_policy_finding(f, source) for f in findings],
                not blocked, 3 if blocked else 0))
            return 3 if blocked else 0
        for finding in findings:
            label = "REFUSED" if finding.severity == "forbid" else "warning"
            sys.stderr.write(f"{label}: {finding.what}\n")
            line = finding.line
            if 0 < line <= len(source_lines):
                sys.stderr.write(f"  {opts.script}:{line}  "
                                 f"{source_lines[line - 1].strip()}\n")
            else:
                # A shortfall — "at least one cleanup" — is about the script
                # as a whole, so there is no line to point at.
                sys.stderr.write(f"  {opts.script}\n")
            # The rule's own comment. A refusal that says only "no" leaves
            # the reader to guess what to do instead.
            if finding.hint:
                sys.stderr.write(f"  why: {finding.hint}\n")
        if blocked:
            sys.stderr.write(
                f"\n{len(blocked)} rule violation(s); the script was not "
                f"run.\n")
            return 3
        if findings:
            sys.stderr.write("\npolicy passed with warnings.\n\n")

    # An import declares what the module it names may do. Exceeding it is a
    # refusal, not a warning: the point is that reading the entry file gives a
    # sound upper bound on the whole program.
    if len(program.modules) > 1:
        breaches = check_all_ceilings(program, audit_program(program))
        if breaches:
            if opts.json:
                emit_json(diagnostics.report(
                    opts.script,
                    [diagnostics.from_finding(f, source) for f in breaches],
                    False, 3))
                return 3
            for f in breaches:
                sys.stderr.write(f"REFUSED: {f.title}\n")
                sys.stderr.write(f"  {f.detail}\n")
            sys.stderr.write(
                f"\n{len(breaches)} import(s) exceeded what they declared; "
                f"the script was not run.\n")
            return 3

    if opts.check:
        # Parse-only. Deliberately before the keystore: checking that a script
        # is well formed must not require the credentials it will use, or it
        # stops being usable as a pre-commit hook.
        if opts.json:
            findings = find_dangers(audit(tree))
            emit_json(diagnostics.report(
                opts.script,
                [diagnostics.from_finding(f, source) for f in findings],
                True, 0, {"statements": len(tree),
                          "verdict": verdict(findings)}))
            return 0
        print(f"{opts.script}: ok ({len(tree)} top-level statements)")
        return 0

    # Secrets are a capability like any other, so the refusal happens here —
    # before anything runs — rather than at the line that reads one, by which
    # point the script may already have done half its work.
    store = None
    if find_secret_names(tree) or opts.keystore:
        store, problem = open_keystore(opts)
        if problem:
            sys.stderr.write(f"frost: {problem}\n")
            return 2
        denials = check_secret_access(tree, store, opts.role)
        for name, line, why in denials:
            sys.stderr.write(f"REFUSED: the secret {name!r} — {why}\n")
            if 0 < line <= len(source_lines):
                sys.stderr.write(f"  {opts.script}:{line}  "
                                 f"{source_lines[line - 1].strip()}\n")
        if denials:
            if opts.json:
                emit_json(diagnostics.report(
                    opts.script,
                    [diagnostics.Diagnostic(
                        "error", "secret-unavailable",
                        f"the secret {name!r} is unavailable: {why}",
                        line=line,
                        source=(source_lines[line - 1].strip()
                                if 0 < line <= len(source_lines) else ""))
                     for name, line, why in denials],
                    False, 3))
                return 3
            sys.stderr.write(
                f"\n{len(denials)} secret(s) unavailable; the script was not "
                f"run.\n")
            return 3

    interp = Interpreter(argv=opts.args, trace=opts.trace,
                         keystore=store, role=opts.role)
    if len(program.modules) > 1:
        interp.install(program)

    guard = None
    if opts.sandbox:
        guard, problem = open_sandbox(opts)
        if problem:
            sys.stderr.write(f"frost: {problem}\n")
            return 2
        interp.sandbox = guard

    from . import journal as J
    recorder = player = None
    if opts.record and opts.replay:
        sys.stderr.write("frost: use --record or --replay, not both\n")
        return 2
    if opts.record:
        recorder = interp.journal = J.Recorder()
    elif opts.replay:
        try:
            player = interp.journal = J.Player.load(opts.replay)
        except FileNotFoundError:
            sys.stderr.write(f"frost: cannot read {opts.replay}\n")
            return 2
        except (ValueError, J.Divergence) as e:
            sys.stderr.write(f"frost: {getattr(e, 'msg', e)}\n")
            return 2
    try:
        status = interp.run_program(tree)
        if recorder is not None:
            recorder.status = status
            recorder.save(opts.record, opts.script, opts.args)
            sys.stderr.write(f"frost: recorded {len(recorder.events)} "
                             f"event(s) to {opts.record}\n")
        if player is not None:
            left = player.unconsumed()
            if left:
                for event in left:
                    sys.stderr.write(
                        f"DIVERGED: the recording also did: "
                        f"{J._describe(event)}\n")
                sys.stderr.write(f"\n{len(left)} recorded effect(s) did not "
                                 f"happen this time.\n")
                return 4
        return status
    except J.Divergence as e:
        where = f"{opts.script}:{e.line}" if e.line else opts.script
        sys.stderr.write(f"\nDIVERGED at {where}\n    {e.msg}\n\n")
        return 4
    except FrostError as e:
        if opts.json:
            e.candidates = sorted(interp.globals)
            emit_json(diagnostics.report(
                opts.script, [diagnostics.from_error(e, source)], False, 1))
            return 1
        report("Error", e.msg, e.line, e.hint, source_lines, opts.script)
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nfrost: interrupted\n")
        return 130
    except RecursionError:
        sys.stderr.write("frost: handlers nested too deeply\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
