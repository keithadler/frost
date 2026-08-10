"""Recursive-descent parser for frost.

The design constraint that shapes everything here: variable names are made of
ordinary English words separated by spaces (`error count`, `branch name`). That
only works if the keyword vocabulary is small and rigidly closed, so an
identifier can be defined as "a run of words that are not keywords".

HARD_WORDS below is that closed set. It is deliberately short. Chunk nouns
(line, word, item, character) are NOT in it, because `line count` has to be a
legal variable name; those are recognised contextually instead.
"""
# SPDX-License-Identifier: MIT

from .lexer import tokenize, Token
from . import ast as A


class ParseError(Exception):
    def __init__(self, msg, line, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line
        self.hint = hint


# Words that can never form part of an identifier.
HARD_WORDS = {
    "put", "into", "before", "after", "run", "with", "try", "to", "pipe",
    "end", "if", "then", "else", "repeat", "times", "while", "until", "for",
    "each", "as", "from", "of", "in", "quit", "the", "it", "and",
    "or", "not", "is", "are", "by", "contains", "starts", "ends", "at",
    "exit", "next", "add", "subtract", "multiply", "divide", "return",
    "matches", "like", "every", "replace", "within", "whole",
    "standard", "exists", "empty", "true", "false", "forever", "step",
    "delete", "greater", "less", "than", "least", "most",
    "global", "ensure", "reading",
}

CHUNK_SINGULAR = {
    "character": "character", "char": "character",
    "word": "word",
    "line": "line",
    "item": "item",
    "match": "match",
}
CHUNK_PLURAL = {
    "characters": "character", "chars": "character",
    "words": "word",
    "lines": "line",
    "items": "item",
    "matches": "match",
}

ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

# Shared with the policy checker, which compares a rule's bound against a
# timeout written in whatever unit the script happened to use.
TIME_UNITS = {
    "millisecond": 0.001, "milliseconds": 0.001, "ms": 0.001,
    "second": 1, "seconds": 1,
    "minute": 60, "minutes": 60,
    "hour": 3600, "hours": 3600,
}

STATEMENT_STARTERS = {
    "put", "run", "try", "pipe", "if", "repeat", "quit", "to", "return",
    "add", "subtract", "multiply", "divide", "exit", "next", "delete",
    "ensure",
}


class Parser:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.i = 0
        # How many `repeat` blocks enclose the statement being parsed. Reset
        # to zero inside a handler body, so loop control cannot reach across a
        # call boundary and break the caller's loop.
        self.loop_depth = 0
        # Set while reading the amount of a timeout. Time units are not
        # reserved words — `second line` has to stay a legal name — so a name
        # would otherwise swallow the unit and leave `within limit seconds`
        # unparseable.
        self.reading_timeout = False

    # ------------------------------------------------------------ token utils

    @property
    def cur(self) -> Token:
        return self.toks[self.i]

    def peek(self, offset=0) -> Token:
        j = self.i + offset
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def at_word(self, *words, offset=0):
        t = self.peek(offset)
        return t.kind == "WORD" and t.value in words

    def at_op(self, *ops, offset=0):
        t = self.peek(offset)
        return t.kind == "OP" and t.value in ops

    def advance(self):
        t = self.cur
        self.i += 1
        return t

    def expect_word(self, word, hint=None):
        if not self.at_word(word):
            raise ParseError(
                f"expected {word!r} but found {self.describe(self.cur)}",
                self.cur.line, hint)
        return self.advance()

    def expect_op(self, op):
        if not self.at_op(op):
            raise ParseError(
                f"expected {op!r} but found {self.describe(self.cur)}",
                self.cur.line)
        return self.advance()

    @staticmethod
    def describe(t):
        if t.kind == "NL":
            return "end of line"
        if t.kind == "EOF":
            return "end of script"
        return repr(t.value)

    def skip_newlines(self):
        while self.cur.kind == "NL":
            self.advance()

    def end_of_statement(self):
        return self.cur.kind in ("NL", "EOF")

    def expect_end_of_statement(self):
        if not self.end_of_statement():
            raise ParseError(
                f"unexpected {self.describe(self.cur)} at end of statement",
                self.cur.line)
        if self.cur.kind == "NL":
            self.advance()

    # ---------------------------------------------------------------- program

    def parse_program(self):
        stmts = []
        self.skip_newlines()
        while self.cur.kind != "EOF":
            stmts.append(self.parse_statement())
            self.skip_newlines()
        return stmts

    def parse_block(self, *terminators):
        """Parse statements until `end <terminator>`. Does not consume `end`."""
        stmts = []
        self.skip_newlines()
        while True:
            if self.cur.kind == "EOF":
                raise ParseError(
                    f"missing 'end {terminators[0]}' before end of script",
                    self.cur.line)
            if self.at_word("end"):
                return stmts
            if self.at_word("else"):
                return stmts
            stmts.append(self.parse_statement())
            self.skip_newlines()

    # ------------------------------------------------------------- statements

    def parse_statement(self):
        t = self.cur
        if t.kind != "WORD":
            raise ParseError(
                f"a statement must start with a word, found {self.describe(t)}",
                t.line)

        w = t.value
        if w == "put":
            return self.parse_put()
        if w == "run":
            return self.parse_run(checked=True)
        if w == "try":
            return self.parse_try()
        if w == "pipe":
            return self.parse_pipe(checked=True)
        if w == "if":
            return self.parse_if()
        if w == "repeat":
            return self.parse_repeat()
        if w == "quit":
            return self.parse_quit()
        if w == "to":
            return self.parse_handler()
        if w == "return":
            return self.parse_return()
        if w in ("add", "subtract", "multiply", "divide"):
            return self.parse_arith()
        if w in ("exit", "next"):
            return self.parse_loop_control()
        if w == "delete":
            return self.parse_delete()
        if w == "replace":
            return self.parse_replace()
        if w == "ensure":
            return self.parse_ensure()
        return self.parse_call()

    # put ------------------------------------------------------------------

    def parse_put(self):
        line = self.advance().line          # 'put'
        expr = self.parse_expression()
        mode = "into"
        target = None
        if self.at_word("into", "before", "after"):
            mode = self.advance().value
            target = self.parse_target()
        self.expect_end_of_statement()
        return A.Put(expr, target, mode, line)

    def parse_target(self):
        if self.at_word("standard"):
            self.advance()
            if self.at_word("output", "error"):
                return A.StreamTarget(self.advance().value)
            raise ParseError(
                "expected 'standard output' or 'standard error'",
                self.cur.line)
        if self.at_word("file"):
            self.advance()
            return A.FileTarget(self.parse_expression())
        if self.at_word("the"):
            # `the global x`, `the environment variable "N"`, `the current
            # folder` — each the writable counterpart of a readable form.
            line = self.advance().line
            if self.at_word("global"):
                self.advance()
                return A.GlobalTarget(
                    self.parse_identifier(target_position=True))
            if self.at_word("environment"):
                self.advance()
                self.expect_word("variable")
                return A.EnvTarget(self.parse_primary())
            if self.at_word("current"):
                self.advance()
                self.expect_word("folder")
                return A.FolderTarget()
            raise ParseError(
                f"cannot assign to 'the {self.describe(self.cur)[1:-1]}'"
                if self.cur.kind == "WORD" else "expected something writable",
                line,
                hint="the writable ones are: the global <name> / "
                     'the environment variable "NAME" / the current folder')
        return A.VarTarget(self.parse_identifier(target_position=True))

    def parse_assign_target(self):
        """A variable or an explicit global, for `add`/`subtract`/`replace`."""
        if self.at_word("the") and self.at_word("global", offset=1):
            self.advance()
            self.advance()
            return A.GlobalTarget(self.parse_identifier(target_position=True))
        return A.VarTarget(self.parse_identifier(target_position=True))

    def parse_identifier(self, target_position=False):
        """A run of non-keyword words joined by single spaces.

        In target position (after `into`) chunk nouns are not treated as the
        start of a chunk expression, so `put 3 into line count` works.
        Genuinely reserved words are still rejected, with a message that says
        which word is the problem.
        """
        words = []
        first = True
        while self.cur.kind == "WORD":
            w = self.cur.value
            if w in HARD_WORDS:
                break
            if self.reading_timeout and words and w in TIME_UNITS:
                break                      # the unit closes the amount
            if not first and not target_position:
                # Stop before something that starts a new construct.
                if w in CHUNK_SINGULAR or w in CHUNK_PLURAL:
                    nxt = self.peek(1)
                    if nxt.kind == "NUM" or (nxt.kind == "WORD"
                                             and nxt.value in ORDINALS):
                        break
            words.append(self.advance().value)
            first = False
        if not words and self.at_word("global"):
            raise ParseError(
                "'global' is a reserved word", self.cur.line,
                hint="to reach a global from inside a handler, write "
                     "'the global <name>'")
        if (target_position and words and self.cur.kind == "WORD"
                and self.cur.value in HARD_WORDS):
            # e.g. `put "" into error times` — `times` belongs to `repeat`.
            raise ParseError(
                f"{self.cur.value!r} is a reserved word and cannot be part of "
                f"a name", self.cur.line,
                hint=f"pick another word, such as "
                     f"{' '.join(words)} list")
        if not words:
            raise ParseError(
                f"expected a name but found {self.describe(self.cur)}",
                self.cur.line)
        return " ".join(words)

    # run / pipe -----------------------------------------------------------

    def parse_run(self, checked=True):
        line = self.expect_word("run").line
        program = self.parse_expression()
        args = []
        if self.at_word("with"):
            self.advance()
            args.append(self.parse_expression())
            while self.at_op(","):
                self.advance()
                self.skip_continuation()
                args.append(self.parse_expression())
        stdin, folder, timeout = self.parse_command_tail()
        self.check_command_line_mistake(program, args, line)
        self.expect_end_of_statement()
        return A.Run(program, args, checked, timeout, stdin, folder, line)

    def parse_command_tail(self):
        """The optional clauses that may follow a run or a pipe.

        `reading EXPR` puts text on the child's standard input, `in folder
        EXPR` chooses its working directory, and `within N seconds` gives it a
        deadline. Any order is accepted; each may appear once.
        """
        stdin = folder = timeout = None
        while True:
            if self.at_word("reading"):
                line = self.advance().line
                if stdin is not None:
                    raise ParseError("only one 'reading' clause is allowed",
                                     line)
                stdin = self.parse_expression()
                continue
            if self.at_word("in") and self.at_word("folder", offset=1):
                line = self.advance().line
                self.advance()
                if folder is not None:
                    raise ParseError("only one 'in folder' clause is allowed",
                                     line)
                folder = self.parse_expression()
                continue
            if self.at_word("within"):
                if timeout is not None:
                    raise ParseError("only one timeout is allowed",
                                     self.cur.line)
                timeout = self.parse_optional_timeout()
                continue
            return stdin, folder, timeout

    def parse_optional_timeout(self):
        """`within 30 seconds` — an optional tail on run and pipe."""
        if not self.at_word("within"):
            return None
        self.advance()
        self.reading_timeout = True
        try:
            amount = self.parse_additive()
        finally:
            self.reading_timeout = False
        scale = 1
        if self.cur.kind == "WORD" and self.cur.value in TIME_UNITS:
            scale = TIME_UNITS[self.advance().value]
        else:
            raise ParseError(
                "a timeout needs a unit", self.cur.line,
                hint="try: within 30 seconds / within 2 minutes / "
                     "within 500 milliseconds")
        if scale == 1:
            return amount
        return A.BinOp("*", amount, A.Lit(scale), amount.line)

    def skip_continuation(self):
        # Allow an argument list to wrap onto the next line after a comma.
        while self.cur.kind == "NL":
            self.advance()

    @staticmethod
    def check_command_line_mistake(program, args, line):
        """Catch `run "ls -la"` — the single most likely beginner error."""
        if isinstance(program, A.Lit) and isinstance(program.value, str):
            text = program.value
            if not args and (" " in text.strip()):
                parts = text.split()
                suggestion = 'run "%s" with %s' % (
                    parts[0], ", ".join('"%s"' % p for p in parts[1:]))
                raise ParseError(
                    "run takes a program name, not a command line",
                    line,
                    hint=f"did you mean:  {suggestion}")

    def parse_try(self):
        line = self.expect_word("try").line
        self.expect_word("to", hint="write 'try to run ...' or 'try to pipe'")
        if self.at_word("run"):
            node = self.parse_run(checked=False)
            node.line = line
            return node
        if self.at_word("pipe"):
            node = self.parse_pipe(checked=False)
            node.line = line
            return node
        raise ParseError("only 'run' and 'pipe' can be tried", line)

    def parse_pipe(self, checked=True):
        line = self.expect_word("pipe").line
        stdin, folder, timeout = self.parse_command_tail()
        self.expect_end_of_statement()
        stages = []
        self.skip_newlines()
        while not self.at_word("end"):
            if self.cur.kind == "EOF":
                raise ParseError("missing 'end pipe'", line)
            if not self.at_word("run"):
                raise ParseError(
                    "every stage of a pipe must be a 'run' statement",
                    self.cur.line)
            stages.append(self.parse_run(checked=True))
            self.skip_newlines()
        self.expect_word("end")
        self.expect_word("pipe")
        self.expect_end_of_statement()
        if len(stages) < 2:
            raise ParseError("a pipe needs at least two stages", line)
        for st in stages:
            if st.timeout is not None:
                raise ParseError(
                    "put the timeout on the pipe, not on a stage", st.line,
                    hint="write 'pipe within 30 seconds'")
            if st.stdin is not None:
                raise ParseError(
                    "only the pipe itself can read input; a stage reads from "
                    "the stage before it", st.line,
                    hint="write 'pipe reading <text>'")
            if st.folder is not None:
                raise ParseError(
                    "put the folder on the pipe, not on a stage", st.line,
                    hint="write 'pipe in folder <path>'")
        return A.Pipe(stages, checked, timeout, stdin, folder, line)

    # if -------------------------------------------------------------------

    def parse_if(self):
        line = self.expect_word("if").line
        cond = self.parse_expression()
        self.expect_word("then",
                         hint="an 'if' condition is followed by 'then'")

        # Single-line form: if X then <statement>
        if not self.end_of_statement():
            stmt = self.parse_statement()
            return A.If(cond, [stmt], None, line)

        self.expect_end_of_statement()
        then_block = self.parse_block("if")
        else_block = None

        if self.at_word("else"):
            self.advance()
            if self.at_word("if"):
                else_block = [self.parse_if()]
                return A.If(cond, then_block, else_block, line)
            self.expect_end_of_statement()
            else_block = self.parse_block("if")

        self.expect_word("end")
        self.expect_word("if")
        self.expect_end_of_statement()
        return A.If(cond, then_block, else_block, line)

    # repeat ---------------------------------------------------------------

    def parse_repeat(self):
        line = self.expect_word("repeat").line

        if self.at_word("forever"):
            self.advance()
            self.expect_end_of_statement()
            block = self.parse_loop_body()
            self.close_repeat()
            return A.RepeatForever(block, line)

        if self.at_word("while", "until"):
            until = self.advance().value == "until"
            cond = self.parse_expression()
            self.expect_end_of_statement()
            block = self.parse_loop_body()
            self.close_repeat()
            return A.RepeatWhile(cond, block, until, line)

        if self.at_word("with"):
            self.advance()
            var = self.parse_identifier()
            self.expect_word("from")
            start = self.parse_expression()
            self.expect_word("to")
            stop = self.parse_expression()
            step = None
            if self.at_word("by", "step"):
                self.advance()
                step = self.parse_expression()
            self.expect_end_of_statement()
            block = self.parse_loop_body()
            self.close_repeat()
            return A.RepeatWith(var, start, stop, step, block, line)

        if self.at_word("for"):
            self.advance()
            self.expect_word("each")
            kind = self.expect_chunk_noun()
            self.expect_word("in", hint="'repeat for each line in <source> as <name>'")
            source = self.parse_expression()
            self.expect_word("as",
                             hint="name the loop variable: '... as this line'")
            var = self.parse_identifier()
            self.expect_end_of_statement()
            block = self.parse_loop_body()
            self.close_repeat()
            return A.RepeatForEach(kind, source, var, block, line)

        # repeat <n> times
        count = self.parse_expression()
        self.expect_word("times")
        self.expect_end_of_statement()
        block = self.parse_loop_body()
        self.close_repeat()
        return A.RepeatTimes(count, block, line)

    def parse_loop_body(self):
        self.loop_depth += 1
        try:
            return self.parse_block("repeat")
        finally:
            self.loop_depth -= 1

    def close_repeat(self):
        self.expect_word("end")
        self.expect_word("repeat")
        self.expect_end_of_statement()

    def expect_chunk_noun(self):
        if self.cur.kind == "WORD":
            w = self.cur.value
            if w in CHUNK_SINGULAR:
                self.advance()
                return CHUNK_SINGULAR[w]
            if w in CHUNK_PLURAL:
                self.advance()
                return CHUNK_PLURAL[w]
        raise ParseError(
            "expected 'character', 'word', 'line' or 'item'", self.cur.line)

    def parse_loop_control(self):
        w = self.advance()
        self.expect_word("repeat")
        if self.loop_depth == 0:
            raise ParseError(
                f"'{w.value} repeat' is not inside a repeat block", w.line,
                hint="loop control cannot cross a handler boundary; "
                     "return a value and let the caller decide")
        self.expect_end_of_statement()
        return A.ExitRepeat(w.line) if w.value == "exit" else A.NextRepeat(w.line)

    # misc statements ------------------------------------------------------

    def parse_quit(self):
        line = self.advance().line
        status = None
        if self.at_word("with"):
            self.advance()
            self.expect_word("status")
            status = self.parse_expression()
        self.expect_end_of_statement()
        return A.Quit(status, line)

    def parse_return(self):
        line = self.advance().line
        expr = None if self.end_of_statement() else self.parse_expression()
        self.expect_end_of_statement()
        return A.Return(expr, line)

    def parse_arith(self):
        tok = self.advance()
        op = tok.value
        amount = self.parse_expression()
        if op == "subtract":
            self.expect_word("from")
        elif op in ("multiply", "divide"):
            self.expect_word("into")
        else:
            self.expect_word("to")
        target = self.parse_assign_target()
        self.expect_end_of_statement()
        return A.Arith(op, amount, target, tok.line)

    def parse_ensure(self):
        line = self.expect_word("ensure").line
        self.expect_end_of_statement()
        block = self.parse_block("ensure")
        self.expect_word("end")
        self.expect_word("ensure")
        self.expect_end_of_statement()
        if not block:
            raise ParseError("an empty 'ensure' block cleans nothing up", line)
        return A.Ensure(block, line)

    def parse_delete(self):
        line = self.advance().line
        self.expect_word("file")
        path = self.parse_expression()
        self.expect_end_of_statement()
        return A.DeleteFile(path, line)

    def parse_replace(self):
        line = self.advance().line
        pattern = self.parse_expression()
        self.expect_word("with", hint='replace "old" with "new" in <name>')
        replacement = self.parse_expression()
        self.expect_word("in")
        target = self.parse_assign_target()
        self.expect_end_of_statement()
        return A.Replace(pattern, replacement, target, line)

    def parse_handler(self):
        line = self.expect_word("to").line
        name = self.parse_identifier()
        params = []
        if self.at_word("with"):
            self.advance()
            params.append(self.parse_identifier())
            while self.at_op(","):
                self.advance()
                params.append(self.parse_identifier())
        self.expect_end_of_statement()
        outer_depth = self.loop_depth
        self.loop_depth = 0
        try:
            block = self.parse_block(name)
        finally:
            self.loop_depth = outer_depth
        self.expect_word("end")
        end_name = self.parse_identifier()
        if end_name != name:
            raise ParseError(
                f"handler {name!r} is closed by 'end {end_name}'", self.cur.line,
                hint=f"write 'end {name}'")
        self.expect_end_of_statement()
        return A.HandlerDef(name, params, block, line)

    def parse_call(self):
        line = self.cur.line
        name = self.parse_identifier()
        args = []
        if self.at_word("with"):
            self.advance()
            args.append(self.parse_expression())
            while self.at_op(","):
                self.advance()
                args.append(self.parse_expression())
        self.expect_end_of_statement()
        return A.Call(name, args, line)

    # ------------------------------------------------------------ expressions

    def parse_expression(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_word("or"):
            line = self.advance().line
            left = A.Logical("or", left, self.parse_and(), line)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_word("and"):
            line = self.advance().line
            left = A.Logical("and", left, self.parse_not(), line)
        return left

    def parse_not(self):
        if self.at_word("not"):
            line = self.advance().line
            return A.UnaryOp("not", self.parse_not(), line)
        return self.parse_comparison()

    COMPARE_SYMBOLS = {"=": "=", "!=": "is not", "<": "<", ">": ">",
                       "<=": "<=", ">=": ">="}

    def parse_comparison(self):
        left = self.parse_concat()

        if self.at_word("exists"):
            line = self.advance().line
            return A.FileExists(left, line)

        if self.cur.kind == "OP" and self.cur.value in self.COMPARE_SYMBOLS:
            op = self.COMPARE_SYMBOLS[self.advance().value]
            return A.Compare(op, left, self.parse_concat(), self.cur.line)

        if self.at_word("is"):
            line = self.advance().line
            negate = False
            if self.at_word("not"):
                self.advance()
                negate = True
            if self.at_word("empty"):
                self.advance()
                return A.Compare("is not empty" if negate else "is empty",
                                 left, None, line)
            if self.at_word("greater"):
                self.advance()
                self.expect_word("than")
                op = ">"
            elif self.at_word("less"):
                self.advance()
                self.expect_word("than")
                op = "<"
            elif self.at_word("at"):
                self.advance()
                if self.at_word("least"):
                    self.advance()
                    op = ">="
                else:
                    self.expect_word("most")
                    op = "<="
            elif self.at_word("like"):
                self.advance()
                node = A.IsLike(left, self.parse_concat(), line)
                return A.UnaryOp("not", node, line) if negate else node
            elif self.at_word("in"):
                self.advance()
                op = "in"
            else:
                op = "="
            right = self.parse_concat()
            node = A.Compare(op, left, right, line)
            if negate:
                return A.UnaryOp("not", node, line)
            return node

        if self.at_word("contains"):
            line = self.advance().line
            return A.Compare("contains", left, self.parse_concat(), line)

        if self.at_word("matches"):
            line = self.advance().line
            return A.Matches(left, self.parse_concat(), line)

        if self.at_word("starts", "ends"):
            which = self.advance().value
            self.expect_word("with")
            return A.Compare(f"{which} with", left, self.parse_concat(),
                             self.cur.line)

        return left

    def parse_concat(self):
        left = self.parse_additive()
        while self.at_op("&", "&&"):
            op = self.advance()
            left = A.BinOp(op.value, left, self.parse_additive(), op.line)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at_op("+", "-"):
            op = self.advance()
            left = A.BinOp(op.value, left, self.parse_multiplicative(), op.line)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at_op("*", "/", "^"):
            op = self.advance()
            left = A.BinOp(op.value, left, self.parse_unary(), op.line)
        return left

    def parse_unary(self):
        if self.at_op("-"):
            line = self.advance().line
            return A.UnaryOp("-", self.parse_unary(), line)
        return self.parse_primary()

    # primary --------------------------------------------------------------

    def parse_primary(self):
        t = self.cur

        if t.kind == "NUM":
            self.advance()
            return A.Lit(t.value, t.line)

        if t.kind == "STR":
            self.advance()
            return A.Lit(t.value, t.line)

        if t.kind == "OP" and t.value == "(":
            self.advance()
            expr = self.parse_expression()
            self.expect_op(")")
            return expr

        if t.kind != "WORD":
            raise ParseError(
                f"expected a value but found {self.describe(t)}", t.line)

        w = t.value

        if w == "it":
            self.advance()
            return A.ItRef(t.line)
        if w == "empty":
            self.advance()
            return A.Lit("", t.line)
        if w == "true":
            self.advance()
            return A.Lit(True, t.line)
        if w == "false":
            self.advance()
            return A.Lit(False, t.line)
        if w == "the":
            return self.parse_the()
        if w == "every":
            self.advance()
            if not self.at_word("match"):
                raise ParseError(
                    "expected 'every match of ...'", t.line)
            self.advance()
            self.expect_word("of")
            pattern = self.parse_chunk_source()
            self.expect_word("in")
            return A.EveryMatch(pattern, self.parse_chunk_source(), t.line)
        if w == "file" and (self.peek(1).kind == "STR"
                            or self.at_op("(", offset=1)):
            self.advance()
            return A.FileRef(self.parse_primary(), t.line)

        # Bare chunk expression: `line 3 of x`, `words 2 to 4 of x`
        chunk = self.try_parse_bare_chunk()
        if chunk is not None:
            return chunk

        name = self.parse_identifier()
        return A.Var(name, t.line)

    def try_parse_bare_chunk(self):
        t = self.cur
        if t.kind != "WORD":
            return None
        w = t.value

        # `first word of X`, `last line of X`, `any item of X` — the article
        # is optional. Only treated as a chunk when a chunk noun follows, so
        # names like `last name` and `first attempt` are unaffected.
        if w in ORDINALS or w in ("last", "middle", "any"):
            nxt = self.peek(1)
            if nxt.kind == "WORD" and (nxt.value in CHUNK_SINGULAR
                                       or nxt.value in CHUNK_PLURAL):
                self.advance()
                index = A.Lit(ORDINALS[w], t.line) if w in ORDINALS else w
                kind = self.expect_chunk_noun()
                return A.Chunk(kind, index, None,
                               self.parse_chunk_of(kind), t.line)
            return None

        kind = CHUNK_SINGULAR.get(w) or CHUNK_PLURAL.get(w)
        if kind is None:
            return None
        nxt = self.peek(1)
        is_index = (
            nxt.kind == "NUM"
            or (nxt.kind == "WORD" and nxt.value in ORDINALS)
            # `word -1 of s` counts back from the end.
            or (nxt.kind == "OP" and nxt.value == "-"
                and self.peek(2).kind == "NUM")
        )
        if not is_index:
            return None
        self.advance()
        start = self.parse_index()
        end = None
        if self.at_word("to"):
            self.advance()
            end = self.parse_index()
        source = self.parse_chunk_of(kind)
        return A.Chunk(kind, start, end, source, t.line)

    def parse_chunk_of(self, kind):
        """Read the `of X` tail. Implicit for match groups."""
        if kind == "match" and not self.at_word("of"):
            return A.MatchGroups(self.cur.line)
        self.expect_word("of")
        return self.parse_chunk_source()

    def parse_index(self):
        if self.cur.kind == "WORD" and self.cur.value in ORDINALS:
            return A.Lit(ORDINALS[self.advance().value], self.cur.line)
        return self.parse_additive()

    def parse_chunk_source(self):
        """The source of a chunk binds tightly: no comparisons, no concat."""
        return self.parse_primary()

    def parse_the(self):
        line = self.expect_word("the").line

        if self.at_word("result"):
            self.advance()
            return A.ResultRef(line)

        if self.at_word("whole"):
            self.advance()
            self.expect_word("match")
            return A.WholeMatch(line)

        if self.at_word("matches"):
            self.advance()
            return A.MatchGroups(line)

        if self.at_word("arguments"):
            self.advance()
            return A.ArgList(line)

        if self.at_word("current"):
            self.advance()
            self.expect_word("folder")
            return A.CurrentFolder(line)

        if self.at_word("global"):
            self.advance()
            return A.GlobalRef(self.parse_identifier(), line)

        if self.at_word("environment"):
            self.advance()
            self.expect_word("variable")
            return A.EnvRef(self.parse_primary(), line)

        if self.at_word("length"):
            self.advance()
            self.expect_word("of")
            return A.LengthOf(self.parse_chunk_source(), line)

        if self.at_word("number"):
            self.advance()
            self.expect_word("of")
            kind = self.expect_chunk_noun()
            if self.at_word("in", "of"):
                self.advance()
                return A.CountOf(kind, self.parse_chunk_source(), line)
            if kind == "match":
                return A.CountOf(kind, A.MatchGroups(line), line)
            raise ParseError(
                "expected 'in' after 'the number of ...'", self.cur.line)

        # the <ordinal> <chunk noun> of X   /   the last line of X
        if self.cur.kind == "WORD" and self.cur.value in ORDINALS:
            index = A.Lit(ORDINALS[self.advance().value], line)
            kind = self.expect_chunk_noun()
            return A.Chunk(kind, index, None, self.parse_chunk_of(kind), line)

        if self.at_word("last", "middle", "any"):
            which = self.advance().value
            kind = self.expect_chunk_noun()
            return A.Chunk(kind, which, None, self.parse_chunk_of(kind), line)

        # the <chunk noun> <n> of X  /  the <chunk nouns> <a> to <b> of X
        if self.cur.kind == "WORD" and (self.cur.value in CHUNK_SINGULAR
                                        or self.cur.value in CHUNK_PLURAL):
            kind = self.expect_chunk_noun()
            start = self.parse_index()
            end = None
            if self.at_word("to"):
                self.advance()
                end = self.parse_index()
            return A.Chunk(kind, start, end, self.parse_chunk_of(kind), line)

        raise ParseError(
            f"'the' must be followed by a property or chunk, "
            f"found {self.describe(self.cur)}", line,
            hint="try: the result / the first line of X / "
                 "the number of words in X / the length of X")


def parse(src):
    return Parser(src).parse_program()
