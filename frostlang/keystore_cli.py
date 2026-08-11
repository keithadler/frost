"""`frost keystore ...`: managing the keystore from the command line.

Kept apart from cli.py because it is a different job: cli.py runs and inspects
scripts, this administers a file. The only thing they share is how a
passphrase is obtained.

A passphrase comes from `FROST_PASSPHRASE` when it is set, and from a terminal
prompt otherwise. The environment variable exists because CI has no terminal;
it is read once and never echoed. Nothing here ever writes a passphrase or a
secret value to a log, and `keystore get`: the one command that prints a
value, says so in its help.
"""
# SPDX-License-Identifier: MIT

import argparse
import getpass
import os
import sys

from .keystore import Keystore, KeystoreError


def read_passphrase(role, confirm=False, stream=None):
    """The passphrase for `role`, from the environment or a prompt."""
    from_env = os.environ.get("FROST_PASSPHRASE")
    if from_env is not None:
        # CI has no terminal, and asking it to confirm a value it read from
        # its own environment would be theatre.
        return from_env

    def ask(prompt):
        if stream is not None:              # piped input, and tests
            line = stream.readline()
            if not line:
                raise KeystoreError("no passphrase given")
            return line.rstrip("\n")
        try:
            return getpass.getpass(prompt)
        except (EOFError, KeyboardInterrupt):
            raise KeystoreError("no passphrase given")

    first = ask(f"passphrase for role {role!r}: ")
    if not first:
        raise KeystoreError("a passphrase cannot be empty")
    if confirm and ask("confirm: ") != first:
        raise KeystoreError("the two passphrases did not match")
    return first


def read_value(name, stream=None):
    """The value for a secret: from stdin when piped, otherwise prompted.

    Prompted rather than taken as an argument on purpose. An argument would
    appear in the shell history and in the process list of every other user
    on the machine.
    """
    if stream is not None:
        return stream.read().rstrip("\n")
    if not sys.stdin.isatty():
        return sys.stdin.read().rstrip("\n")
    try:
        return getpass.getpass(f"value for {name!r}: ")
    except (EOFError, KeyboardInterrupt):
        raise KeystoreError("no value given")


def split_roles(text):
    return [r.strip() for r in (text or "").split(",") if r.strip()]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="frost keystore",
        description="Manage a frost keystore.")
    subs = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, help_text):
        p = subs.add_parser(name, help=help_text, description=help_text)
        p.add_argument("file", metavar="KEYSTORE")
        return p

    p = add("init", "create a keystore with one role")
    p.add_argument("--role", required=True, help="the first role's name")

    p = add("add-role", "add a role")
    p.add_argument("role")

    add("roles", "list the roles")
    add("list", "list the secrets and who may read each")

    p = add("set", "store a secret, readable by the given roles")
    p.add_argument("name")
    p.add_argument("--roles", required=True,
                   help="comma separated; at least one")

    p = add("get", "print a secret in the clear. It will be visible")
    p.add_argument("name")
    p.add_argument("--role", required=True)

    p = add("grant", "let another role read a secret")
    p.add_argument("name")
    p.add_argument("--role", required=True, help="the role being granted")
    p.add_argument("--as", dest="from_role", required=True,
                   help="a role that can already read it")

    p = add("revoke", "stop a role reading a secret")
    p.add_argument("name")
    p.add_argument("--role", required=True)

    p = add("remove", "delete a secret entirely")
    p.add_argument("name")

    p = add("remove-role", "delete a role")
    p.add_argument("role")

    return parser


def main(argv, out=None, passphrases=None, values=None):
    """Run a keystore subcommand. Returns an exit status.

    `passphrases` and `values` are streams, so this is testable without a
    terminal; in normal use they are None and a prompt is used.
    """
    out = out or sys.stdout
    parser = build_parser()
    opts = parser.parse_args(argv)
    if not opts.command:
        parser.print_help(out)
        return 2

    try:
        return run(opts, out, passphrases, values)
    except KeystoreError as e:
        sys.stderr.write(f"frost keystore: {e}\n")
        return 2
    except PermissionError as e:
        sys.stderr.write(f"frost keystore: {e}\n")
        return 3
    except KeyError as e:
        sys.stderr.write(f"frost keystore: there is no secret named {e}\n")
        return 2


def run(opts, out, passphrases, values):
    command = opts.command

    if command == "init":
        if os.path.exists(opts.file):
            raise KeystoreError(f"{opts.file} already exists")
        store = Keystore.create(opts.file)
        store.add_role(opts.role,
                       read_passphrase(opts.role, confirm=True, stream=passphrases))
        store.save()
        out.write(f"created {opts.file} with role {opts.role!r}\n")
        return 0

    store = Keystore.load(opts.file)

    if command == "add-role":
        store.add_role(opts.role,
                       read_passphrase(opts.role, confirm=True, stream=passphrases))
        store.save()
        out.write(f"added role {opts.role!r}\n")
        out.write("it can read nothing yet; grant it a secret with "
                  "'frost keystore grant'\n")
        return 0

    if command == "remove-role":
        store.remove_role(opts.role)
        store.save()
        out.write(f"removed role {opts.role!r}\n")
        return 0

    if command == "roles":
        if not store.roles:
            out.write("no roles yet\n")
        for role in store.roles:
            readable = [n for n in store.names if store.may_read(n, role)]
            out.write(f"  {role}  may read {len(readable)} secret(s)\n")
        return 0

    if command == "list":
        if not store.names:
            out.write("no secrets yet\n")
            return 0
        width = max(len(n) for n in store.names)
        for name, roles in store.inventory():
            out.write(f"  {name.ljust(width)}  {', '.join(roles)}\n")
        return 0

    if command == "set":
        roles = split_roles(opts.roles)
        # Storing needs no passphrase: the data key is sealed to each role's
        # public key. That is what lets someone add a credential for a role
        # whose passphrase they do not have.
        store.set_secret(opts.name, read_value(opts.name, values), roles)
        store.save()
        out.write(f"stored {opts.name!r}, readable by {', '.join(roles)}\n")
        return 0

    if command == "get":
        store.unlock(opts.role,
                     read_passphrase(opts.role, stream=passphrases))
        out.write(store.open_secret(opts.name, opts.role) + "\n")
        return 0

    if command == "grant":
        store.unlock(opts.from_role,
                     read_passphrase(opts.from_role, stream=passphrases))
        store.grant(opts.name, opts.role, opts.from_role)
        store.save()
        out.write(f"{opts.role!r} may now read {opts.name!r}\n")
        return 0

    if command == "revoke":
        store.revoke(opts.name, opts.role)
        store.save()
        out.write(f"{opts.role!r} may no longer read {opts.name!r}\n")
        return 0

    if command == "remove":
        store.remove_secret(opts.name)
        store.save()
        out.write(f"removed {opts.name!r}\n")
        return 0

    raise KeystoreError(f"unknown command {command!r}")   # pragma: no cover
