# frost — reference for code-generating models

Paste this into a system prompt when you want a model to emit frost instead of
bash. It is deliberately short; the full reference is LANGUAGE.md.

## Why emit frost rather than bash

The generated script will be read by a human and checked by a machine before it
runs. Write for that reader. Do not compress.

## Non-negotiable rules

1. **One statement per line.** Newlines are significant.
2. **`run` takes a program name, then arguments as a list.**
   Correct: `run "git" with "status", "--short"`
   Wrong:   `run "git status --short"`  ← syntax error
3. **No string interpolation. No `eval`. No globbing in arguments.**
   Build text with `&` (join) and `&&` (join with a space).
   Never emit `run "sh" with "-c", ...` unless the user explicitly asked to
   invoke a shell; it reintroduces injection and the auditor flags it.
4. **Failure aborts.** Plain `run` stops the script on a non-zero exit. Use
   `try to run` only when you will inspect `the result` immediately after.
5. **Give network commands a deadline:** `within 30 seconds`. Units required.
6. **Names are several plain words:** `put 0 into error count`. Do not use
   underscores or camelCase.

## Statements

```text
put EXPR                          write to standard output
put EXPR into NAME                assign
put EXPR into standard error
put EXPR into file "path"         overwrite      (`after` appends)
run "prog" with A, B              execute; stdout -> `it`, status -> `the result`
try to run "prog" with A          same, but a failure does not abort
run "prog" with A within 30 seconds
pipe / end pipe                   block of `run` stages; fails if ANY stage fails
if COND then ... else ... end if
repeat N times / repeat with i from 1 to N / repeat while COND
repeat for each line in EXPR as NAME
wait 3 seconds                    pause; the unit is required
exit repeat, next repeat
add 1 to NAME, subtract 1 from NAME
replace "regex" with "text" in NAME
delete file EXPR
quit with status N
to NAME with A, B ... end NAME    handler; `return X` lands in `it`
ensure / end ensure               cleanup; runs at exit however the script ends
put EXPR into the global NAME     write a global from inside a handler
put EXPR into the environment variable "N"   what children inherit
put EXPR into the current folder
```

Clauses that may follow `run` (and `pipe`), in any order:

```text
reading EXPR                      text on the child's standard input
in folder EXPR                    the child's working directory
within N seconds                  a deadline; the unit is required
showing output                    write straight to the terminal; `it` stays empty
```

## Chunk expressions — prefer these over cut/awk/sed

```text
the first word of X          the third line of X       the last item of X
word 3 of X                  words 2 to 4 of X         word -1 of X
the number of lines in X     the length of X
the third word of line 7 of file "access.log"     (they nest)
```

Chunk nouns: `character`, `word` (whitespace), `line`, `item` (comma).
Ordinals: `first`..`tenth`, `last`, `middle`, `any`.
Out-of-range yields empty text, not an error.

A plural noun with no index is the whole set, as a list:

```text
the words of X    the lines of X    the items of X    the characters of X
X split by "|"               a list, on any delimiter
X joined by ", "             back to text
the empty list               something to append to
put "c" after names          appends an element when `names` is a list
```

## Functions

```text
the uppercase X   the lowercase X   the trimmed X
the sorted X      the reversed X    the unique X
the rounded X     the absolute X
the sum of X   the largest of X   the smallest of X   the average of X
the NAME of A, B             calls the handler `to NAME with a, b`
```

`the sorted X` orders numerically when every value is a number. An argument
binds tightly: `the double of n - 1` means `(the double of n) - 1`, so
parenthesise when you mean otherwise.

## Comparisons and patterns

```text
is / is not / is greater than / is less than / is at least / is at most
contains / starts with / ends with / is empty / is in
X is like "*.tmp"            glob
X matches "^(\d{3})"         regex; then match 1, the last match,
                             the number of matches, the whole match
every match of "\d+" in X    list of matches
```

## Records and JSON — never shell out to jq

```text
the json of TEXT             parse; objects become records, arrays become lists
the "status" of report       a field, by name
the "name" of the "user" of report        they nest
the keys of R / the values of R
the empty record
put "ok" into the "status" of summary     assign a field (creates the record)
the json text of R           serialise
```

A missing key is empty, exactly as `word 99 of` is, and a field of empty is
empty — so an optional field needs no guard. A field of *text* is an error:
that means the value is not the shape you think it is.

Numbers stay numbers, so `the "count" of r + 1` works. Parsing a secret seals
every field it produces.

## Special values

`it` (last output), `the result` (last exit status), `the error output`
(what the last command wrote to standard error), `the arguments`,
`the environment variable "NAME"`, `the current folder`, `the standard input`,
`the global NAME`, `the current date` / `time` / `timestamp` / `seconds`,
`empty`.

