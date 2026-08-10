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

SUBJECTS = {
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
    return out


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
    """One node process for the whole corpus, to keep the build quick."""
    program = """
const frost = require(%s);
const cases = JSON.parse(process.argv[1]);
const out = cases.map(function (c) {
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
""" % json.dumps(JS)
    proc = subprocess.run(
        ["node", "-e", program, json.dumps(cases)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("node failed:\n" + proc.stderr)
    return json.loads(proc.stdout)


def main():
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
