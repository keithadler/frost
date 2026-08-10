#!/usr/bin/env python3
"""Check that web/chunks.js agrees with frostlang/ on every expression.

The browser evaluator is a second implementation of a slice of the language.
Two implementations drift unless something forces them together, so this runs a
generated corpus through both and fails on the first disagreement. It is a
build step, not an optional check.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang.parser import Parser, ParseError
from frostlang.lexer import LexError
from frostlang.interp import Interpreter, FrostError, to_text

JS = os.path.join(HERE, "web", "chunks.js")

JSON_SUBJECT = ('{"status": "green", "user": {"name": "ada", "id": 7}, '
                '"tags": ["alpha", "beta"], "ratio": 1.5, "ok": true, '
                '"nothing": null, "empty list": [], "empty record": {}}')

SUBJECTS = {
    "json": JSON_SUBJECT,
    "log": ("10.0.0.1 GET /index.html 200 0.014\n"
            "10.0.0.2 POST /api/login 500 1.221\n"
            "10.0.0.3 GET /about.html 200 0.031\n"
            "10.0.0.9 GET /missing 404 0.008"),
    "csv": "alpha, beta , gamma,delta",
    "short": "one two three",
    "one": "solo",
    "blank": "",
    "unicode": "café ñu 日本語 test",
    "spacing": "  ragged   spacing\there  ",
}

ORDINALS = ["first", "second", "third", "fourth", "tenth", "last", "middle"]
NOUNS = ["character", "word", "line", "item"]
PLURALS = {"character": "characters", "word": "words", "line": "lines",
           "item": "items"}


def corpus():
    out = []
    for o in ORDINALS:
        for n in NOUNS:
            out.append(f"the {o} {n} of it")
            out.append(f"{o} {n} of it")
    for n in NOUNS:
        out.append(f"the number of {PLURALS[n]} in it")
        for i in (1, 2, 3, 7, 99, -1, -2):
            out.append(f"{n} {i} of it")
            out.append(f"the {n} {i} of it")
        for a, b in ((1, 2), (2, 4), (2, 2), (3, 1), (1, 99), (-2, -1)):
            out.append(f"{PLURALS[n]} {a} to {b} of it")

    out += [
        "the length of it",
        "the first word of line 2 of it",
        "the third word of line 2 of it",
        "the last word of the first line of it",
        "the number of words in line 2 of it",
        "the number of characters in the first word of it",
        "words 2 to 3 of the last line of it",
        "the first character of the last word of the second line of it",
        '"prefix-" & the first word of it',
        'the first word of it && the last word of it',
        '"a" & "b" & "c"',
        "1 + 2 * 3",
        "(1 + 2) * 3",
        "10 / 4",
        "2 ^ 8",
        "-5 + 3",
        "the number of lines in it is greater than 2",
        "the number of lines in it is at least 4",
        'the first word of it is "10.0.0.1"',
        'the first word of it is not "nope"',
        'it contains "POST"',
        'it starts with "10."',
        'it ends with "zzz"',
        '"" is empty',
        'the first word of it is empty',
        'not (1 is 2)',
        '1 is 1 and 2 is 2',
        '1 is 2 or 2 is 2',
        '"report.tmp" is like "*.tmp"',
        '"report.txt" is like "*.tmp"',
        '"log-2026-08.txt" is like "log-????-??.txt"',
        '"REPORT.TMP" is like "*.tmp"',
        '"abc" is not like "*.tmp"',
        r'it matches "\d+\.\d+"',
        r'it matches "zzz"',
        r'every match of "\d+" in the first line of it',
        r'every match of "\d{3}" in it',
        r'every match of "zzz" in it',
        r'the number of items in every match of "\d+" in the first line of it',
        '"10" is greater than "9"',
        '"abc" is greater than "abd"',
        "true and false",
        "not false",
        "empty is empty",
    ]

    # Lists, transformations and aggregates.
    for n in NOUNS:
        out.append(f"the {PLURALS[n]} of it")
        out.append(f"the number of items in the {PLURALS[n]} of it")
        out.append(f"the sorted the {PLURALS[n]} of it")
        out.append(f"the reversed the {PLURALS[n]} of it")
        out.append(f"the unique the {PLURALS[n]} of it")
        out.append(f'the {PLURALS[n]} of it joined by "|"')

    for op in ("uppercase", "lowercase", "trimmed"):
        out.append(f"the {op} it")
        out.append(f"the {op} of it")
        out.append(f"the {op} the first word of it")
        out.append(f'the {op} "  Mixed Case  "')

    out += [
        "the empty list",
        "the number of items in the empty list",
        "the sorted the words of it joined by \",\"",
        'the sorted (the words of "10 9 100 2") joined by ","',
        'the sorted (the words of "pear Apple fig") joined by ","',
        'the unique (the words of "b a b c a") joined by ","',
        'the reversed (the words of "a b c") joined by ","',
        'the sum of the words of "1 2 3 4"',
        'the largest of the words of "5 3 9 1"',
        'the smallest of the words of "5 3 9 1"',
        'the average of the words of "2 4 6"',
        'the sum of the empty list',
        'the sum of the words of "a b"',
        "the rounded 2.4", "the rounded 2.6", "the rounded -2.6",
        "the rounded 7", "the absolute -5", "the absolute 5",
        "the absolute -2.5", "the rounded the absolute -3.7",
        '"a|b|c" split by "|"',
        '"a:b:c" split by ":"',
        '"a||c" split by "|"',
        '"a b c" split by " "',
        '"abc" split by ""',
        'the number of items in ("a|b|c" split by "|")',
        'item 2 of ("a :: b :: c" split by " :: ")',
        '("a:b:c" split by ":") joined by " | "',
        'the words of it joined by ""',
        'it split by "\\n"',
        'the uppercase (the first word of it)',
        'the trimmed the last line of it',
        'the length of the words of it',
        'the first item of the words of it',
        'the sorted it',
        'the reversed it',
        'the unique it',
        'the uppercase it & "!"',
        'the number of words in the uppercase it',
    ]

    # Records and JSON. The subject is only a document for the "json" case;
    # everywhere else these produce errors, and the two implementations have
    # to agree on the error just as much as on the answer.
    out += [
        # `the matches` and `the whole match` carry state from a preceding
        # comparison. Standing alone they are empty on both sides, which is
        # still a fact the two implementations have to agree on.
        "the matches",
        "the whole match",
        "the number of matches",
        'the matches joined by ","',
        "the padded \"ab\" to 6",
        "the padded \"ab\" to 2",
        "the padded 42 to 6 on the left",
        "the padded \"x\" to 0",
        'the padded (the first word of it) to 14 on the right',
        "the duration of 90",
        "the duration of 3725",
        "the duration of 0.25",
        "the duration of 45",
        "the duration of 0",
        "the duration of 86500",
        'the sorted (the words of "c a b") by each',
        'the sorted (the lines of it) by the first word of each',
        'the sorted (the words of "10 9 100") by each',
        'the sorted (the words of "b a") by the uppercase each',
        "the sorted (the empty list) by each",
        'the sorted (the lines of it) by the number of words in each',
        "the empty record",
        "the json text of the empty record",
        "the json of it",
        "the json text of the json of it",
        'the "status" of the json of it',
        'the "ratio" of the json of it',
        'the "ok" of the json of it',
        'the "nothing" of the json of it',
        'the "id" of the "user" of the json of it',
        'the "name" of the "user" of the json of it',
        'the "user" of the json of it',
        'the "tags" of the json of it',
        'the "empty list" of the json of it',
        'the "empty record" of the json of it',
        'the "absent" of the json of it',
        'the "absent" of the json of it is empty',
        'the "name" of the "nobody" of the json of it',
        'the "name" of the "nobody" of the json of it is empty',
        'item 2 of the "tags" of the json of it',
        'the number of items in the "tags" of the json of it',
        'the "tags" of the json of it joined by ","',
        "the keys of the json of it",
        "the values of the json of it",
        'the keys of the json of it joined by ","',
        "the number of items in the keys of the json of it",
        'the "id" of the "user" of the json of it + 1',
        'the "ratio" of the json of it * 2',
        'the uppercase (the "status" of the json of it)',
        'the "status" of the json of it is "green"',
        # Both sides must refuse these, and refuse them the same way.
        'the "status" of "not a record"',
        'the "status" of the empty list',
        "the json of \"not json at all\"",
        "the keys of \"plain text\"",
        "the values of 5",
    ]
    return out


# ---------------------------------------------------- coverage of the corpus
#
# The corpus is hand-written, so until now a new expression form was compared
# only if somebody remembered to add it — and records shipped in 0.6.0 with no
# browser support at all, silently, because nothing here asked. The check below
# turns that omission into a build failure: every expression node the parser
# can produce must appear in the corpus, or be excused here with a reason.

# Nodes that need a host — a process, a filesystem, an environment, a clock.
# The browser evaluator is a slice of the language on purpose: it evaluates
# expressions against a subject, and none of these mean anything without a
# machine underneath.
NEEDS_A_HOST = {
    "ArgList": "a script's arguments",
    "ClockRef": "the clock",
    "CurrentFolder": "the working directory",
    "EnvRef": "the environment",
    "ErrorRef": "the last command's standard error",
    "FileRef": "the filesystem",
    "FileExists": "the filesystem",
    "FolderExists": "the filesystem",
    "GlobalRef": "a running program's globals",
    "HandlerCall": "handlers, which are statements rather than expressions",
    "ResultRef": "the last command's exit status",
    "RunIdRef": "the identity of an execution, which a page is not having",
    "SecretRef": "the keystore",
    "SecretEnvRef": "the keystore",
    "SecretFileRef": "the keystore",
    "StdInRef": "the standard input",
    "Var": "a variable, which the scratchpad has no scope for",
    "Call": "a handler call; the scratchpad evaluates one expression with no "
            "definitions around it",
    "FuncCall": "a handler call, as above",
}


def expression_nodes():
    """Every AST class that can appear inside an expression."""
    import dataclasses
    from frostlang import ast as A

    # Statements and assignment targets are structural: they can never be the
    # result of parse_expression, so they are not the corpus's business.
    statements = {"Put", "Run", "Pipe", "If", "Repeat", "RepeatFor",
                  "RepeatWith", "RepeatWhile", "RepeatForEach",
                  "RepeatForever", "RepeatTimes", "Quit", "Handler",
                  "HandlerDef", "Return", "Arith", "AddTo", "SubtractFrom",
                  "MultiplyBy", "DivideBy", "ExitRepeat", "NextRepeat",
                  "DeleteFile", "Ensure", "Use", "Replace", "Wait", "Program",
                  "Import", "Ceiling"}
    targets = {n for n in dir(A) if n.endswith("Target")}
    out = set()
    for name in dir(A):
        node = getattr(A, name)
        if (isinstance(node, type) and dataclasses.is_dataclass(node)
                and not name.startswith("_")
                and name not in statements and name not in targets):
            out.add(name)
    return out


def nodes_used_by(exprs):
    """Which AST classes the corpus actually exercises."""
    import dataclasses
    seen = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not dataclasses.is_dataclass(node):
            return
        seen.add(type(node).__name__)
        for f in dataclasses.fields(node):
            walk(getattr(node, f.name))

    for expr in exprs:
        try:
            p = Parser(expr)
            walk(p.parse_expression())
        except (ParseError, LexError):
            continue          # a deliberately-invalid case covers no node
    return seen


def check_coverage(exprs):
    missing = expression_nodes() - nodes_used_by(exprs) - set(NEEDS_A_HOST)
    if missing:
        print("\n%d EXPRESSION FORM(S) NEVER COMPARED:\n" % len(missing))
        for name in sorted(missing):
            print("  %s" % name)
        print("\nAdd an expression using each to corpus(), or list it in "
              "NEEDS_A_HOST with the reason it cannot be evaluated in a "
              "browser. An untested form is one where the two implementations "
              "are free to disagree.")
        return False
    return True


def python_eval(expr, subject):
    interp = Interpreter()
    interp.it = subject
    try:
        p = Parser(expr)
        tree = p.parse_expression()
        if not p.end_of_statement():
            return ("error", "trailing tokens")
        value = interp.eval(tree)
    except (ParseError, LexError) as e:
        return ("error", "syntax")
    except FrostError:
        return ("error", "runtime")
    if isinstance(value, list):
        return ("list", [to_text(v) for v in value])
    return ("value", to_text(value))


def js_eval_all(cases):
    """One node process for the whole corpus, to keep the build quick.

    The corpus goes in on stdin rather than argv. As an argument it is over a
    megabyte of JSON, which is under macOS's limit and over Linux's, so this
    passed locally and failed in CI with `Argument list too long`.
    """
    program = """
