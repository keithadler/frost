"""What a model needs in context to write frost correctly.

LANGUAGE.md is written for a person deciding whether to adopt frost: it argues,
it explains why a thing is absent, it is thousands of lines. Pasting that into
a context window costs more than it returns, and a model that reads the
argument still has to infer the grammar from examples.

This is the other document. It states the forms, names the closed keyword set,
and lists what frost deliberately does not have, because the failures a model
actually produces are `${x}` interpolation, backtick substitution, and an
invented `let` statement. All three come from writing bash or Python with
frost's vocabulary, and all three are cheaper to prevent than to diagnose.

The keyword set is read from the parser rather than typed out here. A list that
was copied once is a list that goes stale silently, and the whole value of this
file is that a model can trust it.
"""
# SPDX-License-Identifier: MIT

from .parser import HARD_WORDS


def keywords():
    """The closed set, from the parser itself."""
    return sorted(HARD_WORDS)


HEAD = """\
# frost, for the model writing it

frost is a shell scripting language whose scripts are read before they run. A
command is argv, never a string, so a value can never become syntax. If you
write it the way you would write bash, it will not parse.

Check what you write with `frost --check`, and read what it can do with
`frost --explain`. Both are cheap and neither runs anything.
"""

FORMS = """\
## The forms

```frost
put "hello" into name                    -- assign
put name                                 -- print
put "a" & "b" into together              -- join, no space
put "a" && "b" into spaced               -- join, with a space

run "ls" with "-la"                      -- program and arguments, separately
run "curl" with url within 30 seconds    -- a timeout
try to run "make" with "test"            -- do not stop on failure
put the result                           -- the exit status of the last run
put it                                   -- what the last command returned
put the error output                     -- what it wrote to standard error
run "cat" reading text                   -- text on standard input
run "make" with "all" in folder path     -- a working folder
run "npm" with "install" showing output  -- straight to the terminal

put file "notes.txt" into text           -- read
put text into file "out.txt"             -- write
put text after file "log.txt"            -- append
delete file "tmp.txt"

put the environment variable "HOME" into home
put "1" into the environment variable "DEBUG"
put the secret environment variable "TOKEN" into token   -- sealed

if the result is 0 then put "ok"
if name is empty then quit with status 1

if name is empty then
    put "no name"
else
    put name
end if

repeat 3 times
    put "again"
end repeat

repeat for each line in the lines of text as line
    put line
end repeat

to greet with who
    return "hello " & who
end greet
put the greet of "world"

ensure
    delete file "lock"
end ensure                               -- runs even if the script fails
```
"""

ABSENT = """\
## What frost does not have

These are the mistakes a model makes, in the order it makes them.

**No string interpolation.** There is no `${name}`, no `$name`, no f-string.
Use `&` to join and `&&` to join with a space.

**No command substitution.** There is no `` `cmd` `` and no `$(cmd)`. Run the
command, then read `it`.

**No eval, and no shell escape in the language.** If you genuinely need shell
behaviour, `run "sh" with "-c", text` works, and it is reported as a danger,
and a policy can refuse it. Reach for it last, not first.

**No globbing.** `run "rm" with "*.tmp"` passes a literal asterisk. Loop over
the output of `find`, or hand the pattern to a program that expands it.

**No `&`, `&&` or `|` as shell operators.** `&` and `&&` join text. Use `pipe`
for a pipeline, and `if the result is 0` for conditional sequencing.

**No `let`, `var`, `const`, `set`, `def`, `func`, `for`, `while`, `elif`,
`fi`, `done`, `esac`.** The forms above are the whole language.

**No background jobs, job control, or a login shell mode.**
"""

RULES = """\
## Getting it right the first time

Write a timeout on anything that touches the network. `--explain` reports a
network command without one as a caution, and a policy can refuse it.

Prefer `reading` over an argument for anything secret. Arguments are visible to
every process on the machine while the command runs.

Say what you mean about failure. A bare `run` stops the script when the command
fails; `try to run` continues and leaves the status in `the result`.

Clean up in an `ensure` block. It runs even when the script stops early, which
is the case the cleanup exists for.

Do not invent a helper you never call, and do not write past a `quit` or
`return`. Both are reported, and both are the clearest signal that a generated
script contains more than anybody intended.
"""


def model_context():
    """The whole document, with the keyword set filled in from the parser."""
    words = keywords()
    columns = 6
    rows = []
    for i in range(0, len(words), columns):
        rows.append("  " + "  ".join(w.ljust(12) for w in words[i:i + columns])
                    .rstrip())
    listing = "\n".join(rows)

    return "\n".join([
        HEAD, FORMS, ABSENT, RULES,
        "## The reserved words",
        "",
        f"All {len(words)} of them. Every other word is available as a name, "
        "which is why",
        "the set is kept small and closed.",
        "",
        "```text",
        listing,
        "```",
        "",
    ])
