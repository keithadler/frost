# Changelog

Notable changes to frost. Dates are the release date; unreleased work sits at
the top.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and
frost follows [semantic versioning](https://semver.org/) — before 1.0, a minor
bump may change the language.

## Unreleased

### Added

**Runtime capability sandboxing.** `--explain` and `--policy` reason about
the text of a script and are honest about their limits: a path built at
runtime is reported as unknowable rather than guessed. That honesty is also
the gap, because once the script runs an unknowable path is a real path.

A boundary is declared in the policy file, allow-shaped — a deny-list cannot
become a sandbox, since `forbid writing to "/etc/*"` says nothing about what
writing is permitted:

```
sandbox may run "git", "make"
sandbox may read "*"
sandbox may write "build/*"
sandbox may reach the network
```

`frost --policy prod.policy --sandbox deploy.frost` then holds it. Child
processes are confined by the operating system — sandbox-exec on macOS,
bubblewrap on Linux — so a path the analyser could not resolve is confined
anyway, because the confinement never needed it resolved. frost's own file
operations are checked by the interpreter, which is a weaker guarantee, and
the two are named apart rather than blurred.

Three refusals, each chosen because the alternative would be a boundary
somebody relies on and that does not hold:

- **Per-host network rules are refused when the policy is read.** macOS
  filters on addresses rather than names and a Linux namespace is
  all-or-nothing, so `sandbox may reach "api.github.com"` cannot be enforced.
  `sandbox may reach the network` means exactly what it says.
- **No backend means no run.** Not a warning and a run anyway.
- **Present is not working.** Before each run frost executes a real confined
  command that tries to write outside its boundary, and refuses if that write
  succeeds.

The tests do the forbidden thing and then look at the filesystem, rather than
asserting the wrapper was built. Disabling confinement entirely fails nine of
them, including the canary.


**`--record` and `--replay`.** A run's capabilities were knowable before it
started; what it actually did was not knowable at all. `--record` writes down
every command with its arguments, input, output and status, every file read,
every environment variable read, and whatever was piped in. `--replay` serves
those answers back and **performs nothing** — no process spawned, no file
written, nothing deleted.

That makes a recording a fixture: change the script, replay it, and a refactor
meant to preserve behaviour either did or did not. A difference is reported
rather than raised, with both sides named, and matching is on the identity of
the effect rather than line numbers, so reformatting replays clean. Exit
status 4, distinct from a policy refusal.

Secret values are never recorded, only names, and revealed plaintext is
scrubbed from everything written down, so a recording is safe to commit.

**Streaming standard input.** `repeat for each line in the standard input`
consumes lines as they arrive instead of reading the whole stream first, so a
filter works against a producer that never ends. `exit repeat` gets out and
leaves the rest in the pipe.

**Constant propagation in the static analyzer.** `put "ls" into tool` then
`run tool` now resolves to `ls` rather than being reported as built at
runtime. The analysis only claims a value it is certain of: a name is a
constant when every definition of it is the same literal and it is never
mutated. Two different literals, an append, an arithmetic statement, a loop
variable, a handler parameter or a value from a command all keep it unknown,
which is the safe answer and the one the manifest already knows how to say.

This immediately found something: `examples/migrate.frost` deleted a lock
file in `/tmp`, which `examples/production.policy` forbids with `forbid
deleting "/*"`. The violation had been invisible because the path was
unknowable. The example now keeps its lock beside the migration, which is
better practice anyway — a lock in a world-readable directory is one any
other user can remove.

### Fixed

- A recording scrubbed secrets from command *output* but not from command
  *arguments*, so `run "psql" with password` wrote the credential into the
  file. Every string in an event is scrubbed now, so a field added later is
  covered without anyone remembering to.
- Only keystore secrets were registered for scrubbing; `the secret
  environment variable` and `the secret file` were not.


**Modules, designed around one constraint.** Everything frost is worth using
for rests on the invariant that the tree you audit is the program you run and
the audit sees all of it. Modules are the feature most likely to break that,
so the goal was not "safe modules" but *modules that cannot put capability
outside the manifest*.

```
use "lib/db.frost" for the connect, the migrate which may run "psql"
```

- **A module is declarations only** — handler definitions and imports, and a
  top-level statement in one is refused. Import-time side effects are the
  most abused feature of every module system ever shipped, and refusing them
  means `use` can never do anything.
- **The path is a literal**, enforced by the parser. A computed import would
  put the graph out of reach of the static analysis everything else rests on.
- **Resolution is relative and bounded** to the entry script's own directory.
  No search path, no environment variable, no absolute paths, no registry.
- **Imports are explicit and the graph is a DAG.** A collision is an error
  rather than one module silently replacing another's handler; cycles are
  refused; the closure is read exactly once, so audit and run come from the
  same bytes.
- **The manifest covers the closure**, attributing each capability to the
  file it came from and the import it arrived through, and a module's
  handlers are audited whether or not anything calls them.
- **A ceiling at the import site.** A module defaults to no capabilities at
  all, and one that exceeds what its import declared is refused before
  anything runs. A reviewer who reads only the entry file therefore has a
  sound upper bound on the whole program, and a shared module that later
  grows a network call breaks the build at the import site instead of quietly
  widening someone's manifest.
- **`--lock` and `--frozen`** pin every file by sha256, closing the window
  modules open between the audit and the run.
- **`--explain` fails closed** on an unresolvable module: exit 2 and no
  manifest, because a manifest with a hole in it is the one output that would
  actively mislead a reviewer.

Deliberately absent: conditional imports, re-export, module-level state,
version solving, namespacing beyond the file path. Each exists to make a
large dependency tree tractable, and a large dependency tree is what this
review model cannot survive.

### Fixed

- **`collect_handlers` was a flat, last-write-wins table.** With two modules
  loaded, one silently shadowed a handler in the other — a hijack rather than
  a hygiene problem. Handler names now resolve in the file that defines the
  code doing the calling, and a collision is an error at load time.
- **Taint was name-based over the whole tree.** A `token` in one file and an
  unrelated `token` in another were the same node, which gave false positives
  directly and false negatives through the shadowing bug above. Taint is now
  per file, crossing a boundary only where data does — through the arguments
  of a handler call.
- `--format` raised an uncaught traceback on any file that imports, because
  it re-parsed with name resolution. Layout is lexical and never needed it,
  and formatting now happens before modules are loaded so a broken import can
  still be tidied.


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
