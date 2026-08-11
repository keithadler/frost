"""Records, and the bridge to JSON.

The gap this closes is the one that pushed people back into a second
language. A script that calls an API gets JSON back, and until now the only
way to read a field was `run "jq" with ".status"`: which puts a second
dialect in a file whose whole argument is that it needs only English, and
hands the auditor an opaque string it cannot see into. `--explain` could tell
you the script runs `jq`; it could never tell you what `jq` was asked for.

So a record is a first-class value:

    put the json of it into report
    put the "status" of report
    put the "name" of the "user" of report

A record is an ordered mapping from text keys to values. JSON objects become
records, JSON arrays become the lists frost already has, and the scalars map
the obvious way, so `item 1 of` and `repeat for each` keep working on
anything that came out of an API.

## Missing keys, and why the rule is not uniform

Asking for a key a record does not have yields empty, exactly as asking for
`word 99` does. Asking for a key of something that is *not* a record is an
error.

Those look inconsistent until you write the failing script. `the "name" of
the "user" of report` on a payload with no `user` yields empty at the inner
step, and if the outer step then errored, every optional field in an API
response would need a guard, people would stop using the feature. So empty
propagates: a field of empty is empty. But a field of `"some text"` is a
mistake about what the value *is*, and returning empty there would hide the
bug at the point where it is still cheap to find.

## Secrets survive the round trip

Parsing a sealed value seals every leaf it produces. A credentials blob read
with `the secret file "~/.aws/credentials.json"` does not stop being secret
because it went through a parser, and the field pulled out of it is still
sealed when it reaches `put`.

Serialising is span-accurate rather than all-or-nothing, reusing the same
machinery as text: `the json text of` a record with one secret field yields
JSON where that one value is a marker and the rest is readable. A record you
cannot print at all is a record people would work around.
"""
# SPDX-License-Identifier: MIT

import json

from .sealed import Sealed, is_sealed, reveal

# A JSON `null` and a missing key are both `empty` in frost, which already
# has one empty and does not need a second one that prints differently.
NULL = None


def is_record(value):
    return isinstance(value, dict)


def new_record():
    return {}


# ------------------------------------------------------------------- reading

def field(source, key, line=None):
    """`the "key" of source`.

    The asymmetry here is deliberate and documented in the module docstring:
    a missing key is empty, empty stays empty, and a key of a non-record is
    an error.
    """
    from .interp import FrostError, to_text

    key = to_text(key)
    if source is None or source == "":
        return None                    # safe navigation through a missing path
    if is_record(source):
        return source.get(key, None)
    if isinstance(source, list):
        raise FrostError(
            f"cannot ask for {key!r} of a list", line,
            hint=f'a list is numbered, not named: try: item 1 of X, or '
                 f'"the {key} of item 1 of X"')
    raise FrostError(
        f"cannot ask for {key!r} of text", line,
        hint="only a record has named fields. If this came from a command, "
             "parse it first: put the json of it into report")


def keys_of(value, line=None):
    from .interp import FrostError
    if value is None or value == "":
        return []
    if is_record(value):
        return list(value.keys())
    raise FrostError("only a record has keys", line,
                     hint="try: the keys of the json of it")


def values_of(value, line=None):
    from .interp import FrostError
    if value is None or value == "":
        return []
    if is_record(value):
        return list(value.values())
    raise FrostError("only a record has values", line,
                     hint="try: the values of the json of it")


def with_field(record, key, value, line=None):
    """`put X into the "key" of record`, returning the updated record."""
    from .interp import FrostError, to_text

    if record is None or record == "":
        record = {}
    if not is_record(record):
        raise FrostError("only a record has named fields", line,
                         hint="start one with: put the empty record into r")
    record[to_text(key)] = value
    return record


# ------------------------------------------------------------------- parsing

class JsonError(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


def from_json(text, origin=None):
    """JSON text to a frost value.

    `origin`, when set, is the name of the secret the text came from: every
    scalar the parse produces is sealed to it. A parser is not a laundry.
    """
    if is_sealed(text):
        origin = text.origin if origin is None else origin
        text = reveal(text)
    else:
        from .interp import to_text
        text = to_text(text)

    try:
        raw = json.loads(text) if text.strip() else None
    except ValueError as e:
        raise JsonError(f"this is not valid JSON: {e}")
    return _adopt(raw, origin)


def _adopt(raw, origin):
    """A parsed JSON value as a frost value, sealing scalars if asked.

    Numbers and booleans are sealed as text when they came from a secret.
    Arithmetic on one then fails loudly, which is the right outcome: a script
    doing sums on a credential and printing the total is the leak this whole
    mechanism exists to prevent.
    """
    if isinstance(raw, dict):
        return {str(k): _adopt(v, origin) for k, v in raw.items()}
    if isinstance(raw, list):
        return [_adopt(v, origin) for v in raw]
    if raw is None:
        return None
    if origin is not None:
        return Sealed(_scalar_text(raw), origin)
    return raw


def _scalar_text(raw):
    if raw is True:
        return "true"
    if raw is False:
        return "false"
    return str(raw)


# ----------------------------------------------------------------- writing

def to_json(value, indent=2):
    """A frost value as JSON text, sealed if any part of it was.

    Built as spans rather than as a string so a record holding one secret
    prints as readable JSON with one marker in it. Serialising through
    `reveal` and re-sealing the whole result would be simpler and would make
    the output useless to look at.
    """
    spans = []
    _emit(value, indent, 0, spans)
    if any(origin is not None for _, origin in spans):
        return Sealed(segments=spans)
    return "".join(text for text, _ in spans)


def _emit(value, indent, depth, spans):
    pad = " " * (indent * (depth + 1)) if indent else ""
    closing = " " * (indent * depth) if indent else ""
    newline = "\n" if indent else ""

    if is_sealed(value):
        # The one place a secret becomes output. The quotes are plain so the
        # result still parses as JSON shape; the value between them is the
        # marker, never the plaintext.
        spans.append(('"', None))
        spans.append((value.marker, value.origin))
        spans.append(('"', None))
        return
    if value is None:
        spans.append(("null", None))
        return
    if value is True:
        spans.append(("true", None))
        return
    if value is False:
        spans.append(("false", None))
        return
    if isinstance(value, (int, float)):
        # The same rule `to_text` uses, so `2.0` serialises as `2`. frost has
        # no visible int/float split, `put 4 / 2` already prints `2`: and a
        # JSON writer that disagreed with every other printing path would be
        # the odd one out, not the correct one.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        spans.append((json.dumps(value), None))
        return
    if is_record(value):
        if not value:
            spans.append(("{}", None))
            return
        spans.append(("{" + newline, None))
        for i, (k, v) in enumerate(value.items()):
            spans.append((pad + json.dumps(str(k)) + ": ", None))
            _emit(v, indent, depth + 1, spans)
            spans.append((("," if i < len(value) - 1 else "") + newline, None))
        spans.append((closing + "}", None))
        return
    if isinstance(value, list):
        if not value:
            spans.append(("[]", None))
            return
        spans.append(("[" + newline, None))
        for i, v in enumerate(value):
            spans.append((pad, None))
            _emit(v, indent, depth + 1, spans)
            spans.append(((("," if i < len(value) - 1 else "")) + newline,
                          None))
        spans.append((closing + "]", None))
        return
    spans.append((json.dumps(str(value)), None))


def record_text(value):
    """How a record prints when it reaches `put`.

    JSON, because a record that printed as `<record>` would send every user
    straight back to jq to look at their own data.
    """
    return to_json(value)
