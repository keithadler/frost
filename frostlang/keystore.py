"""A role-gated keystore.

The problem this solves is not storage. It is that "who may read this
credential" is a decision somebody made once, in a conversation, and then
nothing in the system remembers it. A script either has the environment
variable or it does not, and which humans and which jobs can obtain that
variable lives in a wiki page.

Here it lives in the file. Every entry names the roles that may open it, a
script runs under exactly one role, and `--explain` reports which secrets a
script will ask for — so granting a credential is a capability someone
approves, the same way they approve a command.

    frost keystore init prod.keystore --role deploy
    frost keystore set prod.keystore "db password" --roles deploy,admin
    frost --keystore prod.keystore --role deploy release.frost

## The file

JSON, and deliberately readable. The *names* of the secrets, the roles, and
who may read what are all in plaintext, because that is the part a reviewer
needs to see. Only the values are encrypted.

## The cryptography

Envelope encryption, which is boring on purpose:

  * each role has an X25519 keypair. The public half sits in the file in
    plaintext; the private half is encrypted with a key derived from the
    role's passphrase by scrypt
  * each entry has a random data key, which encrypts the value once with
    AES-256-GCM
  * that data key is wrapped to every authorised role's *public* key

The keypair is the part that earns its place. With passphrase-derived
symmetric keys alone, granting a role would require that role's passphrase —
so whoever adds a credential would need every recipient's secret, which is
precisely the thing a keystore exists to avoid. With public keys, adding a
secret and granting a role need no passphrase at all; only *reading* does.

Granting therefore re-wraps a 32-byte key rather than re-encrypting the
value, and never sees the value's plaintext. Revoking removes a wrapping.

Everything comes from `cryptography`, an optional dependency imported lazily:
`frost script.frost` needs nothing, and only the keystore pulls it in.
Rolling our own cipher was the alternative and is not a serious one — an
audited implementation of a standard construction is worth more than anything
hand-written here, and the pure-Python route would have meant writing an AEAD
from scratch for a security feature.

## What this is not

It is not a secret manager. There is no rotation, no expiry, no audit trail
of reads, no network service. It is a file you can commit next to the scripts
that use it, with the property that reading a value requires a passphrase and
a role. If you already run Vault or SSM, use those — this exists for the many
projects that run neither and keep credentials in a `.env` nobody encrypts.
"""
# SPDX-License-Identifier: MIT

import base64
import json
import os
import re
import secrets

VERSION = 1

# scrypt parameters. n=2**15 costs roughly 100ms and 32MB per derivation,
# which is a reasonable brick wall in front of a passphrase and imperceptible
# once per script run.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12

ROLE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class KeystoreError(Exception):
    """Anything wrong with the keystore itself, as opposed to a denial."""


MISSING_DEPENDENCY = (
    "the keystore needs the 'cryptography' package.\n"
    "  install it with:  pip install 'frostlang[keystore]'\n"
    "Everything else in frost works without it.")


def _aesgcm():
    """The cipher, imported only when a keystore is actually used."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:                                   # pragma: no cover
        raise KeystoreError(MISSING_DEPENDENCY)
    return AESGCM


def _x25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519
    except ImportError:                                   # pragma: no cover
        raise KeystoreError(MISSING_DEPENDENCY)
    return x25519


def _wrap_to_public(public_bytes, data_key, context):
    """Seal `data_key` to a role's public key. No passphrase involved.

    An ephemeral keypair per wrapping, so the same data key sealed twice
    produces unrelated ciphertexts and the recipient set is not inferable
    from the bytes.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    x25519 = _x25519()

    ephemeral = x25519.X25519PrivateKey.generate()
    peer = x25519.X25519PublicKey.from_public_bytes(public_bytes)
    shared = ephemeral.exchange(peer)
    ephemeral_public = ephemeral.public_key().public_bytes_raw()
    wrapping_key = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES,
                        salt=None, info=b"frost-keywrap|" + context
                        ).derive(shared + ephemeral_public + public_bytes)
    blob = _encrypt(wrapping_key, data_key, context)
    blob["ephemeral"] = _b64(ephemeral_public)
    return blob


def _unwrap_with_private(private_bytes, blob, context):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    x25519 = _x25519()

    private = x25519.X25519PrivateKey.from_private_bytes(private_bytes)
    ephemeral_public = _unb64(blob["ephemeral"])
    peer = x25519.X25519PublicKey.from_public_bytes(ephemeral_public)
    shared = private.exchange(peer)
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None,
        info=b"frost-keywrap|" + context
    ).derive(shared + ephemeral_public + private.public_key().public_bytes_raw())
    return _decrypt(wrapping_key, blob, context, what="key")


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception:
        raise KeystoreError("the keystore file is corrupt: bad base64")


