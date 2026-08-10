"""Who approved this, and to what.

An approval is a file that says a script was allowed to do a set of things. On
one machine, written by the person who read the manifest, that is enough. In a
datacenter it is not, because the file says only *that* something was
approved. It does not say **who** approved it, and it does not say **what
version** they were looking at. Anything that can write the file can grant
itself the approval, including the agent whose escalation the approval exists
to catch.

So an approval can be signed.

    frost --new-approver-key ~/.frost/keys/alice
    frost --approve --sign-with ~/.frost/keys/alice --commit $GITHUB_SHA deploy.frost

and the site policy names who is allowed to sign:

    require an approval signed by "kA1b2c...", "kZ9y8x..."

## What is signed

The capability set, the script's path, the commit, and the approver's name and
key, in one canonical JSON encoding with only the signature *value* removed.
Signing the capabilities rather than the file's bytes means reformatting the
file does not break the signature and changing what the script may do does.

The approver being inside the payload is not incidental. An earlier version
signed everything except the whole signature block, which left the name and
the key outside what was covered: a valid approval could be relabelled from
one person to another and still verify. The trust decision was unaffected,
since the key is checked against the policy either way, but a provenance
record that can be edited is not a provenance record.

The commit is carried but not verified here: frost cannot know which commit is
being deployed, only which one the approver said they read. A pipeline that
compares it against the revision it checked out is doing the other half, and
that comparison belongs where the checkout happens.

## Failing closed without a cipher

Signing needs `cryptography`, which frost treats as optional everywhere else.
The optional part is *making* signatures. **Verifying** must never degrade: if
a policy demands signed approvals and the library is missing, the answer is a
refusal, not a shrug. An unverifiable signature is not a valid one.
"""
# SPDX-License-Identifier: MIT

import base64
import json
import os

PREFIX = "k"            # so a key in a log line is recognisable at a glance


class SigningError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.msg = msg
        self.hint = hint


def available():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa
        return True
    except ImportError:
        return False


def _require():
    if not available():
        raise SigningError(
            "signing needs the cryptography package",
            hint="pip install 'frostlang[keystore]' — the same extra the "
                 "keystore uses")


def _encode(raw):
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(text):
    if not text.startswith(PREFIX):
        raise SigningError(f"{text[:12]!r} is not a frost key")
    body = text[len(PREFIX):]
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))


def generate():
    """A new approver key, as (private, public) in frost's text form."""
    _require()
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.generate()
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    raw_public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return _encode(raw_private), _encode(raw_public)


def public_of(private_text):
    _require()
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        _decode(private_text))
    return _encode(private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw))


def write_key(path, private_text):
    """Private keys go out at 0600 or not at all."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as fh:
        fh.write(private_text + "\n")


def read_key(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError as e:
        raise SigningError(f"cannot read the signing key: {e}")


def payload(approval):
    """The bytes a signature covers: everything except the signature value.

    Note *value*, not the whole signature block. The first version of this
    dropped the block entirely, which left the approver's name and key
    outside what was signed: a valid approval could be relabelled from
    "alice" to "the security team" and still verify. The trust decision was
    unaffected, since the key is checked against the policy either way, but
    the audit trail would have named the wrong person, and a provenance record
    that can be edited is not a provenance record.

    Sorted and separator-pinned so the same approval always encodes to the
    same bytes. A signature over a formatting choice would break the first
    time anything re-serialised the file.
    """
    body = dict(approval)
    block = body.get("signature")
    if isinstance(block, dict):
        body["signature"] = {k: v for k, v in block.items() if k != "value"}
    return json.dumps(body, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign(approval, private_text, approver):
    _require()
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        _decode(private_text))
    signed = dict(approval)
    signed["signature"] = {
        "algorithm": "ed25519",
        "approver": approver,
        "public_key": public_of(private_text),
    }
    # `payload` keeps the approver and the key inside what is signed, so a
    # signature cannot be lifted onto a different name.
    signed["signature"]["value"] = _encode(private.sign(payload(signed)))
    return signed


def verify(approval, trusted=()):
    """Whether this approval was signed by somebody the policy trusts.

    Returns (ok, reason). Every failure is a refusal with a sentence, because
    "signature check failed" sends a person to the wrong place more often than
    it helps.
    """
    block = approval.get("signature")
    if not block:
        return False, "the approval is not signed"
    if block.get("algorithm") != "ed25519":
        return False, f"unknown signature algorithm {block.get('algorithm')!r}"

    key = block.get("public_key")
    if trusted and key not in trusted:
        return False, (f"signed by {block.get('approver', 'someone')} "
                       f"({(key or '')[:12]}...), who is not in the list of "
                       f"approvers this policy trusts")
    if not available():
        # Never degrade to "assume valid". An unverifiable signature is not a
        # valid one, and this is the branch a datacenter runs in when somebody
        # trims the image.
        return False, ("this frost cannot verify signatures: the cryptography "
                       "package is missing, and an unverifiable signature is "
                       "not a valid one")

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.exceptions import InvalidSignature

    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(_decode(key))
        public.verify(_decode(block.get("value", "")), payload(approval))
    except (InvalidSignature, SigningError, ValueError):
        return False, ("the signature does not match the approval; it has "
                       "been edited since it was signed")
    return True, f"signed by {block.get('approver', 'an approver')}"
