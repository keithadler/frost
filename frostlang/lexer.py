"""Lexer for frost.

Line-oriented, like HyperTalk. Newlines are significant: one statement per
line. Comments run from `--` or `#` to end of line.
"""
# SPDX-License-Identifier: MIT

from dataclasses import dataclass


@dataclass
class Token:
    kind: str      # STR NUM WORD OP NL EOF
    value: object
    line: int
    col: int

    def __repr__(self):
        return f"{self.kind}({self.value!r})@{self.line}"


OPERATORS = ["&&", "&", "(", ")", ",", "+", "-", "*", "/", "^"]


class LexError(Exception):
    def __init__(self, msg, line):
        super().__init__(msg)
        self.msg = msg
        self.line = line


def tokenize(src):
    tokens = []
    lineno = 1
    i = 0
    n = len(src)

    # Skip a shebang line.
    if src.startswith("#!"):
        while i < n and src[i] != "\n":
            i += 1

    while i < n:
        c = src[i]

        if c == "\n":
            tokens.append(Token("NL", "\n", lineno, i))
            lineno += 1
            i += 1
            continue

        if c in " \t\r":
            i += 1
            continue

        # Line continuation: a trailing backslash folds the next line in.
        if c == "\\" and i + 1 < n and src[i + 1] == "\n":
            lineno += 1
            i += 2
            continue

        # Comments.
        if src.startswith("--", i) or c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue

        # String literal.
        if c == '"':
            i += 1
            buf = []
            while True:
                if i >= n or src[i] == "\n":
                    raise LexError("unterminated string literal", lineno)
                if src[i] == "\\" and i + 1 < n:
                    esc = src[i + 1]
                    # Known escapes are translated. Anything else keeps its
                    # backslash, so regex patterns like "\d+" survive intact.
                    buf.append(
                        {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                         '"': '"', "\\": "\\"}.get(esc, "\\" + esc)
                    )
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                buf.append(src[i])
                i += 1
            tokens.append(Token("STR", "".join(buf), lineno, i))
            continue

        # Number.
        if c.isdigit():
            start = i
            while i < n and (src[i].isdigit() or src[i] == "."):
                i += 1
            text = src[start:i]
            val = float(text) if "." in text else int(text)
            tokens.append(Token("NUM", val, lineno, start))
            continue

        # Word. Identifiers may contain letters, digits, underscore.
        if c.isalpha() or c == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] in "_"):
                i += 1
            tokens.append(Token("WORD", src[start:i].lower(), lineno, start))
            continue

        # Operators, longest match first.
        for op in OPERATORS:
            if src.startswith(op, i):
                tokens.append(Token("OP", op, lineno, i))
                i += len(op)
                break
        else:
            # Comparison symbols are accepted as sugar for the word forms.
            for op, canon in [("<=", "<="), (">=", ">="), ("<", "<"),
                              (">", ">"), ("=", "="), ("!=", "!=")]:
                if src.startswith(op, i):
                    tokens.append(Token("OP", canon, lineno, i))
                    i += len(op)
                    break
            else:
                raise LexError(f"unexpected character {c!r}", lineno)

    tokens.append(Token("NL", "\n", lineno, n))
    tokens.append(Token("EOF", None, lineno, n))
    return tokens
