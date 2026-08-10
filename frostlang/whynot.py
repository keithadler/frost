"""What would have to change, and what else that would allow.

A refusal says no and says which rule said it. The next question is always
"so what would I have to permit?", and answering it by hand means reading the
policy language, finding the rule, and working out what a change to it would
mean. That is a job frost can do, and it already has both halves: the rule
that fired and the capability that tripped it.

## Why this is a report and not a fix

The obvious shape is a patch: emit the policy delta, apply it, carry on. That
would be a mistake, for two reasons.

**A policy change is global.** Relaxing `forbid running "curl"` does not
permit this script's `curl`. It permits every `curl` on that host, in every
script, from then on. A "minimal delta" framing hides exactly the fact a
reviewer needs, so every suggestion here states what it would allow beyond the
script that prompted it.

**The thing asking is often the thing being constrained.** An agent that hits
a refusal and is handed the exact edit that clears it has been handed the
instructions for widening its own bounds, which is the failure `--automated`
exists to prevent elsewhere. So nothing here writes to a policy file, and
under `--automated` it declines to answer at all: a machine that cannot
approve should not be drafting its own permission slip either.

What it is for is the human deciding. "It needs `curl` to `api.github.com`
and nothing else" is a five-second judgement when someone writes it down, and
twenty minutes of policy archaeology when they do not.
"""
# SPDX-License-Identifier: MIT

from .audit import NOT_IN_ALLOW_LIST


def _scope(kind, subject):
    """What a change to this rule would permit beyond the script at hand."""
    if kind == "run":
        return (f"every command running {subject!r}, in every script this "
                f"policy covers")
    if kind == "reach" or kind == "reach_only":
        return f"every connection to {subject}, from any script"
    if kind in ("read", "write", "delete"):
        return f"every {kind} of {subject}, from any script"
    if kind == "getenv":
        return f"reading {subject} anywhere, not only here"
    if kind == "readsecret":
        return f"reading the secret {subject} in any script with the role"
    if kind == "count":
        return "the same allowance for every script this policy covers"
    return "more than this script, wherever this policy applies"


def _suggestion(finding, rules):
    """The narrowest change that would clear one refusal, and its blast radius.

    Narrowest is not the same as small. Where a rule can be tightened rather
    than removed the tightening is offered first, because `require reaching
    only "a", "b"` is a smaller change than deleting the rule and a reviewer
    who is only shown the deletion will take it.
    """
    rule = getattr(finding, "rule", None)
    kind = getattr(rule, "kind", None) or ""
    subject = getattr(rule, "subject", "") or ""

    # Only a refusal that names a real subject can be repaired by widening
    # the list. Asking that positively, rather than trying to recognise the
    # runtime case, means an unfamiliar refusal falls through to the honest
    # non-answer instead of becoming a confident and wrong edit. The first
    # version asked the other way, looked for a sentinel that never appears in
    # this prose, and offered to allow-list the words "a destination built at
    # runtime".
    named = NOT_IN_ALLOW_LIST in finding.what

    if kind == "reach_only":
        if not named:
            return (None,
                    "this destination is built at runtime, so no allow-list "
                    "can cover it",
                    "nothing would make it checkable except putting the URL "
                    "in the command")
        host = finding.what.split("reaching ", 1)[-1].split(",")[0]
        allowed = ", ".join(f'"{h}"' for h in (rule.detail or []))
        return (f'require reaching only {allowed}, "{host}"',
                f"widen the allow-list to include {host}",
                _scope("reach_only", host))

    if kind == "getenv_only":
        if not named:
            return (None,
                    "this variable is named at runtime, so no allow-list can "
                    "cover it",
                    "nothing would make it checkable except naming the "
                    "variable in the script")
        name = finding.what.split("environment ", 1)[-1].split(",")[0]
        allowed = ", ".join(f'"{n}"' for n in (rule.detail or []))
        return (f'require reading only the environment {allowed}, "{name}"',
                f"widen the allow-list to include {name}",
                _scope("getenv", name))

    if kind == "count":
        return (f"require at most {finding.count} {rule.noun}"
                if getattr(finding, "count", None) is not None else None,
                f"raise the limit on {rule.noun}",
                _scope("count", rule.noun))

    if kind in ("run", "read", "write", "delete", "getenv", "setenv",
                "readsecret", "reach", "chfolder"):
        verb = {"run": "running", "read": "reading", "write": "writing to",
                "delete": "deleting", "getenv": "reading the environment",
                "setenv": "setting", "readsecret": "reading secret",
                "reach": "reaching",
                "chfolder": "changing folder"}[kind]
        line = (f'forbid {verb} "{subject}"' if subject and subject != "*"
                else f"forbid {verb}")
        return (f"narrow or remove:  {line}",
                f"the rule as written refuses this outright",
                _scope(kind, subject or "it"))

    return (None, "no mechanical change clears this", "")


def explain_refusals(findings, rules, automated=False):
    """A report on what a policy would have to permit. Never a patch."""
    blocked = [f for f in findings if f.severity == "forbid"]
    if not blocked:
        return ""

    if automated:
        return ("\nA policy change is a decision for a person. This run is "
                "automated,\nso frost will not draft one: a machine that "
                "cannot approve should not\nbe writing its own permission "
                "slip either. Run it again without\n--automated to see what "
                "would have to change.\n")

    out = ["", "What would have to change, if this should be allowed:", ""]
    for finding in blocked:
        change, why, scope = _suggestion(finding, rules)
        out.append(f"  {finding.what}")
        if change:
            out.append(f"    change:  {change}")
        out.append(f"    effect:  {why}")
        if scope:
            out.append(f"    allows:  {scope}")
        out.append("")
    out.append("None of this is applied. A policy change permits more than "
               "the script")
    out.append("that prompted it, so it belongs to whoever owns the policy.")
    return "\n".join(out) + "\n"
