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
from .diagnostics import (collect_diagnostics, first_error_line,
                          repair_until_stuck, MAX_REPAIR_PASSES)
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
def value_options(parser):
    """Which of frost's own flags consume the token after them.

    Read off the parser rather than kept in a list beside it. The list version
    was missing `--trace-to-file` the moment it was added, which made
    `frost --trace-to-file out.log s.frost` treat out.log as the script — a
    silent misparse, since argparse then complained about something else
    entirely.
    """
    out = set()
    for action in parser._actions:
        if action.option_strings and action.nargs != 0:
            out.update(action.option_strings)
    return out


def split_argv(argv, value_taking=None):
    """Separate frost's own options from the script's arguments.

    frost's options end at the script path; everything after it belongs to the
    script, exactly as with `python script.py --flag`. Without this, a script
    that takes `--check` as its own argument could never be given one, because
    argparse would claim the flag for frost.
    """
    if value_taking is None:
        # Deriving it is the default rather than the opt-in. An empty default
        # does not fail loudly, it misparses quietly, which is how the stale
        # list went unnoticed in the first place.
        value_taking = value_options(build_parser())
    own = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-") and arg != "-":
            own.append(arg)
            if arg in value_taking and i + 1 < len(argv):
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


EXIT_CODES = [
    (0, "ok", "the script ran and finished"),
    (1, "failed", "a command failed, or the script hit a runtime error"),
    (2, "unusable", "it did not parse, or frost was asked for something it "
                    "could not set up"),
    (3, "refused", "a policy, an import ceiling, a sandbox boundary or an "
                   "approval said no; nothing ran"),
    (4, "diverged", "a replay did not match its recording"),
    (130, "interrupted", "somebody pressed control-C"),
    (141, "pipe closed", "the reader went away, as with `| head`"),
]


def emit_exit_codes(as_json):
    if as_json:
        emit_json({"schema": 1, "exit_codes": [
            {"code": c, "name": n, "meaning": m} for c, n, m in EXIT_CODES]})
        return 0
    width = max(len(n) for _, n, _ in EXIT_CODES)
    for code, name, meaning in EXIT_CODES:
        print(f"{code:>3}  {name.ljust(width)}  {meaning}")
    return 0


def emit_completion(shell):
    """Completion generated from the parser, not written beside it."""
    flags = " ".join(sorted(
        opt for action in build_parser()._actions
        for opt in action.option_strings))
    if shell == "zsh":
        return ("#compdef frost\n"
                "_frost() {\n"
                f"  local flags=({flags})\n"
                '  _arguments "*:file:_files -g \'*.frost\'" \\\n'
                '    "(- *)"{-h,--help}"[show help]"\n'
                "  compadd -- $flags\n"
                "}\n"
                "_frost \"$@\"\n")
    return ("_frost() {\n"
            '  local cur="${COMP_WORDS[COMP_CWORD]}"\n'
            f'  local flags="{flags}"\n'
            '  if [[ "$cur" == -* ]]; then\n'
            '    COMPREPLY=($(compgen -W "$flags" -- "$cur"))\n'
            "  else\n"
            '    COMPREPLY=($(compgen -f -X "!*.frost" -- "$cur"))\n'
            "  fi\n"
            "}\n"
            "complete -F _frost frost\n")


