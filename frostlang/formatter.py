"""`frost --format` — canonical layout for a frost script.

This works on lines and tokens rather than on the parse tree, for one reason:
the parse tree does not contain comments, and a formatter that silently eats
comments is worse than no formatter at all.

Guarantees, each covered by a test:

  * comments and their placement survive
  * formatting is idempotent — running it twice changes nothing
  * the parse tree before and after is identical, so meaning cannot shift
  * a file that does not parse is refused rather than mangled
"""
# SPDX-License-Identifier: MIT

import re

from .lexer import tokenize
from .parser import parse

INDENT = "    "

# Lines that close a block, or continue one at the outer level.
DEDENT_BEFORE = re.compile(r"^(end\b|else\b)", re.I)
INDENT_AFTER = re.compile(
    r"^(if\b.*\bthen\s*$"
    r"|repeat\b"
    r"|to\b"
    r"|else\s*$"
    r"|else\s+if\b.*\bthen\s*$"
    r"|ensure\s*$"
    r"|(?:try\s+to\s+)?pipe\b(?!.*\bend\s+pipe\b))",
    re.I)


def strip_comment(line):
    """Split a line into code and trailing comment, respecting strings."""
    out = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == '"':
            out.append(c)
            i += 1
            while i < n:
                out.append(line[i])
                if line[i] == "\\" and i + 1 < n:
                    out.append(line[i + 1])
                    i += 2
                    continue
                if line[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if line.startswith("--", i) or c == "#":
            return "".join(out), line[i:]
        out.append(c)
        i += 1
    return "".join(out), ""


SPECIAL_ESCAPES = {"n", "t", "r", "0", '"', "\\"}


def quote(value):
    r"""Re-emit a string literal, escaping only what has to be escaped.

    A regex like `\d+` must come back out as `\d+`, not `\\d+`. A backslash
    only needs doubling when the next character would otherwise turn it into a
    known escape.
    """
    out = ['"']
    i = 0
    while i < len(value):
        c = value[i]
        if c == '"':
            out.append('\\"')
        elif c == "\n":
            out.append("\\n")
        elif c == "\t":
            out.append("\\t")
        elif c == "\r":
            out.append("\\r")
        elif c == "\0":
            out.append("\\0")
        elif c == "\\":
            nxt = value[i + 1] if i + 1 < len(value) else None
            out.append("\\\\" if nxt is None or nxt in SPECIAL_ESCAPES
                       else "\\")
        else:
            out.append(c)
        i += 1
    out.append('"')
    return "".join(out)


def normalise_spacing(code):
    """One space between tokens; none inside a string; tidy commas."""
    if not code.strip():
        return ""
    trailer = ""
    if code.rstrip().endswith("\\"):
        code = code.rstrip()[:-1]
        trailer = " \\"
    try:
        tokens = tokenize(code)
    except Exception:
        return code.strip()

    pieces = []
    for t in tokens:
        if t.kind in ("NL", "EOF"):
            continue
        if t.kind == "STR":
            pieces.append(quote(t.value))
        elif t.kind == "NUM":
            # str() round-trips through the lexer to the same value, which is
            # the point: rewriting 5.0 as 5 would swap a float literal for an
            # int one and break the identical-tree guarantee above.
            pieces.append(str(t.value))
        else:
            pieces.append(str(t.value))

    out = ""
    for piece in pieces:
        if not out:
            out = piece
        elif piece in (",", ")"):
            out += piece
        elif out.endswith("("):
            out += piece
        else:
            out += " " + piece
    return out + trailer


def format_source(text):
    """Return the canonical layout of a script. Raises if it does not parse."""
    # Refuse to format something broken — but without resolving handler
    # names. Layout is a lexical question, and a file that imports cannot
    # have its names resolved on its own.
    parse(text, resolve=False)

    lines = text.split("\n")
    shebang = None
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
        lines = lines[1:]

    out = []
    depth = 0
    blank_run = 0
    continuing = False

    for raw in lines:
        code, comment = strip_comment(raw)
        stripped = code.strip()

        if not stripped and not comment.strip():
            blank_run += 1
            continue

        # Collapse runs of blank lines to one, and drop leading blanks.
        if blank_run and out:
            out.append("")
        blank_run = 0

        was_continuing = continuing
        continuing = stripped.rstrip().endswith("\\")

        if not was_continuing and DEDENT_BEFORE.match(stripped):
            depth = max(0, depth - 1)

        body = normalise_spacing(stripped)
        # A wrapped argument list sits one level in from its own statement.
        pad = INDENT * (depth + 1) if was_continuing else INDENT * depth

        if not body:                              # a comment on its own line
            out.append(pad + comment.strip())
        elif comment:
            out.append(f"{pad}{body}  {comment.strip()}")
        else:
            out.append(pad + body)

        if not was_continuing and INDENT_AFTER.match(stripped):
            depth += 1

    result = "\n".join(out).rstrip("\n")
    if shebang:
        result = shebang + "\n" + result
    return result + "\n"
