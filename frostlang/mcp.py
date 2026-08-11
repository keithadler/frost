"""frost as a Model Context Protocol server, over stdio.

An agent that wants to write a frost script can already shell out to `frost
--explain`, parse the text back, and guess at what the exit code meant. This
serves the same answers as structured data over JSON-RPC, which is the shape
the thing asking is already speaking.

## What it will not do

**It cannot run a script.** That is the whole design, not an omission waiting
to be filled in. frost exists because a machine writes the script and a person
decides whether it runs; a server that executes on request moves the decision
back to the machine and turns the review tool into a remote execution surface
that happens to have a policy engine attached.

So the tools here are exactly the ones a reviewer uses: parse it, describe what
it can do, run it past a policy, compare it with the last version, and say what
would have to change. The verdict comes back to the agent. Running stays a
command a person types.

**It does not read files.** Every tool takes source text. A tool that took a
path would let anything holding the other end of this pipe read any file the
frost process can reach, which is a capability nobody asked this server for and
one that no policy in here governs.

**It does not draft policy changes under automation.** `whynot` already
refuses when `--automated` is set, for the reason that an agent handed the edit
which clears its own refusal has been handed the instructions for widening its
own bounds. Every call through this server is automated by definition, so that
refusal is the one it gives.

## The transport

JSON-RPC 2.0, one message per line, on stdin and stdout. No dependencies: the
standard library has everything this needs, and frost's install claim is Python
and nothing else.

Anything the server wants to say to a human goes to stderr. A stray print on
stdout corrupts the protocol stream, which is a debugging session nobody
enjoys, so the run loop writes frames and nothing else there.
"""
# SPDX-License-Identifier: MIT

import json
import sys

from . import diagnostics
from .audit import (audit, check, describe, find_dangers, parse_policy,
                    verdict, PolicyError)
from .lexer import LexError
from .parser import parse, ParseError
from .interp import FrostError
from .whynot import explain_refusals

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

SOURCE = {
    "type": "string",
    "description": "The frost script itself, as text. Not a path: this "
                   "server does not read files.",
}


