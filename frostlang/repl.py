"""An interactive scratchpad for chunk expressions.

    $ frost --try
    frost> the third word of line 2 of it
    /api/login

The point is to make chunk expressions legible by letting people poke at them
against real text, rather than reading a grammar and hoping. It uses the same
parser and interpreter as a script, so anything that works here works there.
"""
# SPDX-License-Identifier: MIT

import sys

from .lexer import LexError
from .parser import Parser, ParseError, STATEMENT_STARTERS
from .interp import Interpreter, FrostError, to_text

SAMPLE = """10.0.0.1 GET /index.html 200 0.014
10.0.0.2 POST /api/login 500 1.221
10.0.0.3 GET /about.html 200 0.031
10.0.0.2 POST /api/login 500 0.998
10.0.0.9 GET /missing 404 0.008
10.0.0.4 GET /index.html 200 0.019
10.0.0.2 POST /api/pay 500 2.404"""

BANNER = """frost scratchpad: type an expression, see the result.
`it` holds seven lines of a sample access log.

  the first line of it              a chunk by ordinal
  the third word of line 2 of it    chunks nest
  the number of lines in it         counting
  words 2 to 3 of the last line of it   ranges
  every match of "\\d+" in the first line of it

:text <words>   replace the sample    :load <path>   read a file
:show           print the subject     :vars          list variables
:help           show this again       :quit          leave
"""

HELP_TAIL = """
Anything valid in a script is valid here, including whole statements:

  put "hello" into greeting
  run "date" with "+%A"
"""


class Repl:
    def __init__(self, subject=None, out=None):
        self.interp = Interpreter()
        self.interp.it = subject if subject is not None else SAMPLE
        # Resolved per instance, not bound once at import: a default of
        # `sys.stdout` in the signature captures whatever stdout was when this
        # module was first imported, which nothing can redirect afterwards.
        self.out = out if out is not None else sys.stdout

    def write(self, text=""):
        self.out.write(text + "\n")

    # -- one line at a time, so this is testable without a terminal

    def handle(self, line):
        line = line.strip()
        if not line:
            return True

        if line.startswith(":"):
            return self.command(line)

        try:
            value = self.evaluate(line)
        except (ParseError, LexError) as e:
            self.report("Syntax", e)
            return True
        except FrostError as e:
            self.report("Error", e)
            return True

        if value is not None:
            self.write(self.render(value))
        return True

    @staticmethod
    def render(value):
        if isinstance(value, list):
            if not value:
                return "(no matches)"
            shown = ", ".join(to_text(v) for v in value)
            noun = "item" if len(value) == 1 else "items"
            return f"{len(value)} {noun}: {shown}"
        rendered = to_text(value)
        return rendered if rendered != "" else "(empty)"

    def evaluate(self, line):
        """Read the line as an expression; fall back to a statement.

        When both readings fail, report the one the user more likely meant:
        a line starting with a statement keyword gets the statement error,
        anything else gets the expression error, which is usually far more
        specific than "expected a name".
        """
        first = line.split()[0].lower() if line.split() else ""
        looks_like_statement = first in STATEMENT_STARTERS

        expr_error = None
        if not looks_like_statement:
            parser = Parser(line)
            try:
                expr = parser.parse_expression()
                if parser.end_of_statement():
                    return self.interp.eval(expr)
            except (ParseError, LexError) as e:
                expr_error = e

        try:
            tree = Parser(line).parse_program()
        except (ParseError, LexError):
            if expr_error is not None:
                raise expr_error
            raise
        for stmt in tree:
            self.interp.exec_statement(stmt)
        return None

    def report(self, kind, e):
        self.write(f"  {kind.lower()}: {getattr(e, 'msg', e)}")
        hint = getattr(e, "hint", None)
        if hint:
            self.write(f"  hint: {hint}")

    def command(self, line):
        word, _, rest = line.partition(" ")
        rest = rest.strip()

        if word in (":quit", ":q", ":exit"):
            return False

        if word in (":help", ":h", ":?"):
            self.write(BANNER + HELP_TAIL)

        elif word == ":show":
            for n, text in enumerate(self.interp.it.split("\n"), start=1):
                self.write(f"  {n:>2}  {text}")

        elif word == ":text":
            self.interp.it = rest.replace("\\n", "\n")
            self.write(f"  subject is now {len(self.interp.it)} characters")

        elif word == ":load":
            try:
                with open(rest) as fh:
                    self.interp.it = fh.read().rstrip("\n")
            except OSError as e:
                self.write(f"  error: {e}")
                return True
            lines = len(self.interp.it.split("\n"))
            self.write(f"  loaded {rest} ({lines} lines)")

        elif word == ":vars":
            names = sorted(self.interp.globals)
            if not names:
                self.write("  no variables yet")
            for name in names:
                value = to_text(self.interp.globals[name])
                if len(value) > 60:
                    value = value[:57] + "..."
                self.write(f"  {name} = {value}")

        else:
            self.write(f"  unknown command {word!r}. Try :help")

        return True

    def run(self, stream=None, interactive=True):
        stream = stream or sys.stdin
        if interactive:
            self.write(BANNER)
        while True:
            if interactive:
                self.out.write("frost> ")
                self.out.flush()
            line = stream.readline()
            if not line:
                if interactive:
                    self.write()
                return 0
            if not self.handle(line):
                return 0


def main(subject_path=None):
    subject = None
    if subject_path:
        try:
            with open(subject_path) as fh:
                subject = fh.read().rstrip("\n")
        except OSError as e:
            sys.stderr.write(f"frost: cannot read {subject_path}: {e}\n")
            return 2
    repl = Repl(subject)
    try:
        return repl.run(interactive=sys.stdin.isatty())
    except KeyboardInterrupt:
        print()
        return 130
