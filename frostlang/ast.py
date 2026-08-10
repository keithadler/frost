"""AST node definitions for frost."""
# SPDX-License-Identifier: MIT

from dataclasses import dataclass, field
from typing import Any, List, Optional


# ---------------------------------------------------------------- expressions

@dataclass
class Lit:
    value: Any
    line: int = 0


@dataclass
class Var:
    name: str
    line: int = 0


@dataclass
class ItRef:
    line: int = 0


@dataclass
class ResultRef:
    line: int = 0


@dataclass
class ErrorRef:
    """`the error output` — what the last command wrote to standard error."""
    line: int = 0


@dataclass
class EmptyRecord:
    line: int = 0


@dataclass
class FieldRef:
    key: Any
    source: Any
    line: int = 0


@dataclass
class FieldTarget:
    key: Any
    source: Any


@dataclass
class JsonOf:
    expr: Any
    line: int = 0


@dataclass
class JsonTextOf:
    expr: Any
    line: int = 0


@dataclass
class KeysOf:
    expr: Any
    line: int = 0


@dataclass
class ValuesOf:
    expr: Any
    line: int = 0


@dataclass
class RunIdRef:
    """`the run id` — this execution's identity, stable for the whole run."""
    line: int = 0


@dataclass
class ClockRef:
    """`the current date` and friends. A reading, not a constant."""
    which: str
    line: int = 0


@dataclass
class Wait:
    seconds: Any
    line: int = 0


@dataclass
class BinOp:
    op: str
    left: Any
    right: Any
    line: int = 0


@dataclass
class UnaryOp:
    op: str
    operand: Any
    line: int = 0


@dataclass
class Compare:
    op: str
    left: Any
    right: Any
    line: int = 0


@dataclass
class Logical:
    op: str
    left: Any
    right: Any
    line: int = 0


@dataclass
class Chunk:
    """A chunk expression: `the third word of X`, `lines 2 to 5 of X`."""
    kind: str                  # character | word | line | item
    start: Any                 # expression, or the strings 'last'/'middle'/'any'
    end: Optional[Any]         # expression for ranges, else None
    source: Any
    line: int = 0


@dataclass
class CountOf:
    kind: str
    source: Any
    line: int = 0


@dataclass
class LengthOf:
    source: Any
    line: int = 0


@dataclass
class FileRef:
    path: Any
    line: int = 0


@dataclass
class FileExists:
    path: Any
    line: int = 0


@dataclass
class FolderExists:
    path: Any
    line: int = 0


@dataclass
class Padded:
    value: Any
    width: Any
    side: str = "right"
    line: int = 0


@dataclass
class DurationOf:
    seconds: Any
    line: int = 0


@dataclass
class SortedBy:
    """`the sorted X by <key>`, where `each` is the item being weighed."""
    source: Any
    key: Any
    line: int = 0


@dataclass
class EachRef:
    line: int = 0


@dataclass
class EnvRef:
    name: Any
    line: int = 0


@dataclass
class CurrentFolder:
    line: int = 0


@dataclass
class GlobalRef:
    """`the global total` — reads past a handler's local of the same name."""
    name: str
    line: int = 0


@dataclass
class ArgList:
    """`the arguments` — command line arguments as a list value."""
    line: int = 0


@dataclass
class StdInRef:
    """`the standard input` — everything piped into the script."""
    line: int = 0


@dataclass
class SecretRef:
    """`the secret "db password"` — a value read from the keystore.

    Which secrets a script asks for is a capability, so it is reported by
    --explain and can be refused by a policy or by the role before the script
    runs at all.
    """
    name: Any
    line: int = 0


@dataclass
class SecretEnvRef:
    """`the secret environment variable "GITHUB_TOKEN"` — sealed on read."""
    name: Any
    line: int = 0


@dataclass
class SecretFileRef:
    """`the secret file "~/.ssh/id_rsa"` — sealed on read."""
    path: Any
    line: int = 0


@dataclass
class ChunkList:
    """`the words of X` — every chunk at once, as a list.

    The singular form addresses one chunk; the plural with no index is the
    whole set. That makes splitting fall out of the grammar already there.
    """
    kind: str                  # character | word | line | item
    source: Any
    line: int = 0


@dataclass
class EmptyList:
    """`the empty list` — something to append to."""
    line: int = 0


@dataclass
class Transform:
    """`the uppercase X` — one value in, one value out."""
    op: str                    # uppercase | lowercase | trimmed | sorted
                               # | reversed | unique | rounded | absolute
    source: Any
    line: int = 0


@dataclass
class Aggregate:
    """`the sum of X` — a list of numbers in, one number out."""
    op: str                    # sum | largest | smallest | average
    source: Any
    line: int = 0


@dataclass
class SplitBy:
    """`X split by "|"` — text to a list on any delimiter."""
    source: Any
    separator: Any
    line: int = 0


@dataclass
class JoinedBy:
    """`X joined by ", "` — a list back to text."""
    source: Any
    separator: Any
    line: int = 0


