"""Findings in the format code review tools already read.

`--json` is frost's own shape and is the right thing for an agent, which can
be told what the fields mean. A pull request cannot be told anything. GitHub,
GitLab and every code-scanning tool in between read SARIF, and a finding that
arrives as SARIF appears as an annotation on the diff line it concerns, in
front of the person deciding whether to merge.

That is the whole argument. The analysis was already there; what was missing
was arriving at the moment somebody is looking. A refusal in a CI log is read
by whoever opens the log, which on a green-enough day is nobody.

Nothing here re-derives anything. A `Diagnostic` already carries a severity, a
stable code, a line, a column, a hint and any repairs, and this is a remapping
of those onto the vocabulary the tools expect.
"""
# SPDX-License-Identifier: MIT

import json

SCHEMA = ("https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
          "Schemas/sarif-schema-2.1.0.json")
VERSION = "2.1.0"

# frost grades findings for a person reading a manifest; SARIF has three
# levels and tools colour them. `danger` is not "an error in the code", it is
# "this will do something you should look at", which is what a warning is for
# in a review tool. Only a genuine refusal is an error.
LEVELS = {
    "error": "error",
    "danger": "warning",
    "caution": "warning",
    "warning": "warning",
    "note": "note",
    "forbid": "error",
}


def _level(severity):
    return LEVELS.get(severity, "warning")


def _rule(code, diagnostics):
    """One rule entry, described by the first finding that used it."""
    first = next(d for d in diagnostics if d.code == code)
    rule = {
        "id": code,
        "name": code.replace("-", " ").title().replace(" ", ""),
        "shortDescription": {"text": first.message},
        "defaultConfiguration": {"level": _level(first.severity)},
    }
    if first.hint:
        rule["fullDescription"] = {"text": first.hint}
    return rule


def _location(path, diagnostic):
    region = {"startLine": max(1, diagnostic.line or 1)}
    if diagnostic.column:
        region["startColumn"] = diagnostic.column
    if diagnostic.source:
        region["snippet"] = {"text": diagnostic.source}
    return {"physicalLocation": {
        "artifactLocation": {"uri": path},
        "region": region,
    }}


def _fixes(path, diagnostic):
    """A repair, as a whole-line replacement a tool can offer to apply.

    Only replacements are expressed. An insertion whose position frost is not
    certain of would become a one-click edit somebody trusts, and the
    confidence levels exist precisely so that guesses are not applied
    unattended.
    """
    out = []
    for repair in diagnostic.repairs:
        if repair.kind != "replace-line" or repair.confidence == "guess":
            continue
        out.append({
            "description": {"text": f"{repair.why} ({repair.confidence})"},
            "artifactChanges": [{
                "artifactLocation": {"uri": path},
                "replacements": [{
                    "deletedRegion": {"startLine": repair.line,
                                      "endLine": repair.line},
                    "insertedContent": {"text": repair.text},
                }],
            }],
        })
    return out


def report(path, diagnostics, version="0"):
    """A SARIF log for one script."""
    codes = []
    for d in diagnostics:
        if d.code not in codes:
            codes.append(d.code)

    results = []
    for d in diagnostics:
        result = {
            "ruleId": d.code,
            "level": _level(d.severity),
            "message": {"text": d.message
                        + (f" ({d.hint})" if d.hint else "")},
            "locations": [_location(path, d)],
        }
        fixes = _fixes(path, d)
        if fixes:
            result["fixes"] = fixes
        results.append(result)

    return {
        "$schema": SCHEMA,
        "version": VERSION,
        "runs": [{
            "tool": {"driver": {
                "name": "frost",
                "version": version,
                "informationUri": "https://github.com/keithadler/frost",
                "rules": [_rule(c, diagnostics) for c in codes],
            }},
            "results": results,
        }],
    }


def dump(path, diagnostics, version="0"):
    return json.dumps(report(path, diagnostics, version), indent=2) + "\n"
