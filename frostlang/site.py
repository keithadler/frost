"""Policy the machine brings, and proof of which rules were in force.

A policy that lives beside the script is a policy the author of the script
controls. That is right for a project's own rules and useless as a datacenter
control: the thing being constrained should not be holding the constraint. So
frost also reads policy from the host.

    /etc/frost/policy.d/*.policy

## Composition can only narrow

Site rules are added to the project's, never merged with them and never
replaced by them. That the result can only get stricter is not a rule enforced
here, it falls out of how policy is checked: every rule is evaluated
independently and all of them must pass. Two `require reaching only` lists
therefore compose as an intersection, which is the safe direction, and a
project cannot widen a site rule because there is no syntax that removes one.

`--policy` adds. It has never replaced the site's rules and must not learn how.

## No environment override

There is deliberately no variable that says "use this policy instead". A knob
that relaxes a host rule is a bypass with a friendly name, and the first thing
anyone does with a failing build is look for one.

`FROST_SITE_POLICY_DIR` exists and only *adds* another directory, which is how
a test or a container image supplies rules without a writable `/etc`. Pointing
it somewhere empty does not disable anything, because the real directory is
read either way.

## Provenance

Every policy that was applied is recorded by path and by digest, and that ends
up in the manifest and in any recording. Otherwise an audit can establish that
a policy existed and never that this run was governed by it, which is the
difference between a control and a claim about one.
"""
# SPDX-License-Identifier: MIT

import glob
import hashlib
import os

SITE_DIR = "/etc/frost/policy.d"
EXTRA_DIR_ENV = "FROST_SITE_POLICY_DIR"


class SitePolicyError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


def directories(environ=None):
    environ = os.environ if environ is None else environ
    out = [SITE_DIR]
    extra = environ.get(EXTRA_DIR_ENV)
    if extra:
        out.append(extra)
    return out


def files(environ=None):
    """Every site policy file, in a stable order.

    Sorted by name so two machines with the same files apply them in the same
    order, which matters only for the order findings are reported in, and
    matters enough that it should not depend on a directory listing.
    """
    found = []
    for directory in directories(environ):
        found.extend(sorted(glob.glob(os.path.join(directory, "*.policy"))))
    return found


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(environ=None):
    """Site rules, and what they came from.

    Fails closed. A site policy that is present and unreadable is not the same
    as no site policy, and treating it as none is how a machine quietly stops
    being governed.
    """
    from .audit import parse_policy, PolicyError

    rules, provenance = [], []
    for path in files(environ):
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError as e:
            raise SitePolicyError(
                f"the site policy {path} cannot be read: {e}",
                hint="a site policy that is present and unreadable is not the "
                     "same as no site policy. Fix the permissions, or remove "
                     "the file deliberately.")
        try:
            rules.extend(parse_policy(text))
        except PolicyError as e:
            raise SitePolicyError(f"the site policy {path} does not parse: {e}")
        provenance.append({"path": path, "sha256": digest(text),
                           "origin": "site"})
    return rules, provenance


def note(path, text, origin="project"):
    return {"path": path, "sha256": digest(text), "origin": origin}


def describe(provenance):
    """The lines `--explain` prints, so a reader knows what was applied."""
    if not provenance:
        return []
    out = ["Governed by:"]
    width = max(len(p["path"]) for p in provenance)
    for entry in provenance:
        out.append(f"  {entry['path'].ljust(width)}  {entry['sha256'][:12]}  "
                   f"({entry['origin']})")
    return out