@dataclass
class FuncCall:
    """`the double of 5` — a handler called from inside an expression."""
    name: str
    args: List[Any] = field(default_factory=list)
    line: int = 0


# ----------------------------------------------------------------- statements

@dataclass
class VarTarget:
    name: str


@dataclass
class StreamTarget:
    name: str          # output | error


@dataclass
class FileTarget:
    path: Any


@dataclass
class GlobalTarget:
    """`the global total` in target position — writes past the local scope."""
    name: str


@dataclass
class EnvTarget:
    """`the environment variable "NAME"` in target position."""
    name: Any


@dataclass
class FolderTarget:
    """`the current folder` in target position."""


@dataclass
class Put:
    expr: Any
    # VarTarget | GlobalTarget | StreamTarget | FileTarget | EnvTarget
    # | FolderTarget | None
    target: Any
    mode: str = "into"         # into | before | after
    # `with fields "a", "b"` — the shape the author says the value has. None
    # means no claim was made, which is not the same as claiming nothing.
    fields: Optional[List[str]] = None
    line: int = 0


@dataclass
class Run:
    program: Any
    args: List[Any] = field(default_factory=list)
    checked: bool = True
    timeout: Optional[Any] = None
    stdin: Optional[Any] = None     # `reading EXPR` — text on the child's stdin
    folder: Optional[Any] = None    # `in folder EXPR` — the child's cwd
    streaming: bool = False         # `showing output` — inherit the terminal
    line: int = 0


@dataclass
class Pipe:
    stages: List[Run] = field(default_factory=list)
    checked: bool = True
    timeout: Optional[Any] = None
    stdin: Optional[Any] = None     # feeds the first stage
    folder: Optional[Any] = None    # applies to every stage
    line: int = 0


@dataclass
class Use:
    """`use "lib/db.frost" for the connect which may run "psql"`.

    The path is a string literal and the parser enforces that: a computed
    import would put the import graph out of reach of static analysis, which
    is where every other guarantee in frost lives.

    `names` is explicit, so a collision between two modules is an error at
    load time rather than one silently shadowing the other. `ceiling` is the
    capability limit declared at the import site — empty means the module may
    do nothing but compute.
    """
    path: str
    names: List[str] = field(default_factory=list)
    ceiling: Any = None                    # modules.Ceiling, or None for bare
    line: int = 0


@dataclass
class Ensure:
    """A cleanup block, registered when reached and run when the script ends.

    Registered blocks run in reverse order, whether the script finished, hit
    an error, quit, or was interrupted. This is what makes abort-on-failure
    survivable: a lock file taken on line 3 is still released.
    """
    block: List[Any] = field(default_factory=list)
    line: int = 0


@dataclass
class If:
    cond: Any
    then_block: List[Any]
    else_block: Optional[List[Any]]
    line: int = 0


@dataclass
class RepeatTimes:
    count: Any
    block: List[Any]
    line: int = 0


@dataclass
class RepeatWith:
    var: str
    start: Any
    stop: Any
    step: Any
    block: List[Any]
    line: int = 0


@dataclass
class RepeatForEach:
    kind: str
    source: Any
    var: str
    block: List[Any]
    line: int = 0


@dataclass
class RepeatWhile:
    cond: Any
    block: List[Any]
    until: bool = False
    line: int = 0


@dataclass
class RepeatForever:
    block: List[Any]
    line: int = 0


@dataclass
class ExitRepeat:
    line: int = 0


@dataclass
class NextRepeat:
    line: int = 0


@dataclass
class Quit:
    status: Optional[Any]
    line: int = 0


@dataclass
class HandlerDef:
    name: str
    params: List[str]
    block: List[Any]
    line: int = 0


@dataclass
class Call:
    name: str
    args: List[Any]
    line: int = 0


@dataclass
class Return:
    expr: Optional[Any]
    line: int = 0


@dataclass
class Arith:
    """`add 1 to counter`, `subtract 2 from the global counter`."""
    op: str
    amount: Any
    target: Any                # VarTarget | GlobalTarget
    line: int = 0


@dataclass
class DeleteFile:
    path: Any
    line: int = 0


# ------------------------------------------------------- pattern matching

@dataclass
class Matches:
    """`X matches "regex"` — boolean, and records capture groups."""
    subject: Any
    pattern: Any
    line: int = 0


@dataclass
class IsLike:
    """`X is like "*.tmp"` — shell-style glob, no regex knowledge needed."""
    subject: Any
    pattern: Any
    line: int = 0


@dataclass
class MatchGroups:
    """The capture groups of the most recent successful match."""
    line: int = 0


@dataclass
class WholeMatch:
    line: int = 0


@dataclass
class EveryMatch:
    pattern: Any
    source: Any
    line: int = 0


@dataclass
class Replace:
    pattern: Any
    replacement: Any
    target: Any                # VarTarget | GlobalTarget
    line: int = 0