const frost = require(%s);
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", function (chunk) { raw += chunk; });
process.stdin.on("end", function () {
  const out = JSON.parse(raw).map(function (c) {
    try {
      const v = frost.evaluate(c.expr, c.subject);
      if (Array.isArray(v)) return ["list", v.map(frost.text)];
      return ["value", frost.text(v)];
    } catch (e) {
      if (e && e.msg !== undefined) return ["error", "syntax_or_runtime"];
      return ["error", "threw:" + (e && e.message)];
    }
  });
  process.stdout.write(JSON.stringify(out));
});
""" % json.dumps(JS)
    proc = subprocess.run(["node", "-e", program], input=json.dumps(cases),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("node failed:\n" + proc.stderr)
    return json.loads(proc.stdout)


def main():
    if not check_coverage(corpus()):
        raise SystemExit(1)

    cases = []
    for name, subject in SUBJECTS.items():
        for expr in corpus():
            if "any " in expr:          # random, cannot be compared
                continue
            cases.append({"expr": expr, "subject": subject, "subject_name": name})

    js = js_eval_all([{"expr": c["expr"], "subject": c["subject"]}
                      for c in cases])

    mismatches = []
    for case, js_result in zip(cases, js):
        py = python_eval(case["expr"], case["subject"])
        js_kind, js_value = js_result[0], js_result[1]

        if py[0] == "error" and js_kind == "error":
            continue                     # both refuse; that is agreement
        if py[0] != js_kind or py[1] != js_value:
            mismatches.append((case["subject_name"], case["expr"], py,
                               (js_kind, js_value)))

    print(f"compared {len(cases)} expressions across {len(SUBJECTS)} subjects")
    if mismatches:
        print(f"\n{len(mismatches)} DISAGREEMENT(S):\n")
        for name, expr, py, js_r in mismatches[:25]:
            print(f"  [{name}] {expr}")
            print(f"      python: {py}")
            print(f"      js:     {js_r}")
        return 1
    print("the two implementations agree on every case")
    return 0


if __name__ == "__main__":
    sys.exit(main())
