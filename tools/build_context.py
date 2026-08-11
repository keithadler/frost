#!/usr/bin/env python3
"""Write MODEL-CONTEXT.md from the generator in frostlang/context.py.

Checked in so an agent can fetch it from the repository without running
anything, and generated so the reserved-word list cannot drift from the
parser. CI rebuilds every generated file and fails if the tree changes, which
is what keeps the checked-in copy honest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frostlang.context import model_context     # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(REPO, "MODEL-CONTEXT.md")

with open(TARGET, "w") as fh:
    fh.write(model_context())

print(f"wrote {TARGET} ({os.path.getsize(TARGET):,} bytes)")
