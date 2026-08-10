"""The keystore: encryption, roles, and the command line that manages it.

The property worth stating plainly is the one the keypairs exist for: adding
a secret and granting a role need no passphrase, and *reading* needs one. If
storing required every recipient's passphrase, whoever adds a credential
would need everyone's secret, which is the thing a keystore exists to avoid.
"""

import io
import json
import os
import subprocess
import sys

import pytest

from helpers import REPO

pytest.importorskip("cryptography",
                    reason="the keystore is an optional extra")

from frostlang.keystore import (Keystore, KeystoreError, derive_key,
                                SCRYPT_N, KEY_BYTES)
from frostlang import keystore_cli

VALUE = "hunter2-the-actual-password"


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "test.keystore")
    ks = Keystore.create(path)
    ks.add_role("admin", "admin-pass")
    ks.add_role("deploy", "deploy-pass")
    ks.add_role("readonly", "ro-pass")
    ks.save()
    return ks


@pytest.fixture
def stocked(store):
    store.set_secret("db password", VALUE, ["deploy", "admin"])
    store.save()
    return store


def reopen(store):
    """A fresh Keystore from disk, with nothing unlocked."""
    return Keystore.load(store.path)


# ------------------------------------------------------------- the format

def test_the_file_is_readable_json(stocked):
    with open(stocked.path) as fh:
        data = json.load(fh)
    assert set(data) == {"version", "roles", "secrets"}


def test_the_names_and_roles_are_in_plaintext(stocked):
    """What a reviewer needs to see is who may read what, not the values."""
    text = open(stocked.path).read()
    assert "db password" in text
    assert "deploy" in text and "admin" in text


def test_the_value_is_not(stocked):
    assert VALUE not in open(stocked.path).read()


def test_no_passphrase_is_stored(stocked):
    text = open(stocked.path).read()
    for passphrase in ("admin-pass", "deploy-pass", "ro-pass"):
        assert passphrase not in text


def test_the_file_is_written_with_tight_permissions(stocked):
    assert oct(os.stat(stocked.path).st_mode)[-3:] == "600"


def test_a_future_version_is_refused(tmp_path):
    path = tmp_path / "future.keystore"
    path.write_text(json.dumps({"version": 99, "roles": {}, "secrets": {}}))
    with pytest.raises(KeystoreError) as e:
        Keystore.load(str(path))
    assert "version 99" in str(e.value)


