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
class EnvRef:
    name: Any
    line: int = 0


@dataclass
class CurrentFolder:
    line: int = 0


@dataclass
class ArgList:
    """`the arguments` — command line arguments as a list value."""
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
class Put:
    expr: Any
    target: Any                # VarTarget | StreamTarget | FileTarget | None
    mode: str = "into"         # into | before | after
    line: int = 0


@dataclass
class Run:
    program: Any
    args: List[Any] = field(default_factory=list)
    checked: bool = True
    timeout: Optional[Any] = None
    line: int = 0


@dataclass
class Pipe:
    stages: List[Run] = field(default_factory=list)
    checked: bool = True
    timeout: Optional[Any] = None
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
    """`add 1 to counter`, `subtract 2 from counter`."""
    op: str
    amount: Any
    target: str
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
    target: str
    line: int = 0
