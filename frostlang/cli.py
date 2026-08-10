"""Command-line driver for frost."""
# SPDX-License-Identifier: MIT

import argparse
import sys

from . import __version__
from .lexer import LexError
from .parser import parse, ParseError
from .interp import Interpreter, FrostError
from .audit import (audit, describe, parse_policy, check, PolicyError,
                    find_dangers, summarise, verdict)


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
VALUE_OPTIONS = {"--policy"}


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


def main(argv=None):
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
                    help="with --explain, emit the audit as JSON")
    ap.add_argument("--policy", metavar="FILE",
                    help="check the script against a policy file, "
                         "then run it only if it passes")
    own, script_args = split_argv(
        list(sys.argv[1:] if argv is None else argv))
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

    try:
        tree = parse(source)
    except (LexError, ParseError) as e:
        report("Syntax error", e.msg, e.line, getattr(e, "hint", None),
               source_lines, opts.script)
        if opts.fmt:
            sys.stderr.write("frost: refusing to format a script that does "
                             "not parse\n")
        return 2

    if opts.ast:
        import pprint
        pprint.pprint(tree)
        return 0

    if opts.fmt:
        # The parse above already refused anything broken, so this cannot
        # raise; format_source re-checks for the benefit of library callers.
        from .formatter import format_source
        formatted = format_source(source)
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

    if opts.explain:
        caps = audit(tree)
        findings = find_dangers(caps)
        if opts.json:
            import json
            print(json.dumps(audit_json(opts.script, caps, findings,
                                        source_lines), indent=2))
            return 0
        print(f"{opts.script}\n" + "=" * len(opts.script))
        print(summarise(caps))
        print()
        print(describe(caps))
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

        findings = check(audit(tree), rules)
        blocked = [f for f in findings if f[0] == "forbid"]
        for severity, what, line in findings:
            label = "REFUSED" if severity == "forbid" else "warning"
            sys.stderr.write(f"{label}: {what}\n")
            if 0 < line <= len(source_lines):
                sys.stderr.write(f"  {opts.script}:{line}  "
                                 f"{source_lines[line - 1].strip()}\n")
            else:
                # A shortfall — "at least one cleanup" — is about the script
                # as a whole, so there is no line to point at.
                sys.stderr.write(f"  {opts.script}\n")
        if blocked:
            sys.stderr.write(
                f"\n{len(blocked)} rule violation(s); the script was not "
                f"run.\n")
            return 3
        if findings:
            sys.stderr.write("\npolicy passed with warnings.\n\n")

    if opts.check:
        print(f"{opts.script}: ok ({len(tree)} top-level statements)")
        return 0

    interp = Interpreter(argv=opts.args, trace=opts.trace)
    try:
        return interp.run_program(tree)
    except FrostError as e:
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
