"""Sealed values, secrets that cannot be printed by accident.

The failure this exists to prevent is not a malicious script. It is
`put "connecting as" && token` in a script somebody generated, running in CI,
writing a credential into a log that is retained for a year and readable by
everyone in the organisation. That mistake is made by being ordinary, not by
being careless, so the fix has to be structural rather than a rule to
remember, the same argument frost makes about injection.

A sealed value carries its plaintext but refuses to hand it to `to_text`. The
whole language converts to text through that one function, so every printing
path: `put`, string joining, `--trace`, an error message, the scratchpad,
redacts without knowing anything about secrets.

Taint is contagious, so `"postgres://user:" & password` is still sealed: a
connection string built from a password is a password. But a sealed value
remembers which spans of it are actually secret, so printing it keeps the
parts that were never secret:

    put "connecting as" && user && "with" && token
    connecting as deploy with «secret db password»

That matters more than it looks. If the whole line redacted, people would
route around the seal to keep their logs readable, and a mechanism people
route around protects nothing.

The plaintext is released, in full, at the boundaries where a program
genuinely needs it:

    streams redact           put, put into standard error, --trace, errors
    boundaries release       run arguments, `reading`, the child environment
    deliberate acts release  writing to a file: allowed, and reported

What this does not do: it does not stop a script from handing a secret to a
program it was already allowed to run. Nothing at this layer can. What it
does is make the accidental path impossible and the deliberate path visible
in `--explain`, so the release points are things a person approves rather
than things they have to find by reading.
"""
# SPDX-License-Identifier: MIT

import hmac

REDACTED = "«secret {}»"
REDACTED_ANONYMOUS = "«secret»"


class Sealed:
    """Text of which some spans are secret.

    `segments` is a list of (text, origin) pairs. An origin of None marks a
    span that was never secret and may be printed; anything else names the
    secret it came from, so the marker says `«secret db password»` rather
    than an unhelpful `«secret»`.
    """

    __slots__ = ("_segments",)

    def __init__(self, plaintext="", origin=None, segments=None):
        if segments is not None:
            self._segments = list(segments)
        else:
            text = plaintext if isinstance(plaintext, str) else str(plaintext)
            self._segments = [(text, origin if origin is not None else "")]

    # -- the only way out

    def reveal(self):
        """The plaintext. Every call site is a boundary the auditor reports."""
        return "".join(text for text, _ in self._segments)

    # -- everything else redacts

    @property
    def marker(self):
        out = []
        for text, origin in self._segments:
            if origin is None:
                out.append(text)
            elif text == "":
                continue
            else:
                marker = (REDACTED.format(origin) if origin
                          else REDACTED_ANONYMOUS)
                # Collapse a run of spans from the same secret into one
                # marker, so splitting and rejoining does not produce a wall
                # of identical markers.
                if not out or out[-1] != marker:
                    out.append(marker)
        return "".join(out)

    @property
    def origins(self):
        """Names of every secret contributing to this value, in order."""
        seen = []
        for _, origin in self._segments:
            if origin is not None and origin not in seen:
                seen.append(origin)
        return seen

    @property
    def origin(self):
        names = self.origins
        return names[0] if names else ""

    def __str__(self):
        return self.marker

    def __repr__(self):
        # Matters more than __str__: this is what lands in a traceback, a
        # pprint of the tree, or a debugger.
        return f"Sealed({', '.join(repr(o) for o in self.origins)})"

    def __format__(self, spec):
        return self.marker

    def __len__(self):
        return len(self.reveal())

    def __bool__(self):
        return bool(self.reveal())

    def __eq__(self, other):
        """Constant time, so comparing a secret does not leak it by timing."""
        if isinstance(other, Sealed):
            return hmac.compare_digest(self.reveal(), other.reveal())
        if isinstance(other, str):
            return hmac.compare_digest(self.reveal(), other)
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self):
        # Deliberately not the plaintext's hash: a hash table keyed on
        # secrets would be a way to test them for equality offline.
        return hash(("sealed", tuple(self.origins)))

    # -- combining

    def joined(self, other, separator=""):
        """`self & other`, keeping which spans were secret."""
        return Sealed(segments=self._segments
                      + _spans(separator) + _spans(other))

    def preceded_by(self, other, separator=""):
        """`other & self`, keeping which spans were secret."""
        return Sealed(segments=_spans(other) + _spans(separator)
                      + self._segments)

    def rewrap(self, plaintext):
        """A value derived from this one: a chunk of it, a transform of it.

        The derived text no longer lines up with the original spans, so it is
        treated as wholly secret under this value's first origin. Erring
        towards more redaction is the right way round.
        """
        return Sealed(plaintext, self.origin)


def _spans(value):
    """A value as a list of (text, origin) spans."""
    if isinstance(value, Sealed):
        return list(value._segments)
    if value == "":
        return []
    return [(value if isinstance(value, str) else _plain(value), None)]


def _plain(value):
    from .interp import to_text          # late: interp imports this module
    return to_text(value)


def is_sealed(value):
    return isinstance(value, Sealed)


def reveal(value):
    """Plaintext of a sealed value, or the value unchanged.

    Call this only where a secret is genuinely leaving frost for a program.
    Everywhere else, `to_text` is correct and redacts.
    """
    return value.reveal() if isinstance(value, Sealed) else value


def seal_like(source, plaintext):
    """Seal `plaintext` with the same origin as `source`, if that was sealed.

    Used wherever a value is derived from another, so taint follows the data
    rather than stopping at the first operation nobody thought about.
    """
    return source.rewrap(plaintext) if isinstance(source, Sealed) else plaintext


def any_sealed(values):
    return any(isinstance(v, Sealed) for v in values)


def first_sealed(values):
    for v in values:
        if isinstance(v, Sealed):
            return v
    return None
