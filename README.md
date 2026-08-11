# frost

[![PyPI](https://img.shields.io/pypi/v/frostlang)](https://pypi.org/project/frostlang/)
[![CI](https://github.com/keithadler/frost/actions/workflows/ci.yml/badge.svg)](https://github.com/keithadler/frost/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**A shell scripting language for the era when machines write the scripts and
humans only get to review them: readable by default, structurally immune to
injection, and auditable before a single process starts.**

A grammar descended from HyperTalk, an interpreter rather than a login shell.

## What you get

Everything below follows from one decision: **a frost program is a parse tree,
never a string.** Nothing is interpolated, nothing is re-parsed, and no value
can become syntax. That single property is what lets a script be checked as a
contract instead of trusted as a guess.

| Capability | What it buys you |
| --- | --- |
| **No interpolation, no `eval`** | Injection is *unrepresentable*, not mitigated. Hostile text stays text wherever it came from: a filename, an issue title, a web page an agent just read. |
| **`--explain`** | A capability manifest before anything runs: every program spawned, file read or written, secret released. Approving capabilities takes seconds; deriving them by reading code takes minutes, and that is where mistakes happen. |
| **`--policy`** | Business rules checked against the tree. Not just *may it use curl*, but how many times, for how long, and whether it cleans up after itself. And every rule explains itself when it fires. Violations exit 3 and the script never starts. |
| **Sealed secrets** | A value from the role-gated keystore cannot be printed by accident. The seal survives concatenation, comparison is constant-time, and `--explain` names every place a secret is released to a program. |
| **Modules** | An import declares a capability ceiling, so reading the entry file gives a sound upper bound on the whole program. A shared module that later grows a network call breaks the build at the import site rather than quietly widening someone's manifest. |
| **`--sandbox`** | The kernel holds the boundary while the script runs, so a path the analyser *could not* resolve is confined anyway. Fails closed: where the boundary cannot be enforced, frost refuses to run rather than warning and continuing. |
| **`the run id`** | One identity per execution, supplied by the pipeline or generated. It reaches the recording, the trace and every child process, so a log line three layers down joins back to the run that caused it. Also an idempotency key, and a scratch path that cannot collide. |
| **`--record` / `--replay`** | Snapshot testing for shell scripts. A recording is a fixture you can commit; replay spawns no process, writes no file, and reports a divergence rather than a stack trace. Secret values are never written down. |
| **Records and JSON** | `the "name" of the "user" of report`: API responses without a second language in the file. Shelling out to `jq` handed the auditor a string it could not see into; a record is part of the tree. |
| **`the error output`** | Why a command failed, not just that it did, without `sh -c "... 2>&1"`, which is the one construct the auditor flags and the spec forbids. |
| **Declared record shapes** | `with fields "status", "number"` makes a mistyped field a `--check` failure instead of a silent empty, and verifies the payload at the line that parsed it. |
| **`--events`** | NDJSON for Splunk, New Relic, Datadog or a collector. Every command timed, every effect reported, secrets redacted. The finish event says which approved capabilities went **unused**, which is a signal a shell cannot produce and which drives tightening an approval before it is abused. |
| **Dead code** | Unreachable statements, handlers nobody calls, values computed and dropped. Harmless individually, and together the clearest sign a generated script contains more than anyone intended. |
| **`frost mcp`** | The review tools over Model Context Protocol, stdio JSON-RPC, no dependencies. It cannot run a script and reads no files, by design: frost exists because the decision to run sits with a person, and a server that executes on request moves it back to the machine. |
| **`frost context`** | What a model should read before writing frost: the forms, the reserved words taken from the parser, and the constructs it deliberately lacks. Every snippet in it is parsed by the test suite, so it cannot teach a form that does not work. |
| **Volume limits** | `require at most 10 megabytes of output` and `--max-output 10MB`. A deadline says nothing about a command that answers instantly with a gigabyte. The child is killed at the ceiling rather than measured after the fact, because a limit that notices afterwards prevents nothing. |
| **Nested interpreters** | `xargs sh -c`, `env sh -c`, `sudo sh -c`, `find -exec`, `ssh host "..."`. The escape check used to fire only when the interpreter was the program name, so every indirect form reported nothing at all. A manifest may overstate; understating is what makes it worse than none. |
| **`frost diff`** | `frost diff old.frost new.frost` compares two versions by what they can do, not by their text. Three rearranged lines can be a widening and thirty can be a rename, so a review that reads the text diff is reading the wrong artefact. |
| **Output masking** | A program handed a credential often echoes it back. frost finds the plaintext in what a child wrote and re-seals it, so it redacts wherever the script prints it. Exact-match only: a mask that guesses at shapes fails in both directions and gets trusted for the one it fails at quietly. |
| **Repair report** | A refusal names the narrowest policy change that would clear it, and states what else that change would allow. It is never a patch, and under `--automated` it declines to answer: an agent handed the exact edit that clears its own refusal has been handed the instructions for widening its own bounds. |
| **Environment rules** | `forbid reading the environment "AWS_*"` and `require reading only the environment "PATH"`. Setting had a rule and reading did not, which was the wrong way round. |
| **`--deadline`** | A budget for the whole run, honoured with cleanup and exiting 124. A loop doing arithmetic has no capabilities, so the manifest called it clean; an unbounded loop is now a finding, and a policy can impose the budget centrally. |
| **Site policy** | `/etc/frost/policy.d/*.policy` applies to every run on the host, whether or not anyone passed `--policy`. Site rules add to a project's and can only narrow them, and every policy applied is named by digest in the manifest and the recording. |
| **`--automated`** | An unattended run refuses `--approve` and `--ignore-approval`. A repair loop that can approve is one that approves its own capability escalation. |
| **Signed approvals** | `--sign-with` binds an approval to a named approver and a commit; `require an approval signed by "..."` names who a host trusts. Verification never degrades: without the cipher, an unverifiable signature is refused. |
| **`--approve`** | Records what a script does today, then binds by default: a regeneration that does more is refused without any flag. A content hash fires on every edit, so it cannot be used on a script an agent rewrites. This fires only when the script gained a capability. |
| **SARIF and an Action** | `--check --sarif` feeds GitHub code scanning, so a refusal appears on the diff line in front of the person merging rather than in a log nobody opens. `action.yml` wires check, explain, policy and approvals into six lines of workflow. |
| **`--policy-from`** | Writes a starter policy describing what a script already does. The policy engine was the most useful thing here and the least used, because the first step was a blank file. |
| **`--json` / `--repair`** | Every diagnostic as structured data with the edit attached, so the model that wrote the script can repair it without a human in the loop. |

### The lifecycle

Each stage is optional, and each one narrows what the next has to trust:

```text
frost --check     s.frost      does it parse, and are the names real?
frost --explain   s.frost      what could it possibly do?
frost --policy p  s.frost      is that allowed here?
frost --sandbox   s.frost      hold the boundary while it runs
frost --record r  s.frost      write down what it actually did
frost s.frost                 refuse it if it gained a capability
frost --events e  s.frost     tell a monitoring system what happened
```

The first three cost about a millisecond and all happen before a single
process starts. The fourth is enforced by the operating system. The fifth
turns a run into a fixture.

### Where the rest is documented

- **[LANGUAGE.md](LANGUAGE.md)**: the full reference and grammar.
- **[MODEL-SPEC.md](MODEL-SPEC.md)**: a prompt-sized spec, built to be pasted
  into a system prompt so a model writes correct frost first time.
- **[CHANGELOG.md](CHANGELOG.md)**: what changed and why.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: how the pieces fit together.
- **[PLATFORM.md](PLATFORM.md)**: for the team that operates the machines:
  what frost enforces, what it cannot, and what actually makes any of it
  mandatory. The honest answer to that last one lives mostly outside frost.
- **[Try it in the browser](https://keithadler.github.io/frost/play.html)**,
  the scratchpad, and below it frost itself compiled to WebAssembly. The same
  Python the command line runs, so `--explain`, `--policy`, `--repair` and an
  approval comparison answer exactly as they would in a terminal. Nothing to
  install.
- **[Reference](https://keithadler.github.io/frost/docs.html)** and
  **[a visual audit report](https://keithadler.github.io/frost/audit.html)**.
  (These are committed as `docs.html`, `audit.html` and `play.html` too, but
  GitHub shows a committed page as source rather than rendering it, so the
  links above are the ones to follow.)

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

## Why

Shell syntax was optimised for a cost that no longer dominates. `cut -d: -f1`
is terse because a human typed it a thousand times on a serial terminal. When a
model writes the script and a human reads it once: at 3am, while production is
down: the scarce resource is comprehension at review time, not keystrokes.

frost inverts the optimisation. It is not a login shell; it is an interpreter
you point at a file. Because nobody types it at a prompt, nothing in the design
has to be short.

The speed objection does not apply, though not for the reason you might
expect. `python tools/benchmark.py` measures it rather than asserting it:

```text
release.frost           78 lines    parse 664us   audit 290us
one fork+exec of true                            1992us   (the floor)
one fork+exec of git --version                  12767us   (a real command)
```

Parsing an 80-line script and deriving its entire capability manifest costs
about a millisecond. So does starting a process, the two are the same order
of magnitude, and which one wins depends entirely on the machine. `fork`/
`exec` of `true` is roughly 0.7ms on Linux and 2.4ms on macOS; `git
--version` is 1.2ms on Linux and 12ms on macOS. Parsing varies far less.

The tempting claim, *you can parse verbose syntax faster than you can spawn
a process*, is therefore true on macOS and false on Linux. It was in this
README until CI on Linux disproved it.

What actually holds is the thing the design relies on: **parsing is paid
once, spawning is paid per command.** A script that runs ten commands spends
ten process spawns against one parse, so the parse is a rounding error no
matter which platform it runs on. Verbosity costs nothing at this scale
because the front end is a fixed cost, not because it wins a race.

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
to someone who has never seen frost: no `set -euo pipefail`, no `IFS`
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

Capture groups use the same chunk grammar as everything else: `match 1`,
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

Timed-out children are killed and reaped: no orphans, no wedged script.

## Cleanup that actually runs

Abort-on-failure is the headline default, which makes the way out matter as
much as the way through. `ensure` registers a block when execution reaches it,
and it runs when the script ends: normally, on error, on `quit`, or on Ctrl-C
most recent first:

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

A list keeps its elements separate, which comma-delimited text cannot, an
element may contain a comma and stay one element. `the sorted X` compares
numerically when every element is a number, because sorting 10 before 9 is
never what a counter meant.

Text and numbers come with the article, so they cost nothing from the name
vocabulary, `sorted count` is still a perfectly good variable:

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

## Structured data, without a second grammar

Every real script eventually calls an API and reads a field out of the answer.
Until now that meant `run "jq" with ".status"`: a second language inside a
file whose entire argument is that it needs only one, and a string the auditor
could not see into. `--explain` could tell you the script ran `jq`. It could
never tell you what for.

```
run "curl" with "-fsS", "https://api.example.com/build" within 30 seconds
put the json of it into build

if the "status" of build is not "green" then
    put "build" && the "number" of build && "failed" into standard error
    put the "name" of the "author" of build && "was last to push"
    quit with status 1
end if
```

Objects become records, arrays become the lists frost already has, and numbers
stay numbers: so `item 1 of`, `repeat for each` and `+ 1` all keep working. A
missing key is empty, like `word 99 of`, and a field of empty is empty, so an
optional field needs no guard. A field of *text* is an error, because that
means the value is not the shape the script thinks it is.

Parsing a secret seals every field it produces, and serialising redacts field
by field rather than all at once, a record you cannot print at all is a
record people work around.

```text
put the json of the secret file "credentials.json" into config
put "connecting as" && the "user" of config
connecting as «secret credentials.json»
```

## Knowing why it failed

`the error output` sits beside `it` and `the result`: what the last command
wrote to standard error, what it wrote to standard output, and how it exited.

```
try to run "curl" with "-fsS", url within 30 seconds
if the result is not 0 then
    put "curl failed:" && the error output into standard error
    quit with status 1
end if
```

The alternative was `run "sh" with "-c", "... 2>&1"`, which reintroduces the
shell frost exists to remove and which the auditor flags on sight. Wanting to
know why something failed is completely ordinary, and it should not require
defeating the language's main guarantee to get it.

## A clock that replays

```
put "started at" && the current timestamp
wait 5 seconds
```

Both are recorded. `--replay` serves back the reading that was recorded rather
than reading the clock again, a fixture whose timestamps move on every replay
is a diff generator, not a fixture: and it does not sleep, so replaying a
script that backs off for thirty seconds costs nothing. A script that waits
says so in `--explain`.

## Secrets that cannot be logged by accident

The failure worth designing against is not a malicious script. It is
`put "connecting as" && token` in a generated script, running in CI, writing a
credential into a log that is retained for a year. That mistake is made by
being ordinary, so the fix has to be structural.

A secret is a *sealed* value. It refuses to become text, and every printing
path in the language goes through one conversion: so `put`, joining,
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
write are deliberate, so they get the real value, and `--explain` names every
place it happens:

```text
Reads these secrets:
  db password  at line 4  (from the keystore)

Lets a secret leave the process:
  on the standard input of psql  at line 9
```

Values live in a keystore, and each one names the roles that may read it:

```bash
frost keystore set prod.keystore "db password" --roles deploy,admin
frost --keystore prod.keystore --role deploy release.frost
```

If the role cannot open a secret the script names, frost exits 3 and nothing
runs. The secret *names* and the role grants are stored in plaintext, because
that is the part a reviewer needs; only the values are encrypted. Roles hold
X25519 keypairs, so storing a secret and granting a role need no passphrase,
only reading does.

It does not stop a script handing a secret to a program it is allowed to run;
nothing at this layer can. And once the plaintext reaches another program,
frost cannot follow it. The manifest reports the release rather than pretending
otherwise.

## Knowing what it did, not just what it could do

`--explain` answers what a script *can* do. What it *did* needed watching.

```bash
frost --record run.json deploy.frost      # run it, write down everything
frost --replay run.json deploy.frost      # run it again, spawn nothing
```

Replay performs nothing at all: no process, no write, no delete, and serves
the recorded answers back. So a recording is a fixture: change the script,
replay it, and a refactor meant to preserve behaviour either did or did not.

```text
DIVERGED at deploy.frost:3
    the recording ran: echo two
    this run wants:  echo CHANGED
```

Reformatting replays clean, because matching is on the identity of the effect
rather than on line numbers. Secret values are never written down, only their
names: and any revealed plaintext is scrubbed from everything recorded, so
the fixture is safe to commit.

## Telling a monitoring system what happened

```bash
frost --events run.ndjson deploy.frost      # or - for standard error
```

One JSON object per line, flushed as things happen. NDJSON is what Splunk's
HTTP collector, New Relic's log API, Datadog, Vector and Fluent Bit all ingest
without a translator, and a line-oriented file survives a run that is killed
halfway, which a single JSON document does not.

```json
{"event": "run.start",      "declares": {"programs": ["curl"], "hosts": ["x.example"]}}
{"event": "command.start",  "program": "curl", "argv": ["curl", "-fsS", "..."]}
{"event": "command.finish", "program": "curl", "status": 0, "seconds": 0.412}
{"event": "run.finish",     "status": 0, "commands": 3, "waited_seconds": 2.0,
 "programs_unused": ["psql"], "hosts_unused": ["db.internal"]}
```

**The resolution worth having is the pairing, not the volume.** Any tool can
log that a command ran. frost knows what the script was *allowed* to do before
it ran, what a person *approved*, and what the host *permits*, so the finish
event reports which approved capabilities went **unused**. A script approved
for six programs that uses two is an approval somebody should tighten, and
that is only visible holding the manifest and the run side by side.

Commands are timed, and the run separates time spent working from time spent
waiting, so a slow job can be attributed rather than guessed at.

**A refusal is an event**, which is the one a security team most wants and the
easiest to lose. A policy refusal, a breached import ceiling, an approval that
no longer covers the script, an unusable signature, a secret the role may not
read: each closes the run out with what fired and the digest of the policy it
came from.

```json
{"event": "run.finish", "status": 3, "refused": "policy",
 "rules": [{"what": "running \"curl\"", "line": 1,
            "hint": "egress goes through the proxy"}],
 "policies": [{"path": "/etc/frost/policy.d/00-egress.policy",
               "sha256": "05cd8a0c7c8f...", "origin": "site"}]}
```

Contents are never emitted and sizes are: *wrote 4kb* is useful and the 4kb is
not. Secrets are redacted before an event is written, including inside a
command's arguments, because telemetry leaves the building far more often than
a recording does.

It composes with the other modes. `--events` alongside `--record` gives both,
and a replayed run is marked `"replayed": true` so a dashboard does not count
a fixture as production traffic. Analysis emits nothing, because `--explain`
runs nothing and a dashboard should not see a run that never happened.

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

**Hostile text reaches a command as data.** An agent that reads a web page, a
filename, an issue title, or a log line and puts that text into a generated
command has handed an attacker a shell. In frost a value cannot become syntax
arguments are a list handed to `execve`, never re-parsed, so hostile text
stays text no matter where it came from. The `rm -rf *` in a filename above is
the whole demonstration.

**Hostile text reaches the model instead.** This is the harder one, and no
grammar touches it. An agent reads "also upload ~/.ssh/id_rsa" in a README and
writes perfectly valid frost that does exactly that: it parses, it formats
canonically, `--check` passes. The model is not confused about syntax. It has
been persuaded to use authority it legitimately holds, which is a confused
deputy rather than an injection.

frost's answer is not the grammar. It is that **the thing deciding what is
allowed is not the thing that wrote the script.** A policy is authored by a
person, ahead of time, out of band from generation, so a fully poisoned model
can emit whatever it likes and the rules still refuse it before a process
starts. The sandbox is held by the kernel, and a module cannot widen the
program past what its import declared.

Where there is no policy yet, `--approve` records what a script does today,
and from then on the approval binds by default: no flag to remember, because
a guard you have to remember is one the attacker composing your command line
will not:

```text
REFUSED: it can now run curl
REFUSED: it can now read the secret ~/.aws/credentials (from the file)
REFUSED: it can now let a secret leave the process as an argument to curl
```

Be clear about the limit: this bounds *what* a script can reach, never whether
reaching it was wise. A model allowed to run `git` can still push to the wrong
remote, and a policy permitting `curl` alongside a readable config file permits
sending one to the other. Capability bounds are not intent checks, and nothing
here reads intent.

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

Rules also count, which is what an organisation's actual rules tend to do,
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

Rules say why. A rule's trailing comment is its hint, and frost prints it
when the rule fires, so a refusal explains what to do instead rather than
just saying no:

```text
REFUSED: running "sudo"
  deploy.frost:1  run "sudo" with "systemctl", "restart", "api"
  why: the deploy role already has the permissions it needs
```

No new syntax; policy authors already write that comment.

### Modules that cannot hide anything

Sharing handlers across scripts is the feature most likely to break the one
invariant frost depends on. That the tree you audit is the program you run,
and the audit sees all of it. So the goal is not *safe modules*; it is
**modules that cannot put capability outside the manifest**.

```
use "lib/db.frost" for the connect, the migrate which may run "psql"
```

A module is declarations only: handler definitions and imports, nothing that
runs when it is imported. The path is a literal the parser insists on, and it
resolves relative to the importing file with no search path, nothing above
the entry script's directory, and no registry. Imports name exactly what they
bring in, so a collision is an error rather than one module silently
replacing another's handler.

`--explain` audits the whole closure and says where each capability came
from:

```text
lib/db.frost   (imported by deploy.frost:1)
  Runs these programs:
    psql  at line 2
```

And the `which may` clause is the part that makes single-file review survive
multi-file code. A module defaults to no capabilities at all. If it does more
than its import declared, the program is refused before anything runs:

```text
REFUSED: lib/sneaky.frost may not run curl
  The import at deploy.frost:2 allows: nothing but compute.
```

A ceiling bounds the whole subtree an import pulls in, not just the file it
names, a module allowed to run `psql` cannot import a second one that runs
`curl`. So a reviewer who reads only the entry file has a sound upper bound on
the whole program, and a shared module that later grows a network call breaks
the build at the import site rather than quietly widening someone's manifest.
`frost --lock` and `--frozen` pin the bytes.

### Closing the loop with the thing that wrote it

Every refusal above is a sentence for a person. `--json` is the same
information as data, with the edit attached wherever frost already knew it:

```json
{"code": "missing-then", "line": 2, "column": 20,
 "message": "expected 'then' but found end of line",
 "repairs": [{"kind": "replace-line", "line": 2,
              "text": "if error count is 0 then", "confidence": "high"}]}
```

`frost --repair --write` applies the high-confidence ones and repeats until
nothing certain is left: fixing one error reveals the next, so a single pass
would give up on any script with two mistakes. A pass is kept only if it made
progress, which is what makes it safe to run unattended:

```text
repaired deploy.frost (3 change(s))
  line 2: an 'if' condition is closed by 'then'
  line 3: run takes a program and a list of arguments, never a command line
  line 5: a global is written 'the global <name>'
```

That is the loop: generate, check, repair, re-check, with a policy deciding
what is acceptable and a manifest a human approves at the end.

### Boundaries the kernel holds

Everything above reasons about the text of a script, and is careful to say
when it cannot know something. Once the script runs, an unknowable path is a
real path. So the boundary is declared once and held at runtime:

```policy
sandbox may run "git", "make"
sandbox may read "*"
sandbox may write "build/*"
```

```bash
frost --policy prod.policy --sandbox deploy.frost
```

```text
sh: /tmp/anywhere-else.txt: Operation not permitted
```

Child processes are confined by the operating system, `sandbox-exec` on
macOS, `bubblewrap` on Linux, so a path the analyser could not resolve is
confined anyway. frost's own file operations are checked by frost, which is a
weaker guarantee, and the docs keep the two apart rather than blurring them.

Two things it deliberately will not do. **Per-host network rules are refused,
not faked**: macOS filters on addresses and a Linux namespace is
all-or-nothing, so `sandbox may reach "api.github.com"` is a parse error and
`sandbox may reach the network` means exactly what it says. And if a boundary
is declared but cannot be enforced here, **frost refuses to run** rather than
warning and continuing, including when the backend is present but a live
self-test shows it not actually confining.

That self-test runs two controls, not one. A forbidden write must be refused
*and* a permitted write must succeed. Checking only the first is the trap the
feature is most likely to ship with: a sandbox that fails to start blocks the
forbidden write too, so every "is it blocked?" assertion passes and the thing
reports itself healthy while confining nothing. Both backends were caught by
the second control, Linux dying on a network namespace it was not allowed to
enter, macOS naming an unresolved `/tmp` path the kernel never matches.

Built-in checks catch the classics with no policy at all, `curl … | sh` is
reported as *downloaded code piped into a shell*, and a script that reads
`~/.ssh/id_rsa` and then makes a network call is flagged as *secrets read, then
the network is contacted*, the shape of data theft. Both are facts about the
tree, not pattern matches on the text, and both hold even when the sensitive
path is assembled at runtime from a variable and a string fragment.

### What this does not do

Analysis covers literals. If a script builds a program name or path at runtime,
frost reports it as *built at runtime* rather than guessing, the manifest tells
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
  rm  at line 8   (no timeout)
  curl  at line 12  (1 allowed to fail, no timeout)

Writes these files:
  /etc/cleanup.state  at line 11
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

[The audit report](https://keithadler.github.io/frost/audit.html), or `open audit.html` locally, shows four scripts, a fake "dotfile backup"
that exfiltrates your keys and a cleanup script that quietly does four dangerous
things, both refused, alongside a health check and a log analyzer that pass.

## Install

Requires Python 3.10+. No dependencies.

```bash
pip install frostlang
frost --version
```

Or with Homebrew:

```bash
brew install keithadler/frost/frost
```

The keystore is the one optional extra, because it needs a real cipher:

```bash
pip install "frostlang[keystore]"
```

From a checkout instead, which is what you want if you are changing frost:

```bash
git clone https://github.com/keithadler/frost.git && cd frost
ln -s "$PWD/frost" /usr/local/bin/frost
frost examples/hello.frost
```

Coexists with zsh: you are adding an interpreter, not replacing your shell.
Make scripts executable with a shebang and run them directly:

```bash
chmod +x report.frost
./report.frost access.log
```

## Try it in 30 seconds

No dependencies beyond Python 3.10. Paste this whole block:

```bash
git clone https://github.com/keithadler/frost.git && cd frost

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
  date  at line 1  (no timeout)

Reads these files:
  /etc/hosts  at line 4

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

## Starting a project

```bash
frost init
```

Writes `main.frost` and `frost.policy` beside it, and prints the three
commands worth running next. The policy is generated from the script, so the
two agree before you have typed anything: every later edit is measured against
something that was true once.

It will not overwrite either file.

## In a pipeline

As a pre-commit hook:

```yaml
repos:
  - repo: https://github.com/keithadler/frost
    rev: v0.9.2
    hooks:
      - id: frost-check
```

`frost-check` runs `--check --strict`, which exits 1 on a dangerous verdict.
`frost-explain`, `frost-policy` and `frost-format` are there too.

In a container, for CI without a Python setup step:

```bash
docker run --rm -v "$PWD:/work" ghcr.io/keithadler/frost:latest \
  --check --strict deploy.frost
```

It reviews scripts. It is a poor place to run one, deliberately: the image has
none of the tools a real script calls, and an image built for reviewing should
not become the place things execute.

## Giving it to an agent

Two commands, for the two halves of the loop.

`frost context` prints what a model needs in order to write frost: the
statement forms, the reserved words taken from the parser itself, and the
constructs frost deliberately lacks, since the mistakes a model makes are
`${x}`, backticks and an invented `let`. It is a few thousand characters,
which is the point: [LANGUAGE.md](LANGUAGE.md) argues a case across thousands
of lines and is the wrong document to paste into a context window. The same
text is committed as [MODEL-CONTEXT.md](MODEL-CONTEXT.md), and every snippet
in it is parsed by the test suite, so it cannot teach a form that does not
work.

`frost mcp` serves the review tools over Model Context Protocol on stdio:
`frost_check`, `frost_explain`, `frost_policy`, `frost_diff` and
`frost_grammar`. Add it to Claude Code with:

```bash
claude mcp add frost -- frost mcp
```

or by hand, in `claude_desktop_config.json` or any other MCP client:

```json
{
  "mcpServers": {
    "frost": {
      "command": "frost",
      "args": ["mcp"]
    }
  }
}
```

**It cannot run a script, and it reads no files.** That is the design rather
than a gap. frost exists because a machine writes the script and a person
decides whether it runs; a server that executes on request moves the decision
back to the machine, and a tool that took a file path would let whatever holds
the other end of the pipe read anything the process can reach. A refusal comes
back with the rule that fired and a refusal to draft the widening, because an
agent handed the exact edit that clears its own refusal has been handed the
instructions for widening its own bounds.

## Tooling

**Scratchpad**: the fastest way to understand chunk expressions:

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

[The scratchpad](https://keithadler.github.io/frost/play.html) is the same thing in a browser, with the text editable and a set of
worked examples. Its evaluator is a second implementation, and
`tools/verify_chunks.py` runs 1,288 expressions through both it and the real
interpreter on every build, the page is not written if they disagree.

**Formatter**: canonical layout, comments preserved:

```bash
frost --format script.frost          # print
frost --format --write script.frost  # rewrite in place
```

It refuses to format a script that does not parse, is idempotent, and produces
an identical parse tree, so it cannot quietly change meaning. The examples in
this repo are its style reference, and a test fails if any of them drifts.

**Editor support**: `editors/` holds a TextMate grammar and a VS Code
manifest, generated from the parser so the highlighted keywords cannot drift
from the real ones, with indent rules taken from the formatter so typing does
not fight `--format`.

**For code-generating models**: `MODEL-SPEC.md` is a compact reference sized
for a system prompt. Point a model at it and it will emit frost instead of
bash; then `--explain` and `--policy` check the result before it runs.

## Usage

```text
frost script.frost [args...]     run a script
frost --check script.frost       parse only, report errors
frost --ast script.frost         dump the syntax tree
frost --trace script.frost       print each statement as it runs
frost --trace-to-file F s.frost  write that trace to a file instead
frost --run-id ID s.frost        name this execution (else FROST_RUN_ID)
frost --automated s.frost        unattended: refuse anything that widens
frost --events F s.frost         one JSON object per event (- for stderr)
frost --events-format otel s.frost   OTLP/JSON traces instead of NDJSON
frost --deadline N s.frost       stop the whole run after N seconds
frost --enforce-hosts s.frost    check a command's real destination at spawn
frost --egress-rules squid s.frost   the allow-list, as proxy configuration
frost --new-approver-key F       a signing key for approvals
frost --approve --sign-with F s.frost   an approval somebody is accountable for
frost --check --sarif s.frost    findings for code scanning on a pull request
frost --policy-from s.frost      a starter policy describing what it does
frost --explain --against F s.frost   what changed since it was approved
frost --exit-codes [--json]      what each exit status means
frost --completion bash|zsh      a completion script
frost --explain script.frost     describe what it can do, without running it
frost --check --json s.frost     diagnostics as JSON, with repairs
frost --repair [--write] s.frost apply the repairs frost is sure about
frost --lock s.frost             record the sha256 of every module
frost --frozen s.frost           refuse to run if a module changed
frost --approve s.frost          record what it may do, in <script>.approved
frost --as-approved s.frost      insist an approval exists, and match it
frost --ignore-approval s.frost  run despite <script>.approved
frost --record run.json s.frost  run it and write down everything it did
frost --replay run.json s.frost  run it against a recording, spawning nothing
frost --policy p --sandbox s.frost   hold the declared boundary at runtime
frost --explain --json s.frost   the manifest, as JSON
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
  4  a replay diverged from its recording
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
    program_audit.py  the same, across an imported closure
    modules.py        import resolution, ceilings, lockfile
    baseline.py       what a script was approved to do
    runid.py          one identity per execution
    site.py           policy the host brings, and its provenance
    telemetry.py      events for a monitoring system
    egress.py         the allow-list, as proxy configuration
    signing.py        approvals bound to an approver and a commit
    sarif.py          findings for code review tools
    scaffold.py       a starter policy from a manifest
    browser.py        the analysis surface with no OS underneath
    sandbox.py        capability boundaries the kernel holds
    sealed.py         values that cannot be printed by accident
    keystore.py       role-gated envelope encryption
    keystore_cli.py   the frost-keys command
    journal.py        record and replay
    diagnostics.py    structured findings and repair payloads
    structured.py     records, and the bridge to JSON
    formatter.py      canonical layout
    repl.py           the --try scratchpad
    cli.py            driver and error reporting
examples/             runnable scripts
tests/                2100 tests, python3 -m pytest tests/ -q
    gen.py            generates valid frost, for the property tests
    golden/           recorded --explain output for every example
LANGUAGE.md           full reference and grammar
docs.html             browsable docs (tools/build_docs.py)
audit.html            visual audit report (tools/build_audit.py)
play.html             live scratchpad (tools/build_play.py)
MODEL-SPEC.md         prompt-sized reference (tools/build_model_spec.py)
PLATFORM.md           running frost as a platform control
action.yml            the GitHub Action, tested against this repo
web/chunks.js         browser evaluator, verified against frostlang/
canary_browser.py     boots play.html in Chromium and checks its answers
build_site.py         assembles what GitHub Pages publishes
action.yml            the GitHub Action
editors/              syntax highlighting
```

## Status

Version 0.9.2. The language runs, the examples are real, and 2100 tests cover
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
- **Modules**: `use "lib/db.frost" for the connect which may run "psql"`,
  declarations only, with a capability ceiling at the import site and
  `--lock`/`--frozen` to pin the bytes.
- **Streaming input**: `repeat for each line in the standard input` consumes
  lines as they arrive, so a frost filter works against a producer that never
  ends.
- **Quantitative policy**: counts, ranges and limits, with units reconciled
  and a hint on every rule.
- **Secrets**: a role-gated keystore, sealed values, and redaction that
  survives concatenation.
- **Runtime confinement**: `--sandbox`, held by `sandbox-exec` on macOS and
  `bubblewrap` on Linux, verified confining in CI on both.
- **Record and replay**: `--record` writes a committable fixture; `--replay`
  spawns nothing.
- **Telemetry**: `--events` writes NDJSON for a monitoring system, with every
  command timed, every refusal reported, and secrets redacted.
- **A datacenter's own rules**: `/etc/frost/policy.d/*`, applied to every run,
  composing so they can only narrow, and named by digest in the manifest and
  the recording.
- **Approvals somebody signed**: Ed25519, bound to an approver and a commit,
  verified against keys a policy names.
- **An automation guard**: `--automated` refuses anything that would widen
  what a script may do.
- **Records and JSON**: `the json of it`, `the "status" of report`, nested
  fields, `the json text of`: with sealing preserved in both directions.
- **Captured standard error**: `the error output`, beside `it` and
  `the result`.
- **A clock and `wait`**: both recorded, so a replay is still deterministic
  and still fast.

Remaining, honestly:

- **`--explain` reads what is derivable, not what is computed.** A name whose
  definitions are all literals is followed through, including when they differ
  a branch picking one of two hosts is reported as two hosts. A host is read
  out of a joined URL when the literal closes the authority, so
  `"https://api.github.com/repos/" & repo` reaches `api.github.com`. A value
  genuinely assembled at runtime is still reported as *unknowable* rather than
  guessed, which is why `--sandbox` exists: the kernel confines what the
  analyser could not resolve.
- **A declared shape is one level deep.** `with fields "a", "b"` checks the
  top level of a record. A nested shape has to be declared by pulling the
  inner record out into its own name first.
- **OTLP is a batch format, so a killed run leaves no trace.**
  `--events-format otel` writes its document when the run ends. NDJSON is the
  default because it streams, and a run killed halfway still leaves every line
  up to the moment it died.
- **No compile-to-bash mode** for machines without frost installed. The
  interpreter is a tree walker; a bytecode pass would be straightforward if
  process spawn ever stopped dominating the runtime, which it will not.
- **Per-host rules are checked in two places and enforced in a third.** The
  static check reads the script; `--enforce-hosts` reads a command's real
  arguments in the moment before it spawns, which is the only place a computed
  URL can be judged; and `--egress-rules squid` writes the allow-list as
  configuration for the proxy that actually holds it. frost holds the first
  two, and a program that ignores its arguments is stopped only by the third. `forbid reaching "*.telemetry.example"`
  and `require reaching only "api.github.com"` are policy rules, checked
  against the text before anything runs, and an unknowable destination fails
  them closed. The *sandbox* is still all-or-nothing: `sandbox may reach
  "api.github.com"` remains a parse error, because macOS filters on addresses
  and a Linux namespace has no middle setting. Two different guarantees, and
  the one the kernel holds is the weaker of the two here.

## The name

Robert Frost was born in San Francisco in 1874, and is remembered for poems a
person can read on first pass and not exhaust on the tenth. Plain surface,
real weight underneath, nothing you have to decode before you are allowed to
judge it.

That is the trick this language is after. A script written by a machine and
read by a person at 3am has to be legible immediately and still hold up when
somebody reads it properly the next morning. Terseness was a virtue when a
human typed every character into a serial terminal. It stopped being one when
the writing got cheap and the reading became the whole job.

Making machine-written code reachable by the humans accountable for it is the
entire point. The poet is a better model for that than the shell ever was.

## License

MIT. See [LICENSE](LICENSE). Do what you like with it; keep the copyright
notice.