def build_parser():
    """frost's own options. One definition, so nothing can drift from it."""
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
    ap.add_argument("--run-id", metavar="ID", dest="run_id",
                    help="identity for this execution; otherwise FROST_RUN_ID, "
                         "otherwise generated")
    ap.add_argument("--trace-to-file", metavar="FILE", dest="trace_to_file",
                    help="write the trace to a file instead of standard error")
    ap.add_argument("--explain", action="store_true",
                    help="describe what the script can do, without running it")
    ap.add_argument("--sarif", action="store_true",
                    help="findings as SARIF, for code scanning on a pull "
                         "request")
    ap.add_argument("--exit-codes", action="store_true", dest="exit_codes",
                    help="what each exit status means")
    ap.add_argument("--completion", metavar="SHELL",
                    choices=["bash", "zsh"],
                    help="print a completion script for bash or zsh")
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
                    help="insist on an approval: refuse if there is none, or "
                         "if the script gained a capability since it")
    ap.add_argument("--against", metavar="FILE",
                    help="compare the manifest with a recorded approval, "
                         "without running anything")
    ap.add_argument("--policy-from", action="store_true", dest="policy_from",
                    help="write a starter policy describing what this script "
                         "already does")
    ap.add_argument("--sign-with", metavar="KEYFILE", dest="sign_with",
                    help="sign the approval with this key")
    ap.add_argument("--approver", metavar="NAME",
                    help="who is approving; recorded inside the signature")
    ap.add_argument("--commit", metavar="SHA",
                    help="the revision this approval was read against")
    ap.add_argument("--new-approver-key", metavar="KEYFILE",
                    dest="new_approver_key",
                    help="write a new signing key and print its public half")
    ap.add_argument("--automated", action="store_true",
                    help="this run is unattended: refuse anything that would "
                         "widen what a script may do")
    ap.add_argument("--ignore-approval", action="store_true",
                    dest="ignore_approval",
                    help="run even though <script>.approved says otherwise")
    ap.add_argument("--record", metavar="FILE",
                    help="run the script and write down everything it did")
    ap.add_argument("--replay", metavar="FILE",
                    help="run the script against a recording, spawning "
                         "nothing and changing nothing")
    ap.add_argument("--sandbox", action="store_true",
                    help="hold the boundary the policy declares; refuses to "
                         "run if it cannot be enforced here")
    return ap


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    # `frost keystore ...` administers a keystore rather than running a
    # script. Checked before the main parser so its own flags do not collide.
    if raw and raw[0] == "keystore":
        from .keystore_cli import main as keystore_main
        return keystore_main(raw[1:])

    ap = build_parser()
    own, script_args = split_argv(raw, value_options(ap))
    opts = ap.parse_args(own)
    opts.args = script_args

    # Answer these before a script is required: both are questions about
    # frost, not about anything it was asked to run.
    automated = opts.automated or os.environ.get("FROST_AUTOMATED") == "1"
    if automated:
        blocked = [name for flag, name in
                   ((opts.approve, "--approve"),
                    (opts.ignore_approval, "--ignore-approval")) if flag]
        if blocked:
            sys.stderr.write(
                f"frost: {', '.join(blocked)} cannot be used in an automated "
                f"run.\n"
                f"  An unattended loop that can approve is a loop that "
                f"approves its own\n"
                f"  capability escalation. A person decides this one.\n")
            return 2

    if opts.new_approver_key:
        from . import signing
        try:
            private, public = signing.generate()
            signing.write_key(opts.new_approver_key, private)
        except signing.SigningError as e:
            sys.stderr.write(f"frost: {e.msg}\n")
            if e.hint:
                sys.stderr.write(f"  hint: {e.hint}\n")
            return 2
        print(f"wrote {opts.new_approver_key} (keep it; it is the private "
              f"half)")
        print(f"public key: {public}")
        print("Name it in a policy:  require an approval signed by "
              f'"{public}"')
        return 0

    if opts.exit_codes:
        return emit_exit_codes(opts.json)
    if opts.completion:
        sys.stdout.write(emit_completion(opts.completion))
        return 0

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
        if opts.sarif:
            # A syntax error is the finding most worth annotating on a diff,
            # so this cannot wait until after the script has parsed.
            from . import sarif as S
            sys.stdout.write(S.dump(
                opts.script, [diagnostics.from_error(e, source)], __version__))
            return 2
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

    if opts.approve:
        from . import baseline as B
        caps = audit_program(program).merged
        path = B.path_for(opts.script)

        if True:
            previous = None
            try:
                previous = B.read(path)
            except B.BaselineError:
                pass                       # nothing approved yet is not a fault
            key = None
            if opts.sign_with:
                from . import signing
                try:
                    key = signing.read_key(opts.sign_with)
                except signing.SigningError as e:
                    sys.stderr.write(f"frost: {e.msg}\n")
                    return 2
            commit = opts.commit or os.environ.get("GITHUB_SHA")
            try:
                B.write(opts.script, caps, path, sign_with=key,
                        approver=opts.approver, commit=commit)
            except Exception as e:
                sys.stderr.write(f"frost: cannot sign the approval: {e}\n")
                return 2
            print(f"wrote {path}"
                  + (f", signed by {opts.approver or 'unnamed'}" if key else ""))
            if previous is not None:
                for item in B.widenings(previous, B.capability_set(caps)):
                    print(f"  wider:    {item}")
                for item in B.narrowings(previous, B.capability_set(caps)):
                    print(f"  narrower: {item}")
            return 0


    if opts.ast:
        import pprint
        pprint.pprint(tree)
        return 0

    from . import site as SITE
    try:
        site_rules, provenance = SITE.load()
    except SITE.SitePolicyError as e:
        sys.stderr.write(f"frost: {e.msg}\n")
        if e.hint:
            sys.stderr.write(f"  hint: {e.hint}\n")
        return 2

    if opts.policy_from:
        from . import scaffold
        sys.stdout.write(scaffold.policy_for(
            opts.script, audit_program(program).merged))
        return 0

    if opts.against:
        from . import baseline as B
        try:
            approved = B.read(opts.against)
        except B.BaselineError as e:
            sys.stderr.write(f"frost: {e.msg}\n")
            if e.hint:
                sys.stderr.write(f"  hint: {e.hint}\n")
            return 2
        current = B.capability_set(audit_program(program).merged)
        gained, lost = B.widenings(approved, current), B.narrowings(approved,
                                                                    current)
        for item in gained:
            print(f"wider:    {item}")
        for item in lost:
            print(f"narrower: {item}")
        if not gained and not lost:
            print("unchanged: it can do exactly what it was approved to do.")
        # Reviewing is reading, so this reports rather than refuses — except
        # when something widened, which is the answer CI needs to act on.
        return 3 if gained else 0

    if opts.explain:
        program_caps = audit_program(program)
        caps = program_caps.merged
        findings = find_dangers(caps) + check_all_ceilings(program,
                                                           program_caps)
        findings.sort(key=lambda f: f.line)
        if opts.sarif:
            from . import sarif as S
            sys.stdout.write(S.dump(
                opts.script,
                [diagnostics.from_finding(f, source) for f in findings],
                __version__))
            return 0
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
        for line in SITE.describe(provenance):
            print(line)
        if provenance:
            print()
        print(f"Verdict: {verdict(findings)}")
        return 0 if verdict(findings) in ("clean", "caution") else 1

    policy_rules = list(site_rules)
    if opts.policy:
        try:
            with open(opts.policy) as fh:
                text = fh.read()
            # Added, never substituted. A project cannot widen a site rule
            # because there is no syntax that removes one, and `--policy` has
            # never replaced the host's rules.
            policy_rules = site_rules + parse_policy(text)
            provenance.append(SITE.note(opts.policy, text))
        except OSError as e:
            sys.stderr.write(f"frost: cannot read policy: {e}\n")
            return 2
        except PolicyError as e:
            sys.stderr.write(f"frost: {e}\n")
            return 2

    # Enforced whenever there are rules, from wherever they came. Checking only
    # when --policy was passed would mean a machine's own policy applied solely
    # to people who volunteered for it.
    if policy_rules:
        rules = policy_rules
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

    if opts.check and opts.sarif:
        from . import sarif as S
        sys.stdout.write(S.dump(opts.script,
                                collect_diagnostics(opts.script, source),
                                __version__))
        return 0

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

    if not opts.ignore_approval:
        from . import baseline as B
        path = B.path_for(opts.script)
        exists = os.path.exists(path)
        # `require an approval` in the policy makes the file mandatory, so an
        # organisation can insist centrally rather than hoping every caller
        # remembers the flag.
        demanded = opts.as_approved or any(
            r.kind == "approval" for r in (policy_rules or []))
        signers = [k for r in (policy_rules or [])
                   if r.kind == "approval_signed" for k in (r.detail or [])]
        demanded = demanded or bool(signers)
        if not exists and demanded:
            sys.stderr.write(
                f"frost: {opts.script} has no approval, and one is "
                f"required.\n  hint: read what it does with --explain, then "
                f"record it with --approve\n")
            return 2
        if exists or demanded:
            if signers:
                from . import signing
                try:
                    whole = B.read_whole(path)
                except B.BaselineError as e:
                    sys.stderr.write(f"frost: {e.msg}\n")
                    return 2
                ok, why = signing.verify(whole, signers)
                if not ok:
                    sys.stderr.write(
                        f"REFUSED: the approval for {opts.script} is not "
                        f"usable.\n  {why}\n\n"
                        f"The policy requires an approval signed by one of "
                        f"{len(signers)} named approver(s).\n")
                    return 3
            try:
                approved = B.read(path)
            except B.BaselineError as e:
                sys.stderr.write(f"frost: {e.msg}\n")
                if e.hint:
                    sys.stderr.write(f"  hint: {e.hint}\n")
                return 2
            gained = B.widenings(approved,
                                 B.capability_set(audit_program(program).merged))
            if gained:
                for item in gained:
                    sys.stderr.write(f"REFUSED: {item}\n")
                sys.stderr.write(
                    f"\n{len(gained)} capability change(s) since {path}; "
                    f"it was not run.\n"
                    f"  See them in context with --explain, then re-approve "
                    f"with --approve.\n")
                return 3

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

    from . import runid as R
    try:
        run_id, run_id_from = R.resolve(opts.run_id, os.environ)
    except R.RunIdError as e:
        sys.stderr.write(f"frost: {e.msg}\n")
        if e.hint:
            sys.stderr.write(f"  hint: {e.hint}\n")
        return 2

    trace_stream = None
    if opts.trace_to_file:
        try:
            trace_stream = open(opts.trace_to_file, "w")
            trace_stream.write(f"[frost] run {run_id} ({run_id_from})\n")
        except OSError as e:
            sys.stderr.write(f"frost: cannot write the trace: {e}\n")
            return 2

    interp = Interpreter(argv=opts.args, trace=opts.trace, source=source,
                         trace_to=trace_stream, run_id=run_id,
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
        recorder = interp.journal = J.Recorder(run_id, provenance)
    elif opts.replay:
        try:
            player = interp.journal = J.Player.load(opts.replay)
        except FileNotFoundError:
            sys.stderr.write(f"frost: cannot read {opts.replay}\n")
            return 2
        except (ValueError, J.Divergence) as e:
            sys.stderr.write(f"frost: {getattr(e, 'msg', e)}\n")
            return 2
    outcome = 1
    try:
        outcome = status = interp.run_program(tree)
        if player is not None:
            left = player.unconsumed()
            if left:
                for event in left:
                    sys.stderr.write(
                        f"DIVERGED: the recording also did: "
                        f"{J._describe(event)}\n")
                sys.stderr.write(f"\n{len(left)} recorded effect(s) did not "
                                 f"happen this time.\n")
                outcome = 4
                return outcome
        return status
    except J.Divergence as e:
        where = f"{opts.script}:{e.line}" if e.line else opts.script
        sys.stderr.write(f"\nDIVERGED at {where}\n    {e.msg}\n\n")
        outcome = 4
        return outcome
    except FrostError as e:
        outcome = 1
        if opts.json:
            e.candidates = sorted(interp.globals)
            emit_json(diagnostics.report(
                opts.script, [diagnostics.from_error(e, source)], False, 1))
            return 1
        report("Error", e.msg, e.line, e.hint, source_lines, opts.script)
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nfrost: interrupted\n")
        outcome = 130
        return outcome
    except BrokenPipeError:
        # `frost s.frost | head` is an ordinary thing to do, and the reader
        # closing early is not an error in the script. A traceback here says
        # frost broke when the shell did exactly what it was asked.
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        outcome = 141                      # what a shell reports for SIGPIPE
        return outcome
    except RecursionError:
        sys.stderr.write("frost: handlers nested too deeply\n")
        outcome = 1
        return outcome
    finally:
        # However the run ended. A recording that only survives success is
        # useless for the case it is most wanted in: the run that failed,
        # was interrupted, or wedged is exactly the one somebody needs to
        # read afterwards, and it was the one being thrown away.
        if trace_stream is not None:
            trace_stream.close()
        if recorder is not None:
            recorder.status = outcome
            try:
                recorder.save(opts.record, opts.script, opts.args)
                sys.stderr.write(f"frost: recorded {len(recorder.events)} "
                                 f"event(s) to {opts.record}\n")
            except OSError as e:
                # Never let a failure to write the record hide the failure
                # the record was about.
                sys.stderr.write(f"frost: could not write {opts.record}: "
                                 f"{e}\n")


if __name__ == "__main__":
    sys.exit(main())
