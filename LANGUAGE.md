# The frost Language Reference

Version 0.7.0

frost is a scripting language for the jobs shell scripts do, with a grammar
descended from HyperTalk. It exists because the economics changed: scripts are
now written quickly and read carefully, often by someone who did not write them
and is reading at 3am while something is broken. Terseness was the right
optimisation for a serial terminal. It is the wrong one for a code review.

frost is not a login shell. It is an interpreter you point at a file:

```
#!/usr/bin/env frost
```

That choice is deliberate. Because nobody types frost at a prompt forty times a
day, there is no pressure anywhere in the design to shorten anything.

---

## Contents

1. [Design rules](#1-design-rules)
2. [Lexical structure](#2-lexical-structure)
3. [Values](#3-values)
4. [Variables and names](#4-variables-and-names)
5. [Chunk expressions](#5-chunk-expressions)
5a. [Lists](#5a-lists)
5b. [Functions](#5b-functions)
6. [Operators](#6-operators)
7. [Statements](#7-statements)
8. [Running programs](#8-running-programs)
9. [Pipes](#9-pipes)
10. [Failure handling](#10-failure-handling)
10a. [Cleanup](#10a-cleanup)
11. [Files](#11-files)
12. [Handlers](#12-handlers)
12a. [Modules](#12a-modules)
13. [Special values](#13-special-values)
13a. [Secrets](#13a-secrets)
13b. [Talking to the thing that wrote the script](#13b-talking-to-the-thing-that-wrote-the-script)
13c. [Recording and replaying a run](#13c-recording-and-replaying-a-run)
14. [Grammar](#14-grammar)
15. [Deliberate omissions](#15-deliberate-omissions)

---

## 1. Design rules

Four rules explain nearly every decision in the language.

**Values never become syntax.** There is no string interpolation and no
`eval`. Arguments to a program are a list, passed directly to `execve`. A
variable holding `notes.txt; rm -rf *` produces a file with that literal name.
Injection is not mitigated in frost; it is unrepresentable.

**Failure stops the script.** `run` aborts on a non-zero exit unless you wrote
`try to run`. There is no `set -e` to remember, because there is no other mode.

**Pipes fail if any stage fails.** Not just the last one. `bash`'s default here
has probably caused more silent data loss than any other decision in shell
design.

**The keyword vocabulary is small and closed.** This is what makes `error
count` a legal variable name, and it is also the discipline that keeps frost
from pretending to understand English. `put the first line of X into Y` works;
`grab the first line from X` does not, and says so clearly.

---

## 2. Lexical structure

**Line-oriented.** One statement per line. Newlines are significant.

**Comments** run from `--` or `#` to end of line.

```
-- This is a comment.
# So is this.
```

**Line continuation.** A trailing backslash folds the next line in. An argument
list may also wrap after a comma without one.

```
run "rsync" with "--archive", "--verbose", \
    source folder, destination folder
```

**Case-insensitive keywords and names.** `Put`, `put`, and `PUT` are the same
word. Names are normalised to lowercase internally.

**Shebang.** A first line beginning `#!` is ignored.

**String literals** use double quotes and support `\n`, `\t`, `\"`, `\\`.

---

## 3. Values

frost has four value types.

| Type | Examples | Notes |
|---|---|---|
| Text | `"hello"`, `empty` | `empty` is the empty string |
| Number | `42`, `3.14`, `-7` | Integers stay integers |
| Truth | `true`, `false` | |
| List | `the arguments` | Produced by the language, not written literally |

Conversion is automatic and predictable. `"10" is greater than "9"` is true,
because both sides look like numbers, so both are compared as numbers. If
either side does not look like a number, both are compared as text.

Truthiness: `false`, `0`, `""`, `"false"`, and `"no"` are false. Everything
else is true.

---

## 4. Variables and names

A name is one or more ordinary words separated by spaces.

```
put 0 into error count
put "release-1.4" into tag name
put the current folder into working directory
```

This is the reason the keyword list is short. A word is available for use in a
name unless it is structurally load-bearing. Chunk nouns (`line`, `word`,
`item`, `character`) are *not* reserved — `line count` is a perfectly good
variable — because frost recognises chunk expressions by context rather than by
reserving the noun.

Assignment is `put ... into ...`. Reading a name that was never assigned is an
error, not an empty string:

```text
Error at report.frost:12
    12 |     put total cost
       there is no variable named 'total cost'
       hint: assign it first with:  put ... into total cost
```

You can also build text onto an existing variable:

```
put "world" into message
put "hello " before message      -- "hello world"
put "!" after message            -- "hello world!"
```

---

## 5. Chunk expressions

This is the centre of the language. Chunk expressions replace `cut`, `awk
'{print $3}'`, `sed -n '7p'`, `head`, and `tail` with one uniform grammar that
reads the same everywhere.

Four chunk nouns: **character**, **word**, **line**, **item**.

- `word` splits on whitespace
- `line` splits on newlines
- `item` splits on commas, trimming surrounding spaces
- `character` splits into single characters

### By ordinal

```
put the first word of headline
put the third line of report
put the last item of csv row
put the middle word of phrase
put any line of quotes file        -- picks one at random
```

Ordinals run `first` through `tenth`, plus `last`, `middle`, and `any`.

### By number

```
put word 3 of headline
put the line 7 of report
put word -1 of headline            -- negative counts from the end
```

### By range

```
put words 2 to 4 of headline
put lines 10 to 20 of report
put characters 1 to 3 of name      -- "fro"
```

A range is rejoined with the natural separator for its kind: words with spaces,
lines with newlines, items with `", "`.

### Counting

```
put the number of lines in file "access.log"
put the number of words in this sentence
put the length of name             -- characters, or list elements
```

### Nesting

Chunk expressions compose, and this is where they pay for themselves:

```
put the third word of line 7 of file "access.log"
```

The bash equivalent is `sed -n '7p' access.log | awk '{print $3}'`, which
requires knowing two tool dialects. The frost version requires knowing English.

### Records and JSON

A record is an ordered mapping from text keys to values, and JSON is how one
usually arrives. Objects become records, arrays become the lists frost already
has, and the scalars map the obvious way:

```
run "curl" with "-fsS", "https://api.example.com/build" within 30 seconds
put the json of it into build

put the "status" of build
put the "name" of the "author" of build
put item 1 of the "tags" of build
put the "attempt" of build + 1
```

This exists because the alternative was `run "jq" with ".status"`, which puts
a second language in a file whose whole argument is that it needs only one —
and hands the auditor a string it cannot see into. `--explain` could say the
script runs `jq`; it could never say what `jq` was asked for.

Build one field at a time. The first assignment creates the record, so no
declaration is needed:

```
put "green" into the "status" of summary
put 2 into the "failures" of summary
put the json text of summary into file "report.json"
```

`the keys of R` and `the values of R` are lists. A record printed with `put`
prints as JSON, because a record that printed as its type name would send you
straight back to jq to look at your own data.

**Missing keys.** Asking for a key a record does not have yields empty,
exactly as `word 99 of` does, and a field of empty is empty — so
`the "name" of the "user" of build` is safe on a payload with no `user`.
Asking for a field of *text* is an error: that means the value is not the
shape the script thinks it is, and empty would hide the bug while it is still
cheap to find.

**Secrets survive the round trip.** Parsing a sealed value seals every field
it produces, and serialising redacts field by field rather than all at once:

```
put the json of the secret file "credentials.json" into config
put "connecting as" && the "user" of config
-- connecting as «secret credentials.json»
```

### Declaring the shape of a record

A missing key is empty, which is right for an optional field and lethal for a
typo. `the "staus" of build` reads as empty, the comparison against it quietly
goes the wrong way, and nothing anywhere says that `staus` was never a field.

So say what shape you expect:

```
run "curl" with "-fsS", url within 30 seconds
put the json of it into build with fields "status", "number", "author"
```

Two things follow from that one line.

**The names become checkable.** `the "staus" of build` is now a mistake
`--check` catches, with the near miss suggested as a repair, before a single
process starts.

**The payload is verified where it arrives.** If the response does not carry
`number`, the script stops at the line that parsed it and says what was there
instead, rather than failing somewhere further down wearing a different
disguise.

Only names that were declared are checked, and only against a literal key. The
declaration is your claim about the payload, so checking against it is
checking the script against itself. Inferring a shape from whatever JSON
happened to arrive during development would reject correct scripts, so frost
does not do it. A record with no declaration is not second-guessed at all.

A name reassigned without a claim loses its shape, and a shape declared inside
a block does not leak out of it. Field names must be written out: one built at
runtime could not be checked, which is the whole point of declaring it.

### Standard error

`the error output` is what the last command wrote to its standard error, the
same way `it` is what it wrote to standard output and `the result` is its exit
status:

```
try to run "curl" with "-fsS", url within 30 seconds
if the result is not 0 then
    put "curl failed:" && the error output into standard error
    quit with status 1
end if
```

Without this the only way to see why something failed was
`run "sh" with "-c", "... 2>&1"` — which reintroduces the shell frost exists
to avoid, and which the auditor flags. Needing an error message should not
require defeating the language's main guarantee.

A command's standard error is still written through to the terminal as it
happens, so a failure is never silent whether or not anything reads it. In a
`pipe`, `the error output` is the last stage's; the earlier stages write
straight to the terminal.

### Time and waiting

```
put the current date          -- 2026-08-10
put the current time          -- 14:23:05
put the current timestamp     -- 2026-08-10T14:23:05Z   (UTC)
put the current seconds       -- epoch seconds, for measuring a duration

wait 3 seconds
wait 500 milliseconds
```

The unit is required, for the same reason `within` requires one: a bare `3`
means seconds to one reader and milliseconds to another.

Both are recorded. `--replay` serves back the clock reading that was recorded
rather than reading the clock again — a fixture whose timestamps move on every
replay is a diff generator, not a fixture — and it does not actually sleep, so
replaying a script that backs off for thirty seconds is no slower than
replaying anything else.

A script that waits says so in `--explain`, under `Waits:`. It is not a
capability, since it touches nothing, but a reviewer approving a CI job wants
to know it sleeps for ten minutes.

### The identity of a run

```
put "starting" && the run id
run "curl" with "-H", ("Idempotency-Key: " & the run id), url within 30 seconds
put "build/" & the run id & "/out.txt" into scratch
```

A script run by an agent or a pipeline is never asked "what happened" in the
abstract. It is asked what *that* run did: the one in the incident, the one
whose fixture is on disk, the one an API saw a duplicate request from. So each
execution has an id.

`--run-id ID` sets it, otherwise `FROST_RUN_ID`, otherwise frost generates a
UUID. An id from outside always wins, because joining frost's record to the
pipeline's is the whole point and a job id is more useful than anything frost
could invent.

It reaches four places. The recording carries it at the top level, so a
fixture can be joined to an audit log without being parsed. The trace opens
with it. Every child process inherits it as `FROST_RUN_ID`. And the script can
read it, which is what makes it usable as an idempotency key or as a scratch
path that cannot collide with a concurrent run.

A replay reports the id of the run it is replaying, not a fresh one, for the
same reason it serves the recorded clock: a fixture that changed on every
replay would not be a fixture.

Ids are checked rather than trusted: letters, digits, dot, colon, dash and
underscore, up to 128 characters. The value ends up in log lines, in child
environments and in any path a script builds from it, so a newline in it would
forge a log entry and a slash would move a file. That is the ordinary shape of
trusting text from somewhere else, which is the thing this language exists to
refuse.

### Watching a run, and auditing one

Two different questions, and it is worth reaching for the right one.

**What did it do?** `--record run.json` writes down every effect: each command
with its arguments, standard input, output and exit status, every file read or
written, every environment variable, every clock reading. Secrets are never
recorded. That is the auditable artefact, and it is a replayable fixture as
well.

The recording is written **however the run ends**. A run that failed, was
interrupted or wedged is exactly the one somebody needs to read afterwards,
and for a while frost was throwing precisely those away.

**What did it execute?** `--trace` prints each statement as it runs, and
`--trace-to-file FILE` puts that somewhere you can read later:

```text
[frost]    1  put 0 into error count
[frost]    2  repeat for each line in the standard input as row
[frost]    3      if row contains "ERROR" then add 1 to error count
```

A recording cannot answer this. It holds effects, so a script that took the
wrong branch and therefore did nothing produces an empty recording and no
explanation. The trace shows the condition being evaluated and the branch not
taken.

The trace is flushed line by line, because the run worth tracing is often the
one that never finishes and a buffered trace of a wedged script is an empty
file. It prints source text and never runtime values, so a credential cannot
reach it.

### Rules about where a script reaches

```policy
forbid reaching "*.telemetry.example"   -- no third-party reporting from here
require reaching only "api.github.com", "*.internal"
```

Checked against the text, before anything runs. That is possible because the
analyser reads a host out of a joined URL when the literal closes the
authority — `"https://api.github.com/repos/" & repo` reaches
`api.github.com`, and nothing after the slash can move it — and because a name
whose definitions are all literals contributes every one of them, so a branch
choosing between two hosts is two known hosts rather than an unknown one.

A destination that genuinely cannot be read fails both rules. "Cannot be shown
to be allowed" is not "is allowed", and an allow-list that quietly passed the
one case nobody can check would be worth nothing.

### Checking the destination that only exists at runtime

An allow-list refuses a destination it cannot read, which is right and makes
the rule unusable for any URL built at runtime: a script that fetches a URL
from its input can never satisfy `require reaching only`, so people delete the
allow-list rather than the dynamic URL.

`--enforce-hosts` moves the check to where the value is concrete:

```bash
frost --enforce-hosts --policy prod.policy fetch.frost
```

The host is read out of the command's real arguments in the moment before it
spawns, and a destination the policy does not allow is refused there. With the
flag on, a statically unknowable destination is no longer refused up front,
because it will be judged when it exists. A network command whose destination
frost still cannot read at that point is refused, exactly as the static check
does: cannot be shown to be on the list is not the same as is.

**frost holds this, not the kernel.** A program that ignores its arguments and
dials out on its own is untouched by it. It closes the gap between a policy
that could only refuse dynamic URLs and one that can permit the right ones; it
does not become a boundary by doing so.

### Writing the rules for the thing that can enforce it

```bash
frost --egress-rules squid --policy prod.policy > frost.acl
```

The allow-list a policy states, as configuration for a forward proxy. Both
come from one file, so the list review reads and the list that actually holds
cannot drift, which is the failure that makes host policies decorative.

A hostname allow-list is not something a packet filter can express: nftables
and iptables match addresses, resolutions change, one address serves many
names. Emitting an nftables ruleset from hostnames would produce a file that
looks like enforcement and is wrong the first time a CDN rotates. A proxy sees
the name, which is the layer where the rule can be stated exactly. `--egress-rules list`
gives one host per line for whatever configuration already exists.

**This is not the sandbox.** `sandbox may reach "api.github.com"` is still a
parse error: macOS filters on addresses rather than names, and a Linux network
namespace has no middle setting, so a per-host boundary is not something the
kernel here can hold. A policy bounds what the *text* can reach; the sandbox
bounds what the *process* can reach, all or nothing. Keeping those apart
matters, because the second is the stronger guarantee and the first is the
more precise one, and pretending either is the other would be a lie in
whichever direction someone relied on it.

### Code that cannot run, and code nobody uses

A script written by a machine has a shape. Invented helpers, a branch after a
`return`, a value computed and dropped: each is harmless on its own, and
together they are the clearest sign that what is on the page is not what
anybody intended.

```text
[caution] line 9   Code after the script has already stopped
[note   ] line 1   The handler 'helper' is never called
[note   ] line 5   'error count' is set and never read
```

Three separate questions, all decidable from the text. **Unreachable**:
statements after a `quit`, `return`, `exit repeat` or `next repeat` in the
same block, reported once per block rather than once per line, because a run
of ten dead lines is one mistake. **Never called**: a handler used nowhere in
the program — across an import, not within one file, since defined here and
called there is the normal shape of a module. **Never read**: a name assigned
and never used, where reading it by any route counts, including `add 1 to n`,
which reads the value before writing it.

These are notes rather than dangers, except the unreachable one. Dead code is
a smell, not a hazard, and a verdict that shouted about an unused variable
would be a verdict people stop reading. A policy can still refuse it:
`require at most 0 dead code`.

### Rules about the environment

```policy
forbid reading the environment "AWS_*"       -- credentials come from the keystore
require reading only the environment "PATH", "HOME", "CI"
```

Setting a variable already had a rule and reading one did not, which is the
wrong way round: what a script *takes* from the environment is where the
credentials are. Both forms exist now, and they are separate from
`forbid setting`, because a policy that looked like it covered reads while
only covering writes would be worse than one that plainly does not.

A variable named at runtime fails an allow-list closed, on the same rule as
everywhere else: cannot be shown to be allowed is not allowed.

### Loops that cannot end, and runs that will not

`within 30 seconds` bounds one command, and a policy can bound how many
commands there are. Neither touches a loop doing arithmetic, which spawns
nothing, reads nothing and writes nothing. It has no capabilities at all, so
the manifest had nothing to report and reported it approvingly:

```text
This script does nothing observable.

Verdict: clean
```

That script never terminates. The cheapest way for a generated script to wedge
a runner was the one thing frost called harmless.

**A loop with no way out is now a finding.** `repeat forever`, `repeat while
true` and `repeat until false` are checked for anything in the body that could
end them: an `exit repeat`, a `quit`, a `return`.

```text
[DANGER ] line 2  A loop that cannot end (repeat forever)
```

Presence counts, not reachability. An `exit repeat` behind a condition that
never fires still counts, which understates the problem and never overstates
it. That is the right way round for a check which would otherwise flag working
code and be switched off. A condition that is not literally `true` is left
alone, because `repeat while n is less than 10` may well terminate and
guessing is how a check earns a reputation for crying wolf.

**A budget bounds the whole run.**

```bash
frost --deadline 300 deploy.frost
```

```policy
require the run to finish within 5 minutes
```

The run stops when the budget is spent, exiting 124 — what a shell reports for
a timeout, and what frost already returns when a single command runs too long.
The same answer to the same question at a different scale.

It is raised rather than killed, so `ensure` blocks still run. A deadline that
skipped cleanup would leave exactly the mess it was meant to bound. The
tightest budget wins, so a flag cannot widen what a site policy imposed.

### Telling a monitoring system what happened

```bash
frost --events run.ndjson deploy.frost      # or - for standard error
```

One JSON object per line, flushed as things happen. NDJSON is what Splunk's
HTTP collector, New Relic's log API, Datadog, Vector and Fluent Bit all ingest
without a translator, and a line-oriented file survives a run that is killed
halfway, which a single JSON document does not.

Every event shares an envelope: `ts`, `run`, `script`, `seq`, `event`. A
collector routes on `event` and groups on `run` without knowing anything else
about frost.

```json
{"event": "run.start",      "declares": {"programs": ["curl"], "hosts": ["x.example"]}}
{"event": "command.start",  "program": "curl", "argv": ["curl", "-fsS", "..."]}
{"event": "command.finish", "program": "curl", "status": 0, "seconds": 0.412}
{"event": "run.finish",     "status": 0, "commands": 3, "waited_seconds": 2.0,
 "programs_unused": ["psql"], "hosts_unused": ["db.internal"]}
```

**The resolution worth having is the pairing.** Any tool can log that a
command ran. frost knows what the script was *allowed* to do before it ran,
what a person *approved*, and what the host *permits*, so the finish event can
say which approved capabilities went unused. A script approved for six
programs that uses two is an approval that should be tightened, and that is
only visible holding the manifest and the run side by side.

Commands are timed, and the run separates time spent working from time spent
waiting, so a slow job can be attributed rather than guessed at.

Contents are never emitted, sizes are: "wrote 4kb" is useful and the 4kb is
not. Secrets are redacted before an event is written, including in a command's
arguments, because telemetry leaves the building more often than a recording
does.

**Traces, where a dashboard wants them.**

```bash
frost --events run.json --events-format otel deploy.frost
```

OTLP/JSON, which New Relic and Datadog read natively: a root span for the run
and a child span per command, so a slow job renders as a flame graph rather
than a table. The instrumentation already existed, because a command has a
start, an end and a status, which is a span with the labels changed.

NDJSON stays the default and the trade-off is worth knowing rather than
discovering. OTLP is a batch format, so the document is written when the run
ends: a run killed hard leaves nothing, where NDJSON would have left every
line up to the moment it died.

Trace ids are derived from the run id, so a replay of a recording produces the
trace id of the run it replays. Same reason the clock is recorded: a fixture
whose identity moves is not one.

A refusal is an attribute on the root span rather than an error status by
itself, because a monitoring system that cannot tell a refused run from a
broken one will page for the wrong one.

**A refusal is an event.** The one a security team most wants, and the one
that is easiest to lose: a policy refusal, a breached import ceiling, an
approval that no longer covers the script, an unusable signature, a secret the
role may not read. Each closes the run out with what fired and which policy it
came from, so a dashboard counting starts against finishes does not drift every
time a policy does its job.

```json
{"event": "run.finish", "status": 3, "refused": "policy",
 "rules": [{"what": "running \"curl\"", "line": 1,
            "hint": "egress goes through the proxy"}],
 "policies": [{"path": "/etc/frost/policy.d/00-egress.policy",
               "sha256": "05cd8a0c7c8f...", "origin": "site"}]}
```

Analysis produces no run events. `--explain` runs nothing, so there is no run
to report on and a dashboard should not see one.

It composes with the other modes. `--events` alongside `--record` gives both,
and a replayed run is marked `"replayed": true` so a dashboard does not count
a fixture as production traffic.

### Policy the machine brings

Rules in `/etc/frost/policy.d/*.policy` apply to every run on that host,
whether or not anyone passed `--policy`. A policy beside the script is
controlled by whoever writes the script, which is right for a project's own
rules and useless as a datacenter control: the thing being constrained should
not be holding the constraint.

Site rules are **added** to the project's, never replaced by them. That the
result can only get stricter is not a special case, it falls out of how policy
works: every rule is checked independently and all must pass, so two
`require reaching only` lists compose as an intersection and there is no
syntax that removes a rule.

There is deliberately no variable meaning "use this policy instead". A knob
that relaxes a host rule is a bypass with a friendly name, and the first thing
anyone does with a failing build is look for one.
`FROST_SITE_POLICY_DIR` only *adds* a directory, for a container or a test
without a writable `/etc`.

A site policy that is present and unreadable is a refusal, not a shrug.

Every policy that was applied is named by path and digest in `--explain` and
in any recording:

```text
Governed by:
  /etc/frost/policy.d/00-egress.policy  05cd8a0c7c8f  (site)
  deploy.policy                         9f2a1b40c7de  (project)
```

Without that, an audit can show a policy existed and never that this run was
subject to it, which is a claim about a control rather than a control.

### Runs nobody is watching

```bash
frost --automated deploy.frost        # or FROST_AUTOMATED=1
```

An automated run refuses `--approve` and `--ignore-approval`. A loop that can
approve is a loop that approves its own capability escalation, and the failure
is mundane rather than exotic: an agent hits
`REFUSED: it can now reach telemetry.example`, and the most helpful-looking
next step in its search is to re-approve. Everything else works normally, so a
repair loop can fix syntax and can never fix its way past the gate.

### Approvals somebody is accountable for

An unsigned approval says *that* something was approved. It does not say who
approved it or what they were looking at, and anything that can write the file
can grant itself the approval.

```bash
frost --new-approver-key ~/.frost/keys/alice
frost --approve --sign-with ~/.frost/keys/alice --approver alice \
      --commit $GITHUB_SHA deploy.frost
```

```policy
require an approval signed by "kA1b2c...", "kZ9y8x..."
```

The signature covers the capability set, the script, the commit, and the
approver's own name and key, so it cannot be lifted onto somebody else. An
approval signed by a key the policy does not name is refused, and so is an
unsigned one.

Making signatures needs the `keystore` extra. **Verifying never degrades**: if
a policy demands signed approvals and the library is missing, the answer is a
refusal. An unverifiable signature is not a valid one.

The commit is recorded and not checked here, because frost cannot know which
revision is being deployed, only which one the approver said they read.
Comparing it against the checkout belongs in the pipeline that did the
checkout.

### Approving what a script may do

`--frozen` asks whether a script is byte-identical to the one that was
reviewed. That is the right question for a vendored module and the wrong one
for a script a model regenerates: every regeneration trips it, so you re-lock
every time, and re-locking every time means the check has stopped telling you
anything.

`--approve` asks the question that survives regeneration — did it get *more
powerful*?

```bash
frost --approve deploy.frost
```

That writes `deploy.frost.approved`, a record of every capability the script
has today: the programs it runs, **the hosts it reaches**, the files it reads,
writes and deletes, the
environment it touches, the secrets it reads and where they are released, and
how many names it builds at runtime. No line numbers, so moving a comment does
not move the baseline.

Once that file exists, **it binds by default**. No flag is needed:

```bash
frost deploy.frost
```

runs it only if nothing widened:

```text
REFUSED: it can now run curl
REFUSED: it can now read the secret ~/.aws/credentials (from the file)
REFUSED: it can now let a secret leave the process as an argument to curl

3 capability change(s) since deploy.frost.approved; it was not run.
  Read what changed, then re-approve with --approve.
```

Binding by default is the difference between a guard and a suggestion. While
it was opt-in, a poisoned agent did not have to defeat it — it just left the
flag off, and in most agent loops the agent is the thing composing the command
line. Skipping it now takes `--ignore-approval`: still possible, but a choice
someone made and a reviewer can see, rather than what happens when nobody
types anything. Deleting the approval file works too, and shows up in a diff.

`--as-approved` keeps a stronger meaning for CI: not *honour an approval if
there is one* but *there had better be one*, refusing with exit 2 when none
exists. `--check`, `--explain` and `--format` are never blocked — they are how
you review the change, and blocking them would push people straight to
`--ignore-approval`.

A capability that *disappears* is not reported and never refuses. The
asymmetry is the point: a script that stops touching the network needs no
ceremony, and one that starts needs a person. Re-approving prints what changed,
so the moment of approval has something in it to read.

The baseline covers imported modules too — a capability that arrives through
an import is still a capability, and measuring only the entry file would make
"add an import" the way around this.

**What it is for.** Not injection: frost already stops a value becoming
syntax. This is for the case where an agent reads something hostile and writes
perfectly valid frost obeying it. The script parses, formats canonically and
passes `--check`; the model was never confused about syntax, it was persuaded
to use authority it legitimately holds. A policy file answers that properly,
by being written by a person ahead of time — but a policy has to be written,
and a baseline needs no rules at all. It compares against the reviewer's own
past judgement instead of a security model they had to author.

Destinations are recorded, not just program names. Without that,
`curl https://api.github.com` and `curl https://telemetry.example` are the same
capability — and a persuaded model does not need a new program, only a new
destination. The host is taken from literal arguments only: a scheme, or an
scp-style `user@host:path`. A bare `example.com` is indistinguishable from a
filename and is not guessed at, and a network command whose destination is not
a literal is recorded as unknowable rather than omitted.

**What it is not.** A capability bound is not an intent check. A script
allowed to run `git` can still push to the wrong remote, and a baseline that
already includes `curl` and a readable config file will not object to one
being sent to the other. This bounds what a script can reach, never whether
reaching it was wise.

### Streaming input

`repeat for each line in the standard input` consumes lines as they arrive
rather than reading the whole stream first, so a filter works against a
producer that never ends:

```
repeat for each line in the standard input as row
    if row contains "ERROR" then put the uppercase row
    if the number of characters in row is 0 then exit repeat
end repeat
```

`exit repeat` gets out of an unbounded stream and leaves the rest in the pipe
for whoever reads next. Asking for `the standard input` as a value still
reads what remains, all at once.

### Folders, padding, durations and sorting by a key

```
if "build" is a folder then put "already there"

put "[" & the padded name to 14 & "]"          -- left aligned
put "[" & the padded count to 6 on the left & "]"   -- right aligned, for numbers

put "took" && the duration of 3725             -- 1 hour 2 minutes 5 seconds

put the sorted (the lines of it) by the second word of each
```

`the padded X to N` never truncates: a width is a minimum, and silently
cutting a value to make a column line up loses the thing the column was for.

`the sorted X by KEY` evaluates KEY once per item with `each` bound to that
item, and orders numerically when every key is a number, so a column of counts
does not put 10 before 9. `each` means nothing outside a sort key and says so.

`the duration of N` is for reports and reads as a person would say it. It is
not the formatter used in timeout messages, which says "90 seconds" because
that is what the author wrote.

### Out of range

Asking for a chunk that does not exist yields empty text, not an error. This
matches HyperTalk and avoids a class of defensive length checks.

```
put word 99 of "short phrase"      -- empty, not a crash
```

---

## 5a. Lists

A plural chunk noun with no index is the whole set, as a list. Splitting is
therefore not a new feature; it is the grammar already in section 5, read the
other way.

```
put the words of "alpha beta gamma" into names
put the lines of report into rows
put the items of "a,b,c" into fields
put the characters of "abc" into letters
```

A list keeps its elements separate, which comma-delimited text cannot: an
element may itself contain a comma.

```
put the empty list into people
put "Smith, John" after people
put "Doe, Jane" after people
put the number of items in people        -- 2
put item 1 of people                     -- Smith, John
```

`after` appends and `before` prepends when the target is a list; on text they
still join, as in section 7.

### Any delimiter

The chunk nouns cover whitespace, newlines and commas. For anything else:

```
put "root:x:0:0:root:/root:/bin/bash" into entry
put item 1 of (entry split by ":")       -- root
```

and back again:

```
put the words of "a b c" joined by ", "  -- a, b, c
```

`split by ""` is an error rather than a silent character split, because
`the characters of X` already says that and says it more clearly.

### Ordering

```
put the sorted names into ordered
put the reversed names into backwards
put the unique names into distinct
```

`the sorted X` compares numerically when every element is a number, and
alphabetically otherwise. Sorting `10` before `9` is never what a counter
meant. None of the three modifies the original.

---

## 5b. Functions

Text, applied with the article:

| Expression | Result |
|---|---|
| `the uppercase X` | `X` in capitals |
| `the lowercase X` | `X` in lower case |
| `the trimmed X` | `X` without surrounding whitespace |

Numbers:

| Expression | Result |
|---|---|
| `the rounded X` | nearest whole number |
| `the absolute X` | magnitude, without the sign |
| `the sum of X` | total of a list of numbers |
| `the largest of X` / `the smallest of X` | extremes |
| `the average of X` | mean |

```
if the lowercase target is "production" then
    put "deploying to production"
end if

put the sum of the words of "1 2 3 4"    -- 10
```

An aggregate of an empty list is an error, not zero: the sum of nothing is a
question with no answer, and returning one would hide the empty list.

These are recognised only after `the`, so they cost nothing from the name
vocabulary. `sorted count` and `average total` remain perfectly good variable
names.

---

## 6. Operators

### Text

| Operator | Meaning | Example | Result |
|---|---|---|---|
| `&` | join | `"a" & "b"` | `ab` |
| `&&` | join with a space | `"a" && "b"` | `a b` |

### Arithmetic

`+`, `-`, `*`, `/`, `^`, with conventional precedence. Parentheses group.
Division by zero is an error.

### Comparison

Both word forms and symbols are accepted. The word forms are preferred in
scripts; the symbols exist so that dense arithmetic conditions stay readable.

| Word form | Symbol |
|---|---|
| `is` | `=` |
| `is not` | `!=` |
| `is greater than` | `>` |
| `is less than` | `<` |
| `is at least` | `>=` |
| `is at most` | `<=` |

Additional text comparisons, which have no symbol form:

```
if name contains "test" then put "looks like a test"
if path starts with "/usr" then put "system path"
if filename ends with ".tmp" then delete file filename
if answer is empty then quit with status 1
if branch name is in allowed branches then put "allowed"
if filename is like "*.tmp" then put "temporary"
if line matches "^\d+$" then put "all digits"
```

The article is optional on ordinal chunks, so `first word of line` and `the
first word of line` are the same expression. Prefer the article in scripts; it
reads better in a sentence.

### Logic

`and`, `or`, `not`. Short-circuiting.

---

## 6a. Patterns

Two forms, because the two jobs are different.

### Globs — `is like`

For filename-shaped matching, where regex is overkill and unreadable:

```
if filename is like "*.tmp" then delete file filename
if name is not like "test_*" then put "not a test"
if log is like "log-????-??.txt" then put "dated log"
```

`*` matches any run of characters, `?` matches one, `[abc]` matches a set.
Case-sensitive.

### Regular expressions — `matches`

For everything else. frost does not pretend regex is readable; it makes it
explicit instead, so a reader knows to slow down at exactly the places that
deserve it.

```
if request matches "^(\S+) (\w+) (\d+)$" then
    put match 1 into client address
    put match 2 into method
    put the last match into status code
end if
```

A successful `matches` records its capture groups, which are then addressed
with the same chunk grammar as everything else:

| Expression | Meaning |
|---|---|
| `match 1`, `match 2` | Capture group by number |
| `the first match`, `the last match` | Capture group by ordinal |
| `the number of matches` | How many groups captured |
| `the whole match` | The entire matched text |

Groups persist until the next `matches`. A failed match clears them, so
`the number of matches` is `0` after a miss rather than stale. A group that
did not participate in the match reads as empty, never as missing.

Patterns are searched, not anchored. Use `^` and `$` when you mean the whole
subject. Syntax is standard regular expressions; an invalid pattern is a clear
runtime error naming the problem.

### Finding every occurrence

```
put every match of "\d+" in request into numbers
put the number of items in numbers
put item 1 of numbers
```

Returns a list of the matched text, not of groups.

### Replacing

```
put "2026-08-09" into date text
replace "(\d+)-(\d+)-(\d+)" with "\3/\2/\1" in date text
-- date text is now "09/08/2026"
```

`replace` edits a variable in place and replaces every occurrence.
Backreferences are `\1`, `\2`, and so on.

### A note on escapes

`\n`, `\t`, `\r`, `\0`, `\"` and `\\` are translated in string literals.
Every other backslash is preserved, so `"\d+"` reaches the pattern engine
intact and needs no doubling.

---

## 7. Statements

### put

```
put EXPRESSION                          -- writes to standard output
put EXPRESSION into NAME
put EXPRESSION into standard error
put EXPRESSION into standard output
put EXPRESSION into file "path.txt"     -- overwrites
put EXPRESSION after file "log.txt"     -- appends
put EXPRESSION before NAME              -- prepends to a variable
```

### if

```text
if COND then
    ...
else if COND then
    ...
else
    ...
end if
```

A single-line form exists for guards:

```
if attempts is 0 then quit with status 1
```

### repeat

Five forms, all closed by `end repeat`.

```text
repeat 3 times
    ...
end repeat

repeat with counter from 1 to 10
    ...
end repeat

repeat with counter from 10 to 1 by -1
    ...
end repeat

repeat for each line in file "hosts.txt" as this host
    ...
end repeat

repeat while queue depth is greater than 0
    ...
end repeat

repeat until file "ready.flag" exists
    ...
end repeat

repeat forever
    ...
end repeat
```

`exit repeat` leaves the loop. `next repeat` starts the next pass.

### Arithmetic statements

```
add 1 to error count
subtract 5 from budget
multiply 2 into scale factor
divide 3 into portion size
```

### quit

```
quit                    -- status 0
quit with status 1
```

---

## 8. Running programs

```
run "git"
run "git" with "status", "--short"
run "cp" with source path, destination path
```

The program name comes first. Every argument is a separate expression in the
`with` list. **The list is passed straight to the operating system.** Nothing is
re-parsed, so nothing needs quoting or escaping:

```
put "my report (final).pdf" into filename
run "cp" with filename, "/backup"
```

That works. There is no shell in the middle to be confused by the spaces or the
parentheses.

Writing a whole command line as one string is a syntax error, caught at parse
time with a suggested fix:

```text
Syntax error at build.frost:4
     4 | run "ls -la"
       run takes a program name, not a command line
       hint: did you mean:  run "ls" with "-la"
```

### Timeouts

Any `run` may carry a deadline:

```
run "curl" with "--silent", endpoint within 30 seconds
run "make" with "test" within 10 minutes
try to run "ping" with "-c", "1", host within 500 milliseconds
```

Units are required — `within 5` is a syntax error, because a bare number could
mean seconds or milliseconds and the reader should not have to guess. Accepted
units: `milliseconds` (or `ms`), `seconds`, `minutes`, `hours`, singular or
plural.

When the deadline passes the child is killed. A plain `run` then aborts the
script; a `try to run` sets `the result` to **124**, the same status GNU
`timeout` uses, and leaves any partial output in `it`.

Pipes take a deadline for the whole chain, not per stage:

```
try to pipe within 1 minute
    run "find" with "/", "-name", "*.log"
    run "xargs" with "wc", "-l"
end pipe

if the result is 124 then
    put "search took too long" into standard error
end if
```

Putting `within` on an individual stage is a syntax error that says so. When a
pipe times out, every stage is killed and reaped — no orphans.

### Output

`run` captures standard output into `it`, with the trailing newline removed.
Standard error passes straight through to the terminal. The exit code lands in
`the result`.

```
run "git" with "rev-parse", "--short", "HEAD"
put "building revision" && it
```

To show a program's output, put it:

```
run "df" with "-h"
put it
```

### Input

`reading` places text on a program's standard input. It is the heredoc, without
a quoting dialect.

```
run "sort" reading names
run "wc" with "-l" reading log text
```

The text is data all the way down, exactly as arguments are: it is written to
the child's stdin and never re-parsed as anything.

### Where a program runs

```
run "make" with "all" in folder build path
```

The folder applies to that command only, so a reader need not track a mutable
working directory while reading the rest of the script. To move the script
itself, assign to `the current folder` — see section 11.

### Watching a program work

`run` captures output, which means a ten-minute build shows nothing until it
finishes and an interactive program cannot work at all. `showing output` hands
the terminal to the child instead:

```
run "make" with "all" showing output within 30 minutes
```

Nothing is captured, so `it` is empty afterwards rather than stale. `the
result` still reports the exit status, and failure still aborts.

These clauses may appear in any order, and each may appear once.

---

## 9. Pipes

A pipe is a block, not a connective. English has no graceful N-ary connective
for this, so frost does not invent one — position carries the meaning.

```
pipe
    run "cat" with "access.log"
    run "grep" with "ERROR"
    run "sort"
    run "uniq" with "-c"
end pipe

put the number of lines in it into distinct errors
```

Advantages over `a | b | c | d` that matter in review: it reads top to bottom,
it survives ten stages without becoming a wall, a diff shows exactly which stage
changed, and you can comment one stage.

Every stage must be a `run`. A pipe needs at least two stages.

**Pipes fail if any stage fails**, and `the result` reports the first failure.
This is `set -o pipefail` with no way to turn it off.

```
try to pipe
    run "cat" with "missing.log"
    run "wc" with "-l"
end pipe

put the result       -- 1, not 0
```

---

## 10. Failure handling

Plain `run` and `pipe` mean *this must succeed*. If it does not, the script
stops and reports the line:

```text
Error at deploy.frost:18
    18 | run "make" with "test"
       'make' failed with status 2
       hint: if this failure is expected, write 'try to run ...' and check 'the result'
```

`try to` means *I will handle this myself*, and is the only case where checking
`the result` is meaningful:

```
try to run "ping" with "-c", "1", host name
if the result is not 0 then
    put host name && "is unreachable" into standard error
    quit with status 1
end if
```

The safe path is shorter to write than the risky one, and the risky one
announces itself in the source. A reviewer looking for unchecked failures greps
for `try to`.

---

## 10a. Cleanup

Failure aborts the script. That is the right default, and on its own it leaks
every lock file and temporary directory the script had taken.

`ensure` registers a block when execution reaches it. Registered blocks run
when the script ends — normally, on error, on `quit`, or on interrupt — most
recent first, so cleanup unwinds in the reverse of the order things were
acquired.

```
put "/tmp/deploy.lock" into lock path
put "held" into file (lock path)

ensure
    delete file (lock path)
end ensure

run "make" with "deploy"
```

The lock is released whether `make` succeeds or not.

A block that is never reached is never registered, so a lock taken inside a
branch is only released if it was actually taken. A failure inside a cleanup
block is reported on standard error and does not stop the other blocks, and it
never replaces the error that ended the script — that error is the one the
reader needs first.

---

## 11. Files

Reading a file is an expression:

```
put file "config.txt" into settings
put the first line of file "VERSION"
```

If the path is in a variable, parenthesise it — this is how frost tells
`file "x"` from a variable named `file path`:

```
put item 1 of the arguments into log path
put the number of lines in file (log path)
```

Writing and appending are `put` targets:

```
put report text into file "report.txt"
put "another line" after file "report.txt"
```

Testing and removing:

```
if file "lock.pid" exists then
    quit with status 1
end if

delete file "lock.pid"
```

Missing files are a clear error, never silent empty text.

Relative paths resolve against the working directory. `~` expands.

### The working folder and the environment

`the current folder` and `the environment variable "NAME"` are readable, and
they are writable by the same `put ... into` that writes everything else. There
is no separate `set` keyword.

```
put "/tmp/build" into the current folder
put "clang" into the environment variable "CC"
put ":/opt/bin" after the environment variable "PATH"
```

The environment is the script's own copy. A frost script cannot quietly rewrite
the environment of whatever ran it, but every program it starts inherits the
changes. `--explain` lists both, and a policy can forbid them, because
otherwise setting `PATH` would be a way around every rule about which programs
may run.

---

## 12. Handlers

A handler is a named block of statements.

```
to warn about with subject, detail
    put "WARNING:" && subject & " — " & detail into standard error
end warn about

warn about with "disk", "92% full"
```

The handler name may be several words. `end` must repeat the name — a rule that
costs one word and makes long scripts navigable.

Handlers may return a value, which arrives in `it`:

```
to short revision
    run "git" with "rev-parse", "--short", "HEAD"
    return it
end short revision

short revision
put "at revision" && it
```

Parameters are local. Handlers do not see the caller's variables, and variables
assigned inside a handler do not leak out. Argument count is checked at the
call.

### Reaching a global

A handler can read a global directly, but a plain `put` inside one creates a
local — even when a global of that name exists. To write through, say so:

```
put 0 into error total

to record with status code
    if status code is not "200" then
        add 1 to the global error total
    end if
end record
```

`the global NAME` works for `put`, `before`, `after`, the arithmetic
statements, and `replace`. It also reads past a local of the same name. Writing
it at the point of the write, rather than declaring it at the top of the
handler, means a reader never has to carry the declaration in their head.

`global` is a reserved word, so `put 5 into global total` is an error rather
than a local named `global total`.

### Calling a handler in an expression

A handler used with `the ... of ...` returns its value into the expression:

```
to double with n
    return n * 2
end double

put the double of 5 + the double of 10       -- 30
```

An argument binds tightly, exactly as a chunk source does: `the double of n - 1`
is `(the double of n) - 1`. Parenthesise when you mean otherwise.

A handler taking no arguments is called without `of`:

```
to short revision
    run "git" with "rev-parse", "--short", "HEAD"
    return it
end short revision

put "at revision" && the short revision
```

The expression form does not touch `it`; the statement form still lands there.
An expression buried inside another expression must not quietly replace the
last command's output.

A built-in property always wins: a handler named `length` does not shadow
`the length of X`. An unknown name is reported when the script is parsed, not
when the line runs, so `--check` catches a typo in a branch that rarely
executes.

---

## 12a. Modules

Everything frost is worth using for rests on one invariant: **the tree you
audit is the program you run, and the audit sees all of it.** A module system
is the feature most likely to break that. If a module could contribute a
`run` that `--explain` does not print, frost would be worse than bash — bash
never claimed to have audited anything.

So the design goal is not *safe modules*. It is **modules that cannot put
capability outside the manifest**, and every rule below is chosen for that.

```
use "lib/db.frost" for the connect, the migrate which may run "psql"
use "lib/text.frost" for the shout
```

### A module is declarations only

A module file may contain handler definitions and `use`, and nothing else. A
top-level statement in one is refused:

```text
Module error at deploy.frost
       a module may only define handlers, and lib/bad.frost has a statement
       that would run when it is imported
       hint: move it into a handler.
```

Import-time side effects are the most abused feature of every module system
ever shipped — Python's `__init__.py`, npm's `postinstall` — and they turn
`use` into *run this file*. Refusing them means `use` can never do anything,
which is what makes it safe to read the whole graph before deciding anything.

### The path is written out in full

`use (module name)` is a syntax error, not a runtime one. A computed import
is `eval` wearing a hat: it would put the import graph out of reach of the
static analysis that every other guarantee in this document depends on.

### Resolution is relative, and bounded

A path resolves relative to the file that imports it. There is no search
path, no environment variable, no absolute paths, nothing above the entry
script's own directory, no network and no registry. One path string resolves
to one file, the same way on every machine.

The boundary is the entry script's directory, not the repository root, so a
script in `tools/` cannot reach a sibling `lib/`. That is restrictive on
purpose: the directory a reviewer opens is the directory the program lives
in. Vendoring is the feature — if a module has to live with the script, then
the review that covered the repository covered the module.

### Imports are explicit, and the graph is a DAG

`for the connect, the migrate` names exactly what arrives. Only those names
are in scope; the module's other handlers are not. Two imports bringing in
the same name is an error, as is an import shadowing a local handler — with
a single flat table one would silently replace the other, which is a hijack
rather than a hygiene problem.

Cycles are refused rather than resolved, and the whole closure is read
exactly once: resolve, read, hash, parse, audit and run all come from the
same bytes.

### Names resolve in the file that defines the code

A handler defined in a module calls its own file's handlers, whether or not
the entry script imported them:

```
-- lib/a.frost
to inner
    return "the module's own"
end inner

to outer
    return the inner
end outer
```

```
use "lib/a.frost" for the outer
to inner
    return "the entry script's"
end inner

put the outer          -- the module's own
put the inner          -- the entry script's
```

### The manifest covers the closure

`--explain` audits every file, attributes each capability to the file it came
from, and names the import it arrived through:

```text
lib/db.frost   (imported by deploy.frost:1)
  Runs these programs:
    psql  — line 2  (no timeout)
```

A module's handlers are audited whether or not anything calls them, which is
the sound direction. An unresolvable module fails closed — exit 2 and no
manifest at all, because a manifest with a hole in it is the one output that
would actively mislead a reviewer.

### The ceiling at the import site

This is the part that makes single-file review survive multi-file code. A
module defaults to **no capabilities** — pure computation, chunk expressions
and the string and number functions. Widen it explicitly:

```
use "lib/db.frost" for the connect which may run "psql", "pg_dump"
use "lib/net.frost" for the fetch which may run "curl" and write "/tmp/*"
```

If the module does more than its import allows, the program is refused before
anything runs:

```text
REFUSED: lib/sneaky.frost may not run curl
  The module runs it, but the import does not allow it. The import at
  deploy.frost:2 allows: nothing but compute.
```

Two things fall out. A reviewer who reads only the entry file has a sound
upper bound on what the entire program can do. And a shared module that later
grows a network call breaks the build at the import site instead of quietly
widening somebody's manifest — which is the supply-chain shape you become
exposed to the moment modules can be shared at all.

The vocabulary is the policy language's, pointed inward: `run`, `read`,
`write`, `delete`, `set`, `read secret` and `change folder`, each taking
globs. A capability built at runtime always exceeds a ceiling, because a
limit that cannot be checked is not a limit.

### Pinning what you run

Modules open a window between the audit and the run that a single file never
had. The closure being read once closes most of it; a lockfile closes the
rest:

```bash
frost --lock deploy.frost      # records the sha256 of every file
frost --frozen deploy.frost    # refuses to run if any of them changed
```

```text
REFUSED: lib/text.frost has changed since the lockfile was written

the program does not match its lockfile; it was not run.
```

### Deliberately absent

No conditional imports, no re-export, no module-level state, no version
solving, no namespacing beyond the file path. Every one of those exists to
make a large dependency tree tractable, and a large dependency tree is the
thing this review model cannot survive. Modules here are for sharing five
handlers across four scripts in one repository.

---

## 13. Special values

| Expression | Meaning |
|---|---|
| `it` | Output of the last `run`, `pipe`, or handler `return` |
| `the result` | Exit status of the last `run` or `pipe` |
| `the arguments` | Command-line arguments, as a list |
| `the environment variable "NAME"` | Environment lookup; empty if unset |
| `the current folder` | Working directory; writable |
| `the standard input` | Everything piped into the script, read once |
| `the global NAME` | A global, from inside a handler |
| `the empty list` | A list with no items |
| `empty` | The empty string |

```
put item 1 of the arguments into target
put the number of items in the arguments into argument count
put the environment variable "HOME" into home folder
```

---

## 13a. Secrets

The failure this exists to prevent is not a malicious script. It is

```
put "connecting as" && token
```

in a generated script, running in CI, writing a credential into a log that is
retained for a year and readable by everyone in the organisation. That mistake
is made by being ordinary, not by being careless, so the fix is structural
rather than a rule to remember — the same argument frost makes about
injection.

### Sealed values

Three expressions produce a *sealed* value:

```
put the secret "db password" into password          -- from the keystore
put the secret environment variable "GITHUB_TOKEN" into token
put the secret file "~/.ssh/id_rsa" into key
```

A sealed value refuses to become text. Every printing path in the language
goes through one conversion, so `put`, joining, `--trace`, error messages and
the scratchpad all redact without knowing secrets exist:

```
put "connecting as" && user && "with" && password
```

```text
connecting as deploy with «secret db password»
```

Only the secret spans redact. If the whole line disappeared, people would
route around the seal to keep their logs readable, and a mechanism people
route around protects nothing.

### The seal is contagious

A connection string built from a password is a password:

```
put "postgres://user:" & password & "@host/db" into url
put url                             -- postgres://user:«secret db password»@host/db
```

The same holds for chunks, `split by`, the transformations, and a value
returned from a handler. Anything derived from a secret is a secret.

### Where the plaintext is released

| Where | What happens |
|---|---|
| `put`, `put into standard error`, `--trace`, errors | redacted |
| a released value echoed back by a program | re-sealed, so it redacts |
| a program's arguments | released |
| `reading <secret>` | released |
| `put ... into the environment variable "N"` | released |
| `put ... into file "..."` | released |

The rule: **streams redact, boundaries release.** Printing is the accidental
path and is closed. Handing a value to a program is a deliberate act, so it
works — and `--explain` reports every place it happens.

Comparisons and measurements see through the seal, because the alternative is
answering a different question:

```
if password is empty then quit with status 1
if token starts with "ghp_" then put "looks like a github token"
put the length of password
```

Equality on a sealed value is a constant-time comparison, so it cannot be
turned into an oracle that recovers the value a character at a time.

### When a program prints it back

A program handed a credential often echoes it. `psql` names the connection
string in an error, a deploy tool prints the flags it was given, a health check
reports the URL it called. That output used to come back into the script as
ordinary text: sealed on the way out, plain on the way in, and printed by the
next `put` that touched it.

frost now finds the released plaintext in what the child wrote and re-seals it
in place:

```
put the secret "db_password" into pw
run "psql" with "--password", pw          -- released, and reported
put it                                     -- the password redacts
```

The value is preserved, not deleted. An earlier version replaced the plaintext
with a marker, which closed the leak and broke every script that used the
surrounding text: the error message you needed to read was still there, with a
hole where the detail was. Re-sealing keeps the text intact and redacts it at
the point of printing, which is where the leak actually happens.

Three limits, stated plainly.

**Exact match only.** It re-seals a secret frost itself released. It does not
detect things that look like credentials. A mask that guesses at shapes fails
in both directions, and the direction that matters is the quiet one: it gets
trusted for the case it misses.

**A derivation is not tracked through the child.** If a program base64s the
value before printing it, the encoded form is not recognised. Nothing at this
layer could recognise it.

**Inside a `pipe`, only the last stage's streams pass through frost.** The
intermediate stages are connected to each other directly, which is what makes
a pipe a pipe, and frost never sees what crosses them.

### The keystore

A keystore is a file holding encrypted values, each labelled with the roles
that may read it. A script runs as exactly one role.

```bash
frost keystore init prod.keystore --role deploy
frost keystore add-role prod.keystore admin
frost keystore set prod.keystore "db password" --roles deploy,admin
frost keystore list prod.keystore
frost --keystore prod.keystore --role deploy release.frost
```

The *names* of the secrets, the roles, and who may read what are all stored in
plaintext, because that is the part a reviewer needs. Only the values are
encrypted.

Each role has an X25519 keypair whose private half is encrypted under a
passphrase with scrypt; each value has a random data key sealed to every
authorised role's public key, with AES-256-GCM throughout. The consequence
worth knowing is that **storing a secret and granting a role need no
passphrase — only reading does.** Somebody can add a credential for a role
whose passphrase they do not have, which is the usual case.

This needs the `cryptography` package: `pip install "frostlang[keystore]"`.
The interpreter and everything else still have no dependencies, and the two
environment-and-file forms of `the secret ...` work without it.

### Refused before it runs

Which secrets a script asks for is a capability, so it appears in the
manifest and is checked before anything executes:

```text
$ frost --explain release.frost

Reads these secrets:
  db password  — line 4  (from the keystore)

Lets a secret leave the process:
  on the standard input of psql  — line 9
```

If the role cannot open a secret the script names, frost exits 3 and nothing
runs — the same contract as `--policy`. A policy can also refuse outright:

```policy
forbid reading secret "prod/*"
require at most 2 secrets read
forbid any secret releases
```

### What this does not do

It does not stop a script handing a secret to a program it was already
allowed to run. Nothing at this layer can.

Once the plaintext reaches another program, frost cannot follow it *into*
that program. What it can do is recognise the value coming back out, and it
does: see below. The manifest still reports the release rather than pretending
the seal survives the boundary.

Passing a secret as a command-line argument is reported as a caution for a
second reason: arguments are visible to every other process on the machine
while the command runs. `reading <secret>` is the better form and is not
flagged.

The keystore is not a secret manager. There is no rotation, no expiry, no
audit trail of reads and no network service. If you run Vault or SSM, use
those. This exists for the many projects that run neither and keep
credentials in a `.env` that nobody encrypts.

---

## 13b. Talking to the thing that wrote the script

Every error in this document is a sentence with a line number and usually a
hint, which is the right output for a person reading at 3am. It is the wrong
output for a model, which has to parse the English and guess an edit.

`--json` gives the same information as data:

```bash
frost --check --json report.frost
```

```json
{
  "schema": 1,
  "ok": false,
  "exit": 2,
  "diagnostics": [
    {
      "severity": "error",
      "code": "missing-then",
      "message": "expected 'then' but found end of line",
      "line": 2, "column": 20,
      "source": "if error count is 0",
      "hint": "an 'if' condition is followed by 'then'",
      "repairs": [
        {"kind": "replace-line", "line": 2,
         "text": "if error count is 0 then", "confidence": "high",
         "why": "an 'if' condition is closed by 'then'"}
      ]
    }
  ]
}
```

It works with `--check`, `--explain`, `--policy`, and on a runtime failure,
so one flag covers every way a script can be refused.

### Repairs

A repair is an edit, not advice. Most come from information the front end
already had — several hints in this document literally contain the corrected
line — so handing it over as data costs nothing and saves a round trip.

| Confidence | Meaning |
|---|---|
| `high` | a mechanical rewrite; the parser knew the answer |
| `likely` | the fix is right, a detail is inferred — which unit a timeout meant, where a missing `end repeat` goes |
| `guess` | a name that looks close to one that exists |

```bash
frost --repair --write report.frost
```

applies `high` repairs only, and repeats until nothing is left that it is
sure about — a recursive-descent parser stops at the first error, so fixing
one reveals the next, and a single pass would give up on any script with two
mistakes.

A pass is kept only if it made progress: the script now parses, or the first
error moved strictly later. That is what makes the loop safe to run
unattended. An error with no mechanical fix gets no repair at all, because a
wrong repair costs a round trip and teaches the wrong grammar.

---

## 13c. Recording and replaying a run

What a script *can* do is knowable before it runs. What it *did* was not
knowable at all — you ran it and watched.

```bash
frost --record run.json deploy.frost      # run it, write down everything
frost --replay run.json deploy.frost      # run it again, spawn nothing
```

A recording holds every command with its arguments, standard input, output
and exit status; every file read and its contents; every environment variable
read; and whatever was piped in. **Replay performs nothing**: no process is
spawned, no file is written, nothing is deleted. The recorded answers are
served back in order.

That makes a recording a fixture. Change the script, replay it, and every
command it would run is compared against what it ran before — so a refactor
meant to preserve behaviour either did or did not, and you find out without a
database or a network.

A difference is reported rather than raised:

```text
DIVERGED at deploy.frost:3
    the recording ran: echo two
    this run wants:  echo CHANGED
```

Matching is on the identity of the effect — which program with which
arguments, which path — not on line numbers, so reformatting or adding
comments replays clean. Exit status is 4 for a divergence, distinct from a
policy refusal.

Secret *values* are never recorded, only their names, and any plaintext the
run revealed is scrubbed from everything written down — including command
arguments, which is where the first version of this leaked. So a recording is
safe to commit, which is the point: a fixture you cannot check in is not a
fixture. The scrubbing is exact-match, so a program that transforms a secret
before printing it will defeat it; the manifest already reports that the
secret was released there.

---

## 14. Grammar

EBNF. `{ }` is zero or more, `[ ]` optional, `|` alternation.

```ebnf
program      = { statement NEWLINE } ;

statement    = put | run | try | pipe | if | repeat | quit | ensure
             | handler | return | arith | loopctl | delete | call ;

put          = "put" expression [ ("into" | "before" | "after") target ] ;
target       = "standard" ("output" | "error")
             | "file" expression
             | "the" "global" identifier
             | "the" "environment" "variable" primary
             | "the" "current" "folder"
             | identifier ;

run          = "run" expression [ "with" arglist ] { runclause } ;
runclause    = timeout
             | "reading" expression
             | "in" "folder" expression
             | "showing" "output" ;
timeout      = "within" expression timeunit ;
timeunit     = "millisecond" | "milliseconds" | "ms"
             | "second" | "seconds" | "minute" | "minutes"
             | "hour" | "hours" ;
arglist      = expression { "," expression } ;
try          = "try" "to" ( run | pipe ) ;

pipe         = "pipe" { pipeclause } NEWLINE run NEWLINE run { NEWLINE run }
               NEWLINE "end" "pipe" ;
pipeclause   = timeout | "reading" expression | "in" "folder" expression ;

ensure       = "ensure" NEWLINE block "end" "ensure" ;

if           = "if" expression "then"
               ( statement
               | NEWLINE block [ "else" ( if | NEWLINE block ) ]
                 "end" "if" ) ;

repeat       = "repeat" repeatspec NEWLINE block "end" "repeat" ;
repeatspec   = expression "times"
             | "with" identifier "from" expression "to" expression
               [ ("by" | "step") expression ]
             | "for" "each" chunknoun "in" expression "as" identifier
             | ("while" | "until") expression
             | "forever" ;
loopctl      = ("exit" | "next") "repeat" ;

quit         = "quit" [ "with" "status" expression ] ;
handler      = "to" identifier [ "with" identifier { "," identifier } ]
               NEWLINE block "end" identifier ;
return       = "return" [ expression ] ;
arith        = "add" expression "to" assigntarget
             | "subtract" expression "from" assigntarget
             | ("multiply" | "divide") expression "into" assigntarget ;
assigntarget = [ "the" "global" ] identifier ;
delete       = "delete" "file" expression ;
replace      = "replace" expression "with" expression "in" assigntarget ;
call         = identifier [ "with" arglist ] ;

block        = { statement NEWLINE } ;

(* expressions, loosest binding first *)
expression   = or ;
or           = and { "or" and } ;
and          = notexpr { "and" notexpr } ;
notexpr      = "not" notexpr | comparison ;
comparison   = concat [ compop concat | "exists" ] ;
compop       = "is" [ "not" ] [ "greater" "than" | "less" "than"
                              | "at" "least" | "at" "most" | "in" | "empty" ]
             | "contains" | "matches" | "is" "like"
             | "starts" "with" | "ends" "with"
             | "=" | "!=" | "<" | ">" | "<=" | ">=" ;
concat       = postfix { ("&" | "&&") postfix } ;
postfix      = additive { ("split" | "joined") "by" additive } ;
additive     = multiplicative { ("+" | "-") multiplicative } ;
multiplicative = unary { ("*" | "/" | "^") unary } ;
unary        = [ "-" ] primary ;

primary      = NUMBER | STRING | "it" | "empty" | "true" | "false"
             | "(" expression ")"
             | "file" ( STRING | "(" expression ")" )
             | "every" "match" "of" primary "in" primary
             | thephrase
             | chunk
             | identifier ;

thephrase    = "the" ( "result" | "arguments" | "current" "folder"
                     | "standard" "input" | "empty" "list"
                     | "secret" primary
                     | "secret" "environment" "variable" primary
                     | "secret" "file" primary
                     | "global" identifier
                     | "whole" "match" | "matches"
                     | "environment" "variable" primary
                     | "length" "of" primary
                     | "number" "of" chunknoun ("in" | "of") primary
                     | transform [ "of" ] unary
                     | aggregate "of" unary
                     | chunkplural "of" primary
                     | ordinal chunknoun "of" primary
                     | chunknoun index [ "to" index ] "of" primary
                     | identifier [ "of" unary { "," unary } ] ) ;

transform    = "uppercase" | "lowercase" | "trimmed" | "sorted"
             | "reversed" | "unique" | "rounded" | "absolute" ;
aggregate    = "sum" | "largest" | "smallest" | "average" ;

(* The last thephrase alternative is a handler called in an expression.
   Every form above it wins the name, so a handler cannot shadow a
   built-in property. An unknown name is rejected after parsing. *)

chunk        = chunknoun index [ "to" index ] "of" primary ;
index        = ordinal | [ "-" ] NUMBER | additive ;
ordinal      = "first" | "second" | ... | "tenth"
             | "last" | "middle" | "any" ;
chunknoun    = "character" | "char" | "word" | "line" | "item" | "match"
             | plural forms ;
(* for chunknoun "match" the `of X` tail is implicit: the last match *)

identifier   = WORD { WORD }   (* words not in the reserved set *) ;
```

### Reserved words

These cannot appear inside a name:

```text
add        after      and        are        as         at
before     by         contains   delete     divide     each
else       empty      end        ends       ensure     every
exists     exit       false      for        forever    from
global     greater    if         in         into       is
it         joined     least      less       like       matches
may        most       multiply   next       not        of
or         pipe       put        quit       reading    repeat
replace    return     run        showing    split      standard
starts     step       subtract   than       the        then
times      to         true       try        until      use
which      while      whole      with       within
```

Notably absent: `line`, `word`, `item`, `character`, `match`, `file`, `status`,
`error`, `output`, `count`, `name`, `path`, `result`. These are recognised by position,
so they remain available for names like `line count`, `error count`, `match
count`, `file path`, and `exit status`.

---

## 14a. Reading a script before you run it

A frost script is parsed, not string-substituted, so everything it can do is
visible in the tree. Two tools use that.

### `--explain`

A capability manifest — what the script touches, without running it.

```text
$ frost --explain cleanup.frost

Runs these programs:
  chmod  - line 13  (no timeout)
  curl   - line 12  (1 allowed to fail, no timeout)
  find   - line 4   (no timeout)
  rm     - line 8   (no timeout)

Writes these files:
  /etc/cleanup.state  - line 11

Can exit with:
  status 1
```

Only literals are reported. A program name or path assembled at runtime is
listed as built at runtime and counted in a closing note, because a manifest
that quietly omits things is worse than no manifest.

`--explain` also runs a set of built-in checks that need no policy file at
all. They fire on every script:

| Severity | Examples |
|---|---|
| danger | `rm -rf`, delete with a wildcard, `sudo`, `chmod 777`, `dd`, writing or deleting under `/etc` `/usr` `/System`, reading `~/.ssh` `.env` `*.pem`, a shell escape via `sh -c`, a network fetch piped into an interpreter, and **secrets read followed by a network call** (the exfiltration pattern) |
| caution | recursive delete, a network command with no timeout, a `try to run` whose result is never examined, a program name built at runtime |
| note | any command that reaches the network, with the host named where it is a literal |

The verdict is `clean`, `caution`, `dangerous`, or `blocked`, and `--explain`
exits non-zero on `dangerous` so it can gate a commit hook.

### `--policy`

Rules checked before a single process is spawned. If any rule is broken the
script does not run, and frost exits with status 3.

```policy
forbid running "rm" with "-rf"
forbid running "sudo"
forbid writing to "/etc/*"
forbid deleting "/*"

warn running "curl"

require timeout on "curl"
require every command to be checked
```

```text
$ frost --policy production.policy cleanup.frost

REFUSED: running "rm" with "-rf"
  cleanup.frost:8   run "rm" with "-rf", scratch folder
REFUSED: writing to /etc/cleanup.state
  cleanup.frost:11  put "cleaned" into file "/etc/cleanup.state"
warning: running "curl"
  cleanup.frost:12  try to run "curl" with "--silent", metrics url

2 rule violation(s); the script was not run.
```

Subjects are globs, so `forbid writing to "/etc/*"` covers the whole tree.
`forbid running "rm" with "-rf"` matches the program *and* an argument, so
ordinary `rm` still works — the rule targets the dangerous combination rather
than banning a useful tool.

`require every command to be checked` understands the difference between an
ignored failure and a handled one: a `try to run` whose result is examined in
the next statement or two passes, while one whose failure is silently dropped
does not.

The capabilities added since — setting an environment variable and changing
the working folder — have rules of their own, because otherwise they would be
a way around the rest of the policy:

```policy
forbid setting "PATH"
forbid setting "LD_*"
forbid changing folder
```

#### Saying why

A rule's trailing comment is its hint, and frost prints it when the rule
fires. A refusal that says only *no* leaves the reader to guess what to do
instead:

```policy
forbid running "sudo"          -- the deploy role already has the permissions it needs
require timeout on "curl"      -- an unbounded fetch wedges the whole pipeline
require at least 1 cleanup     -- every job must release its lock, even when it fails
```

```text
REFUSED: running "sudo"
  deploy.frost:1  run "sudo" with "systemctl", "restart", "api"
  why: the deploy role already has the permissions it needs
```

There is no new syntax: policy authors already write that comment, so every
policy that already exists gains the explanation for free. A comment on its
own line is a section header and is not attached to any rule.

#### Counting rules

The rules above ask whether something appears at all. Business rules usually
ask *how much*, so the same vocabulary counts:

```policy
require at most 12 commands
require at least 1 cleanup
require between 1 and 5 files written
forbid more than 2 runs of "curl"
forbid any files deleted
```

`forbid more than N` and `require at most N` are the same rule said two ways,
as are `forbid fewer than N` and `require at least N`; use whichever reads
better for the noun. `forbid any X` is a limit of zero. `warn` may replace
`forbid` or `require` to report without blocking.

The countable nouns, singular or plural:

| Noun | Counts |
|---|---|
| `commands` | every program the script can run |
| `network commands` | those that reach the internet |
| `runs of "curl"` | commands whose program matches a glob |
| `files read`, `files written`, `files deleted` | file access by path |
| `environment reads`, `environment writes` | environment variables touched |
| `folder changes` | assignments to `the current folder` |
| `cleanups` | `ensure` blocks |
| `unchecked commands` | `try to run` whose result is never examined |
| `commands without a timeout` | commands with no deadline |
| `runtime names` | program names or paths built at runtime |
| `handlers` | handler definitions |
| `pipes` | pipe stages |

A count that is exceeded reports the line of the occurrence that crossed the
limit, not the whole script. A count that falls short has no line to point at,
so it is reported against the file.

#### The sandbox boundary

Every rule above is a statement about the *text* of a script, and every one of
them is honest about its limits: a path built at runtime is reported as
unknowable rather than guessed. That honesty is also the gap. Once the script
runs, an unknowable path is a real path.

A boundary closes it. Declared in the same file, but **allow-shaped**, because
a deny-list cannot become a sandbox — `forbid writing to "/etc/*"` says
nothing about what writing *is* permitted:

```policy
sandbox may run "git", "make"
sandbox may read "*"
sandbox may write "build/*", "/tmp/frost-*"
sandbox may reach the network
```

```bash
frost --policy prod.policy --sandbox deploy.frost
```

A path the analyser could not resolve is still confined, because the
confinement never needed it resolved.

##### Two enforcers, and the difference is real

**Child processes are confined by the operating system** — `sandbox-exec` on
macOS, `bubblewrap` on Linux. Once a program runs inside one, frost is not in
the loop: the kernel refuses the write. That holds even if the program is
hostile, even if frost has a bug.

**frost's own file operations are confined by frost.** `put X into file
(path)` never becomes a child process, so the check is a check in the
interpreter — enforced by the same code being trusted to run the script at
all. A weaker claim, and named differently here for that reason.

##### What it cannot do

**Per-host network rules.** The obvious thing to want is *may reach
api.github.com and nothing else*. macOS's sandbox language filters on
addresses, not names; a Linux namespace gives you a network or no network. A
hostname allow-list needs a proxy, which is a different program. So network is
all-or-nothing, `sandbox may reach the network` says exactly that, and

```text
sandbox may reach "api.github.com"
```

is **refused when the policy is read** rather than accepted and quietly
under-enforced. A boundary that does not hold is worse than no boundary,
because somebody relies on it.

**Platforms with no backend.** If a boundary is declared and cannot be
enforced here, frost refuses to run — it does not warn and continue. Before
each run it also executes a real confined command that tries to write outside
its boundary, and refuses if that write succeeds: present is not the same as
working.

**Anything a permitted program then does.** A sandbox that may run `git` may
run every `git` subcommand. Confinement bounds the blast radius; it does not
read intent.

#### Bounded timeouts

`require timeout on "curl"` asks only that a deadline exists. A deadline of
six hours satisfies it, and so does one of a millisecond — the first hangs the
script, the second kills healthy work. So the bound can be given:

```policy
require timeout on "curl" of at most 30 seconds
require timeout on "*" of at least 1 second
require timeout on "*" between 1 and 120 seconds
```

Units are reconciled between the rule and the script: a policy written in
seconds catches a script written in minutes, because `within 2 minutes` is
already `2 * 60` in the tree and folds to 120 before the comparison.

A timeout computed at runtime cannot be checked ahead of time, and is refused
rather than assumed acceptable — the same rule the manifest follows for a
program name built at runtime.

Sensitive-path detection works on literal *fragments*, so a path assembled at
runtime — `file (home & "/.ssh/id_rsa")` — is still recognised even though no
whole-string literal ever appears in the source.

This is the part a traditional shell cannot offer. `rm -rf "$DIR"` is a string
until the moment it executes, so there is nothing to inspect beforehand. In
frost the program and its arguments are separate nodes in a tree, which is what
makes a script checkable as a contract rather than trusted as a guess.

---

## 14b. `frost diff`

Two versions of a script, compared by what they can do.

```bash
frost diff old.frost new.frost
```

```text
wider:    it can now run curl
wider:    it can now reach telemetry.example
```

It exits 3 when the second version is wider than the first, 0 when it is the
same or narrower, so it drops into a pre-merge hook without any parsing.

A text diff answers the wrong question. Three rearranged lines can be a
widening and thirty can be a rename, and a reviewer reading a regenerated
script has no way to tell which they are looking at without reading all of it.
This compares the two manifests instead: what the new version can do that the
old one could not, and what it no longer does.

What it does not compare is behaviour. Two scripts that run the same commands
and reach the same hosts are identical to this, whatever else changed between
them. It answers "did the blast radius grow", which is what a reviewer is
actually asking of a script a machine rewrote.

---

## 14c. When a rule refuses

A refusal names the rule. The next question is always what would have to
change, and answering it by hand means finding the rule and working out what a
change to it would mean everywhere else.

```text
What would have to change, if this should be allowed:

  reaching telemetry.example, which is not in the allow-list
    change:  require reaching only "api.github.com", "telemetry.example"
    effect:  widen the allow-list to include telemetry.example
    allows:  every connection to telemetry.example, from any script

None of this is applied. A policy change permits more than the script
that prompted it, so it belongs to whoever owns the policy.
```

The `allows:` line is the point. Relaxing `forbid running "curl"` does not
permit this script's `curl`; it permits every `curl` on that host, in every
script, from then on. A "minimal delta" framing hides exactly that, so every
suggestion states its reach.

Where a rule can be tightened rather than removed, the tightening is offered
first: `require reaching only "a", "b"` is a smaller change than deleting the
rule, and a reviewer shown only the deletion will take the deletion.

Two things it deliberately will not do.

**It never writes to a policy file.** The output is a report. Applying it is a
decision about every script the policy covers, and it belongs to whoever owns
that policy.

**Under `--automated` it declines to answer at all.** An agent that hits a
refusal and is handed the exact edit that clears it has been handed the
instructions for widening its own bounds, which is the failure `--automated`
exists to prevent everywhere else. A machine that cannot approve should not be
drafting its own permission slip either.

A subject that does not exist until the script runs gets an honest non-answer
rather than an invented one, because no allow-list entry can cover a host
assembled at runtime.

---

## 15. Deliberate omissions

Things frost does not have, and why.

**String interpolation.** The entire injection surface of shell comes from
values being re-read as syntax after substitution. Use `&` and `&&`.

**A shell escape / `eval`.** Same reason. If you need shell behaviour, run the
shell explicitly: `run "sh" with "-c", script text`. That line is greppable in
review, which is the point.

**Globbing in `run` arguments.** `run "rm" with "*.tmp"` passes a literal
asterisk. Expansion happens where you can see it — loop over the output of
`find`, or hand the pattern to a program that expands it.

**Background jobs, job control, `&`, `fg`, `bg`.** These belong to an
interactive shell. frost is not one.

**A login shell mode.** Deliberate. Terminal modes, line editing, process
groups, and prompt expansion are most of the work in a shell and none of it is
this idea. Keep zsh; add frost.

**Terse aliases for anything.** There is no `-v` flag syntax, no punctuation
variables, no abbreviations. If a construct is worth having, it is worth
spelling.
