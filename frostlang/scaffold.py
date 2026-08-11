"""A starter policy, written from what a script already does.

The policy engine is the most useful thing here and the least used, because
the first step is a blank file. Somebody has to sit down and enumerate what a
script may do, in a language they have just met, before they get anything
back. Most people do not, and then the engine that would have refused the bad
script never runs.

frost already knows what the script does. It can write the boring 80% and
leave the judgement.

What comes out is a *description*, not a recommendation. Every capability the
script currently uses appears as a rule allowing it, with the dangerous ones
marked so they are read rather than skimmed. The author's job is to delete the
lines describing things that should not have been there, which is a much
easier job than starting from nothing.
"""
# SPDX-License-Identifier: MIT

import os

from .audit import NETWORK_PROGRAMS, RUNTIME_HOST, classify_path

HEADER = """-- A starter policy for {name}, written by `frost --policy-from`.
--
-- These rules describe what the script does today. That is not the same as
-- what it should be allowed to do: read each line and delete the ones that
-- describe something you did not intend. What is left is a contract.
--
-- Anything not named here is unconstrained. Rules that would refuse something
-- the script currently does are listed at the bottom, commented out.
"""


def policy_for(path, caps):
    name = os.path.basename(path)
    out = [HEADER.format(name=name)]

    programs = sorted({c.program for c in caps.commands if c.program})
    unknown = [c for c in caps.commands if not c.program]
    if programs:
        # An allow-list, matching what this file already does for hosts. It
        # used to emit `warn running "x"` per program, which described the
        # script accurately and taught the wrong shape: a deny-list is a list
        # of the programs somebody thought of, and it can never be completed.
        # The starter policy is where that habit is set.
        out.append("-- Programs it runs. Anything not named here is refused,")
        out.append("-- which is the point: a deny-list is a list of the")
        out.append("-- programs somebody thought of.")
        out.append("require running only "
                   + ", ".join(f'"{p}"' for p in programs))
        reaching = [p for p in programs if p in NETWORK_PROGRAMS]
        for program in reaching:
            out.append(f"--   {program} reaches the network")
        out.append("")
    if unknown:
        out.append(f"-- {len(unknown)} command(s) build the program name at "
                   f"runtime, so no rule can name them.")
        out.append("-- Consider refusing that outright:")
        out.append("-- forbid running \"*\"")
        out.append("")

    hosts = sorted({h for h, _ in caps.reaches if h != RUNTIME_HOST})
    if hosts:
        out.append("-- Where it reaches. Tighten this to the ones you meant.")
        out.append("require reaching only "
                   + ", ".join(f'"{h}"' for h in hosts))
        out.append("")
    if any(h == RUNTIME_HOST for h, _ in caps.reaches):
        out.append("-- A destination is built at runtime, so an allow-list "
                   "cannot be checked against it.")
        out.append("")

    for label, entries, verb in (
            ("Files it writes", caps.writes, "writing to"),
            ("Files it deletes", caps.deletes, "deleting"),
    ):
        paths = sorted({p for p, _ in entries if p})
        if not paths:
            continue
        out.append(f"-- {label}.")
        for p in paths:
            scope = classify_path(p)
            marker = "  -- outside the project" if scope != "project" else ""
            out.append(f'warn {verb} "{p}"{marker}')
        out.append("")

    secrets = sorted({n for n, _, _ in caps.secret_reads if n})
    if secrets:
        out.append("-- Secrets it reads.")
        for secret in secrets:
            out.append(f'warn reading secret "{secret}"')
        out.append("")

    out.append("-- Limits, sized to what it does now. Lower them.")
    out.append(f"require at most {max(1, len(caps.commands))} commands")
    if caps.writes:
        out.append(f"require at most {len(caps.writes)} files written")
    if caps.cleanups:
        out.append("require at least 1 cleanup")
    out.append("")

    out.append("-- The usual refusals. Uncomment the ones that apply here.")
    suggestions = (
        ('forbid running "sudo"', "the job should already have what it needs"),
        ('forbid running "rm" with "-rf"', "name the paths instead"),
        ('forbid writing to "/etc/*"', "machine config is managed elsewhere"),
        ('require every command to be checked',
         "a failure nobody reads is a failure nobody fixes"),
        ('require timeout on "*"', "an unbounded command wedges the job"),
        ("require an approval",
         "refuse a version that does more than was approved"),
    )
    width = max(len(r) for r, _ in suggestions) + 2
    for rule, why in suggestions:
        if _would_refuse(rule, caps):
            out.append(f"-- {rule.ljust(width)}-- {why}")
            out.append(f"--{' ' * width}   this one refuses the script as it "
                       f"stands; fix the script or leave it commented out")
        else:
            out.append(f"{rule.ljust(width + 3)}-- {why}")

    return "\n".join(out).rstrip() + "\n"


def _would_refuse(rule, caps):
    """Whether a suggested rule would refuse the script as it is now.

    Suggesting a rule that immediately fails the build is how a scaffold gets
    deleted rather than edited, so those are emitted commented out and marked.
    """
    from .audit import parse_policy, check
    try:
        findings = check(caps, parse_policy(rule))
    except Exception:                          # pragma: no cover
        return False
    return any(f.severity == "forbid" for f in findings)