TOOLS = [
    {
        "name": "frost_check",
        "description":
            "Parse a frost script and report whether it is well formed, with "
            "the line and a repair for anything that is not. Use this before "
            "handing a script to anyone.",
        "inputSchema": {
            "type": "object",
            "properties": {"source": SOURCE},
            "required": ["source"],
        },
    },
    {
        "name": "frost_explain",
        "description":
            "The capability manifest: every program the script runs, file it "
            "reads or writes, host it reaches, environment variable it "
            "touches, and every standing danger found in it. This is what a "
            "human reviewer is shown.",
        "inputSchema": {
            "type": "object",
            "properties": {"source": SOURCE},
            "required": ["source"],
        },
    },
    {
        "name": "frost_policy",
        "description":
            "Check a script against a policy and report every refusal, with "
            "the rule that refused it and what would have to change. Nothing "
            "is applied: a policy change permits more than the script that "
            "prompted it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": SOURCE,
                "policy": {
                    "type": "string",
                    "description": "The policy text to check against.",
                },
            },
            "required": ["source", "policy"],
        },
    },
    {
        "name": "frost_diff",
        "description":
            "Compare two versions of a script by what they can do rather than "
            "by their text. Answers whether a rewrite widened the blast "
            "radius, which a text diff cannot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "before": {"type": "string",
                           "description": "The earlier script, as text."},
                "after": {"type": "string",
                          "description": "The later script, as text."},
            },
            "required": ["before", "after"],
        },
    },
    {
        "name": "frost_grammar",
        "description":
            "How to write frost: the statement forms, the closed keyword set, "
            "and the constructs it deliberately does not have. Read this "
            "before writing a script rather than guessing from examples.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text(body):
    return {"content": [{"type": "text", "text": body}]}


def _failed(body):
    return {"content": [{"type": "text", "text": body}], "isError": True}


def _parse_or_report(source):
    """(tree, error_result). Exactly one of the two is None.

    LexError and ParseError are not FrostError, and catching only the latter
    sent the commonest failure of all through the generic handler: the agent
    got the words "LexError: unexpected character" instead of a line, a column
    and a repair. The mistake that produces it is `${name}`, which is the
    first thing a model writes, so this is the path that matters most.
    """
    try:
        return parse(source), None
    except (FrostError, LexError, ParseError) as e:
        report = diagnostics.report(
            "<script>", [diagnostics.from_error(e, source)], False, 1)
        return None, _failed(json.dumps(report, indent=2))


def tool_check(args):
    tree, failure = _parse_or_report(args.get("source", ""))
    if failure:
        return failure
    caps = audit(tree)
    found = find_dangers(caps)
    return _text(json.dumps({
        "parses": True,
        "verdict": verdict(found),
        "statements": len(tree),
        "findings": [{"severity": f.severity, "title": f.title,
                      "detail": f.detail, "line": f.line} for f in found],
    }, indent=2))


def tool_explain(args):
    source = args.get("source", "")
    tree, failure = _parse_or_report(source)
    if failure:
        return failure
    caps = audit(tree)
    found = find_dangers(caps)
    manifest = describe(caps)
    if found:
        manifest += "\n\nFindings:\n" + "\n".join(
            f"  [{f.severity}] line {f.line}  {f.title}\n      {f.detail}"
            for f in found)
    manifest += f"\n\nVerdict: {verdict(found)}\n"
    return _text(manifest)


def tool_policy(args):
    tree, failure = _parse_or_report(args.get("source", ""))
    if failure:
        return failure
    try:
        rules = parse_policy(args.get("policy", ""))
    except PolicyError as e:
        return _failed(f"the policy does not parse: {e}")

    findings = check(audit(tree), rules)
    refused = [f for f in findings if f.severity == "forbid"]
    body = {
        "allowed": not refused,
        "findings": [{"severity": f.severity, "what": f.what, "line": f.line,
                      "hint": f.hint} for f in findings],
        # Automated by definition: every call here is a machine asking. The
        # report refuses to draft a widening for the same reason --automated
        # does, and says so rather than returning an empty field.
        "what_would_have_to_change": explain_refusals(findings, rules,
                                                      automated=True),
    }
    return _text(json.dumps(body, indent=2))


def tool_diff(args):
    """The same comparison `frost diff` makes, on text instead of paths.

    Built from the capability sets directly rather than by calling the CLI
    helper, which takes paths and prints. Routing this through a function that
    writes to stdout would corrupt the protocol stream on the very first call.
    """
    from . import baseline as B

    sets = {}
    for name in ("before", "after"):
        tree, failure = _parse_or_report(args.get(name, ""))
        if failure:
            return failure
        sets[name] = B.capability_set(audit(tree))

    gained = B.widenings(sets["before"], sets["after"])
    lost = B.narrowings(sets["before"], sets["after"])
    return _text(json.dumps({
        "wider": bool(gained),
        "gained": list(gained),
        "lost": list(lost),
        "summary": ("unchanged: both can do exactly the same things"
                    if not gained and not lost
                    else f"{len(gained)} gained, {len(lost)} lost"),
    }, indent=2))


def tool_grammar(args):
    from .context import model_context
    return _text(model_context())


HANDLERS = {
    "frost_check": tool_check,
    "frost_explain": tool_explain,
    "frost_policy": tool_policy,
    "frost_diff": tool_diff,
    "frost_grammar": tool_grammar,
}


def handle(message):
    """One request in, one response out, or None for a notification."""
    method = message.get("method")
    ident = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": ident, "result": {
            "protocolVersion": asked if asked in SUPPORTED else PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "frost", "version": _version()},
            "instructions":
                "frost reviews shell scripts before they run. This server "
                "reads and reports; it cannot run a script, and it never "
                "reads a file. Call frost_grammar before writing frost.",
        }}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(ident, -32602, f"there is no tool named {name!r}")
        try:
            return {"jsonrpc": "2.0", "id": ident,
                    "result": handler(params.get("arguments") or {})}
        except Exception as e:                      # noqa: BLE001
            # A tool that raises must answer with an error result rather than
            # killing the loop. The agent on the other end can act on "that
            # failed"; it can do nothing at all with a closed pipe.
            return {"jsonrpc": "2.0", "id": ident,
                    "result": _failed(f"{type(e).__name__}: {e}")}

    if ident is None:
        return None                                 # unknown notification
    return _error(ident, -32601, f"unknown method {method!r}")


def _error(ident, code, message):
    return {"jsonrpc": "2.0", "id": ident,
            "error": {"code": code, "message": message}}


def _version():
    from . import __version__
    return __version__


def serve(stdin=None, stdout=None):
    """Read framed requests until the stream ends. Returns an exit code."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write(stdout, _error(None, -32700, "that is not JSON"))
            continue
        if isinstance(message, list):
            # A batch. Answer each, and drop the notifications, which is what
            # the specification asks for and what a client will expect.
            answers = [a for a in (handle(m) for m in message) if a]
            if answers:
                for answer in answers:
                    _write(stdout, answer)
            continue
        answer = handle(message)
        if answer is not None:
            _write(stdout, answer)
    return 0


def _write(stream, payload):
    stream.write(json.dumps(payload) + "\n")
    stream.flush()