Prefer `the error output` over `run "sh" with "-c", "... 2>&1"`. Inspecting
why a command failed is a normal thing to do and does not need a shell:

```text
try to run "curl" with "-fsS", url within 30 seconds
if the result is not 0 then
    put "curl failed:" && the error output into standard error
    quit with status 1
end if
```

## Secrets

```text
the secret "db password"                     from the keystore
the secret environment variable "TOKEN"      sealed on read
the secret file "~/.ssh/id_rsa"              sealed on read
```

A sealed value redacts itself everywhere it would be printed, and the seal
survives concatenation, chunks and transformations. It is released only where
a program needs it: arguments, `reading`, the child environment, a file write.

Emit `run "psql" reading password`, not `run "psql" with "--password",
password` — arguments are visible to every process on the machine, and the
auditor flags them. Never write a secret to a file unless asked to.

## Reading a file

`file "path.txt"` is an expression. If the path is in a variable, parenthesise:
`file (log path)`.

## What the auditor will flag

Assume `frost --explain` and `frost --policy` run before the script does.
Avoid, unless explicitly requested: `rm -rf`, wildcards in delete arguments,
`sudo`, `chmod 777`, writes under `/etc` `/usr` `/System`, `sh -c`, piping a
download into an interpreter, network calls without `within`, and `try to run`
whose result is never checked.

## Worked example

```
put item 1 of the arguments into log path
if log path is empty then
    put "usage: report <logfile>" into standard error
    quit with status 2
end if

if not file (log path) exists then
    put "no log at" && log path into standard error
    quit with status 1
end if

put 0 into error count
repeat for each line in file (log path) as this request
    if the fourth word of this request starts with "5" then
        add 1 to error count
    end if
end repeat

put "server errors:" && error count
if error count is greater than 2 then quit with status 1
```

## Reserved words — cannot appear in a name

  add         after       and         are         as          at        
  before      by          contains    delete      divide      each      
  else        empty       end         ends        ensure      every     
  exists      exit        false       for         forever     from      
  global      greater     if          in          into        is        
  it          joined      least       less        like        matches   
  may         most        multiply    next        not         of        
  or          pipe        put         quit        reading     repeat    
  replace     return      run         showing     split       standard  
  starts      step        subtract    than        the         then      
  times       to          true        try         until       use       
  which       while       whole       with        within    

Everything else is available for names, including `line`, `word`, `item`,
`match`, `file`, `status`, `error`, `count`, `name`, `path`, and `result`,
so `line count` and `error count` are valid variables.

## Modules

```text
use "lib/db.frost" for the connect, the migrate which may run "psql"
```

A module contains handler definitions and imports, nothing else — a top-level
statement in one is an error. The path is a literal, relative to the importing
file, and cannot go above the entry script's directory. Name only the handlers
you use.

`which may ...` declares the ceiling. A module defaults to no capabilities at
all, and is refused if it exceeds what the import allows. Vocabulary: `run`,
`read`, `write`, `delete`, `set`, `read secret`, `change folder`, each taking
globs, joined with `and`. Declare the narrowest thing that works.

## Sandboxing

If the caller runs with `--sandbox`, the policy declares an allow-list and the
runtime holds it — child processes are confined by the OS, so a path built at
runtime is confined too:

```text
sandbox may run "git", "make"
sandbox may write "build/*"
sandbox may reach the network
```

Network is all-or-nothing; a per-host rule is refused rather than faked. Write
scripts that stay inside a narrow boundary: prefer a scratch directory the job
owns over /tmp, and name only the programs actually needed.

## When frost refuses

Ask for JSON and act on it rather than reading the prose:

```bash
frost --check --json script.frost
```

Each diagnostic has a `code`, a `line`, and often a `repairs` list. A repair
with `"confidence": "high"` is a mechanical rewrite — apply it verbatim.
`frost --repair --write script.frost` applies all of those for you and
repeats until nothing certain is left.

## Self-check before returning a script

- Every `run` has a bare program name and a `with` list, never a command line.
- Every `try to run` is followed by a check of `the result`.
- Every network command has a `within` clause.
- Any credential is read with `the secret ...` and reaches a program through
  `reading`, never as an argument and never in a log line.
- Anything that takes a lock or a temporary file releases it in an `ensure`
  block, because a failure aborts the script immediately.
- No interpolation, no `sh -c`, no globs in arguments.
- `end if` / `end repeat` / `end <handler name>` all present and matched.
- It would read clearly to someone seeing it for the first time at 3am.
