# frost, for the model writing it

frost is a shell scripting language whose scripts are read before they run. A
command is argv, never a string, so a value can never become syntax. If you
write it the way you would write bash, it will not parse.

Check what you write with `frost --check`, and read what it can do with
`frost --explain`. Both are cheap and neither runs anything.

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

## The reserved words

All 71 of them. Every other word is available as a name, which is why
the set is kept small and closed.

```text
  add           after         and           are           as            at
  before        by            contains      delete        divide        each
  else          empty         end           ends          ensure        every
  exists        exit          false         for           forever       from
  global        greater       if            in            into          is
  it            joined        least         less          like          matches
  may           most          multiply      next          not           of
  or            pipe          put           quit          reading       repeat
  replace       return        run           showing       split         standard
  starts        step          subtract      than          the           then
  times         to            true          try           until         use
  which         while         whole         with          within
```
