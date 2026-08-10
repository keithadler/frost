"""The analysis frost can do with no operating system underneath.

`play.html` used to run `web/chunks.js`, a second implementation of a slice of
the language, because a browser has no Python. That slice could evaluate
expressions and nothing else, so the demo showed the least interesting part of
frost: a visitor could try `the first word of it` and could not see a
capability manifest, a policy refusal, or an approval.

With CPython compiled to WebAssembly the real `frostlang` runs in the page, so
what the demo shows is what the tool does. There is no second implementation
to keep honest.

The reason this works at all is that **everything worth demonstrating is
static analysis.** `--check`, `--explain`, `--policy` and the approval
comparison are all facts about the parse tree, and a parse tree needs no
processes, no filesystem and no network. Only `run` needs a machine, and that
is exactly the one thing a stranger's browser should not be doing.

This module is the whole browser-facing surface. It is plain Python with no
imports the page has to supply, so the test suite exercises it directly rather
than through a headless browser.
"""
# SPDX-License-Identifier: MIT

from .parser import parse, ParseError
from .lexer import LexError
from .audit import (audit, describe, summarise, find_dangers, verdict,
                    parse_policy, check, PolicyError)
from . import baseline as B
from .diagnostics import collect_diagnostics, repair_until_stuck


def _tree(source):
    """Parse, or a message a person can act on. Never a traceback."""
    try:
        return parse(source), None
    except (ParseError, LexError) as e:
        where = f"line {e.line}" if getattr(e, "line", None) else "somewhere"
        hint = getattr(e, "hint", None)
        return None, (f"Syntax error on {where}: {e.msg}"
                      + (f"\n  hint: {hint}" if hint else ""))


def check_only(source):
    tree, problem = _tree(source)
    if problem:
        return problem
    return f"ok ({len(tree)} top-level statements)"


def explain(source):
    """The manifest, the findings and the verdict, as `--explain` prints them."""
    tree, problem = _tree(source)
    if problem:
        return problem
    caps = audit(tree)
    findings = find_dangers(caps)
    out = [summarise(caps), "", describe(caps)]
    if findings:
        out.append("")
        out.append("Findings:")
        for f in findings:
            label = "DANGER " if f.severity == "danger" else "caution"
            out.append(f"  [{label}] line {f.line}  {f.title}")
            out.append(f"             {f.detail}")
    out.append("")
    out.append(f"Verdict: {verdict(findings)}")
    return "\n".join(out)


def enforce(source, policy_text):
    tree, problem = _tree(source)
    if problem:
        return problem
    try:
        rules = parse_policy(policy_text)
    except PolicyError as e:
        return f"The policy itself does not parse: {e}"

    findings = check(audit(tree), rules)
    if not findings:
        return "The script satisfies every rule. It would run."

    lines = source.split("\n")
    out = []
    for f in findings:
        out.append(("REFUSED: " if f.severity == "forbid" else "warning: ")
                   + f.what)
        if 0 < f.line <= len(lines):
            out.append(f"  line {f.line}  {lines[f.line - 1].strip()}")
        if f.hint:
            out.append(f"  why: {f.hint}")
    blocked = [f for f in findings if f.severity == "forbid"]
    out.append("")
    out.append(f"{len(blocked)} rule violation(s); the script would not run."
               if blocked else "Warnings only; the script would run.")
    return "\n".join(out)


def diagnose(source):
    """Every diagnostic, with the repair attached where frost knows one.

    This is what `--check --json` hands an agent. Showing it in a page is the
    difference between "your script is wrong" and "here is the edit", which is
    the entire argument for structured diagnostics.
    """
    found = collect_diagnostics(None, source)
    if not found:
        return "No diagnostics. The script parses and nothing looks dangerous."

    lines = source.split("\n")
    out = []
    for d in found:
        out.append(f"[{d.severity}] {d.code}")
        out.append(f"  {d.message}")
        if d.line and 0 < d.line <= len(lines):
            out.append(f"  line {d.line}:  {lines[d.line - 1].strip()}")
        if d.hint:
            out.append(f"  hint: {d.hint}")
        for r in d.repairs:
            out.append(f"  repair ({r.confidence}): {r.why}")
            out.append(f"    {r.kind} line {r.line}:  {r.text.strip()}")
        if not d.repairs:
            out.append("  no repair: the fix is not derivable from the text")
        out.append("")
    return "\n".join(out).rstrip()


def repair(source):
    """The repaired script, or the original with a reason it stayed put.

    Uses the same loop the command line uses, imported rather than copied. A
    second implementation of the repair rules would drift, which is the exact
    problem running real Python in the page was meant to end.
    """
    fixed, applied = repair_until_stuck(source)
    if not applied:
        return source, "Nothing frost was sure enough about to apply."
    why = "; ".join(f"line {r.line}: {r.why}" for r in applied)
    return fixed, f"{len(applied)} repair(s) applied. {why}"


def approve(source):
    """The capability set a `--approve` would write down."""
    tree, problem = _tree(source)
    if problem:
        return problem
    import json
    return json.dumps(B.capability_set(audit(tree)), indent=2, sort_keys=True)


def compare(source, approved_source):
    """What `--as-approved` would say about a regeneration.

    `source` is the script in front of you and `approved_source` is the older
    one, matching every other action here where the first argument is the
    thing being examined. Taking them the other way round reported a poisoned
    script as a set of narrowings, which reads as reassuring and is exactly
    backwards.
    """
    after, problem = _tree(source)
    if problem:
        return "The new version: " + problem
    before, problem = _tree(approved_source)
    if problem:
        return "The approved version: " + problem

    approved = B.capability_set(audit(before))
    current = B.capability_set(audit(after))
    gained = B.widenings(approved, current)
    lost = B.narrowings(approved, current)

    if not gained:
        out = ["Nothing widened. It would run."]
        out += [f"  narrower: {item}" for item in lost]
        return "\n".join(out)

    out = [f"REFUSED: {item}" for item in gained]
    out.append("")
    out.append(f"{len(gained)} capability change(s); it was not run.")
    return "\n".join(out)


def repair_json(source):
    """`repair`, as JSON, so a page gets the new text and the reason together
    without depending on how a bridge marshals a Python tuple."""
    import json
    fixed, note = repair(source)
    return json.dumps({"source": fixed, "note": note})


ACTIONS = {
    "check": lambda src, extra: check_only(src),
    "explain": lambda src, extra: explain(src),
    "policy": enforce,
    "approve": lambda src, extra: approve(src),
    "compare": compare,
    "diagnose": lambda src, extra: diagnose(src),
    "repair": lambda src, extra: repair_json(src),
}


def run(action, source, extra=""):
    """One entry point for the page, so the JavaScript stays a thin shell."""
    handler = ACTIONS.get(action)
    if handler is None:
        return f"unknown action {action!r}"
    try:
        return handler(source, extra)
    except Exception as e:                       # pragma: no cover
        # A traceback in a demo teaches nothing and looks broken. Anything
        # unexpected still says which action failed.
        return f"{action} could not finish: {type(e).__name__}: {e}"
