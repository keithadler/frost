"""A generator of valid frost programs, for property tests.

Random *characters* almost never parse, so a character fuzzer only ever
exercises the rejection path. This walks the grammar instead and emits source
that is valid by construction, which is what makes the interesting properties
testable at all: the formatter round-trip, the totality of the auditor, and —
for the subset that touches nothing outside the process — the interpreter.

Every generator takes an explicit `rng`, so a failing seed reproduces exactly.
"""

import random

# Names built only from words outside HARD_WORDS, so they are always legal
# identifiers. Multi-word names are the frost idiom, so most of these are.
NAMES = [
    "alpha", "beta", "gamma", "delta", "counter", "total", "request count",
    "error total", "branch name", "log path", "user data", "line total",
    "word count", "first attempt", "last name", "match count",
]

PROGRAMS = ["echo", "true", "cat", "sort", "uniq", "grep", "wc", "date",
            "hostname", "printf", "head", "tail"]

FLAGS = ["-l", "-n", "-1", "--silent", "-c", "-r", "a.txt", "b.txt",
         "pattern", "hello", "{print $1}"]

TRANSFORMS = ["uppercase", "lowercase", "trimmed", "sorted", "reversed",
              "unique"]
SEPARATORS = ['"|"', '":"', '", "', '" -- "']

CHUNK_NOUNS = ["character", "word", "line", "item"]
CHUNK_PLURALS = {"character": "characters", "word": "words",
                 "line": "lines", "item": "items"}
ORDINALS = ["first", "second", "third", "fourth", "fifth"]

TEXTS = ["hello", "alpha beta gamma", "one,two,three", "a b c\\nd e f",
         "", "line one\\nline two", "  padded  ", "42", "3.5", "x"]

TIME_UNITS = ["seconds", "minutes", "milliseconds", "second", "ms"]


