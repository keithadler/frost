# Changelog

Notable changes to frost. Dates are the release date; unreleased work sits at
the top.

The format is loosely [Keep a Changelog](https://keepachangelog.com/), and
frost follows [semantic versioning](https://semver.org/): before 1.0, a minor
bump may change the language.

## Unreleased

### Added

**The manifest invariant is now checked against generated scripts, not
chosen ones.** `--explain` may overstate what a script can do and must never
understate it, and everything else here rests on that: the policy engine,
approvals, the sandbox, the MCP server. It was checked by example, over a list
of scripts somebody thought of.

`tests/test_manifest_property.py` generates scripts from a grammar of
effectful shapes, runs them with the real interpreter, and records what
actually happened at the boundary: every process spawned through Popen, every
file opened for reading or writing, every file removed. Then it asserts that
everything which happened appears in the manifest. Overstatement stays legal,
because a branch not taken is still a capability.

The grammar includes the cases most likely to defeat a static reading: a path
built by joining a literal to a name, a path built from an environment
variable, a program name held in a variable, effects inside a handler called
from a loop, and both arms of a branch where only one runs.

Three guards keep it from passing vacuously. One asserts the generator
actually produces each kind of effect. One asserts the instrument observed
each kind across a batch, since the comparisons iterate over what was recorded
and an empty list satisfies them all. And one points the comparison at a
manifest with a command removed by hand, and requires it to notice.

That last one earned its place immediately: it caught the instrument counting
every spawn twice, because `subprocess.run` builds a `Popen` internally and
both were being recorded.

No understatement was found across 500 seeds. That is a weaker claim than a
proof and a much stronger one than a list of examples.

## 0.10.0 - 2026-08-10

### Added

**A script on standard input.** `frost --check -` reviews a buffer that is not
on disk yet, for an editor, a pipe, or an agent holding text it has just
written. The analysis never needed a file, which the MCP server already
demonstrated. Running one that way is refused: a script that runs wants its
own standard input and cannot have both.

**`--brief`.** One line naming what a script touches and the verdict, for a
log with forty scripts in it. Not a shortened manifest: it answers "is this
worth opening" and nothing else.

**`frost diff` reads a git revision.** `frost diff HEAD~1 deploy.frost` is the
question a reviewer actually has. A path that exists on disk is always treated
as a path, because a file called HEAD is a file.

**`--watch`.** Re-runs the review whenever the script changes. A loop around
the review rather than a mode inside it, so what is watched stays exactly what
CI runs.

**A man page**, generated from the argument parser by `tools/build_man.py` and
checked by the same job that keeps every other generated file current. A man
page maintained by hand describes last year's flags.

**A policy beside the script is found and used.** `frost init` wrote
`frost.policy` next to `main.frost` and then required `--policy frost.policy`
on every run, which is a papercut this changelog created a few entries ago.
The search walks up from the script's own folder and stops at a `.git`,
because a policy from outside the project is somebody else's contract. The
path used is printed, since a rule that applies without being asked for has to
be visible, and `--no-policy` opts out.

**Colour, on a terminal only.** A manifest is read under time pressure and a
danger that looks like a note gets skimmed. Off when standard output is not a
terminal, so SARIF, JSON, pipes and CI logs are byte for byte what they were,
and `NO_COLOR` wins over `FORCE_COLOR` because people who set it mean it.

**A folder of scripts.** `frost --check examples/` used to fail with `Is a
directory`, so any repository with more than one script wrote a shell loop:
precisely the shell frost is trying to replace. Review only, and the worst
exit code wins. Running a folder is refused, because guessing which script was
meant is worse than saying no.

**The manifest on the GitHub run summary.** The Action emitted SARIF, which
lands on the diff where reviewers read it, and left the run page saying
nothing about what the scripts can do. That page is the one people open first
when a job fails.

**A published JSON Schema for `--explain --json`.** The output has always
carried `"schema": 1` and nothing said what 1 was. `schema/explain-1.schema.json`
now says, and every golden manifest in the suite is validated against it, so
the promise cannot drift from the program.

Writing it caught the author guessing: `reads` and `writes` are lists of
objects, and the first draft called them strings because the manifest used for
reference happened to have both arrays empty.

### Fixed

**A cached secret was returned without checking the role.** `open_secret`
consulted its cache before answering the permission question, so once any role
had read a secret in a process, every other role got the plaintext back with
no check at all. A single run uses a single role, which is why nobody hit it,
and "usually only one role" is not an access rule.

Found by a test written for rotation, against code older than rotation.

### Added

**Rotation.** `frost keystore rotate <keystore> <name> --as <role>` replaces a
value under a fresh data key. Both halves matter: a new value, because the old
one is known to whoever read it, and a new data key, so a role removed earlier
cannot use anything it kept. The role set is preserved, since rotation is not
the moment to change who can read a credential and doing both at once means
the trail cannot say which was intended.

**Revoking says what it does not achieve.** Removing a wrapped key removes
future access and does not remove what the role already read. `revoke` now
returns and prints that, every time, with the only move that actually helps:
change the credential where it lives, then rotate.

**A trail of administrative changes.** `frost keystore history` reports who
granted, revoked, rotated or removed what, and when.

Reads are deliberately absent, and the command says so in its own output. A
keystore is a file people copy; a read happens on their machine against their
copy, and nothing here would ever see it. A read log recording only the reads
that went through this CLI would be believed exactly when it mattered and
wrong exactly then.

## 0.9.4 - 2026-08-10

### Fixed

**The formatting pre-commit hook reformatted nothing.** Its entry was `frost
--format`, which prints the canonical layout to standard output and leaves the
file alone, so the hook passed every time and changed nothing. It is `frost
--format --write` now. A formatting hook that silently does nothing is worse
than no hook, because it is believed.

`--write` already existed. The bug was in the hook, not the tool, which is
worth saying: the flag was there and the file that shipped did not use it.

**A hook that can stop a commit now says so.** `frost-explain` was described
as printing the manifest for the record, and `--explain` exits 1 on a
dangerous verdict, so it blocked commits exactly like `frost-check --strict`.
Somebody adding it for a log line would have got a failing gate.

Both are now checked rather than described. The suite runs the formatting
hook's own command line against a messy file and asserts the file changed, and
any hook that can fail a commit must say so in its description.

### Changed

**`--policy-from` covers the volume ceilings and shell escapes.** Both were
added after the generator was written, so a starter policy went on describing
an older frost and silently omitted two capabilities. A scaffold that omits a
rule teaches that the rule does not exist.

Neither can be sized from the text, because what a command returns is a fact
about the run, so they are emitted as a figure to argue with rather than as a
finding. The escape rule is commented out when the script already reaches an
interpreter, since a suggestion that fails the build immediately is how a
scaffold gets deleted rather than edited.

There is now a test that no generated policy refuses the script it came from,
across every example in the repository.

## 0.9.3 - 2026-08-10

### Fixed

**The container is built from the wheel this release published**, rather than
fetching it back from PyPI.

Fetching it back looked like the more honest choice and was a race. PyPI's
index is eventually consistent across edges: on the 0.9.2 release the runner
probed and reported the version available, and sixteen seconds later the build
container, talking to a different edge, could not find it. Waiting longer on
the runner cannot fix that, because the runner was never the one that had to
see it. 0.9.2 published to PyPI with no container image.

Building from the artefact is also the stronger guarantee. The image holds the
exact file that was uploaded rather than a copy that resembles it, and the
build ends by running `frost --version` inside the image, so a wheel that
installed but could not run would fail the build instead of shipping.

## 0.9.2 - 2026-08-10

### Fixed

**The container is built for arm64 as well as amd64.** The first image was
amd64 only, so every Apple Silicon machine and every arm64 runner pulled
something it had to emulate. That is slow enough that people quietly stop
using it, which is the kind of failure nobody reports.

Found by inspecting the published image config through the registry rather
than trusting the green check on the build.

## 0.9.1 - 2026-08-10

### Added

**`frost init`** writes a first script and the policy that describes it. The
pair, not the script alone: a policy written later is written against a script
somebody has stopped reading, and the blank file is why the policy engine went
unused. The policy is generated from the script just written, so the two agree
on the first run.

The starter script is not `put "hello"`. It runs a program, checks the status,
handles the failure and cleans up, because there is nothing to learn from
`--explain` on a script with an empty manifest. It refuses to overwrite: init
in a directory somebody has worked in is a typo far more often than it is an
instruction.

**A container image**, `ghcr.io/keithadler/frost`, published on each tag. For
a pipeline that wants `--check --strict` without a Python setup step. It
installs the released version from PyPI rather than the build context, so the
image holds a version somebody can name, and it runs as a non-root user. It
reviews scripts and does not run them well, which is the right shape: an image
built for reviewing is not a place to execute what it reviewed.

**A guard on manifest output quoted in the documentation.** README.md and
LANGUAGE.md paste fragments of `--explain`, and those had drifted twice: once
when the separator became a dash and once when it stopped being one. Ten
samples across the two files were stale, and nothing would ever have said so.

They cannot be generated, because most quote a fragment of a script the page
never shows in full and printing the whole manifest would make the page worse.
What is enforced instead is that a pasted sample uses vocabulary the manifest
can actually produce: a section header it really prints, and the separator it
really uses. That is the drift that happened, twice.

**A pre-commit hook.** `.pre-commit-hooks.yaml` provides `frost-check`,
`frost-explain`, `frost-policy` and `frost-format`. `--check --strict` is the
form that belongs in a gate, and it existed with nothing wired to it.

**A security policy.** SECURITY.md, which matters more here than for most
tools: a weakness in frost is a manifest that told somebody their script was
safe when it was not. It states what counts, starting with an understated
manifest, and what does not, starting with anything past `sh -c`.

**`.gitattributes`.** `.frost` scripts were being counted as whatever GitHub
guessed, so the repository's language bar described a project this is not.
Generated files are marked as generated, and the golden files are pinned to LF
so a checkout on Windows cannot rewrite what they are compared against byte
for byte.

### Changed

**`--policy-from` writes an allow-list.** It emitted `warn running "x"` per
program, which described the script accurately and taught the wrong shape. The
starter policy is where that habit gets set, and a deny-list is a list of the
programs somebody thought of. It now emits `require running only`, matching
what it already did for hosts.

**Every em dash is gone**, from the documentation, the source, the tests and
the manifest's own output, and a test keeps them gone. The manifest now reads
`curl  at line 2` rather than using a dash as a column separator, which is
plainer and removes the ambiguity of two columns running together.

Worth recording: the samples of `--explain` output pasted into README.md and
LANGUAGE.md had gone stale, and nothing would have caught it. Documentation
that quotes program output is documentation that drifts silently.

**The workflows moved off Node 20 actions**, which GitHub has deprecated.

## 0.9.0 - 2026-08-10

### Added

**A program allow-list: `require running only "git", "npm"`.** Hosts had one
and environment reads had one; the capability that matters most was
deny-shaped only, so a policy could name what it feared and never what it
trusted. A deny-list cannot be completed, because it is a list of the programs
somebody thought of. A program named at runtime is refused rather than assumed
to be on the list, which is the same answer the host allow-list gives.

**Shell escapes are countable, so a policy can refuse them.** `forbid running
"sh"` matches the program frost spawns, which for `xargs sh -c` is xargs, so
the rule never fired on the indirect form. The manifest named an escape that
no rule could act on. `require at most 0 shell escapes` now covers both forms,
and the finding and the count come from one detector so they cannot disagree.

### Changed

**`--check` reports the verdict.** It printed `ok` and stopped, which is true
and useless: `rm -rf /` parses. The `--json` form carried the verdict and the
human form dropped it, so the reading most people get, from the flag most
likely to sit in a pre-commit hook, was the one that said nothing.

It now names the verdict and lists any dangers. The exit code is unchanged
without `--strict`, deliberately: flipping it would turn every existing hook
into a failing one on upgrade day, which is how a safety feature gets removed
rather than obeyed. `--check --strict` exits 1 on a dangerous verdict, which
is what `--explain` has always done.

**Published to PyPI.** `pip install frostlang` works, and the README has been
telling people to run it since before there was anything there. The release
workflow builds on a version tag, checks the tag matches the declared version,
installs the built wheel into a clean environment and runs a script through
it. Testing the source tree would prove the source tree works, which was never
in doubt.

### Added

**`frost mcp`, the review tools over Model Context Protocol.** A stdio
JSON-RPC server exposing `frost_check`, `frost_explain`, `frost_policy`,
`frost_diff` and `frost_grammar`. An agent already shells out to frost, parses
the text back and guesses at the exit code; this serves the same answers as
structured data, in the shape the caller is already speaking. No dependencies:
the standard library has everything it needs.

It cannot run a script, and that is the design rather than a gap waiting to be
filled. frost exists because a machine writes the script and a person decides
whether it runs, and a server that executes on request moves that decision back
to the machine. It also reads no files: every tool takes source text, because a
path argument would let whatever holds the other end of the pipe read anything
the process can reach. Both are asserted in the tests, not just described.

Refusals come back with the rule that fired, and with a refusal to draft the
widening: every call here is automated by definition, and an agent handed the
exact edit that clears its own refusal has been handed the instructions for
widening its own bounds.

**`frost context` and MODEL-CONTEXT.md, for the model doing the writing.**
LANGUAGE.md argues a case across thousands of lines, which is the wrong
document to paste into a context window. This one states the statement forms,
the closed keyword set read from the parser itself, and the constructs frost
deliberately lacks, because the mistakes a model actually makes are `${x}`,
backticks and an invented `let`.

Every snippet in it is parsed by the test suite. That caught the first
version teaching `put "a" & "b" into joined`, where `joined` is a reserved
word: a reference that teaches a form the parser rejects is worse than no
reference, because the model believes it.

**Ceilings on how much data a run may move.** `require at most 10 megabytes of
output`, `require at most 2 megabytes from one command`, `require at most 500
kilobytes written to files`, and the flags `--max-output`, `--max-one-command`
and `--max-written`. A policy and a flag both apply and the tighter wins, so a
flag can narrow a site limit and never widen one.

Time was bounded and volume was not. A deadline says nothing about a command
that answers instantly with a gigabyte, and `cat` of the wrong file or `yes`
with no `head` is cheap to write and looks like nothing in a manifest.

Enforced by reading the child's output as it arrives and killing it at the
ceiling. Measuring what was captured would report the overrun accurately and
prevent nothing, which is the shape of a limit that exists to be quoted rather
than to hold. A file write is checked before it happens, for the same reason.

Two holes, both stated rather than papered over: `showing output` connects the
child to the terminal, so those bytes never pass through frost and nothing
here can see them, and inside a `pipe` only the last stage is counted.

The bytes are counted whether or not a limit is set, and reported in the
finish event, because the question people ask before setting a ceiling is what
a run normally moves.

**An interpreter reached through another program is reported.** The shell
escape check asked whether the *program* was an interpreter and whether `-c`
was among its arguments. That is the shape somebody writes when they mean to
use a shell, and not the shape they write when they do it by accident.

`xargs sh -c`, `env sh -c`, `sudo sh -c`, `timeout 5 bash -c`, `nohup sh -c`,
`docker run img sh -c`, `find -exec`, `find -execdir` and `ssh host "..."` all
walked past a manifest that then reported no shell escape at all. A manifest
may overstate. Understating is the failure that makes it worse than having
none, and this was an understatement.

Each case says what actually happens rather than one blanket sentence. `find
-exec rm` involves no shell: it runs a program per match, with arguments from
the filesystem, and that program appears nowhere in the manifest. `ssh` hands
everything after the destination to a shell on another machine, where none of
frost's guarantees apply.

The launcher list is a floor and the documentation says so. `awk` can call
`system()`, `make` runs a shell for every recipe line, and some program nobody
has heard of takes a `--command` flag.

### Fixed

**A policy comment marker inside quotes ended the line.** Policy comments
start with `--`, and the stripper partitioned on the first one anywhere. An
Ed25519 public key is urlsafe base64, so it contains `-`, so about one key in
a hundred contains `--`. `require an approval signed by "kA--bQ..."` was cut
mid-string, leaving an unterminated quote and a policy that did not parse.

That is the intermittent CI failure that has been chased for several rounds:
it failed roughly one run in five, never once locally, and looked like a flaky
approval test rather than a policy parser that could not hold a value frost
had itself generated. Quoted text is now respected, so a program named
`my--tool` or a path holding `#` survives too.

**A NUL in an argument printed a Python traceback.** It cannot be passed to
`execve`, the standard library reports that by raising out of the fork, and
what reached the terminal said nothing about the script and everything about
frost's internals. It is a frost error now, with the useful suggestion
attached: pass the data on standard input with `reading`.

## 0.8.0 - 2026-08-10

### Added

**`frost diff`, comparing two versions by what they can do.** `frost diff
old.frost new.frost` reports the capability change between two scripts: what
the second version can do that the first could not, and what it no longer
does. A review that reads a text diff is reading the wrong artefact, because
three lines of rearranged text can be a widening and thirty can be a rename.

It compares the manifests, not the behaviour. Two scripts that reach the same
hosts and run the same commands are the same to this, whatever else changed
between them. It answers "did the blast radius grow", which is the question a
reviewer is actually asking of a regenerated script.

**A secret a child prints back is re-sealed.** A program handed a credential
often echoes it: into a log line, an error, a summary of what it just did. It
used to come back into the script as ordinary text, sealed on the way out and
plain on the way in.

The plaintext is now found in what a child wrote and re-sealed in place, so it
redacts wherever the script prints it. The value is preserved rather than
deleted: an earlier attempt substituted a marker, which closed the leak and
broke every script that used the surrounding text.

Exact-match only. It re-seals a secret frost released, not anything that looks
like a credential, because a mask that guesses at shapes fails in both
directions and gets trusted for the one it fails at quietly. One hole worth
naming: inside a `pipe`, only the last stage's streams pass through frost.

**A refusal says what would have to change, and what that would allow.**
Every `forbid` now comes with the narrowest policy change that would clear it,
and a plain statement of what else that change permits. Widening an allow-list
by one host is one line; that line applies to every script the policy covers,
and that is the fact a reviewer needs and the one a "minimal delta" hides.

It is a report and never a patch. Nothing is written to a policy file, and
under `--automated` it declines to answer at all: an agent that hits a refusal
and is handed the exact edit that clears it has been handed the instructions
for widening its own bounds.

**Dead code is reported.** Unreachable statements after a `quit`, `return`,
`exit repeat` or `next repeat`; handlers called nowhere in the program; names
assigned and never read. Individually harmless, and together the clearest sign
that a generated script contains more than anybody intended.

Notes rather than dangers, except unreachable code, because a verdict that
shouted about an unused variable is a verdict people stop reading. A policy
can refuse them with `require at most 0 dead code`.

**Rules about reading the environment.** `forbid reading the environment
"AWS_*"` and `require reading only the environment "PATH", "HOME"`. Setting a
variable already had a rule and reading one did not, which is the wrong way
round: what a script takes from the environment is where the credentials are.
A name built at runtime fails an allow-list closed.

### Added

**A loop that cannot end is a finding, and `--deadline` bounds the run.**

`within` bounds one command and a policy can bound how many there are.
Neither touches a loop doing arithmetic, which spawns nothing, reads nothing
and writes nothing. Having no capabilities, it produced an empty manifest and
a clean verdict: the cheapest way for a generated script to wedge a runner was
the one thing frost reported as harmless.

`repeat forever`, `repeat while true` and `repeat until false` are now checked
for anything that could end them. Presence counts rather than reachability,
which understates and never overstates, and a condition that is not literally
true is left alone, because guessing is how a check earns a reputation for
crying wolf.

`--deadline SECONDS` and `require the run to finish within N minutes` bound the
whole run, exiting 124, the code a shell uses for a timeout and the one frost
already returns when a single command runs too long. It is raised rather than
killed so `ensure` blocks still run, and the tightest budget wins so a flag
cannot widen what a site policy imposed.

### Added

**PLATFORM.md**, for the team that operates the machines. It answers the
question frost cannot answer about itself: what makes any of this mandatory.
The honest answer is that frost cannot, and the parts that can are a
controlled image, a controlled PATH, mandatory access control and a proxy. It
also separates the three strengths of guarantee here, since mixing them up is
the likeliest way to trust something that was never load-bearing.

**The Action tests itself.** A new workflow runs `uses: ./` against this
repository's own examples on every push, checks that a deliberately dangerous
example is refused, and that a widened script fails its own approval. An
action that only ever passes is one nobody has watched say no.

### Fixed

**The Action would have failed on its first line for every user.** Its install
step defaulted to `pip install frostlang`, and no such package is published
(PyPI returns 404). It now installs from `$GITHUB_ACTION_PATH`, so
`uses: keithadler/frost@v0.7.0` installs exactly the frost that shipped with
v0.7.0 and the tool cannot disagree with the action. Found by running the
thing, which is the entire argument for the self-test above.

**A refused run produced no telemetry at all.** The event sink was created in
the run path, below every gate, so a policy refusal, a breached ceiling, an
approval that no longer covered the script, an unusable signature and a denied
secret all returned before it existed. That is exactly backwards: the refusal
is the event a security team most wants, and a monitoring system hearing only
about runs which got as far as starting is missing the ones somebody needs to
look at.

The sink now opens before anything can refuse, every refusal path closes the
run out with what fired and the digest of the policy it came from, and the run
id is resolved at the top where everything reporting on a run can reach it.

Analysis modes emit nothing, because `--explain` runs nothing and a dashboard
should not see a run that never happened.

## 0.7.0, 2026-08-10

Everything since 0.6.0: modules that cannot widen a program, an identity per
execution, declared record shapes, findings a review tool reads, a policy the
host brings, approvals somebody signed, and events a monitoring system can
act on.

### Added

**`--events FILE`, telemetry as NDJSON.** One JSON object per line, flushed as
things happen, in the format Splunk, New Relic, Datadog, Vector and Fluent Bit
all read without a translator.

Commands are now timed, which nothing measured before, and the run separates
time spent working from time spent waiting.

The resolution worth having is the pairing rather than the volume. Any tool
can log that a command ran; frost knows what the script was allowed to do
before it ran, so the finish event reports which approved capabilities went
**unused**. A script approved for six programs that uses two is an approval
that should be tightened, and that is visible only by holding the manifest and
the run side by side.

Contents are never emitted and sizes are. Secrets are redacted before an event
is written, including inside a command's arguments, because telemetry leaves
the building more often than a recording does. The observer wraps whatever
journal is in use rather than adding a second set of hooks that would drift
from the first, so `--events` composes with `--record` and `--replay`, and a
replayed run is marked so a dashboard does not count a fixture as production
traffic.


### Added

**Site policy.** `/etc/frost/policy.d/*.policy` applies to every run on the
host, with or without `--policy`. A policy beside the script is controlled by
whoever writes the script, which is right for a project and useless as a
datacenter control. Site rules are added, never replaced, and composition can
only narrow: every rule is checked independently and all must pass, so two
allow-lists intersect and no syntax removes a rule. There is no variable
meaning "use this instead"; `FROST_SITE_POLICY_DIR` only adds a directory. A
site policy that is present and unreadable is a refusal.

**Policy provenance.** Every policy applied is named by path and digest in
`--explain` and in any recording. Without it an audit shows that a policy
existed, never that a given run was subject to it.

**`--automated`** (and `FROST_AUTOMATED=1`) refuses `--approve` and
`--ignore-approval`. A loop that can approve is a loop that approves its own
capability escalation, and the failure is mundane: an agent hits a refusal and
the most helpful-looking next step in its search is to re-approve.

**Signed approvals.** `--new-approver-key`, `--approve --sign-with`, and
`require an approval signed by "kA1b2c..."`. Ed25519 over the capability set,
the script, the commit, and the approver's own name and key. An approval
signed by a key the policy does not name is refused, and so is an unsigned
one. Making signatures needs the keystore extra; verifying never degrades,
because an unverifiable signature is not a valid one.

### Fixed

**The signature did not cover the approver's name.** The first version signed
everything except the whole signature block, so a valid approval could be
relabelled from one person to another and still verify. The trust decision was
unaffected, since the key is checked against the policy either way, but a
provenance record that can be edited is not a provenance record. Caught by the
test written for exactly that property, against a docstring that claimed it.

**SARIF, and a GitHub Action.** `--check --sarif` and `--explain --sarif` emit
what every code-scanning tool already reads, so a finding arrives as an
annotation on the diff line it concerns, in front of the person deciding
whether to merge. A refusal in a CI log is read by whoever opens the log,
which on a green-enough day is nobody. A repair becomes an applicable fix,
except at `guess` confidence: the confidence levels exist so a guess is not
applied unattended, and a one-click fix in a review tool is exactly unattended.

`action.yml` wires check, explain, policy and approvals into a few lines of
workflow, for people who will never install frost locally.

**`--policy-from`** writes a starter policy describing what a script already
does. The policy engine was the most useful thing here and the least used,
because the first step was a blank file and nobody enumerates capabilities in
a language they have just met. Anything that would refuse the script as it
stands is emitted commented out and marked, since a scaffold that fails the
build immediately is one people delete rather than edit.

**`--explain --against FILE`** diffs a script against a recorded approval
without running it, which is what a reviewer wants and what CI needs.

**`require an approval`** lets a policy insist a script carries a matching
approval, so an organisation can mandate it centrally rather than hoping every
caller passes a flag. The driver enforces it: whether a file exists is not
something the policy checker can see, and giving it the filesystem would undo
the reason it takes only a parse tree.

**Exit codes are published** with `--exit-codes [--json]`, and **completion**
with `--completion bash|zsh`, generated from the parser rather than written
beside it.

**`the run id`, and `--run-id`.** These scripts are run by agents and
pipelines, where the question afterwards is never "what happened" but "what
did *that* run do". Each execution now has an identity: supplied with
`--run-id`, otherwise taken from `FROST_RUN_ID`, otherwise generated. An
outside id wins, because joining frost's record to the pipeline's is the point.

It reaches the recording (at the top level, so a fixture joins to an audit log
without being parsed), the trace header, every child process as
`FROST_RUN_ID`, and the script itself, which is what makes it usable as an
idempotency key or as a scratch path that cannot collide with a concurrent
run. A replay reports the id of the run it is replaying, for the same reason
it serves the recorded clock.

Ids are validated, not trusted: letters, digits, dot, colon, dash, underscore,
128 characters. The value reaches log lines, child environments and any path
built from it, so a newline would forge a log entry and a slash would move a
file.

### Fixed

**`frost s.frost | head` printed a Python traceback.** A reader closing early
is not an error in the script, and a traceback there says frost broke when the
shell did what it was asked. It exits 141 quietly now, as a shell reports for
SIGPIPE.

**The analyser reads what is derivable, not only what is spelled out.** A host
is read out of a joined URL when the literal closes the authority, so
`run "curl" with ("https://api.github.com/repos/" & repo)` reaches
`api.github.com` rather than "a destination built at runtime". And
`constant_sets` follows a name whose definitions are all literals even when
they differ, so a branch picking one of two hosts reports both. Calling either
of those unknowable was not honesty; it was a manifest declining to read what
was in front of it. Without the authority terminator: `"https://" & host`,
the destination is still genuinely unknown and still says so.

**Per-host policy rules.** `forbid reaching "*.telemetry.example"` and
`require reaching only "api.github.com", "*.internal"`, checked against the
text before anything runs, with an unknowable destination failing them closed.

The sandbox is unchanged and still all-or-nothing, deliberately: macOS filters
on addresses and a Linux namespace has no middle setting, so
`sandbox may reach "api.github.com"` remains a parse error. A policy bounds
what the text can reach and the sandbox bounds what the process can reach.
The docs keep those apart, because the second is the stronger guarantee and
the first is the more precise one.

**`--trace-to-file FILE`**, and a trace worth writing anywhere. It printed
`[frost] line 5: Run`, which names the interpreter's internals rather than
anything the author wrote; it now prints the line itself, flushed as it goes,
because the run worth tracing is often the one that never finishes.

### Fixed

**`--record` threw away the run most worth recording.** The recording was
saved only on the success path, so a script that failed, was interrupted, or
hit a recursion limit left nothing behind. It is now written however the run
ends, carrying the real exit status, and a failure to write it can no longer
hide the failure it was about.

**`--trace-to-file out.log s.frost` treated `out.log` as the script.** The set
of flags that consume the token after them was kept in a list beside the
argument parser instead of read off it, and went stale the moment a flag was
added. It is derived now, and deriving it is the default rather than the
opt-in: an empty default does not fail loudly, it misparses quietly.

**`play.html` runs the real frost.** The scratchpad was `web/chunks.js`, a
second implementation of a slice of the language in JavaScript. It could
evaluate expressions and nothing else, so the demo showed the least
interesting part of the project: a visitor could try `the first word of it`
and could not see a manifest, a policy refusal, or an approval.

The page now loads CPython compiled to WebAssembly and runs `frostlang`
itself, so what the demo shows is what the tool does. `--check`, `--explain`,
`--policy`, `--approve`, `--as-approved`, `--check --json` and `--repair` all
work in the page, against seven samples read from `examples/` so they cannot
drift from the scripts the test suite already runs.

This works because everything worth demonstrating is static analysis. A
manifest, a policy refusal and an approval are facts about the parse tree, and
a parse tree needs no processes, no filesystem and no network. Only `run`
needs a machine, which is the one thing a stranger's browser should not be
doing. `frostlang/browser.py` is the whole surface, and a test asserts it
imports nothing that touches an operating system.

The repair loop moved from `cli.py` to `diagnostics.py` on the way. It is pure
text in, text out, and the page needs it too; a second copy would have
recreated exactly the drift the differential verifier exists to police.

**The page is a CI canary and has a correctness oracle.** Two layers, because
they catch different things. `tests/test_playground.py` proves every embedded
module is byte-identical to the file on disk and records what each sample
answers in process; it is fast and runs everywhere. `tools/canary_browser.py`
boots the page in real Chromium, presses the real buttons, and requires the
answers to match those recordings.

The offline test catches the failure a contributor is most likely to cause,
which is editing `frostlang` and forgetting that `play.html` carries a copy.
The canary catches what no offline test can see: Pyodide is fetched from a
pinned CDN version, the page writes modules into an emscripten filesystem, and
JavaScript marshals arguments across a bridge, and any of that can break with
no commit to this repository.

Both are mutation-checked. Making the embedded copy stale by one string fails
the offline test by module name and the canary by which answers moved.

### Fixed

**`--approve` reported narrowings as "it no longer reachs".** The headings are
phrases, not verbs, and no suffix rule inflects "let a secret leave the
process" correctly. They read "it no longer needs to ..." now.

**The browser comparison took its two scripts the wrong way round**, reporting
a poisoned regeneration as a list of narrowings, which reads as reassuring and
is exactly backwards.

**`--approve` and `--as-approved`: a capability baseline.** `--frozen` asks
whether a script is byte-identical to the reviewed one. That is right for a
vendored module and wrong for a script a model regenerates, every
regeneration trips it, you re-lock every time, and re-locking every time means
the check has stopped saying anything.

```
frost --approve deploy.frost        record what it may do today
frost --as-approved deploy.frost    refuse if it gained a capability
```

**The approval binds by default.** While `--as-approved` was opt-in, a
poisoned agent never had to defeat it: it just left the flag off, and in an
agent loop the agent is usually the thing composing the command line. A guard
that only applies when the caller remembers is a guard the attacker controls.
An approval file is now honoured whenever it exists. Skipping it takes
`--ignore-approval`: still possible, but a deliberate choice a reviewer can
see, and deleting the file shows up in a diff. `--check`, `--explain` and
`--format` are never blocked, since they are how the change gets reviewed.

The baseline records **where a script reaches**, not only what it runs.
Recording program names alone made `curl https://api.github.com` and
`curl https://telemetry.example` the same capability, which is precisely the
room a persuaded model needs: not a new program, a new destination. Hosts come
from literal arguments only: a scheme, or `user@host:path`: because a bare
`example.com` is indistinguishable from a filename and inventing hosts in a
manifest people trust is worse than reporting none. A network command with no
literal destination is recorded as unknowable rather than omitted. `--explain`
gained a "Reaches these hosts:" section, and a policy can now count them.

The baseline records the capability set with no line numbers, so moving a
comment does not move it. A capability that disappears is never refused; one
that appears refuses with exit 3 and says what it was:

```
REFUSED: it can now run curl
REFUSED: it can now read the secret ~/.aws/credentials (from the file)
REFUSED: it can now let a secret leave the process as an argument to curl
```

This closes the gap between two claims frost was making. Injection immunity is
a data-flow property: a value cannot become syntax. It says nothing about an
agent that *reads* something hostile and writes perfectly valid frost obeying
it: a confused deputy, not an injection, and no grammar reaches it. A policy
file answers that properly by being authored out of band from generation, but
a policy has to be written; a baseline needs no rules and compares against the
reviewer's own past judgement.

It bounds what a script can reach, never whether reaching it was wise. A
script allowed to run `git` can still push to the wrong remote.


**`--repair` can fix a missing wait unit.** `wait 3` now carries the same
mechanical repair its exact twin `within 3` has had for two releases.

**The differential verifier checks its own coverage.** `tools/verify_chunks.py`
compares frostlang against the browser evaluator across a hand-written corpus,
which meant a new expression form was only compared if somebody remembered to
add one: and records shipped in 0.6.0 with no browser support at all, silently,
because nothing asked. It now enumerates every expression node the parser can
produce and fails the build on any that the corpus never exercises. A form may
be excused only by naming it, with the reason it needs a host the browser does
not have.

The browser evaluator gained records, JSON, field access and `the keys/values
of`, and the new coverage check immediately earned itself: the first run found
the two implementations disagreeing on `the json text of the json of ""`,
where one produced `null` and the other `""`.

### Changed

**The README no longer implies the grammar answers prompt injection.** The
paragraph headed "Prompt injection becomes shell injection" described the
poisoned-input setup and then answered only the data-flow case. The two
attacks are now separated, with the limits of each stated.

### Fixed

**`the json text of` printed `2.0` where the rest of the language prints `2`.**
frost has no visible int/float split, `put 4 / 2` already prints `2`: so a
JSON writer that disagreed with every other printing path was the odd one out.


**A capability ceiling did not compose.** `which may run "psql"` constrained
only the file the import named, so a module could widen the program past what
its importer allowed simply by importing another module: allowed `psql`,
imported something that ran `curl`, and nothing objected. That made the claim
the whole module design rests on. That reading the entry file gives a sound
upper bound on the program, false at any depth greater than one.

A ceiling now bounds everything an import pulls in, however far down it lives.
A breach names the file that actually holds the capability and the import that
forbade it, and says which module it was reached through; blaming the module
the import names would send a reader to a file with no offending line in it.

The manifest was never wrong here, `--explain` reported the `curl` and
attributed it to the right file the whole time. It was the enforcement that
had the hole, which is the more dangerous half: a manifest is read by someone
who is already paying attention, and a ceiling is what protects the people who
are not.

## 0.6.0, 2026-08-10

The three gaps that pushed a real script back out of frost. Each one had a
workaround, and every workaround handed capability to something the auditor
could name but not see inside, which is the one trade frost exists to refuse.

### Added

**Records and JSON.** `the json of it` parses; objects become records, arrays
become the lists frost already has, and numbers stay numbers, so `item 1 of`
and `+ 1` keep working on anything an API returns.

```
run "curl" with "-fsS", url within 30 seconds
put the json of it into build
put the "name" of the "author" of build
```

Fields nest, `the keys of` and `the values of` are lists, and a record is
built a field at a time with `put "green" into the "status" of summary`: the
first assignment creates it. The alternative was `run "jq" with ".status"`: a
second language in the file, and a string `--explain` could not see into. It
could tell you the script ran `jq`; it could never tell you what for.

A missing key is empty, exactly as `word 99 of` is, and a field of empty is
empty, so an optional field needs no guard. A field of *text* is an error,
that means the value is not the shape the script thinks it is, and empty
would hide the bug while it is still cheap to find.

Secrets survive the round trip in both directions. Parsing a sealed value
seals every field it produces, because a parser is not a laundry; serialising
redacts field by field rather than all at once, because a record you cannot
print at all is a record people work around.

**`the error output`.** What the last command wrote to standard error, beside
`it` and `the result`. The only way to see why something failed used to be
`run "sh" with "-c", "... 2>&1"`: the construct MODEL-SPEC tells models never
to emit and the auditor flags on sight. Wanting an error message should not
require defeating the language's main guarantee. Standard error is still
written through to the terminal as it happens, so a failure is never silent
whether or not anything reads it.

**A clock, and `wait`.** `the current date`, `time`, `timestamp` and
`seconds`, plus `wait 3 seconds` with the unit required for the same reason
`within` requires one. Both are recorded: `--replay` serves back the reading
that was recorded rather than reading the clock again, and does not sleep at
all: a fixture whose timestamps move every replay is a diff generator, and a
replay that honours a thirty-second backoff is a replay nobody runs.

A script that waits says so in `--explain`, and a wait inside a loop is
reported as *at least* that long. Reporting a per-attempt sleep as though it
happened once would understate it by the loop count, and the manifest may
overstate a risk but must never understate one.

### Fixed

**The closure audit dropped any capability added after it was written.**
`merge()` iterated a hand-written tuple of field names, so `waits` was
collected by the single-file audit, survived it, and vanished from
`--explain`. It now derives the field list from the dataclass and refuses to
compile a field it does not know how to combine. The same hand-maintained
shape in `count_lines` silently made new nouns unusable in a policy; it now
reads the capability off by name. A manifest that lies by omission is worse
than no manifest.

## 0.5.0, 2026-08-10

The release that turns "frost can describe what a script may do" into "frost
can hold it". Modules, secrets, runtime confinement, record/replay and
structured diagnostics all landed here, and the version bump reflects
substantial new language and tooling surface rather than a breaking change.

### Fixed

**The sandbox self-test could not fail.** It asked one question, did a write
*outside* the boundary happen?, and a sandbox that dies before executing
anything answers no. It certified a completely non-functional Linux backend
as healthy across four CI runs. It now runs two controls: a forbidden write
must be refused *and* a permitted write must succeed. An absence is only
evidence if something was there to be absent.

**macOS denied every write the boundary allowed, for any script under `/tmp`
or `/var`.** Sandbox rules are matched against the resolved path and both of
those are symlinks, so the generated profile named something the kernel never
sees. Boundary patterns are now resolved through symlinks. Found by the
positive control above, on the platform that had been green throughout.

**The Linux backend never ran a single command.** `bwrap` needs a user
namespace, and where one cannot be created it exits before starting the
child. frost detects this and refuses to run; CI relaxes the restriction and
asserts the backend actually confines, so a skipped sandbox test is now a
failing one.

**The sandbox gave its children no working directory.** `bwrap` starts a
child in `/` whatever the parent's directory was, so a command writing to a
relative path wrote somewhere else entirely.

### Added

**Runtime capability sandboxing.** `--explain` and `--policy` reason about
the text of a script and are honest about their limits: a path built at
runtime is reported as unknowable rather than guessed. That honesty is also
the gap, because once the script runs an unknowable path is a real path.

A boundary is declared in the policy file, allow-shaped, a deny-list cannot
become a sandbox, since `forbid writing to "/etc/*"` says nothing about what
writing is permitted:

```
sandbox may run "git", "make"
sandbox may read "*"
sandbox may write "build/*"
sandbox may reach the network
```

`frost --policy prod.policy --sandbox deploy.frost` then holds it. Child
processes are confined by the operating system: sandbox-exec on macOS,
bubblewrap on Linux, so a path the analyser could not resolve is confined
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
those answers back and **performs nothing**: no process spawned, no file
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
better practice anyway, a lock in a world-readable directory is one any
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

- **A module is declarations only**: handler definitions and imports, and a
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
  loaded, one silently shadowed a handler in the other, a hijack rather than
  a hygiene problem. Handler names now resolve in the file that defines the
  code doing the calling, and a collision is an error at load time.
- **Taint was name-based over the whole tree.** A `token` in one file and an
  unrelated `token` in another were the same node, which gave false positives
  directly and false negatives through the shadowing bug above. Taint is now
  per file, crossing a boundary only where data does, through the arguments
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
from information the front end already had, several of the parser's hints
literally contained the corrected line, so handing it over as data costs
nothing and saves an agent a round trip. Confidence is three-valued and
honest: `high` is a mechanical rewrite, `likely` infers a detail, `guess` is a
name that looks close to one that exists.

**`frost --repair [--write]`** applies the high-confidence repairs and repeats
until nothing certain is left. One pass is not enough, a recursive-descent
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

No new syntax: policy authors already write that comment, so every policy
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
redact: `put "connecting as" && user && "with" && token` keeps its context,
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
no passphrase, only reading does, so somebody can add a credential for a
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
  disproved it twice, once for `true` and once for `git --version`. The
  README now states what actually holds, which is the thing the design
  relies on anyway: parsing is paid once and spawning is paid per command,
  so a script running ten commands pays ten spawns against one parse on any
  platform.

### Added, language

**Cleanup blocks.** `ensure ... end ensure` registers a block when execution
reaches it and runs it when the script ends: normally, on error, on `quit`,
or on interrupt, most recent first. Abort-on-failure was the headline default
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

**Real lists.** A plural chunk noun with no index is the whole set, `the
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

**Continuous integration** across Python 3.10 to 3.13 on Linux and macOS, with a
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
  metasyntax tables in MODEL-SPEC.md were fenced as frost. That file goes
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