def test_something_that_is_not_a_keystore_is_refused(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text('{"hello": "world"}')
    with pytest.raises(KeystoreError) as e:
        Keystore.load(str(path))
    assert "does not look like a keystore" in str(e.value)


def test_a_missing_file_is_a_clear_error():
    with pytest.raises(KeystoreError) as e:
        Keystore.load("/no/such/keystore")
    assert "no keystore at" in str(e.value)


def test_corrupt_json_is_a_clear_error(tmp_path):
    path = tmp_path / "broken.keystore"
    path.write_text("{not json")
    with pytest.raises(KeystoreError) as e:
        Keystore.load(str(path))
    assert "not a valid keystore" in str(e.value)


# ------------------------------------------------- storing needs no secret

def test_storing_needs_no_passphrase(store):
    """The whole reason roles have keypairs."""
    fresh = reopen(store)                    # nothing unlocked
    fresh.set_secret("db password", VALUE, ["deploy"])
    fresh.save()
    reader = reopen(store)
    reader.unlock("deploy", "deploy-pass")
    assert reader.open_secret("db password", "deploy") == VALUE


def test_the_inventory_needs_no_passphrase(stocked):
    assert reopen(stocked).inventory() == [("db password", ["admin", "deploy"])]


def test_who_may_read_is_answerable_without_a_passphrase(stocked):
    fresh = reopen(stocked)
    assert fresh.may_read("db password", "deploy") is True
    assert fresh.may_read("db password", "readonly") is False


# ------------------------------------------------------------- reading

def test_an_authorised_role_reads_it(stocked):
    fresh = reopen(stocked)
    fresh.unlock("deploy", "deploy-pass")
    assert fresh.open_secret("db password", "deploy") == VALUE


def test_every_authorised_role_reads_the_same_value(stocked):
    for role, passphrase in (("deploy", "deploy-pass"), ("admin", "admin-pass")):
        fresh = reopen(stocked)
        fresh.unlock(role, passphrase)
        assert fresh.open_secret("db password", role) == VALUE


def test_an_unauthorised_role_is_denied(stocked):
    fresh = reopen(stocked)
    fresh.unlock("readonly", "ro-pass")
    with pytest.raises(PermissionError) as e:
        fresh.open_secret("db password", "readonly")
    assert "may not read" in str(e.value)
    assert "admin, deploy" in str(e.value)


def test_a_wrong_passphrase_is_refused(stocked):
    with pytest.raises(PermissionError) as e:
        reopen(stocked).unlock("deploy", "not-the-passphrase")
    assert "wrong passphrase" in str(e.value)


def test_a_locked_role_cannot_read(stocked):
    with pytest.raises(PermissionError) as e:
        reopen(stocked).open_secret("db password", "deploy")
    assert "not been unlocked" in str(e.value)


def test_no_role_at_all_is_refused(stocked):
    with pytest.raises(PermissionError) as e:
        reopen(stocked).open_secret("db password", None)
    assert "--role" in str(e.value)


def test_an_unknown_secret_raises_key_error(stocked):
    with pytest.raises(KeyError):
        reopen(stocked).open_secret("no such secret", "deploy")


def test_an_unknown_role_is_a_clear_error(stocked):
    with pytest.raises(KeystoreError) as e:
        reopen(stocked).unlock("nobody", "x")
    assert "no role 'nobody'" in str(e.value)
    assert "admin, deploy, readonly" in str(e.value)


# ------------------------------------------------------------ tampering

@pytest.mark.parametrize("path", [
    ["secrets", "db password", "value", "ciphertext"],
    ["secrets", "db password", "value", "nonce"],
    ["secrets", "db password", "wrapped", "deploy", "ciphertext"],
    ["roles", "deploy", "private", "ciphertext"],
])
def test_modifying_the_file_is_detected(stocked, path):
    """AES-GCM authenticates, so a change is caught rather than decrypting
    to something plausible."""
    with open(stocked.path) as fh:
        data = json.load(fh)
    target = data
    for key in path[:-1]:
        target = target[key]
    original = target[path[-1]]
    target[path[-1]] = ("A" if original[0] != "A" else "B") + original[1:]
    with open(stocked.path, "w") as fh:
        json.dump(data, fh)

    fresh = Keystore.load(stocked.path)
    with pytest.raises(PermissionError):
        fresh.unlock("deploy", "deploy-pass")
        fresh.open_secret("db password", "deploy")


def test_a_secret_cannot_be_moved_to_another_name(stocked):
    """The name is authenticated data, so renaming an entry breaks it —
    otherwise a low-value secret could be swapped for a high-value one."""
    with open(stocked.path) as fh:
        data = json.load(fh)
    data["secrets"]["other name"] = data["secrets"].pop("db password")
    with open(stocked.path, "w") as fh:
        json.dump(data, fh)

    fresh = Keystore.load(stocked.path)
    fresh.unlock("deploy", "deploy-pass")
    with pytest.raises(PermissionError):
        fresh.open_secret("other name", "deploy")


def test_a_wrapping_cannot_be_copied_between_roles(stocked):
    """The role is authenticated too, so a role cannot be given access by
    copying another role's wrapped key."""
    with open(stocked.path) as fh:
        data = json.load(fh)
    wrapped = data["secrets"]["db password"]["wrapped"]
    wrapped["readonly"] = wrapped["deploy"]
    with open(stocked.path, "w") as fh:
        json.dump(data, fh)

    fresh = Keystore.load(stocked.path)
    fresh.unlock("readonly", "ro-pass")
    with pytest.raises(PermissionError):
        fresh.open_secret("db password", "readonly")


# --------------------------------------------------------- grant and revoke

def test_granting_lets_another_role_read(stocked):
    granter = reopen(stocked)
    granter.unlock("deploy", "deploy-pass")
    granter.grant("db password", "readonly", "deploy")
    granter.save()

    reader = reopen(stocked)
    reader.unlock("readonly", "ro-pass")
    assert reader.open_secret("db password", "readonly") == VALUE


def test_you_cannot_grant_what_you_cannot_read(stocked):
    granter = reopen(stocked)
    granter.unlock("readonly", "ro-pass")
    with pytest.raises(PermissionError) as e:
        granter.grant("db password", "readonly", "readonly")
    assert "cannot read" in str(e.value)


def test_granting_twice_is_harmless(stocked):
    granter = reopen(stocked)
    granter.unlock("deploy", "deploy-pass")
    granter.grant("db password", "readonly", "deploy")
    granter.grant("db password", "readonly", "deploy")
    assert granter.roles_for("db password") == ["admin", "deploy", "readonly"]


def test_revoking_removes_access(stocked):
    stocked.revoke("db password", "admin")
    stocked.save()
    reader = reopen(stocked)
    reader.unlock("admin", "admin-pass")
    with pytest.raises(PermissionError):
        reader.open_secret("db password", "admin")


def test_revoking_the_last_role_is_refused(stocked):
    stocked.revoke("db password", "admin")
    with pytest.raises(KeystoreError) as e:
        stocked.revoke("db password", "deploy")
    assert "readable by nobody" in str(e.value)


def test_removing_a_role_that_is_the_only_reader_is_refused(store):
    store.set_secret("lonely", "x", ["deploy"])
    with pytest.raises(KeystoreError) as e:
        store.remove_role("deploy")
    assert "readable by nobody" in str(e.value)


def test_removing_a_role_drops_its_wrappings(stocked):
    stocked.remove_role("admin")
    assert stocked.roles_for("db password") == ["deploy"]
    assert "admin" not in stocked.roles


# ----------------------------------------------------------- role hygiene

@pytest.mark.parametrize("name", ["", "Deploy", "has space", "-leading",
                                  "has/slash", "has.dot"])
def test_a_bad_role_name_is_refused(store, name):
    with pytest.raises(KeystoreError):
        store.add_role(name, "pass")


def test_a_duplicate_role_is_refused(store):
    with pytest.raises(KeystoreError) as e:
        store.add_role("deploy", "another-pass")
    assert "already exists" in str(e.value)


def test_a_role_needs_a_passphrase(store):
    with pytest.raises(KeystoreError):
        store.add_role("nopass", "")


def test_a_secret_needs_at_least_one_role(store):
    with pytest.raises(KeystoreError) as e:
        store.set_secret("orphan", "x", [])
    assert "readable by nobody" in str(e.value)


def test_a_secret_cannot_name_a_role_that_does_not_exist(store):
    with pytest.raises(KeystoreError):
        store.set_secret("x", "y", ["ghost"])


def test_two_roles_get_unrelated_ciphertexts(stocked):
    """Ephemeral keys per wrapping, so the bytes do not reveal that two roles
    hold the same data key."""
    wrapped = stocked.data["secrets"]["db password"]["wrapped"]
    assert wrapped["deploy"]["ciphertext"] != wrapped["admin"]["ciphertext"]
    assert wrapped["deploy"]["ephemeral"] != wrapped["admin"]["ephemeral"]


def test_the_same_value_stored_twice_differs_on_disk(store):
    store.set_secret("a", VALUE, ["deploy"])
    store.set_secret("b", VALUE, ["deploy"])
    a = store.data["secrets"]["a"]["value"]["ciphertext"]
    b = store.data["secrets"]["b"]["value"]["ciphertext"]
    assert a != b


def test_the_key_derivation_is_deliberately_slow():
    assert SCRYPT_N >= 1 << 14, "scrypt cost is too low to slow a guess down"
    assert len(derive_key("passphrase", b"x" * 16)) == KEY_BYTES


# ------------------------------------------------------------- the CLI

def keystore_cmd(*args, passphrase=None, value=None):
    out = io.StringIO()
    status = keystore_cli.main(
        list(args),
        out=out,
        passphrases=io.StringIO(f"{passphrase}\n{passphrase}\n")
        if passphrase else None,
        values=io.StringIO(value) if value is not None else None)
    return status, out.getvalue()


def test_cli_init_creates_a_keystore(tmp_path):
    path = str(tmp_path / "new.keystore")
    status, out = keystore_cmd("init", path, "--role", "deploy",
                               passphrase="pw")
    assert status == 0
    assert "created" in out
    assert Keystore.load(path).roles == ["deploy"]


def test_cli_init_refuses_to_overwrite(tmp_path, store):
    status, _ = keystore_cmd("init", store.path, "--role", "x",
                             passphrase="pw")
    assert status == 2


def test_cli_set_then_get_round_trips(tmp_path):
    path = str(tmp_path / "k.keystore")
    keystore_cmd("init", path, "--role", "deploy", passphrase="pw")
    status, _ = keystore_cmd("set", path, "token", "--roles", "deploy",
                             value=VALUE)
    assert status == 0
    status, out = keystore_cmd("get", path, "token", "--role", "deploy",
                               passphrase="pw")
    assert (status, out.strip()) == (0, VALUE)


def test_cli_list_shows_who_may_read(stocked):
    status, out = keystore_cmd("list", stocked.path)
    assert status == 0
    assert "db password" in out and "admin, deploy" in out


def test_cli_list_on_an_empty_keystore(store):
    status, out = keystore_cmd("list", store.path)
    assert "no secrets yet" in out


def test_cli_roles_counts_what_each_can_read(stocked):
    status, out = keystore_cmd("roles", stocked.path)
    assert "deploy  — may read 1 secret(s)" in out
    assert "readonly  — may read 0 secret(s)" in out


def test_cli_grant_and_revoke(stocked):
    status, _ = keystore_cmd("grant", stocked.path, "db password",
                             "--role", "readonly", "--as", "deploy",
                             passphrase="deploy-pass")
    assert status == 0
    assert reopen(stocked).may_read("db password", "readonly")

    status, _ = keystore_cmd("revoke", stocked.path, "db password",
                             "--role", "readonly")
    assert status == 0
    assert not reopen(stocked).may_read("db password", "readonly")


def test_cli_remove_deletes_a_secret(stocked):
    status, _ = keystore_cmd("remove", stocked.path, "db password")
    assert (status, reopen(stocked).names) == (0, [])


def test_cli_denies_a_get_for_the_wrong_role(stocked):
    status, _ = keystore_cmd("get", stocked.path, "db password",
                             "--role", "readonly", passphrase="ro-pass")
    assert status == 3


def test_cli_reports_an_unknown_secret(stocked):
    status, _ = keystore_cmd("get", stocked.path, "nope",
                             "--role", "deploy", passphrase="deploy-pass")
    assert status == 2


def test_cli_with_no_command_prints_help():
    status, out = keystore_cmd()
    assert status == 2
    assert "COMMAND" in out


def test_the_passphrase_can_come_from_the_environment(tmp_path, monkeypatch):
    """CI has no terminal."""
    monkeypatch.setenv("FROST_PASSPHRASE", "from-env")
    path = str(tmp_path / "ci.keystore")
    assert keystore_cli.main(["init", path, "--role", "ci"],
                             out=io.StringIO()) == 0
    ks = Keystore.load(path)
    ks.unlock("ci", "from-env")          # raises if it used something else


def test_mismatched_confirmation_is_refused(tmp_path):
    path = str(tmp_path / "k.keystore")
    status = keystore_cli.main(
        ["init", path, "--role", "x"], out=io.StringIO(),
        passphrases=io.StringIO("one\ntwo\n"))
    assert status == 2
    assert not os.path.exists(path)


def test_split_roles():
    assert keystore_cli.split_roles("a, b ,c") == ["a", "b", "c"]
    assert keystore_cli.split_roles("") == []


# ------------------------------------------------- running a script with it

def frost(*args, env=None, stdin=None):
    environ = {**os.environ, "PYTHONPATH": REPO}
    environ.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, cwd=REPO,
                       env=environ, input=stdin, timeout=60)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def script(tmp_path):
    def make(source):
        path = tmp_path / "s.frost"
        path.write_text(source)
        return str(path)
    return make


