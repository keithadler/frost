# Running frost as a platform control

This is for the team that operates the machines, not the team that writes the
scripts. It covers the one question frost cannot answer about itself: **what
actually makes any of this mandatory?**

The short version is that frost cannot. Everything below is about the parts of
the answer that live outside it, and how to make frost the easy thing to
mandate.

---

## What frost enforces, and what it cannot

frost holds three kinds of guarantee, and they are not equally strong. Mixing
them up is the most likely way to end up trusting something that was never
load-bearing.

**The kernel holds it.** With `--sandbox`, a child process is confined by
`sandbox-exec` on macOS or `bubblewrap` on Linux. Once a program is running
inside one of those, frost is not in the loop: the write is refused even if
the program is malicious and even if frost has a bug. This is the strongest
thing here and the narrowest: it bounds child processes, all-or-nothing on the
network, and nothing else.

**frost holds it.** A policy refusal, an import ceiling, an approval, a role's
access to a secret, and frost's own file operations are enforced by the
interpreter. That is a real control and a weaker claim, because it is enforced
by the same code being trusted to run the script at all.

**Nobody holds it.** Anything a permitted program then does. A sandbox that
may run `git` may run every `git` subcommand. Confinement bounds the blast
radius; it does not read intent.

**And frost cannot make itself mandatory.** Anyone who can run `frost` can run
`python`, or `sh`, or install their own copy from anywhere. A wrapper written
in the thing being enforced is theatre, and the danger is not that it fails
but that it is believed. That belief is the thing this document exists to
prevent.

---

## What actually makes it mandatory

Mandatory is a property of the machine. In descending order of how much it
buys you:

**1. Control the image.** If the only interpreter on the box is frost, and the
image is built by you rather than by whoever is deploying, then frost is the
only way to run a script. Everything else on this list is a weaker version of
this one.

**2. Control the PATH, and remove the alternatives.** A base image without
`bash`, `python3`, `perl` and `busybox sh` is unusual and achievable for a job
runner. Where it is not achievable, you are enforcing a convention rather than
a control, and should say so out loud rather than to yourself.

**3. Mandatory access control.** AppArmor or SELinux profiles restricting
which binaries a job user may execute. This is the mechanism that actually
denies `python3` to a process that wants it, and it is enforced by the kernel
rather than by hope.

**4. Deny the network at the boundary.** frost's per-host rules are checked
against the text before a run and cannot be enforced at runtime, because macOS
filters on addresses and a Linux namespace has no middle setting. A proxy or a
firewall is what makes a host allow-list real. Use the frost rule to catch the
mistake in review and the proxy to catch it in production.

Nothing frost does substitutes for any of these. What frost does is make the
audit possible once they are in place.

---

## Site policy

Put rules the machine imposes in `/etc/frost/policy.d/*.policy`. They apply to
every run on that host, with or without `--policy`.

```policy
forbid running "sudo"              -- the job already has what it needs
forbid writing to "/etc/*"         -- machine configuration is managed elsewhere
require reaching only "*.internal", "api.github.com"
require timeout on "*"             -- an unbounded command wedges the runner
require an approval signed by "kA1b2c..."
```

Composition can only narrow. Site rules are added to whatever the project
passes, every rule is checked independently, and all must pass, so two
`require reaching only` lists intersect. There is no syntax that removes a
rule, so a project cannot loosen yours.

**Own the directory.** `root:root`, `0755` on the directory and `0644` on the
files. A job that can write its own site policy has no site policy.

```bash
install -d -o root -g root -m 0755 /etc/frost/policy.d
install -o root -g root -m 0644 datacenter.policy /etc/frost/policy.d/00-base.policy
```

**A present but unreadable policy is a refusal**, deliberately. Treating it as
absent is how a machine quietly stops being governed, and it fails the way you
want: exit 2, nothing runs.

`FROST_SITE_POLICY_DIR` adds a second directory, for a container image without
a writable `/etc`. It cannot remove the first. There is no variable that means
"use this policy instead", and there should never be one: a knob that relaxes
a host rule is a bypass with a friendly name, and it is the first thing anyone
looks for when a build fails.

---

## Approvals in a pipeline

An unsigned approval says *that* something was approved. It does not say who
approved it, and anything that can write the file can grant itself one,
including the agent whose escalation the approval exists to catch.

Give approvers keys, name them in the site policy, and keep the private halves
out of the runner:

