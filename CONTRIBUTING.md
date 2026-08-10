# Contributing to frost

## Getting set up

Python 3.10 or newer. The interpreter has no dependencies; the test suite and
the page builders do.

```bash
git clone https://github.com/keithadler/frost && cd frost
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -q
```

## The things that are easy to break

Four properties hold across the whole project, and each is enforced by a test
rather than by remembering. If you are changing the language, these are the
ones to know about.

**The front end never crashes.** Whatever is fed to it, the answer is a
`LexError` or a `ParseError` with a line number — never a Python traceback.
`tests/test_fuzz.py` asserts this over random text, over mutations of valid
programs, and over every truncation of a handful of scripts.

**The formatter cannot change meaning.** `format_source` must produce an
identical parse tree and must be idempotent. This is checked against generated
programs, not just fixtures, so a new syntax that the formatter mangles will
fail even if nobody thought to write a case for it.

**The auditor is total.** `--explain` runs on scripts nobody has vetted yet,
so `audit`, `describe`, `summarise` and `verdict` must not throw on anything
that parsed.

**Parsing stays cheaper than spawning.** The whole argument for verbosity is
that a shell's runtime goes on `fork`/`exec`, not on reading source. Run
`python tools/benchmark.py` to see the numbers; a test fails if the front end
ever costs more than one process spawn.

**The browser evaluator agrees with the interpreter.** `web/chunks.js` is a
second implementation of the expression language. `tools/verify_chunks.py`
runs 1,820 expressions through both and the play page is not written if they
disagree. Adding an expression form means adding it in both places.

## Adding syntax

Roughly in order:

1. `frostlang/ast.py` — a node.
2. `frostlang/parser.py` — the syntax. Adding to `HARD_WORDS` takes a word out
   of the name vocabulary forever, so prefer a form gated by `the`, which
   costs nothing.
3. `frostlang/interp.py` — an `exec_` or `eval_` method.
4. `frostlang/audit.py` — if it is a capability, the manifest must see it, or
   `--explain` will understate what a script can do. Consider whether a policy
   should be able to forbid it.
5. `frostlang/formatter.py` — usually nothing, since it works on tokens, but
   check that a block form indents.
6. `tests/gen.py` — emit it, so the property tests cover it.
7. `tests/` — direct tests, including the error cases.
8. `LANGUAGE.md`, and `tools/build_model_spec.py` for the model-facing
   reference. Run the builders.
9. `web/chunks.js` and `tools/verify_chunks.py`, if it is an expression.
10. `tools/build_editors.py` needs nothing if you only added a reserved word —
    it reads `HARD_WORDS` — but rerun it, because the generated grammar under
    `editors/` is committed and CI checks it is current.

## Before opening a pull request

```bash
python -m pytest tests/ -q
python tools/verify_chunks.py
python tools/build_model_spec.py
python tools/build_docs.py
python tools/build_play.py
python tools/build_audit.py
python tools/build_editors.py
git diff --exit-code          # generated files must be committed up to date
```

CI runs all of that on Python 3.10 through 3.13, on Linux and macOS.

## Style

Match the surrounding code. Two things are deliberate and worth keeping:

**Error messages are sentences, and they carry a hint.** `there is no variable
named 'total cost'` with `hint: assign it first with: put ... into total cost`
is the standard. The whole argument for the language is that a human is
reading this at 3am.

**Comments explain why, not what.** Most of the code is plain enough to read.
The comments that earn their place are the ones recording a decision — why
`item` trims, why the pipe input goes through a temporary file, why a time
unit is not a reserved word.

Test names are sentences too. `test_a_shortfall_is_reported_against_the_file`
reads better in a failure than `test_policy_3`.

## Reporting a bug

A frost script that reproduces it is worth more than a description. If it is
a crash, the output of `frost --check` on the script is the first thing to
include.
