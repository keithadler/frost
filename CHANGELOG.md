# Changelog

Notable changes to frost. Dates are the release date; unreleased work sits at
the top.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and
frost follows [semantic versioning](https://semver.org/) — before 1.0, a minor
bump may change the language.

## Unreleased

### Added

**Cleanup blocks.** `ensure ... end ensure` registers a block when execution
reaches it and runs it when the script ends — normally, on error, on `quit`,
or on interrupt — most recent first. Abort-on-failure was the headline default
with no way to release a lock on the way out; this closes that.

**Explicit globals.** `put 99 into the global total` writes through from
inside a handler, and `the global total` reads past a local of the same name.
Works with `before`/`after`, the arithmetic statements, and `replace`.
`global` is now reserved, so `put 5 into global total` is an error rather than
a local of that name.

**Input to a program.** `run "sort" reading names`, and `pipe reading X` feeds
the first stage. A pipe's input goes through a temporary file rather than a
pipe written to while waiting on the last stage, which is the classic
pipeline deadlock.

**Child environment and folder.** `run "make" in folder build path`, and
`put "clang" into the environment variable "CC"`. Both reuse `put ... into
<target>` rather than adding a `set` keyword. The script gets its own copy of
the environment.

**Streamed output.** `run "make" showing output` hands the terminal to the
child, for long builds and interactive programs. `it` is empty afterwards
rather than stale.

**Real lists.** A plural chunk noun with no index is the whole set — `the
words of X`, `the items of X`. `split by` and `joined by` cover delimiters the
chunk nouns cannot express. `put "c" after names` appends an element when the
target is a list. `the sorted X`, `the reversed X`, `the unique X`, with
`sorted` comparing numerically when every element is a number.

**String and number functions.** `the uppercase X`, `the lowercase X`, `the
trimmed X`, `the rounded X`, `the absolute X`, and `the sum / largest /
smallest / average of X`. Recognised only after `the`, so they cost nothing
from the name vocabulary.

**Handlers in expressions.** `the double of 5`. Arguments bind tightly, as
chunk sources do. An unknown name is reported when the script is parsed, so
`--check` catches a typo in a branch that rarely runs.

**The script's own standard input**, as `the standard input`.

**Counting and bounding policy rules.** `require at most 12 commands`,
`require at least 1 cleanup`, `forbid more than 2 runs of "curl"`, `require
between 1 and 5 files written`, `forbid any files deleted`, and
`require timeout on "curl" of at most 30 seconds`. Fourteen countable nouns,
plus `runs of "glob"`. Timeout bounds reconcile units, so a policy in seconds
catches a script in minutes.

**Policy rules for the new capabilities**: `forbid setting "PATH"` and
`forbid changing folder`, so they are not a way around existing rules.

**`--version`.**

**Continuous integration** across Python 3.10–3.13 on Linux and macOS, with a
job that fails if a generated file was committed out of date.

**Editor support**: a TextMate grammar under `editors/`.

### Fixed

- The lexer scanned `[0-9.]+` greedily and then called `float()`, so `1.2.3`
  escaped as a bare Python `ValueError` rather than a syntax error.
- The formatter rewrote `5.0` as `5`, swapping a float literal for an int one
  and breaking the identical-parse-tree guarantee it documents.
- `frost script.frost --check` gave `--check` to frost rather than to the
  script, so a script could never receive its own flags.
- `try to pipe` stages were never indented by the formatter.
- `run "curl" within limit seconds` did not parse: a time unit is not a
  reserved word, so the identifier swallowed it and every timeout had to be a
  literal.
- `the rounded -2.6` did not parse.
- The "result examined" check did not follow a handler call, so factoring the
  check into a helper was reported as an ignored failure.
- `--explain` printed a ragged left column where the README shows an aligned
  one.
- The "refusing to format" message was unreachable.
- A policy shortfall was reported against line 0 rather than the file.
- The scratchpad rendered every example one word per line: `.ex span` matched
  the syntax-highlight spans inside `<code>`, not only the description.
- `Repl(out=sys.stdout)` bound stdout at import, so it could never be
  redirected.
- Two frost snippets in LANGUAGE.md were fenced as `policy`, and three
  metasyntax tables in MODEL-SPEC.md were fenced as frost — that file goes
  into system prompts, so a model was shown `put EXPR into NAME` as syntax.
- The reserved-word list in LANGUAGE.md had drifted from the parser. It is now
  generated, and a test asserts it matches exactly.
- `tour.frost` still announced itself as frost 0.2.0.

### Changed

- The test suite is seventeen modules and a shared `helpers.py` rather than
  one 1,412-line file, and grew from 189 tests to 917: a grammar-aware
  generator drives property tests over the formatter, auditor and interpreter;
  the CLI is tested both in process and as a subprocess; every frost block in
  the documentation is parsed; and every example has a recorded `--explain`
  manifest.
- `web/chunks.js` covers the new expression forms, and the corpus that checks
  it against the interpreter grew from 1,288 expressions to 1,820.

## 0.3.0

First public version. The language runs, the examples are real, and the
auditor, policy engine, formatter and scratchpad all work.
