# Changelog

Notable changes to frost. Dates are the release date; unreleased work sits at
the top.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and
frost follows [semantic versioning](https://semver.org/) — before 1.0, a minor
bump may change the language.

## Unreleased

### Added

**Structured diagnostics.** `--json` now works with `--check`, `--policy`
and on a runtime failure, not only with `--explain`. One schema covers every
way a script can be refused: severity, a stable `code`, line and column, the
offending source, the hint, and any repairs.

**Repair payloads.** A diagnostic can carry the edit that fixes it. Most come
from information the front end already had — several of the parser's hints
literally contained the corrected line — so handing it over as data costs
nothing and saves an agent a round trip. Confidence is three-valued and
honest: `high` is a mechanical rewrite, `likely` infers a detail, `guess` is a
name that looks close to one that exists.

**`frost --repair [--write]`** applies the high-confidence repairs and repeats
until nothing certain is left. One pass is not enough — a recursive-descent
parser stops at the first error, so fixing it reveals the next, and a single
round would give up on any script with two mistakes. A pass is kept only if
it made progress: the script parses, or the first error moved strictly later.
That is what makes it safe to run unattended.

**Policy rules carry hints.** A rule's trailing comment is its explanation,
and frost prints it when the rule fires:

```
REFUSED: running "sudo"
  deploy.frost:1  run "sudo" with "systemctl", "restart", "api"
  why: the deploy role already has the permissions it needs
```

No new syntax — policy authors already write that comment, so every policy
that already exists gains the explanation for free. A comment on its own line
stays a section header. `examples/production.policy` now explains all eleven
of its rules.

### Changed

- `check()` returns a `PolicyFinding` with a `hint`, rather than a bare
  triple. It still unpacks in the same order.

## 0.4.0

### Added

**Secrets that cannot be logged by accident.** `the secret "db password"`
reads from a role-gated keystore; `the secret environment variable "N"` and
`the secret file "path"` seal a value on read and need no keystore at all.

A sealed value refuses to become text. Every printing path in the language
goes through one conversion, so `put`, joining, `--trace`, error messages and
the scratchpad redact without knowing secrets exist. Only the secret spans
redact — `put "connecting as" && user && "with" && token` keeps its context —
because a mechanism that destroys logs is one people route around. The seal is
contagious through concatenation, chunks, `split by` and the transformations,
so a connection string built from a password is still a password.

Streams redact; boundaries release. A program's arguments, its standard
input, its environment and a file write get the real value, and `--explain`
names every place that happens. Comparisons and `the length of` see through
the seal, because returning the marker's length would be silently wrong;
equality is constant time.

**A keystore**, with per-role X25519 keypairs, scrypt-protected private keys
and AES-256-GCM envelope encryption. Storing a secret and granting a role need
no passphrase — only reading does — so somebody can add a credential for a
role whose passphrase they do not have. Secret names and role grants are
stored in plaintext because that is what a reviewer needs; only values are
encrypted. `frost keystore init|add-role|set|get|list|grant|revoke|remove`,
`--keystore` and `--role`. Needs `pip install "frostlang[keystore]"`; nothing
else in frost gained a dependency.

**Secrets in the manifest and the policy.** `--explain` lists which secrets a
script asks for and where a plaintext leaves the process, with taint followed
through variable assignment so `put the secret ... into pw` then `run "psql"
with pw` is reported. Writing a secret to a file or handing one to a network
program is a danger; passing one as a command-line argument is a caution,
since arguments are visible to every process on the machine. Policy gains
`forbid reading secret "glob"` and the countable nouns `secrets read` and
`secret releases`. A script naming a secret its role cannot open is refused
before anything runs, exit 3.

**A benchmark for the claim the design rests on.** `tools/benchmark.py`
measures the front end against the cost of spawning a process, on every
example.

### Changed

- Version bumped to 0.4.0: this adds substantial language surface.
- **The README's performance claim is corrected.** It said you can parse
  verbose syntax faster than you can spawn a process. That is true on macOS
  and false on Linux: `fork`/`exec` of `true` costs about 0.7ms on Linux
  against 2.4ms on macOS, while parsing varies far less. CI on Linux
  disproved it twice — once for `true` and once for `git --version`. The
  README now states what actually holds, which is the thing the design
  relies on anyway: parsing is paid once and spawning is paid per command,
  so a script running ten commands pays ten spawns against one parse on any
  platform.

### Added — language

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
