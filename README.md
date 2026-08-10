# frost

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
frost --policy rules.policy s.frost   enforce rules, then run if it passes
frost --try                      scratchpad for chunk expressions
frost --format [--write] s.frost canonical layout
```

## Layout

```text
frost                 executable entry point
frostlang/
    lexer.py          tokeniser
    parser.py         recursive-descent parser
    ast.py            node definitions
    interp.py         tree-walking evaluator
    cli.py            driver and error reporting
examples/             runnable scripts
tests/                566 tests — python3 -m pytest tests/ -q
LANGUAGE.md           full reference and grammar
docs.html             browsable docs (tools/build_docs.py)
audit.html            visual audit report (tools/build_audit.py)
play.html             live scratchpad (tools/build_play.py)
MODEL-SPEC.md         prompt-sized reference (tools/build_model_spec.py)
web/chunks.js         browser evaluator, verified against frostlang/
```

## Status

Version 0.3.0. The language runs, the examples are real, and 566 tests cover
lexing, parsing, chunk semantics, pattern matching, timeouts, process
execution, pipe failure, static analysis, policy enforcement, and the
injection property.

Known gaps, roughly in the order they hurt:

- **Handlers read globals but cannot write them.** `put 99 into total` inside a
  handler silently creates a local even when a global of that name is readable
  two lines up. Needs either real lexical scoping or an explicit global form.
- **No way to feed text into a program's stdin.** Pipes chain program to
  program; there is no equivalent of a heredoc.
- **No cleanup on abort.** Failure aborts hard, so lock files and temp
  directories leak. Given that abort-on-failure is the headline default, this
  is a design hole rather than a missing convenience.
- **No real lists.** `put "b" after xs` concatenates text. `the arguments` and
  `every match` are the only lists the language can produce.
- **No string functions** — no uppercase, trim, or splitting on an arbitrary
  delimiter.
- **No environment control** for child processes, and no way to change the
  working directory.
- Output is captured, not streamed, so long-running commands show nothing until
  they finish and interactive programs do not work at all.
- Handlers cannot be called from inside an expression; results arrive in `it`.

Not yet built: a compile-to-bash mode for machines without frost installed. The
interpreter is a tree walker; a bytecode pass would be straightforward if
process spawn ever stopped dominating the runtime, which it will not.

## License

MIT. See [LICENSE](LICENSE). Do what you like with it; keep the copyright
notice.

