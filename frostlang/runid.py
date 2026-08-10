"""One identity for one execution.

These scripts are run by agents and pipelines, not by a person watching a
terminal, and the question asked afterwards is never "what happened" in the
abstract. It is "what did *that* run do": the one referenced in the incident,
the one whose fixture is on disk, the one an API saw a duplicate request from.
Without an identity every run looks like every other, and a recording is a
file with no join key.

So a run has an id, and four things fall out of it.

**Traceability.** The recording carries it, the trace opens with it, and the
child processes inherit it, so a log line from `git` three layers down can be
tied back to the frost run that caused it.

**Idempotency.** `the run id` is a value the script can send as an
idempotency key, which is the difference between a retried deploy and two
deploys.

**Dedupe.** A pipeline that re-runs a step can tell the second attempt from
the first without guessing at timestamps.

**Isolated fixtures.** A scratch path built from the id cannot collide with a
concurrent run, which is the failure that only appears once something runs in
parallel and is miserable to diagnose when it does.

## Where it comes from

An id supplied from outside always wins, because the whole value of the thing
is joining frost's record to somebody else's. A pipeline's job id or an
agent's task id is more useful than anything frost could invent. `--run-id`
first, then `FROST_RUN_ID`, then a fresh UUID.

## Why it is validated

It ends up in log lines, in filenames a script builds, and in the environment
of every child process. A value with a newline in it can forge a log entry; a
value with a slash in it can move where a fixture is written. Both are the
familiar shape of trusting text that came from elsewhere, which is the thing
this language exists to refuse, so the id is checked rather than assumed.
"""
# SPDX-License-Identifier: MIT

import re
import uuid

ENV_NAME = "FROST_RUN_ID"
MAX_LENGTH = 128

# Deliberately narrow: letters, digits, and the three separators every CI
# system already uses. Anything else is refused rather than sanitised, because
# quietly rewriting an id breaks the join it exists for.
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class RunIdError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


def generate():
    return str(uuid.uuid4())


def validate(value, where):
    """A supplied id, or a refusal naming where it came from."""
    if not value:
        raise RunIdError(f"the run id from {where} is empty")
    if len(value) > MAX_LENGTH:
        raise RunIdError(
            f"the run id from {where} is {len(value)} characters; "
            f"the limit is {MAX_LENGTH}")
    if not _SAFE.match(value):
        raise RunIdError(
            f"the run id from {where} contains something that is not allowed",
            hint="letters, digits, dot, colon, dash and underscore only. It "
                 "reaches log lines, child environments and any path a script "
                 "builds from it, so a newline or a slash in it would forge a "
                 "log entry or move a file.")
    return value


def resolve(supplied=None, environ=None):
    """The id for this run, and where it came from.

    Supplied first, then the environment, then a fresh one. An outside id wins
    because joining frost's record to the pipeline's is the point.
    """
    environ = environ if environ is not None else {}
    if supplied:
        return validate(supplied, "--run-id"), "--run-id"
    from_env = environ.get(ENV_NAME)
    if from_env:
        return validate(from_env, ENV_NAME), ENV_NAME
    return generate(), "generated"
