"""Machine-readable diagnostics, with repairs an agent can apply.

frost already refuses bad scripts well: the errors are sentences, they carry a
line, and most carry a hint. That is the right output for a person reading at
3am. It is the wrong output for the thing that wrote the script.

An agent that gets `expected 'then' but found end of line` has to parse
English, guess the edit, and try again. The information needed to make the
edit already exists — several of the parser's hints literally contain the
corrected line — it is just buried in prose. This turns it into data:

    {"code": "missing-then", "line": 3, "column": 10,
     "message": "expected 'then' but found end of line",
     "repairs": [{"kind": "replace-line", "line": 3,
                  "text": "if count is 1 then", "confidence": "high"}]}

which closes the loop: generate, check, apply the repair, re-check. `frost
--repair` does exactly that and refuses to write unless the result parses,
so the loop cannot make a script worse.

Confidence is deliberately three-valued and honest about it:

    high    a mechanical rewrite. The parser knew the answer; this is only
            the plumbing to hand it over.
    likely  the fix is right, the placement or a detail is inferred — where
            to put a missing `end repeat`, which unit a timeout meant.
    guess   a name that looks close to one that exists. Worth offering to a
            human or a model, never worth applying unattended.

`--repair` applies `high` only.
"""
# SPDX-License-Identifier: MIT

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

SCHEMA_VERSION = 1

HIGH, LIKELY, GUESS = "high", "likely", "guess"


@dataclass
class Repair:
    """An edit that would fix a diagnostic.

    Line numbers are 1-based and refer to the source as it was checked, so
    repairs from a single run are applied back to front.
    """
    kind: str                   # replace-line | insert-line | delete-line
    line: int
    text: str = ""
    confidence: str = LIKELY
    why: str = ""

    def as_dict(self):
        return {"kind": self.kind, "line": self.line, "text": self.text,
                "confidence": self.confidence, "why": self.why}


@dataclass
class Diagnostic:
    severity: str               # error | danger | caution | note
    code: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None
    source: str = ""
    hint: str = ""
    repairs: List[Repair] = field(default_factory=list)

    def as_dict(self):
        out = {"severity": self.severity, "code": self.code,
               "message": self.message}
        if self.line is not None:
            out["line"] = self.line
        if self.column is not None:
            out["column"] = self.column
        if self.source:
            out["source"] = self.source
        if self.hint:
            out["hint"] = self.hint
        out["repairs"] = [r.as_dict() for r in self.repairs]
        return out


# ----------------------------------------------------------------- codes

def slug(message):
    """A stable-ish code for an error that has not been given one.

    Derived from the message with the variable parts stripped, so
    `there is no variable named 'total cost'` and `... named 'branch'` share
    a code. A hand-assigned code is always better; this keeps the schema
    usable before every raise site has one.
    """
    text = re.sub(r"'[^']*'", "", message)
    text = re.sub(r'"[^"]*"', "", text)
    text = re.sub(r"\d+", "", text)
    words = [w for w in re.findall(r"[a-z]+", text.lower())
             if w not in ("a", "an", "the", "is", "are", "of", "to", "in",
                          "it", "that", "this", "and", "or", "but", "be",
                          "was", "were", "there", "no", "not")]
    return "-".join(words[:4]) or "error"


def line_and_column(source, offset):
    """1-based line and column for a character offset."""
    if offset is None or offset < 0 or offset > len(source):
        return None, None
    prefix = source[:offset]
    line = prefix.count("\n") + 1
    column = offset - (prefix.rfind("\n") + 1) + 1
    return line, column


def source_line(source_lines, line):
    if line is not None and 0 < line <= len(source_lines):
        return source_lines[line - 1].rstrip()
    return ""


# --------------------------------------------------------------- repairs

def nearest(name, candidates, limit=2):
    """Names close enough to `name` to be worth suggesting."""
    if not name or not candidates:
        return []
    return difflib.get_close_matches(name, sorted(candidates), n=limit,
                                     cutoff=0.6)


def indent_of(text):
    return text[:len(text) - len(text.lstrip())]


