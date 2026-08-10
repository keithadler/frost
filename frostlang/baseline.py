"""What a script was approved to do, and whether it still fits.

`--frozen` asks *is this byte-identical to what I reviewed?* That is the right
question for a vendored module and the wrong one for a script a model
regenerates: every regeneration trips it, so you re-lock every time, and
re-locking every time means the check has stopped telling you anything.

This asks the question that survives regeneration: **did it get more powerful
than what was approved?**

    frost --approve deploy.frost        record what it may do today
    frost --as-approved deploy.frost    refuse if it gained a capability

## The attack this is for

Not injection. frost already makes a value unable to become syntax, and that
closes the case where hostile text flows into a command. It does nothing about
the case where an agent *reads* something hostile and then writes perfectly
valid frost that obeys it — the script parses, formats canonically, and would
survive any grammar. The model is not confused about syntax. It has been
persuaded to use authority it legitimately holds.

A policy file answers that by being written by a person, ahead of time, out of
band from generation. But a policy has to be written, and most repositories
will not have one on day one. A baseline needs no rules at all: it records
what the script already did on the day somebody read it, and fails the build
when a later version does more. The comparison is against the reviewer's own
past judgement rather than against a security model they had to author.

## Widening only

A capability that disappears is fine and is not reported. A capability that
appears is a widening, and under `--as-approved` it refuses. That asymmetry is
the whole point: a script that stops touching the network needs no ceremony,
and one that starts touching it needs a human.

Line numbers are deliberately absent. A baseline that changed when a comment
moved would be re-approved reflexively, and a check people re-approve without
reading is worse than no check, because it launders exactly the change it was
meant to catch.

What is not recorded: how long a script waits, and which exit codes it can
return. Neither is authority — a script that sleeps longer is slower, not more
powerful — and putting them here would produce churn that trains people to
approve without looking.
"""
# SPDX-License-Identifier: MIT

import json

SCHEMA = 1
RUNTIME = "(built at runtime)"

# Each entry is (key, heading) — the heading is what a refusal says, so it has
# to read as a sentence: "it can now run curl".
SETS = (
    ("programs", "run"),
    ("reaches", "reach"),
    ("reads", "read"),
    ("writes", "write to"),
    ("deletes", "delete"),
    ("env_reads", "read the environment variable"),
    ("env_writes", "set the environment variable"),
    ("folder_changes", "work in"),
    ("secrets", "read the secret"),
    ("secret_releases", "let a secret leave the process"),
)


class BaselineError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


def capability_set(caps):
    """The manifest as comparable facts: no lines, no counts, no order."""
    def paths(pairs):
        return sorted({p or RUNTIME for p, _ in pairs})

    return {
        "programs": sorted({c.program or RUNTIME for c in caps.commands}),
        # Where it goes, not just what it runs. Without this, swapping the URL
        # a `curl` points at is invisible — and a persuaded model does not need
        # a new program, only a new destination.
        "reaches": sorted({h for h, _ in caps.reaches}),
        "reads": paths(caps.reads),
        "writes": paths(caps.writes),
        "deletes": paths(caps.deletes),
        "env_reads": sorted({n or RUNTIME for n, _ in caps.env_reads}),
        "env_writes": sorted({n or RUNTIME for n, _ in caps.env_writes}),
        "folder_changes": paths(caps.folder_changes),
        "secrets": sorted({f"{n or RUNTIME} (from the {src})"
                           for n, src, _ in caps.secret_reads}),
        "secret_releases": sorted({_release(where, what)
                                   for where, what, _ in
                                   caps.secret_releases}),
        # Not a set: an unknowable name is a capability nobody can bound, so
        # more of them is more power even when no set gained a member.
        "unknowable": caps.dynamic,
    }


def _release(where, what):
    return {
        "argument": f"as an argument to {what or 'a program'}",
        "input": f"on the standard input of {what or 'a program'}",
        "file": f"written to {what or 'a file'}",
        "environment": f"in the environment variable {what}",
    }[where]


def widenings(approved, current):
    """Everything `current` can do that `approved` did not allow.

    Order is the order of SETS, so a refusal reads the same way every time.
    """
    out = []
    for key, verb in SETS:
        gained = [v for v in current.get(key, [])
                  if v not in set(approved.get(key, []))]
        for value in gained:
            out.append(f"it can now {verb} {value}")

    before = approved.get("unknowable", 0)
    now = current.get("unknowable", 0)
    if now > before:
        out.append(
            f"it now builds {now} name(s) at runtime, up from {before} — "
            f"a name nobody can read ahead of time is a capability nobody "
            f"can bound")
    return out


def narrowings(approved, current):
    """Capabilities that went away. Reported by `--approve`, never refused."""
    out = []
    for key, verb in SETS:
        lost = [v for v in approved.get(key, [])
                if v not in set(current.get(key, []))]
        for value in lost:
            # "needs to" rather than conjugating the verb. Adding an "s"
            # produced "it no longer reachs" and "leave the processs": the
            # headings are phrases, not verbs, and there is no rule that
            # inflects all of them correctly.
            out.append(f"it no longer needs to {verb} {value}")
    return out


# ------------------------------------------------------------------ the file

def path_for(script):
    return script + ".approved"


def write(script, caps, path=None, sign_with=None, approver=None,
          commit=None):
    payload = {"schema": SCHEMA, "script": script,
               "capabilities": capability_set(caps)}
    if commit:
        payload["commit"] = commit
    if sign_with:
        from . import signing
        payload = signing.sign(payload, sign_with, approver or "unnamed")
    with open(path or path_for(script), "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return payload


def read(path):
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise BaselineError(
            f"there is no approval at {path}",
            hint="record one with --approve, read what it says, and commit it")
    except ValueError as e:
        raise BaselineError(f"{path} is not a usable approval: {e}")

    if payload.get("schema") != SCHEMA:
        raise BaselineError(
            f"{path} is a version {payload.get('schema')} approval; this "
            f"frost writes version {SCHEMA}",
            hint="re-approve with --approve, after reading what changed")
    if "capabilities" not in payload:
        raise BaselineError(
            f"{path} records no capabilities",
            hint="re-approve with --approve")
    return payload["capabilities"]


def read_whole(path):
    """The approval as stored, signature and all, for verification."""
    import json as _json
    try:
        with open(path) as fh:
            return _json.load(fh)
    except FileNotFoundError:
        raise BaselineError(
            f"there is no approval at {path}",
            hint="record one with --approve, read what it says, and commit it")
    except ValueError as e:
        raise BaselineError(f"{path} is not a usable approval: {e}")