def test_a_script_reads_a_secret_and_redacts_it(stocked, script):
    path = script('put the secret "db password" into pw\n'
                  'put "using" && pw')
    status, out, err = frost("--keystore", stocked.path, "--role", "deploy",
                             path, env={"FROST_PASSPHRASE": "deploy-pass"})
    assert status == 0, err
    assert out.strip() == "using «secret db password»"
    assert VALUE not in out


def test_a_script_is_refused_before_running_for_the_wrong_role(stocked,
                                                               script,
                                                               tmp_path):
    marker = tmp_path / "ran.txt"
    path = script(f'put "x" into file "{marker}"\n'
                  'put the secret "db password" into pw')
    status, _, err = frost("--keystore", stocked.path, "--role", "readonly",
                           path, env={"FROST_PASSPHRASE": "ro-pass"})
    assert status == 3
    assert "REFUSED" in err
    assert "may not read" in err
    assert not marker.exists(), "the refused script ran anyway"


def test_a_missing_secret_is_refused_before_running(stocked, script):
    path = script('put the secret "no such thing" into pw')
    status, _, err = frost("--keystore", stocked.path, "--role", "deploy",
                           path, env={"FROST_PASSPHRASE": "deploy-pass"})
    assert status == 3
    assert "no such secret" in err


def test_a_script_without_a_keystore_is_refused(script):
    path = script('put the secret "db password" into pw')
    status, _, err = frost(path)
    assert status == 3
    assert "no keystore is open" in err


def test_a_wrong_passphrase_stops_the_run(stocked, script):
    path = script('put the secret "db password" into pw')
    status, _, err = frost("--keystore", stocked.path, "--role", "deploy",
                           path, env={"FROST_PASSPHRASE": "wrong"})
    assert status == 2
    assert "wrong passphrase" in err


def test_explain_lists_the_secrets_without_a_keystore(script):
    """Reviewing a script must not require the credentials it uses."""
    path = script('put the secret "db password" into pw\nrun "psql" with pw')
    status, out, _ = frost("--explain", path)
    assert "db password" in out
    assert "Reads these secrets" in out


def test_the_keystore_subcommand_works_through_the_real_cli(tmp_path):
    path = str(tmp_path / "cli.keystore")
    status, out, err = frost("keystore", "init", path, "--role", "deploy",
                             env={"FROST_PASSPHRASE": "pw"})
    assert status == 0, err
    status, out, err = frost("keystore", "list", path)
    assert (status, "no secrets yet" in out) == (0, True)