def derive_key(passphrase, salt):
    """A role's key from its passphrase. Deliberately slow."""
    import hashlib
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    return hashlib.scrypt(passphrase, salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                          p=SCRYPT_P, dklen=KEY_BYTES,
                          maxmem=SCRYPT_N * SCRYPT_R * 200)


def _encrypt(key, plaintext, associated=b""):
    AESGCM = _aesgcm()
    nonce = secrets.token_bytes(NONCE_BYTES)
    raw = plaintext if isinstance(plaintext, bytes) \
        else plaintext.encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, raw, associated)
    return {"nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}


def _decrypt(key, blob, associated=b"", what="value"):
    AESGCM = _aesgcm()
    try:
        return AESGCM(key).decrypt(_unb64(blob["nonce"]),
                                   _unb64(blob["ciphertext"]), associated)
    except KeyError:
        raise KeystoreError(f"the keystore file is missing part of a {what}")
    except Exception:
        # GCM authenticates, so this is either the wrong passphrase or a
        # modified file. Both mean "do not trust this", and distinguishing
        # them for the caller would be an oracle.
        raise PermissionError(
            "wrong passphrase, or the keystore has been modified since it "
            "was written")


class Keystore:
    """An open keystore. `unlock` turns a passphrase into a usable role key."""

    def __init__(self, data, path=None):
        self.path = path
        self.data = data
        self._role_keys = {}       # role -> private key bytes, once unlocked
        self._cache = {}           # name -> plaintext, so scrypt runs once

    # -- files

    @classmethod
    def create(cls, path=None):
        return cls({"version": VERSION, "roles": {}, "secrets": {}}, path)

    @classmethod
    def load(cls, path):
        try:
            with open(path) as fh:
                data = json.load(fh)
        except FileNotFoundError:
            raise KeystoreError(f"there is no keystore at {path}")
        except json.JSONDecodeError as e:
            raise KeystoreError(f"{path} is not a valid keystore: {e}")
        if not isinstance(data, dict) or "secrets" not in data:
            raise KeystoreError(f"{path} does not look like a keystore")
        if data.get("version") != VERSION:
            raise KeystoreError(
                f"{path} is a version {data.get('version')} keystore; "
                f"this frost understands version {VERSION}")
        return cls(data, path)

    def save(self, path=None):
        target = path or self.path
        if target is None:
            raise KeystoreError("no path to save the keystore to")
        # Written through a temporary file and moved into place, so an
        # interrupted write cannot leave a keystore half-rewritten.
        temporary = f"{target}.tmp{os.getpid()}"
        with open(temporary, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        self.path = target

    # -- roles

    @property
    def roles(self):
        return sorted(self.data["roles"])

    def add_role(self, role, passphrase):
        if not ROLE_PATTERN.match(role):
            raise KeystoreError(
                f"{role!r} is not a usable role name; use lower-case letters, "
                f"digits, dashes and underscores")
        if role in self.data["roles"]:
            raise KeystoreError(f"the role {role!r} already exists")
        if not passphrase:
            raise KeystoreError("a role needs a passphrase")

        x25519 = _x25519()
        private = x25519.X25519PrivateKey.generate()
        private_bytes = private.private_bytes_raw()
        salt = secrets.token_bytes(SALT_BYTES)
        passphrase_key = derive_key(passphrase, salt)

        self.data["roles"][role] = {
            "salt": _b64(salt),
            "public": _b64(private.public_key().public_bytes_raw()),
            # The private key is what the passphrase protects. Decrypting it
            # is also the passphrase check: a typo fails here, with a message
            # about the passphrase, rather than later on a secret where it
            # would look like a permissions problem.
            "private": _encrypt(passphrase_key, private_bytes,
                                role.encode("utf-8")),
        }
        self._role_keys[role] = private_bytes
        return private_bytes

    def remove_role(self, role):
        self._require_role(role)
        orphaned = [name for name, entry in self.data["secrets"].items()
                    if list(entry["wrapped"]) == [role]]
        if orphaned:
            raise KeystoreError(
                f"removing {role!r} would leave "
                f"{', '.join(repr(n) for n in orphaned)} readable by nobody; "
                f"grant another role first, or remove those secrets")
        del self.data["roles"][role]
        self._role_keys.pop(role, None)
        for entry in self.data["secrets"].values():
            entry["wrapped"].pop(role, None)

    def unlock(self, role, passphrase):
        """Recover a role's private key. Raises PermissionError if wrong."""
        record = self._require_role(role)
        passphrase_key = derive_key(passphrase, _unb64(record["salt"]))
        private_bytes = _decrypt(passphrase_key, record["private"],
                                 role.encode("utf-8"), what="role")
        self._role_keys[role] = private_bytes
        return private_bytes

    def public_key(self, role):
        """A role's public key — enough to grant it a secret, not to read one."""
        return _unb64(self._require_role(role)["public"])

    def is_unlocked(self, role):
        return role in self._role_keys

    def _require_role(self, role):
        if role not in self.data["roles"]:
            known = ", ".join(self.roles) or "none"
            raise KeystoreError(
                f"this keystore has no role {role!r} (it has: {known})")
        return self.data["roles"][role]

    def _key_for(self, role):
        if role not in self._role_keys:
            raise PermissionError(
                f"the role {role!r} has not been unlocked; a passphrase is "
                f"needed")
        return self._role_keys[role]

    # -- secrets

    @property
    def names(self):
        return sorted(self.data["secrets"])

    def roles_for(self, name):
        entry = self.data["secrets"].get(name)
        if entry is None:
            raise KeyError(name)
        return sorted(entry["wrapped"])

    def may_read(self, name, role):
        """Can `role` open `name`? Answerable without any passphrase.

        This is what lets a script be refused before it runs.
        """
        entry = self.data["secrets"].get(name)
        return entry is not None and role in entry["wrapped"]

    def set_secret(self, name, value, roles):
        """Store `value`, readable by `roles`.

        Needs no passphrase at all: a fresh data key is sealed to each role's
        public key. Someone can add a credential for a role whose passphrase
        they do not have, which is the usual case and the point of the
        keypairs.
        """
        if not name:
            raise KeystoreError("a secret needs a name")
        roles = list(dict.fromkeys(roles))
        if not roles:
            raise KeystoreError(
                f"the secret {name!r} would be readable by nobody; "
                f"name at least one role")
        for r in roles:
            self._require_role(r)

        data_key = secrets.token_bytes(KEY_BYTES)
        self.data["secrets"][name] = {
            "value": _encrypt(data_key, value, name.encode("utf-8")),
            "wrapped": {r: self._wrap(r, data_key, name) for r in roles},
        }
        self._cache.pop(name, None)

    def _wrap(self, role, data_key, name):
        return _wrap_to_public(self.public_key(role), data_key,
                               f"{name}:{role}".encode("utf-8"))

    def _unwrap(self, role, blob, name):
        return _unwrap_with_private(self._key_for(role), blob,
                                    f"{name}:{role}".encode("utf-8"))

    def open_secret(self, name, role):
        """The plaintext of `name`, if `role` may read it and is unlocked."""
        entry = self.data["secrets"].get(name)
        if entry is None:
            raise KeyError(name)
        if name in self._cache:
            return self._cache[name]
        if role is None:
            raise PermissionError(
                f"no role was given, so the secret {name!r} cannot be read; "
                f"run with --role")
        if role not in entry["wrapped"]:
            allowed = ", ".join(sorted(entry["wrapped"])) or "no role"
            raise PermissionError(
                f"the role {role!r} may not read the secret {name!r} "
                f"(allowed: {allowed})")
        data_key = self._unwrap(role, entry["wrapped"][role], name)
        plaintext = _decrypt(data_key, entry["value"], name.encode("utf-8"),
                             what="value").decode("utf-8")
        self._cache[name] = plaintext
        return plaintext

    def grant(self, name, role, from_role):
        """Let `role` read `name`.

        `from_role` must be unlocked and able to read it: granting reseals
        the data key, which means recovering it first. There is no way to
        pass on access you do not have.
        """
        entry = self.data["secrets"].get(name)
        if entry is None:
            raise KeyError(name)
        self._require_role(role)
        if role in entry["wrapped"]:
            return
        if from_role not in entry["wrapped"]:
            raise PermissionError(
                f"the role {from_role!r} cannot read {name!r}, so it cannot "
                f"grant it")
        data_key = self._unwrap(from_role, entry["wrapped"][from_role], name)
        entry["wrapped"][role] = self._wrap(role, data_key, name)

    def revoke(self, name, role):
        entry = self.data["secrets"].get(name)
        if entry is None:
            raise KeyError(name)
        if role not in entry["wrapped"]:
            return
        if len(entry["wrapped"]) == 1:
            raise KeystoreError(
                f"revoking {role!r} would leave {name!r} readable by nobody; "
                f"grant another role first, or remove the secret")
        del entry["wrapped"][role]

    def remove_secret(self, name):
        if name not in self.data["secrets"]:
            raise KeyError(name)
        del self.data["secrets"][name]
        self._cache.pop(name, None)

    # -- reporting, for `keystore list` and --explain

    def inventory(self):
        """(name, roles) for every secret. No passphrase needed."""
        return [(name, self.roles_for(name)) for name in self.names]
