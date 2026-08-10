# frost

[![CI](https://github.com/keithadler/frost/actions/workflows/ci.yml/badge.svg)](https://github.com/keithadler/frost/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A shell scripting language for the era when machines write the scripts and
humans only get to review them — readable by default, structurally immune to
injection, and auditable before a single process starts.**

A grammar descended from HyperTalk, an interpreter rather than a login shell.

```
#!/usr/bin/env frost

put the number of lines in file "access.log" into request count
put "processing" && request count && "requests"

pipe
    run "grep" with "ERROR", "access.log"
    run "awk" with "{print $1}"
    run "sort"
    run "uniq" with "-c"
end pipe

repeat for each line in it as tally
    put the second word of tally && "failed" && the first word of tally && "times"
end repeat
```

## Why

Shell syntax was optimised for a cost that no longer dominates. `cut -d: -f1`
is terse because a human typed it a thousand times on a serial terminal. When a
model writes the script and a human reads it once — at 3am, while production is
down — the scarce resource is comprehension at review time, not keystrokes.

frost inverts the optimisation. It is not a login shell; it is an interpreter
you point at a file. Because nobody types it at a prompt, nothing in the design
has to be short.

The speed objection does not apply. A shell's runtime is dominated by
`fork`/`exec`, not parsing — HyperTalk's whole grammar fit in HyperCard on a
68000. You can parse verbose syntax faster than you can spawn a process.

That is measured, not assumed. `python tools/benchmark.py` parses and audits
every example against the cost of one `fork`/`exec`:

```text
release.frost           78 lines    parse 659us   audit 292us
one fork+exec of true                            2002us
```

Reading the whole script and deriving its capability manifest costs about
half what it costs to start a single program. A test fails if that ever
stops being true, because the argument for verbosity rests on it.

## Three things it fixes

**Injection is unrepresentable, not mitigated.** There is no interpolation and
no `eval`. Arguments are a list handed to `execve`, never re-parsed.

```text
$ cat hostile.frost
put "notes.txt; rm -rf *" into evil name
run "touch" with evil name

$ frost hostile.frost && ls
'notes.txt; rm -rf *'   keep_me.txt   precious.db

$ bash -c "touch $EVIL" && ls
                        # empty. everything is gone.
```

**Failure stops the script.** No `set -e` to forget. `run` aborts on non-zero
exit; `try to run` opts out and is greppable in review.

**Pipes fail if any stage fails.** `cat missing.log | wc -l` reports success in
bash. In frost the first failing stage wins, and there is no way to turn that
off.

## Side by side

```bash
#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

LOG="${1:?usage: report LOGFILE}"
[[ -f "$LOG" ]] || { echo "no log at $LOG" >&2; exit 1; }

n=$(wc -l < "$LOG")
errs=$(awk '$4 ~ /^5/' "$LOG" | wc -l)

echo "requests: $n"
echo "errors:   $errs"
(( errs > 2 )) && { echo "ALERT" >&2; exit 1; }
```

```
#!/usr/bin/env frost

put item 1 of the arguments into log path
if log path is empty then
    put "usage: report <logfile>" into standard error
    quit with status 2
end if

if not file (log path) exists then
    put "no log at" && log path into standard error
    quit with status 1
end if

put the number of lines in file (log path) into request count

put 0 into error count
repeat for each line in file (log path) as this request
    if the fourth word of this request starts with "5" then
        add 1 to error count
    end if
end repeat

put "requests:" && request count
put "errors:  " && error count

if error count is greater than 2 then
    put "ALERT" into standard error
    quit with status 1
end if
```

Longer, and that is the trade. Nothing in the second version needs explaining
to someone who has never seen frost — no `set -euo pipefail`, no `IFS`
incantation, no `${1:?}`, no `$4 ~ /^5/`, no `>&2`. The safety that bash gets
from three lines of ceremony, frost gets from having no other mode.

## Patterns and timeouts

Globs for filenames, regex when you actually need it, and neither pretending to
be the other:

```
if filename is like "*.tmp" then delete file filename

if request matches "^(\S+) (\w+) (\d+)$" then
    put match 1 into client address
    put the last match into status code
end if

put every match of "\d+" in request into numbers
replace "(\d+)-(\d+)-(\d+)" with "\3/\2/\1" in date text
```

Capture groups use the same chunk grammar as everything else — `match 1`,
`the last match`, `the number of matches`, `the whole match`.

Any command can carry a deadline, with a required unit:

```
run "curl" with "--silent", endpoint within 30 seconds

try to pipe within 1 minute
    run "find" with "/", "-name", "*.log"
    run "xargs" with "wc", "-l"
end pipe

if the result is 124 then put "took too long" into standard error
```

Timed-out children are killed and reaped — no orphans, no wedged script.

## Cleanup that actually runs

Abort-on-failure is the headline default, which makes the way out matter as
much as the way through. `ensure` registers a block when execution reaches it,
and it runs when the script ends — normally, on error, on `quit`, or on Ctrl-C
— most recent first:

```
put "held" into file (lock path)

ensure
    delete file (lock path)
end ensure

run "make" with "deploy"
```

The lock is released whether `make` succeeds or not. A failure inside a
cleanup block is reported but never replaces the error that ended the script.

## Lists, without a second grammar

A plural chunk noun with no index is the whole set, so splitting is the
grammar from the previous section read the other way:

```
put the words of headline into terms
put the lines of report into rows
put item 1 of (passwd entry split by ":")     -- cut -d: -f1
put the sorted (the unique terms) joined by ", "
```

A list keeps its elements separate, which comma-delimited text cannot — an
element may contain a comma and stay one element. `the sorted X` compares
numerically when every element is a number, because sorting 10 before 9 is
never what a counter meant.

Text and numbers come with the article, so they cost nothing from the name
vocabulary — `sorted count` is still a perfectly good variable:

```
if the lowercase target is "production" then put "shipping for real"
put the trimmed reply into answer
put the sum of the words of counts
```

And a handler can be used inside an expression, so composing two of them no
longer needs three statements and a temporary:

```
to double with n
    return n * 2
end double

put the double of 5 + the double of 10        -- 30
```

An unknown name there is caught when the script is checked, not when the line
happens to run.

## Secrets that cannot be logged by accident

The failure worth designing against is not a malicious script. It is
`put "connecting as" && token` in a generated script, running in CI, writing a
credential into a log that is retained for a year. That mistake is made by
being ordinary, so the fix has to be structural.

A secret is a *sealed* value. It refuses to become text, and every printing
path in the language goes through one conversion — so `put`, joining,
`--trace` and error messages all redact without knowing secrets exist:

```
put the secret "db password" into password
put "connecting as" && user && "with" && password
```

```text
connecting as deploy with «secret db password»
```

Only the secret spans redact; the rest of the line survives, because a
mechanism that destroys your logs is one people route around. The seal is
contagious, so `"postgres://user:" & password & "@host"` is still sealed and
still works when it reaches a program.

**Streams redact, boundaries release.** Printing is the accidental path and is
closed. A program's arguments, its standard input, its environment and a file
write are deliberate, so they get the real value — and `--explain` names every
place it happens:

```text
Reads these secrets:
  db password  — line 4  (from the keystore)

Lets a secret leave the process:
  on the standard input of psql  — line 9
```

Values live in a keystore, and each one names the roles that may read it:

```bash
frost keystore set prod.keystore "db password" --roles deploy,admin
frost --keystore prod.keystore --role deploy release.frost
```

If the role cannot open a secret the script names, frost exits 3 and nothing
runs. The secret *names* and the role grants are stored in plaintext, because
that is the part a reviewer needs; only the values are encrypted. Roles hold
X25519 keypairs, so storing a secret and granting a role need no passphrase —
only reading does.

It does not stop a script handing a secret to a program it is allowed to run;
nothing at this layer can. And once the plaintext reaches another program,
frost cannot follow it. The manifest reports the release rather than pretending
otherwise.

## Why this matters for AI agents

Shell scripts are increasingly written by models and reviewed by people. That
inverts the assumption every shell was designed under, and it breaks in three
specific places.

**Review does not scale at generation speed.** A model can produce forty lines
of bash faster than a person can verify one of them, and `set -euo pipefail`,
`IFS=$'\n\t'`, `${1:?}`, and `$4 ~ /^5/` all have to be read carefully to be
read at all. The reviewer either slows to the speed of careful reading, or
starts skimming. Most people skim. frost moves the cost: the script is longer,
but nothing in it needs decoding, so skimming and reading converge.

**Prompt injection becomes shell injection.** An agent that reads a web page, a
filename, an issue title, or a log line and puts that text into a generated
command has handed an attacker a shell. This is not hypothetical; it is the
main path by which agent tooling gets compromised. In frost a value cannot
become syntax — arguments are a list handed to `execve`, never re-parsed —
so hostile text stays text no matter where it came from. The `rm -rf *` in a
filename above is the whole demonstration.

**Approval needs something to approve.** "Do you want to run this script?"
asks a person to simulate an interpreter in their head. `--explain` replaces
that with a capability manifest:

```text
This script runs tar and date, reads 2 files (runtime),
and writes 3 files (temporary).
```

A human can approve *capabilities* in seconds. Reading the code to derive those
capabilities takes minutes and is where mistakes happen.

### The part that is actually new

Because a frost script is a parse tree rather than a string, an agent's output
can be checked mechanically **before** anything executes:

```policy
forbid running "rm" with "-rf"
forbid writing to "/etc/*"
forbid running "sudo"
require timeout on "curl"
require every command to be checked
```

Violations exit 3 and the script never starts. This is sandboxing at the
language level rather than the container level, and it composes with a
container rather than competing with it. The agent proposes; the policy
disposes; the human reads a manifest instead of code.

Rules also count, which is what an organisation's actual rules tend to do —
not *may it use curl*, but *how many times, for how long, and does it clean up
after itself*:

```policy
require at most 12 commands
require at most 2 files written
require at least 1 cleanup
forbid more than 2 runs of "curl"
forbid any files deleted
require timeout on "*" between 1 and 120 seconds
```

Units are reconciled, so a policy written in seconds catches a script written
in minutes. A limit that is exceeded points at the line that crossed it. A
deadline computed at runtime is refused rather than assumed acceptable, on the
same principle as the manifest: say what is unknowable, do not guess it.

Built-in checks catch the classics with no policy at all — `curl … | sh` is
reported as *downloaded code piped into a shell*, and a script that reads
`~/.ssh/id_rsa` and then makes a network call is flagged as *secrets read, then
the network is contacted* — the shape of data theft. Both are facts about the
tree, not pattern matches on the text, and both hold even when the sensitive
path is assembled at runtime from a variable and a string fragment.

### What this does not do

Analysis covers literals. If a script builds a program name or path at runtime,
frost reports it as *built at runtime* rather than guessing — the manifest tells
you that something is unknowable, not what it is. A determined script can still
put itself out of reach that way, and a policy that permits a command permits
its consequences. This narrows the blast radius and makes review tractable; it
is not a sandbox and does not claim to be.

## The feature that carries the language

Chunk expressions. One uniform grammar replaces `cut`, `awk '{print $3}'`,
`sed -n '7p'`, `head`, and `tail`:

```
put the third word of line 7 of file "access.log"
put the last item of csv row
put words 2 to 4 of headline
put the number of lines in report
```

Text addressing is most of what shell scripting actually is, and this is the
best notation anyone has shipped for it. It was HyperTalk's, and it deserves
another run.

## A script you can check before you run it

**Before you run it, you can read exactly what it is allowed to do. Bash
cannot do this.**

Because frost is parsed rather than string-substituted, a script's capabilities
are visible in the tree. `--explain` prints them:

```text
Runs these programs:
  rm    - line 8   (no timeout)
  curl  - line 12  (1 allowed to fail, no timeout)

Writes these files:
  /etc/cleanup.state  - line 11
```

And `--policy` enforces rules before anything is spawned:

```policy
forbid running "rm" with "-rf"
forbid writing to "/etc/*"
require timeout on "curl"
require every command to be checked
```

```text
REFUSED: running "rm" with "-rf"
  cleanup.frost:8   run "rm" with "-rf", scratch folder

2 rule violation(s); the script was not run.
```

`rm -rf "$DIR"` in bash is a string until it executes, so there is nothing to
inspect first. In frost the program and its arguments are separate nodes, which
is what makes a script checkable as a contract instead of trusted as a guess.

### Built-in checks

Beyond a policy file, every script gets a standing set of checks. On a script
that looks like routine cleanup:

```text
Findings:
  [DANGER ] line 12  Recursive forced delete
  [DANGER ] line 15  Writes to a system location (/etc/cleanup.state)
  [DANGER ] line 18  Permissive or recursive permission change
  [caution] line 21  No timeout on curl
  [caution] line 21  Failure ignored (curl)
  [DANGER ] line 26  Downloaded code piped into a shell

Verdict: dangerous
```

`open audit.html` for a visual report of four scripts — a fake "dotfile backup"
that exfiltrates your keys and a cleanup script that quietly does four dangerous
things, both refused, alongside a health check and a log analyzer that pass.

## Install

Requires Python 3.10+. No dependencies.

```bash
git clone <repo> frost && cd frost
ln -s "$PWD/frost" /usr/local/bin/frost
frost examples/hello.frost
```

The keystore is the one optional extra, because it needs a real cipher:

```bash
pip install "frostlang[keystore]"
```

Coexists with zsh — you are adding an interpreter, not replacing your shell.
Make scripts executable with a shebang and run them directly:

```bash
chmod +x report.frost
./report.frost access.log
```

## Try it in 30 seconds

No dependencies beyond Python 3.10. Paste this whole block:

```bash
git clone <repo> frost && cd frost

cat > hello.frost <<'END'
run "date" with "+%A"
put "Today is" && it

put the number of lines in file "/etc/hosts" into host lines
put "Your hosts file has" && host lines && "lines"
END

./frost hello.frost
```

```text
Today is Monday
Your hosts file has 4 lines
```

Now ask what that script is allowed to do, without running it:

```bash
./frost --explain hello.frost
```

```text
This script runs date, and reads 1 file (system).

Runs these programs:
  date  - line 1  (no timeout)

Reads these files:
  /etc/hosts  - line 4

Verdict: clean
```

Then try to break it. This filename is a shell injection payload:

```bash
cat > risky.frost <<'END'
put "notes.txt; rm -rf *" into filename
run "touch" with filename
run "ls" with "-1"
put it
END

./frost risky.frost
```

```text
hello.frost
keep_me.txt
notes.txt; rm -rf *
risky.frost
```

A file was created with that literal name. Nothing was deleted. The same two
lines in bash would have emptied the directory.

## Tooling

**Scratchpad** — the fastest way to understand chunk expressions:

```bash
frost --try
```

```text
frost> the third word of line 2 of it
/api/login
frost> the number of lines in it
7
frost> every match of "\d+" in the first line of it
5 items: 10, 0, 0, 1, 200
```

`play.html` is the same thing in a browser, with the text editable and a set of
worked examples. Its evaluator is a second implementation, and
`tools/verify_chunks.py` runs 1,288 expressions through both it and the real
interpreter on every build — the page is not written if they disagree.

**Formatter** — canonical layout, comments preserved:

```bash
frost --format script.frost          # print
frost --format --write script.frost  # rewrite in place
```

It refuses to format a script that does not parse, is idempotent, and produces
an identical parse tree, so it cannot quietly change meaning. The examples in
this repo are its style reference, and a test fails if any of them drifts.

**Editor support** — `editors/` holds a TextMate grammar and a VS Code
manifest, generated from the parser so the highlighted keywords cannot drift
from the real ones, with indent rules taken from the formatter so typing does
not fight `--format`.

**For code-generating models** — `MODEL-SPEC.md` is a compact reference sized
for a system prompt. Point a model at it and it will emit frost instead of
bash; then `--explain` and `--policy` check the result before it runs.

## Usage

```text
frost script.frost [args...]     run a script
frost --check script.frost       parse only, report errors
frost --ast script.frost         dump the syntax tree
frost --trace script.frost       print each statement as it runs
frost --explain script.frost     describe what it can do, without running it
frost --explain --json s.frost   the same, as JSON
frost --policy rules.policy s.frost   enforce rules, then run if it passes
frost --try [subject.txt]        scratchpad for chunk expressions
frost --keystore F --role R s.frost   run with access to secrets
frost keystore init|set|get|list|grant|revoke|roles
frost --format [--write] s.frost canonical layout
frost --version
```

frost's own options end at the script path; everything after it belongs to the
script, so `frost report.frost --check` passes `--check` to the script.

Exit status is the contract:

```text
  0  ran, or answered a question
  1  the script failed, or --explain judged it dangerous
  2  could not read or parse it, or the arguments were wrong
  3  a policy refused it; nothing was run
130  interrupted
```

## Layout

```text
frost                 executable entry point
frostlang/
    lexer.py          tokeniser
    parser.py         recursive-descent parser
    ast.py            node definitions
    interp.py         tree-walking evaluator
    audit.py          capability manifest, danger checks, policy engine
    formatter.py      canonical layout
    repl.py           the --try scratchpad
    cli.py            driver and error reporting
examples/             runnable scripts
tests/                1125 tests — python3 -m pytest tests/ -q
    gen.py            generates valid frost, for the property tests
    golden/           recorded --explain output for every example
LANGUAGE.md           full reference and grammar
docs.html             browsable docs (tools/build_docs.py)
audit.html            visual audit report (tools/build_audit.py)
play.html             live scratchpad (tools/build_play.py)
MODEL-SPEC.md         prompt-sized reference (tools/build_model_spec.py)
web/chunks.js         browser evaluator, verified against frostlang/
editors/              syntax highlighting
```

## Status

Version 0.4.0. The language runs, the examples are real, and 1125 tests cover
lexing, parsing, chunk semantics, pattern matching, timeouts, process
execution, pipe failure, static analysis, policy enforcement, and the
injection property.

The gaps this file used to list are closed. In the order they were listed:

- Handlers write globals with `put 99 into the global total`, and `global` is
  reserved so the near-miss is an error rather than a local of that name.
- `run "sort" reading names` puts text on a program's standard input.
- `ensure ... end ensure` releases what a script took, whether it finished,
  failed, quit, or was interrupted.
- Lists are real: `the words of X`, `split by`, `joined by`, `the sorted X`,
  and `put "c" after names` appends an element rather than concatenating.
- String and number functions: `the uppercase X`, `the trimmed X`,
  `the rounded X`, `the sum of X`.
- `run "make" in folder build path`, and `put "clang" into the environment
  variable "CC"`.
- `run "make" showing output` hands the terminal to the child, for long builds
  and interactive programs.
- Handlers are callable in expressions: `the double of 5`.

Remaining, honestly:

- **`--explain` reasons about literals only.** A program name or path built at
  runtime is reported as unknowable rather than guessed. This is deliberate,
  but it does mean a determined script can put itself out of reach.
- **No modules.** A script is one file; there is no way to share a handler
  between two of them.
- **No structured data.** Lists are flat lists of text. Nothing reads JSON.
- **`the standard input` is read whole**, so frost cannot filter a stream that
  never ends.
- **No compile-to-bash mode** for machines without frost installed. The
  interpreter is a tree walker; a bytecode pass would be straightforward if
  process spawn ever stopped dominating the runtime, which it will not.

## License

MIT. See [LICENSE](LICENSE). Do what you like with it; keep the copyright
notice.