```bash
frost --new-approver-key ~/.frost/keys/alice     # on a person's machine
# public key: kA1b2c...  -> goes in /etc/frost/policy.d/10-approvals.policy
```

```policy
require an approval signed by "kA1b2c...", "kZ9y8x..."
```

```bash
frost --approve --sign-with ~/.frost/keys/alice --approver alice \
      --commit "$GITHUB_SHA" deploy.frost
```

The signature covers the capability set, the script, the commit, and the
approver's own name and key, so it cannot be lifted onto somebody else.
`.approved` files are committed and reviewed like any other change.

**Verification never degrades.** If the `cryptography` extra is missing from
an image, a required signature is refused rather than assumed valid. Trimming
a base image cannot silently disable the check.

**The commit is recorded and not verified by frost**, which cannot know which
revision is being deployed, only which one the approver said they read.
Comparing it against the checkout belongs in the pipeline that did the
checkout, and is a three-line step worth writing:

```bash
approved=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('commit',''))" deploy.frost.approved)
[ "$approved" = "$GITHUB_SHA" ] || { echo "approval is for $approved"; exit 1; }
```

---

## Unattended runs

Set this on the runner, once:

```bash
export FROST_AUTOMATED=1
```

An automated run refuses `--approve` and `--ignore-approval`. A loop that can
approve is a loop that approves its own capability escalation, and the failure
is mundane rather than exotic: an agent hits `REFUSED: it can now reach
telemetry.example`, and the most helpful-looking next step in its search is to
re-approve. Everything else works normally, so a repair loop can fix syntax
and can never fix its way past the gate.

---

## Secrets

The keystore file **is not secret**. Envelope encryption with per-role public
keys means it can be committed, baked into an image, or served from anywhere.
Only role private keys need protecting, and those belong in the same place as
every other machine credential you already manage.

That single fact removes most of what people mean by "distributing the
keystore". What is genuinely missing today is key rotation and a grant history,
and this document will be wrong about that as soon as they exist.

---

## Telemetry

```bash
frost --events /var/log/frost/$FROST_RUN_ID.ndjson deploy.frost
```

One JSON object per line, flushed as it happens, which every collector reads
and which survives a run that is killed halfway.

Ship it with whatever you already run. A Vector source is four lines:

```toml
[sources.frost]
type = "file"
include = ["/var/log/frost/*.ndjson"]
```

**Alert on `run.finish` where `refused` is present.** That is a control doing
its job, and it is the event most worth a human reading. It carries what
fired, the line, the rule's own explanation, and the digest of the policy it
came from, so the alert answers "who said no and why" without anyone opening
the repository.

**Report on `programs_unused` and `hosts_unused`.** These say which approved
capabilities a script never exercised. A job approved for six programs that
uses two is an approval somebody should tighten, and that is the one signal a
shell cannot produce.

Every run carries an id: `--run-id`, otherwise `FROST_RUN_ID`, otherwise
generated. An id you supply wins, because joining frost's record to your
pipeline's is the entire point. Children inherit it, so a log line from a
program three layers down ties back.

---

## What a run proves afterwards

`--record run.json` writes every effect: each command with its arguments,
exit status and output, every file touched, every secret read by name, and the
digest of every policy that governed the run. It is written **however the run
ends**, including when it failed or was interrupted, because that is the run
somebody needs to read.

Secret values are never recorded, which is what makes a recording safe to keep.

For an auditor, the useful chain is: the recording says what happened, its
`policies` say which rules were in force, and the `.approved` file says who
agreed to the capability set and against which commit.

---

## A checklist

- [ ] Image contains frost and no other interpreter, or MAC policy denies them
- [ ] `/etc/frost/policy.d` owned by root, not writable by the job user
- [ ] Site policy requires signed approvals, and names the approvers' keys
- [ ] Approver private keys are not on the runner
- [ ] `FROST_AUTOMATED=1` exported on every unattended runner
- [ ] `--sandbox` used where a boundary is wanted at runtime, and the run
      refuses when it cannot be enforced
- [ ] Egress restricted by a proxy or firewall, not only by policy text
- [ ] `--events` shipped to your collector, alerting on refusals
- [ ] `--record` retained for the runs you may be asked about
- [ ] Someone has read `--explain` for each script at least once, because
      every control above bounds what a script may reach and none of them
      reads intent