def repairs_for(code, error, source_lines):
    """Every repair we can justify for one error.

    Each branch corresponds to a place the front end already knew the answer.
    Nothing here invents syntax: if the fix cannot be derived, no repair is
    offered, because a wrong repair costs an agent a whole round trip and
    teaches it the wrong grammar.
    """
    line = getattr(error, "line", None)
    text = source_line(source_lines, line)
    hint = getattr(error, "hint", "") or ""
    out = []

    if code == "run-takes-a-program-name":
        # The parser computed the corrected command line for the hint.
        match = re.search(r"did you mean:\s+(.*)$", hint)
        if match and text:
            out.append(Repair("replace-line", line,
                              indent_of(text) + match.group(1).strip(), HIGH,
                              "run takes a program and a list of arguments, "
                              "never a command line"))

    elif code == "missing-then":
        if text and not text.rstrip().endswith("then"):
            out.append(Repair("replace-line", line, text.rstrip() + " then",
                              HIGH, "an 'if' condition is closed by 'then'"))

    elif code == "timeout-needs-a-unit":
        if text:
            out.append(Repair("replace-line", line, text.rstrip() + " seconds",
                              LIKELY,
                              "a timeout needs a unit; seconds is the most "
                              "common, but check the intent"))

    elif code == "wait-needs-a-unit":
        if text:
            out.append(Repair("replace-line", line, text.rstrip() + " seconds",
                              LIKELY,
                              "a wait needs a unit; seconds is the most "
                              "common, but check the intent"))

    elif code == "global-is-reserved":
        if text:
            out.append(Repair("replace-line", line,
                              re.sub(r"\bglobal\b", "the global", text, count=1),
                              HIGH,
                              "a global is written 'the global <name>'"))

    elif code == "no-handler-named":
        wanted = getattr(error, "subject", None)
        for candidate in nearest(wanted, getattr(error, "candidates", ())):
            out.append(Repair(
                "replace-line", line,
                text.replace(wanted, candidate, 1) if text else "",
                GUESS, f"there is a handler named {candidate!r}"))

    elif code == "no-variable-named":
        wanted = getattr(error, "subject", None)
        for candidate in nearest(wanted, getattr(error, "candidates", ())):
            out.append(Repair(
                "replace-line", line,
                text.replace(wanted, candidate, 1) if text else "",
                GUESS, f"there is a variable named {candidate!r}"))

    elif code in ("missing-end-before-end-script", "missing-end-pipe"):
        # The block was never closed. Appending is right; where to append is
        # the inference, so this is never applied unattended.
        closer = re.search(r"missing '(end [^']+)'", str(error))
        if closer:
            out.append(Repair("insert-line", len(source_lines) + 1,
                              closer.group(1), LIKELY,
                              "the block is never closed; this appends the "
                              "closer at the end of the script"))

    elif code == "put-timeout-on-pipe-not-stage":
        match = re.search(r"within [^\n]*", text or "")
        if match and text:
            out.append(Repair("replace-line", line,
                              text.replace(" " + match.group(0), "", 1), LIKELY,
                              "the deadline belongs on the pipe; remove it "
                              "here and write 'pipe within ...'"))

    return out


# ------------------------------------------------------------ conversion

def from_error(error, source, kind="error"):
    """A Diagnostic from a LexError, ParseError or FrostError."""
    source_lines = source.split("\n")
    code = getattr(error, "code", None) or slug(getattr(error, "msg",
                                                        str(error)))
    line = getattr(error, "line", None)
    column = getattr(error, "column", None)
    if column is None:
        _, column = line_and_column(source, getattr(error, "offset", None))
    return Diagnostic(
        severity=kind,
        code=code,
        message=getattr(error, "msg", str(error)),
        line=line,
        column=column,
        source=source_line(source_lines, line),
        hint=getattr(error, "hint", "") or "",
        repairs=repairs_for(code, error, source_lines),
    )


def from_finding(finding, source):
    """A Diagnostic from an audit finding."""
    source_lines = source.split("\n")
    return Diagnostic(
        severity=finding.severity,
        code=slug(finding.title),
        message=finding.title,
        line=finding.line,
        source=source_line(source_lines, finding.line),
        hint=finding.detail,
    )


def from_policy_finding(finding, source):
    """A Diagnostic from a policy violation.

    The hint is the rule's own trailing comment where the author wrote one.
    A refusal that says only "no" leaves the reader — or the agent — to guess
    what to do instead, which is the whole reason rules carry hints.
    """
    source_lines = source.split("\n")
    default = ("refused by the policy" if finding.severity == "forbid"
               else "allowed, but the policy asked to be told")
    return Diagnostic(
        severity="error" if finding.severity == "forbid" else "caution",
        code="policy-" + slug(finding.what),
        message=finding.what,
        line=finding.line or None,
        source=source_line(source_lines, finding.line) if finding.line else "",
        hint=finding.hint or default,
    )


# -------------------------------------------------------------- reporting

def report(script, diagnostics, ok, exit_status, extra=None):
    """The whole answer, in one object an agent can branch on."""
    payload = {
        "schema": SCHEMA_VERSION,
        "script": script,
        "ok": ok,
        "exit": exit_status,
        "diagnostics": [d.as_dict() for d in diagnostics],
    }
    if extra:
        payload.update(extra)
    return payload


# --------------------------------------------------------------- repairing

def apply_repairs(source, diagnostics, minimum=HIGH):
    """Apply every repair at or above `minimum`. Returns (text, applied).

    Back to front, so earlier line numbers stay valid. One repair per line,
    because two edits to the same line cannot both be right and applying
    either blindly would be guessing.
    """
    order = {HIGH: 3, LIKELY: 2, GUESS: 1}
    threshold = order[minimum]

    chosen = {}
    for diagnostic in diagnostics:
        for repair in diagnostic.repairs:
            if order.get(repair.confidence, 0) < threshold:
                continue
            if repair.line not in chosen:
                chosen[repair.line] = repair

    if not chosen:
        return source, []

    lines = source.split("\n")
    applied = []
    for line in sorted(chosen, reverse=True):
        repair = chosen[line]
        index = line - 1
        if repair.kind == "replace-line" and 0 <= index < len(lines):
            lines[index] = repair.text
            applied.append(repair)
        elif repair.kind == "insert-line":
            lines.insert(min(index, len(lines)), repair.text)
            applied.append(repair)
        elif repair.kind == "delete-line" and 0 <= index < len(lines):
            del lines[index]
            applied.append(repair)
    return "\n".join(lines), applied
