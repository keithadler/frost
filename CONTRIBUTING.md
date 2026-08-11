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
`LexError` or a `ParseError` with a line number, never a Python traceback.
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

1. `frostlang/ast.py`: a node.
2. `frostlang/parser.py`: the syntax. Adding to `HARD_WORDS` takes a word out
   of the name vocabulary forever, so prefer a form gated by `the`, which
   costs nothing.
3. `frostlang/interp.py`: an `exec_` or `eval_` method.
4. `frostlang/audit.py`: if it is a capability, the manifest must see it, or
   `--explain` will understate what a script can do. Consider whether a policy
   should be able to forbid it.
5. `frostlang/formatter.py`: usually nothing, since it works on tokens, but
   check that a block form indents.
6. `tests/gen.py`: emit it, so the property tests cover it.
7. `tests/`: direct tests, including the error cases.
8. `LANGUAGE.md`, and `tools/build_model_spec.py` for the model-facing
   reference. Run the builders.
9. `web/chunks.js` and `tools/verify_chunks.py`, if it is an expression.
10. `tools/build_editors.py` needs nothing if you only added a reserved word,
    it reads `HARD_WORDS`: but rerun it, because the generated grammar under
    `editors/` is committed and CI checks it is current.
11. `frostlang/context.py` and `tools/build_context.py`, if you changed a
    statement form. That document is what a model reads before writing frost,
    and `MODEL-CONTEXT.md` is committed, so a stale copy teaches yesterday's
    grammar to everything that fetches it. Every snippet in it is parsed by
    the test suite, so a form that does not work cannot be documented there.

## Before opening a pull request

```bash
python -m pytest tests/ -q
python tools/verify_chunks.py
python tools/build_model_spec.py
python tools/build_docs.py
python tools/build_play.py
python tools/build_audit.py
python tools/build_editors.py
python tools/build_context.py
python tools/build_man.py
python tools/build_site.py       # only to preview what Pages will publish
git diff --exit-code          # generated files must be committed up to date
```

CI runs all of that on Python 3.10 through 3.13, on Linux and macOS.

## Cutting a release

Publishing runs on a version tag, through PyPI trusted publishing, so there is
no API token in this repository to leak or rotate. That needs a one-time setup
on PyPI before the first release: add a trusted publisher for the `frostlang`
project pointing at this repository, the workflow file `release.yml`, and the
environment name `pypi`. Until that exists the publish step fails loudly,
which is the right failure: a release that silently uploaded nothing would be
found by whoever tried to install it.

```bash
# 1. version, changelog, generated files
#    (pyproject.toml, frostlang/__init__.py, editors/package.json,
#     examples/tour.frost all carry the version; the tests check they agree)
python -m pytest tests/ -q
git commit -am "Release 0.9.0"

# 2. the tag has to match the declared version or the workflow refuses
git tag v0.9.0
git push origin master --tags
```

After the release publishes, the Homebrew formula points at a specific sdist
by URL and hash, so it has to be moved by hand. Both come from
`https://pypi.org/pypi/frostlang/<version>/json`, and the formula lives in
[keithadler/homebrew-frost](https://github.com/keithadler/homebrew-frost).
Check it with `brew audit --strict --online` and `brew test` before pushing,
because a tap that installs a broken frost fails on somebody else's machine at
the moment they were deciding whether to trust this.

The workflow builds the sdist and the wheel, checks the metadata, installs the
built wheel into a clean environment and runs a script through it. Installing
the checkout instead would prove the checkout works, which was never in doubt.

## Style

Match the surrounding code. Two things are deliberate and worth keeping:

**Error messages are sentences, and they carry a hint.** `there is no variable
named 'total cost'` with `hint: assign it first with: put ... into total cost`
is the standard. The whole argument for the language is that a human is
reading this at 3am.

**Comments explain why, not what.** Most of the code is plain enough to read.
The comments that earn their place are the ones recording a decision, why
`item` trims, why the pipe input goes through a temporary file, why a time
unit is not a reserved word.

Test names are sentences too. `test_a_shortfall_is_reported_against_the_file`
reads better in a failure than `test_policy_3`.

## Reporting a bug

A frost script that reproduces it is worth more than a description. If it is
a crash, the output of `frost --check` on the script is the first thing to
include.