class Gen:
    """Generates valid frost.

    `safe=True` restricts output to statements with no effect outside the
    interpreter — no subprocesses, no file or stream writes, no deletes, no
    unbounded loops — so the result can actually be run in a test.
    """

    def __init__(self, rng=None, safe=False):
        self.rng = rng or random.Random(0)
        self.safe = safe
        self.scope = []
        self.handlers = []

    # ------------------------------------------------------------- helpers

    def pick(self, seq):
        return self.rng.choice(seq)

    def maybe(self, p=0.5):
        return self.rng.random() < p

    def fresh_name(self):
        name = self.pick(NAMES)
        if name not in self.scope:
            self.scope.append(name)
        return name

    def known_name(self):
        return self.pick(self.scope) if self.scope else self.fresh_name()

    @staticmethod
    def indent(lines, depth):
        pad = "    " * depth
        return [pad + l if l.strip() else l for l in lines]

    # --------------------------------------------------------- expressions

    def literal(self):
        kind = self.rng.randrange(3)
        if kind == 0:
            return str(self.rng.randint(0, 500))
        if kind == 1:
            return f"{self.rng.randint(0, 50)}.{self.rng.randint(0, 99)}"
        return '"%s"' % self.pick(TEXTS)

    def primary(self, depth):
        """Anything legal as a chunk source: no comparisons, no concatenation."""
        options = ["literal", "var", "it"]
        if depth > 0:
            options += ["chunk", "count", "length", "paren", "transform",
                        "chunk_list", "empty_list"]
        if not self.safe:
            options += ["file", "result", "env", "args", "secret"]
        if self.handlers and depth > 0:
            options += ["func_call"]
        choice = self.pick(options)

        if choice == "literal":
            return self.literal()
        if choice == "var":
            return self.known_name()
        if choice == "it":
            return "it"
        if choice == "result":
            return "the result"
        if choice == "args":
            return "the arguments"
        if choice == "env":
            return 'the environment variable "HOME"'
        if choice == "file":
            return 'file "%s"' % self.pick(["a.txt", "notes.log", "in.csv"])
        if choice == "paren":
            return "(%s)" % self.expression(depth - 1)
        if choice == "length":
            return "the length of %s" % self.primary(depth - 1)
        if choice == "empty_list":
            return "the empty list"
        if choice == "secret":
            form = self.rng.randrange(3)
            if form == 0:
                return 'the secret "%s"' % self.pick(
                    ["db password", "api token", "deploy/key"])
            if form == 1:
                return 'the secret environment variable "%s"' % self.pick(
                    ["GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"])
            return 'the secret file "%s"' % self.pick(["id_rsa", ".netrc"])
        if choice == "transform":
            return "the %s %s" % (self.pick(TRANSFORMS),
                                  self.primary(depth - 1))
        if choice == "chunk_list":
            return "the %s of %s" % (CHUNK_PLURALS[self.pick(CHUNK_NOUNS)],
                                     self.primary(depth - 1))
        if choice == "func_call":
            name, arity = self.pick(self.handlers)
            if not arity:
                return "the %s" % name
            return "the %s of %s" % (
                name, ", ".join(self.primary(depth - 1) for _ in range(arity)))
        if choice == "count":
            noun = self.pick(CHUNK_NOUNS)
            return "the number of %s in %s" % (
                CHUNK_PLURALS[noun], self.primary(depth - 1))
        return self.chunk(depth)

    def chunk(self, depth):
        noun = self.pick(CHUNK_NOUNS)
        source = self.primary(depth - 1)
        form = self.rng.randrange(5)
        if form == 0:
            return "the %s %s of %s" % (self.pick(ORDINALS), noun, source)
        if form == 1:
            return "the %s %s of %s" % (
                self.pick(["last", "middle"]), noun, source)
        if form == 2:
            return "%s %s of %s" % (self.pick(ORDINALS), noun, source)
        if form == 3:
            return "%s %d of %s" % (noun, self.rng.randint(1, 5), source)
        lo = self.rng.randint(1, 3)
        return "%s %d to %d of %s" % (
            CHUNK_PLURALS[noun], lo, lo + self.rng.randint(0, 3), source)

    def arithmetic(self, depth):
        left = self.primary(depth)
        for _ in range(self.rng.randint(0, 2)):
            # `/` and `^` are excluded: division by zero and huge powers are
            # runtime errors, not parse questions, and this feeds the runner.
            op = self.pick(["+", "-", "*"])
            left = "%s %s %s" % (left, op, self.primary(depth))
        return left

    def expression(self, depth=2):
        depth = max(depth, 0)
        node = self.postfix(depth)
        for _ in range(self.rng.randint(0, 2)):
            node = "%s %s %s" % (node, self.pick(["&", "&&"]),
                                 self.postfix(depth))
        return node

    def postfix(self, depth):
        node = self.arithmetic(depth)
        for _ in range(self.rng.randint(0, 1)):
            node = "%s %s by %s" % (node, self.pick(["split", "joined"]),
                                    self.pick(SEPARATORS))
        return node

    def condition(self, depth=1):
        form = self.rng.randrange(8)
        left = self.primary(depth)
        if form == 0:
            return "%s is %s" % (left, self.primary(depth))
        if form == 1:
            return "%s is not %s" % (left, self.primary(depth))
        if form == 2:
            return "%s is greater than %s" % (left, self.primary(depth))
        if form == 3:
            return "%s is less than %s" % (left, self.primary(depth))
        if form == 4:
            return "%s is empty" % left
        if form == 5:
            return "%s contains %s" % (left, self.literal())
        if form == 6:
            return '%s is like "%s"' % (left, self.pick(["*.tmp", "a*", "?x"]))
        return "%s starts with %s" % (left, self.literal())

    def boolean(self, depth=1):
        node = self.condition(depth)
        if self.maybe(0.25):
            node = "%s %s %s" % (node, self.pick(["and", "or"]),
                                 self.condition(depth))
        if self.maybe(0.1):
            node = "not (%s)" % node
        return node

    # ---------------------------------------------------------- statements

    def statement(self, depth, in_loop=False):
        """Return a list of source lines for one statement."""
        kinds = ["put", "put_var", "arith", "if", "repeat_times",
                 "repeat_with", "repeat_each", "put_global", "ensure"]
        if in_loop:
            kinds += ["loop_control"]
        if self.handlers:
            kinds += ["call"]
        if not self.safe:
            kinds += ["run", "try_run", "pipe", "put_stream", "put_file",
                      "delete", "replace", "quit", "put_env", "put_folder"]
        if depth <= 0:
            kinds = [k for k in kinds
                     if k not in ("if", "repeat_times", "repeat_with",
                                  "repeat_each", "pipe", "ensure")]

        kind = self.pick(kinds)
        return getattr(self, "st_" + kind)(depth, in_loop)

    def st_put(self, depth, in_loop):
        return ["put %s" % self.expression()]

    def st_put_var(self, depth, in_loop):
        mode = self.pick(["into", "into", "into", "before", "after"])
        if mode == "into":
            return ["put %s into %s" % (self.expression(), self.fresh_name())]
        # `before`/`after` read the target first, so it must already exist.
        return ["put %s %s %s" % (self.expression(), mode, self.known_name())]

    def st_put_global(self, depth, in_loop):
        # Globals must exist before they can be appended to, so this only
        # ever generates the creating form.
        return ["put %s into the global %s" % (self.expression(),
                                               self.fresh_name())]

    def st_put_env(self, depth, in_loop):
        mode = self.pick(["into", "after", "before"])
        return ['put %s %s the environment variable "FROST_GEN_%d"' % (
            self.expression(), mode, self.rng.randint(1, 5))]

    def st_put_folder(self, depth, in_loop):
        return ['put "%s" into the current folder' % self.pick(
            ["/tmp", "/var/tmp", "build"])]

    def st_ensure(self, depth, in_loop):
        # A cleanup block cannot use loop control: it runs after the loop is
        # long gone, so in_loop is deliberately not passed down.
        lines = ["ensure"]
        lines += self.indent(self.block(depth - 1, in_loop=False), 1)
        lines.append("end ensure")
        return lines

    def st_put_stream(self, depth, in_loop):
        return ["put %s into standard %s" % (
            self.expression(), self.pick(["output", "error"]))]

    def st_put_file(self, depth, in_loop):
        return ['put %s into file "%s"' % (
            self.expression(), self.pick(["out.txt", "log/run.txt"]))]

    def st_arith(self, depth, in_loop):
        if not self.scope:
            return ["put 0 into %s" % self.fresh_name()]
        target = self.known_name()
        op = self.pick(["add", "subtract", "multiply", "divide"])
        joiner = {"add": "to", "subtract": "from",
                  "multiply": "into", "divide": "into"}[op]
        amount = self.rng.randint(1, 9)          # never zero: no divide by zero
        return ["%s %d %s %s" % (op, amount, joiner, target)]

    def st_run(self, depth, in_loop):
        line = 'run "%s"' % self.pick(PROGRAMS)
        if self.maybe():
            args = [('"%s"' % self.pick(FLAGS)) if self.maybe(0.7)
                    else self.known_name()
                    for _ in range(self.rng.randint(1, 3))]
            line += " with " + ", ".join(args)
        if self.maybe(0.25):
            line += " reading %s" % self.primary(1)
        if self.maybe(0.2):
            line += ' in folder "%s"' % self.pick(["/tmp", "build", "src"])
        if self.maybe(0.3):
            line += " within %d %s" % (self.rng.randint(1, 60),
                                       self.pick(TIME_UNITS))
        if self.maybe(0.15):
            line += " showing output"
        return [line]

    def st_try_run(self, depth, in_loop):
        return ["try to " + self.st_run(depth, in_loop)[0]]

    def st_pipe(self, depth, in_loop):
        head = "pipe"
        if self.maybe(0.25):
            head += " reading %s" % self.primary(1)
        if self.maybe(0.2):
            head += ' in folder "%s"' % self.pick(["/tmp", "build"])
        if self.maybe(0.3):
            head += " within %d %s" % (self.rng.randint(1, 60),
                                       self.pick(TIME_UNITS))
        if self.maybe(0.3):
            head = "try to " + head
        stages = []
        for _ in range(self.rng.randint(2, 4)):
            stage = 'run "%s"' % self.pick(PROGRAMS)
            if self.maybe(0.6):
                stage += ' with "%s"' % self.pick(FLAGS)
            stages.append(stage)
        return [head] + self.indent(stages, 1) + ["end pipe"]

    def st_if(self, depth, in_loop):
        lines = ["if %s then" % self.boolean()]
        lines += self.indent(self.block(depth - 1, in_loop), 1)
        if self.maybe(0.4):
            lines.append("else")
            lines += self.indent(self.block(depth - 1, in_loop), 1)
        lines.append("end if")
        return lines

    def st_repeat_times(self, depth, in_loop):
        lines = ["repeat %d times" % self.rng.randint(1, 4)]
        lines += self.indent(self.block(depth - 1, in_loop=True), 1)
        lines.append("end repeat")
        return lines

    def st_repeat_with(self, depth, in_loop):
        var = self.fresh_name()
        lo = self.rng.randint(1, 3)
        head = "repeat with %s from %d to %d" % (var, lo, lo + 3)
        if self.maybe(0.3):
            head += " by %d" % self.rng.choice([1, 2])
        lines = [head] + self.indent(self.block(depth - 1, in_loop=True), 1)
        lines.append("end repeat")
        return lines

    def st_repeat_each(self, depth, in_loop):
        noun = self.pick(CHUNK_NOUNS)
        var = self.fresh_name()
        source = ('"%s"' % self.pick(TEXTS)) if self.safe \
            else self.primary(1)
        lines = ["repeat for each %s in %s as %s" % (noun, source, var)]
        lines += self.indent(self.block(depth - 1, in_loop=True), 1)
        lines.append("end repeat")
        return lines

    def st_loop_control(self, depth, in_loop):
        return [self.pick(["exit", "next"]) + " repeat"]

    def st_delete(self, depth, in_loop):
        return ['delete file "%s"' % self.pick(["tmp.txt", "old.log"])]

    def st_replace(self, depth, in_loop):
        return ['replace "%s" with "%s" in %s' % (
            self.pick(["a", "\\\\d+", "x+"]), self.pick(["b", "N"]),
            self.known_name())]

    def st_quit(self, depth, in_loop):
        return ["quit with status %d" % self.rng.randint(0, 3)]

    def st_call(self, depth, in_loop):
        name, arity = self.pick(self.handlers)
        line = name
        if arity:
            line += " with " + ", ".join(self.expression(0)
                                         for _ in range(arity))
        return [line]

    # -------------------------------------------------------------- blocks

    def block(self, depth, in_loop=False, min_statements=1, max_statements=3):
        lines = []
        for _ in range(self.rng.randint(min_statements, max_statements)):
            lines += self.statement(depth, in_loop)
        return lines or ["put 1"]

    def handler(self, depth):
        name = self.pick(["helper", "check value", "report", "tally up"])
        if any(n == name for n, _ in self.handlers):
            return []
        arity = self.rng.randint(0, 2)
        params = ["arg one", "arg two"][:arity]
        head = "to " + name
        if params:
            head += " with " + ", ".join(params)

        outer, self.scope = self.scope, list(params)
        try:
            # loop control cannot cross a handler boundary, hence in_loop=False
            body = self.block(depth - 1, in_loop=False)
            if self.maybe(0.5):
                body.append("return %s" % self.expression(0))
        finally:
            self.scope = outer

        self.handlers.append((name, arity))
        return [head] + self.indent(body, 1) + ["end " + name]

    def program(self, depth=2, statements=None):
        self.scope = []
        self.handlers = []
        lines = []
        # Seed the scope so reads have something defined to find.
        for name in self.rng.sample(NAMES, 3):
            lines.append('put "%s" into %s' % (self.pick(TEXTS), name))
            self.scope.append(name)
        for _ in range(self.rng.randint(0, 2)):
            lines += self.handler(depth)
        n = statements if statements is not None else self.rng.randint(2, 6)
        for _ in range(n):
            lines += self.statement(depth)
        return "\n".join(lines) + "\n"


def programs(count, seed=0, safe=False, depth=2):
    """`count` valid programs, deterministic in `seed`."""
    for i in range(count):
        yield Gen(random.Random(seed * 10_000 + i), safe=safe).program(depth)
