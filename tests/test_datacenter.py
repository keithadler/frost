"""Policy the machine brings, and approvals a person is accountable for.

A policy beside the script is controlled by whoever writes the script, which
is right for a project's rules and useless as a datacenter control: the thing
being constrained should not hold the constraint. And an approval that anything
can write is an approval the agent can grant itself, which is the failure the
whole approval mechanism exists to catch.

Three defences here, and the tests care most about the ways each could be got
around rather than the way each is meant to work.
"""

import json
import os
import subprocess
import sys

import pytest

from frostlang import site, signing
from frostlang.audit import parse_policy, PolicyError

from helpers import REPO

needs_cipher = pytest.mark.skipif(
    not signing.available(),
    reason="signing needs the cryptography extra")


def frost(*args, cwd=None, env=None, timeout=60):
    environ = {**os.environ, "PYTHONPATH": REPO}
    environ.pop("FROST_AUTOMATED", None)
    environ.pop(site.EXTRA_DIR_ENV, None)
    environ.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(REPO, "frost"), *args],
                       capture_output=True, text=True, env=environ, cwd=cwd,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def must(result, what):
    """A setup step that has to have worked, or the test is meaningless.

    A CI failure here read `assert 2 == 3`, which points at the gate when the
    fault was three lines earlier: the approval was never written, so the last
    call was refusing a missing file rather than an untrusted signer. An
    assertion about arrangement should fail as one.
    """
    status, out, err = result
    assert status == 0, f"{what} failed with {status}\n{out}\n{err}"
    return out


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pol").mkdir()

    def write(name, text):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.lstrip("\n"))
        return str(path)
    write.root = tmp_path
    write.policy_dir = str(tmp_path / "pol")
    write.env = {site.EXTRA_DIR_ENV: str(tmp_path / "pol")}
    return write


REACHES = 'run "curl" with "https://x.example" within 30 seconds\nput it\n'
HARMLESS = 'run "echo" with "hello"\nput it\n'


# ------------------------------------------------------------- site policy

def test_a_site_rule_applies_with_no_project_policy(project):
    """The point of the feature. Checking only when --policy was passed would
    mean a machine's own rules applied solely to people who volunteered."""
    project("pol/00-egress.policy", 'forbid running "curl"\n')
    path = project("s.frost", REACHES)
    status, out, err = frost(path, cwd=str(project.root), env=project.env)
    assert status == 3, err
    assert "REFUSED" in err
    assert out == "", "the script ran despite the host forbidding it"


def test_a_project_policy_cannot_loosen_a_site_rule(project):
    """There is no syntax that removes a rule, so composition can only narrow.
    Worth pinning: it is the property the whole arrangement rests on."""
    project("pol/00-egress.policy", 'forbid running "curl"\n')
    loose = project("loose.policy", 'warn running "curl"\n')
    path = project("s.frost", REACHES)
    status, _, err = frost("--policy", loose, path, cwd=str(project.root),
                           env=project.env)
    assert status == 3, err
    assert "REFUSED" in err


def test_two_allow_lists_compose_as_an_intersection(project):
    """Each rule is checked independently and all must pass, so a host allowed
    by one list and not the other is refused. Union would be a widening."""
    project("pol/00-hosts.policy", 'require reaching only "*.internal"\n')
    tighter = project("p.policy", 'require reaching only "x.example"\n')
    path = project("s.frost", REACHES)
    status, _, err = frost("--policy", tighter, path, cwd=str(project.root),
                           env=project.env)
    assert status == 3, err


def test_the_project_policy_still_applies_alongside(project):
    project("pol/00-egress.policy", 'forbid running "nc"\n')
    own = project("p.policy", 'forbid running "curl"\n')
    path = project("s.frost", REACHES)
    status, _, err = frost("--policy", own, path, cwd=str(project.root),
                           env=project.env)
    assert status == 3, err
    assert "curl" in err


def test_an_unreadable_site_policy_fails_closed(project):
    """Present and unreadable is not the same as absent, and treating it as
    absent is how a machine quietly stops being governed."""
    path = project("pol/00.policy", 'forbid running "curl"\n')
    os.chmod(path, 0o000)
    try:
        script = project("s.frost", HARMLESS)
        status, _, err = frost(script, cwd=str(project.root),
                               env=project.env)
        assert status == 2, err
        assert "cannot be read" in err
    finally:
        os.chmod(path, 0o644)


def test_a_broken_site_policy_fails_closed(project):
    project("pol/00.policy", "forbid flying to the moon\n")
    script = project("s.frost", HARMLESS)
    status, _, err = frost(script, cwd=str(project.root), env=project.env)
    assert status == 2, err
    assert "does not parse" in err


def test_the_extra_directory_can_only_add(project):
    """It exists so a container or a test can supply rules without a writable
    /etc. Pointing it at an empty directory must not disable anything."""
    assert site.SITE_DIR in site.directories({})
    assert site.SITE_DIR in site.directories(
        {site.EXTRA_DIR_ENV: "/tmp/elsewhere"})


def test_site_files_are_applied_in_a_stable_order(project):
    project("pol/20-b.policy", 'forbid running "b"\n')
    project("pol/10-a.policy", 'forbid running "a"\n')
    found = site.files(project.env)
    assert [os.path.basename(f) for f in found] == ["10-a.policy",
                                                    "20-b.policy"]


# -------------------------------------------------------------- provenance

def test_explain_names_the_rules_that_governed_it(project):
    project("pol/00-egress.policy", 'forbid running "nc"\n')
    path = project("s.frost", HARMLESS)
    status, out, _ = frost("--explain", path, cwd=str(project.root),
                           env=project.env)
    assert "Governed by:" in out
    assert "00-egress.policy" in out
    assert "(site)" in out


def test_a_recording_carries_the_digest_of_every_policy(project):
    """Otherwise an audit shows a policy existed and never that this run was
    subject to it, which is a claim about a control rather than a control."""
    rules = project("pol/00-egress.policy", 'forbid running "nc"\n')
    path = project("s.frost", HARMLESS)
    rec = project.root / "run.json"
    frost("--record", str(rec), path, cwd=str(project.root), env=project.env)
    policies = json.loads(rec.read_text())["policies"]
    assert [p["origin"] for p in policies] == ["site"]
    assert policies[0]["sha256"] == site.digest(open(rules).read())


def test_the_digest_changes_when_the_rules_do(project):
    before = site.digest('forbid running "nc"\n')
    after = site.digest('forbid running "nc"\nforbid running "curl"\n')
    assert before != after


def test_a_project_policy_is_recorded_too(project):
    own = project("p.policy", 'forbid running "nc"\n')
    path = project("s.frost", HARMLESS)
    rec = project.root / "run.json"
    frost("--policy", own, "--record", str(rec), path, cwd=str(project.root),
          env=project.env)
    origins = [p["origin"] for p in json.loads(rec.read_text())["policies"]]
    assert "project" in origins


# --------------------------------------------------------- automation guard

def test_an_automated_run_may_not_approve(project):
    """A loop that can approve is a loop that approves its own capability
    escalation, and it would defeat every other control here."""
    path = project("s.frost", HARMLESS)
    status, _, err = frost("--automated", "--approve", path,
                           cwd=str(project.root))
    assert status == 2, err
    assert "cannot be used in an automated run" in err
    assert not os.path.exists(path + ".approved")


def test_an_automated_run_may_not_ignore_an_approval(project):
    path = project("s.frost", HARMLESS)
    status, _, err = frost("--automated", "--ignore-approval", path,
                           cwd=str(project.root))
    assert status == 2, err
    assert "cannot be used in an automated run" in err


def test_the_environment_can_declare_automation(project):
    """CI sets this once for the whole runner rather than on every call."""
    path = project("s.frost", HARMLESS)
    status, _, err = frost("--approve", path, cwd=str(project.root),
                           env={"FROST_AUTOMATED": "1"})
    assert status == 2, err
    assert "automated run" in err


def test_an_automated_run_still_runs_ordinary_scripts(project):
    path = project("s.frost", HARMLESS)
    status, out, err = frost("--automated", path, cwd=str(project.root))
    assert status == 0, err
    assert "hello" in out


# ------------------------------------------------------- signed approvals

@needs_cipher
def test_a_signature_survives_a_round_trip():
    private, public = signing.generate()
    approval = {"schema": 1, "script": "d.frost",
                "capabilities": {"programs": ["echo"]}}
    signed = signing.sign(approval, private, "alice")
    assert signing.verify(signed, [public]) == (True, "signed by alice")


@needs_cipher
def test_editing_an_approval_breaks_its_signature():
    """The whole point. An approval anything can rewrite is one the agent can
    grant itself."""
    private, public = signing.generate()
    signed = signing.sign({"schema": 1, "capabilities": {"programs": ["echo"]}},
                          private, "alice")
    signed["capabilities"]["programs"].append("curl")
    ok, why = signing.verify(signed, [public])
    assert not ok
    assert "edited since it was signed" in why


@needs_cipher
def test_a_signature_cannot_be_lifted_onto_another_name():
    """The approver is inside the payload, so renaming them invalidates it."""
    private, public = signing.generate()
    signed = signing.sign({"schema": 1, "capabilities": {}}, private, "alice")
    signed["signature"]["approver"] = "the security team"
    assert not signing.verify(signed, [public])[0]


@needs_cipher
def test_an_untrusted_approver_is_named_in_the_refusal():
    private, _ = signing.generate()
    _, trusted = signing.generate()
    signed = signing.sign({"schema": 1, "capabilities": {}}, private, "mallory")
    ok, why = signing.verify(signed, [trusted])
    assert not ok
    assert "mallory" in why and "not in the list" in why


def test_an_unsigned_approval_is_refused_when_signatures_are_required():
    ok, why = signing.verify({"schema": 1, "capabilities": {}}, ["kAnything"])
    assert not ok
    assert "not signed" in why


def test_verification_never_degrades_to_assuming_valid(monkeypatch):
    """The branch a datacenter runs in when somebody trims the image. An
    unverifiable signature is not a valid one."""
    monkeypatch.setattr(signing, "available", lambda: False)
    approval = {"schema": 1, "capabilities": {},
                "signature": {"algorithm": "ed25519", "approver": "alice",
                              "public_key": "kAbc", "value": "kSig"}}
    ok, why = signing.verify(approval, ["kAbc"])
    assert not ok
    assert "cannot verify" in why


def test_an_unknown_algorithm_is_refused():
    approval = {"signature": {"algorithm": "rot13", "public_key": "kAbc"}}
    assert not signing.verify(approval, ["kAbc"])[0]


# ------------------------------------------------------ the whole chain

@needs_cipher
def test_a_signed_approval_lets_the_script_run(project):
    key = str(project.root / "alice.key")
    out = must(frost("--new-approver-key", key, cwd=str(project.root)),
               "generating an approver key")
    public = [l.split(": ", 1)[1] for l in out.split("\n")
              if l.startswith("public key:")][0]
    project("pol/10-approvals.policy",
            f'require an approval signed by "{public}"\n')
    path = project("s.frost", HARMLESS)

    must(frost("--approve", "--sign-with", key, "--approver", "alice", path,
               cwd=str(project.root)), "signing the approval")
    status, out, err = frost(path, cwd=str(project.root), env=project.env)
    assert status == 0, err
    assert "hello" in out


@needs_cipher
def test_an_agent_cannot_approve_with_its_own_key(project):
    """The escalation this exists to stop: widen the script, sign it yourself,
    walk past the gate."""
    alice = str(project.root / "alice.key")
    out = must(frost("--new-approver-key", alice, cwd=str(project.root)),
               "generating alice's key")
    public = [l.split(": ", 1)[1] for l in out.split("\n")
              if l.startswith("public key:")][0]
    project("pol/10-approvals.policy",
            f'require an approval signed by "{public}"\n')

    rogue = str(project.root / "rogue.key")
    must(frost("--new-approver-key", rogue, cwd=str(project.root)),
         "generating the rogue key")
    path = project("s.frost", REACHES)
    must(frost("--approve", "--sign-with", rogue, "--approver", "the agent",
               path, cwd=str(project.root)),
         "the agent signing its own approval")
    assert os.path.exists(path + ".approved"), (
        "the agent's approval was never written, so the refusal below would "
        "be about a missing file rather than an untrusted signer")

    status, out, err = frost(path, cwd=str(project.root), env=project.env)
    assert status == 3, f"expected a refusal about the signer\n{err}"
    assert "not in the list of approvers" in err
    assert out == ""


@needs_cipher
def test_an_unsigned_approval_is_refused_by_the_policy(project):
    _, out, _ = frost("--new-approver-key", str(project.root / "a.key"),
                      cwd=str(project.root))
    public = [l.split(": ", 1)[1] for l in out.split("\n")
              if l.startswith("public key:")][0]
    project("pol/10-approvals.policy",
            f'require an approval signed by "{public}"\n')
    path = project("s.frost", HARMLESS)
    must(frost("--approve", path, cwd=str(project.root)),
         "writing an unsigned approval")
    status, _, err = frost(path, cwd=str(project.root), env=project.env)
    assert status == 3, err
    assert "not signed" in err


@needs_cipher
def test_the_approval_records_the_commit_it_was_read_against(project):
    key = str(project.root / "a.key")
    frost("--new-approver-key", key, cwd=str(project.root))
    path = project("s.frost", HARMLESS)
    frost("--approve", "--sign-with", key, "--commit", "abc123", path,
          cwd=str(project.root))
    payload = json.loads(open(path + ".approved").read())
    assert payload["commit"] == "abc123"


@needs_cipher
def test_the_commit_is_taken_from_ci_when_not_given(project):
    key = str(project.root / "a.key")
    frost("--new-approver-key", key, cwd=str(project.root))
    path = project("s.frost", HARMLESS)
    frost("--approve", "--sign-with", key, path, cwd=str(project.root),
          env={"GITHUB_SHA": "deadbeef"})
    assert json.loads(open(path + ".approved").read())["commit"] == "deadbeef"


@needs_cipher
def test_a_signing_key_is_not_world_readable(project):
    key = str(project.root / "a.key")
    frost("--new-approver-key", key, cwd=str(project.root))
    assert oct(os.stat(key).st_mode)[-3:] == "600"


def test_a_signed_by_rule_needs_keys():
    with pytest.raises(PolicyError) as e:
        parse_policy("require an approval signed by everyone\n")
    assert "public keys in quotes" in str(e.value)


# ------------------------------ the modules in process, so coverage sees them
#
# The tests above drive these through subprocesses, which proves the entry
# point and measures nothing. scaffold.py taught this lesson an hour earlier
# and I walked into it again with two more modules.

def test_load_reads_and_digests_every_file(tmp_path):
    (tmp_path / "10-a.policy").write_text('forbid running "a"\n')
    (tmp_path / "20-b.policy").write_text('forbid running "b"\n')
    rules, provenance = site.load({site.EXTRA_DIR_ENV: str(tmp_path)})
    assert len(rules) == 2
    assert [p["origin"] for p in provenance] == ["site", "site"]
    assert all(len(p["sha256"]) == 64 for p in provenance)


def test_load_with_nothing_present_is_empty():
    rules, provenance = site.load({site.EXTRA_DIR_ENV: "/no/such/dir"})
    assert (rules, provenance) == ([], [])


def test_load_refuses_an_unreadable_file(tmp_path):
    path = tmp_path / "00.policy"
    path.write_text('forbid running "a"\n')
    os.chmod(path, 0o000)
    try:
        with pytest.raises(site.SitePolicyError) as e:
            site.load({site.EXTRA_DIR_ENV: str(tmp_path)})
        assert "cannot be read" in e.value.msg
        assert "not the same as no site policy" in e.value.hint
    finally:
        os.chmod(path, 0o644)


def test_load_refuses_a_file_that_does_not_parse(tmp_path):
    (tmp_path / "00.policy").write_text("forbid flying\n")
    with pytest.raises(site.SitePolicyError) as e:
        site.load({site.EXTRA_DIR_ENV: str(tmp_path)})
    assert "does not parse" in e.value.msg


def test_describe_lines_up_the_paths():
    lines = site.describe([
        {"path": "/etc/frost/policy.d/a.policy", "sha256": "a" * 64,
         "origin": "site"},
        {"path": "p.policy", "sha256": "b" * 64, "origin": "project"},
    ])
    assert lines[0] == "Governed by:"
    assert "(site)" in lines[1] and "(project)" in lines[2]
    assert len(lines) == 3


def test_describe_says_nothing_when_nothing_governed():
    assert site.describe([]) == []


def test_note_records_an_origin():
    entry = site.note("p.policy", "forbid running \"a\"\n")
    assert entry["origin"] == "project"
    assert entry["sha256"] == site.digest('forbid running "a"\n')


# -- signing, in process

def test_signing_reports_whether_it_is_available():
    assert signing.available() in (True, False)


def test_a_missing_cipher_is_a_sentence_not_an_import_error(monkeypatch):
    monkeypatch.setattr(signing, "available", lambda: False)
    with pytest.raises(signing.SigningError) as e:
        signing._require()
    assert "cryptography" in e.value.msg
    assert "pip install" in e.value.hint


def test_a_key_that_is_not_a_frost_key_is_refused():
    with pytest.raises(signing.SigningError) as e:
        signing._decode("ssh-ed25519 AAAA")
    assert "not a frost key" in e.value.msg


def test_an_unreadable_key_is_a_sentence():
    with pytest.raises(signing.SigningError) as e:
        signing.read_key("/no/such/key")
    assert "cannot read the signing key" in e.value.msg


@needs_cipher
def test_a_key_round_trips_through_the_text_form(tmp_path):
    private, public = signing.generate()
    path = str(tmp_path / "k")
    signing.write_key(path, private)
    assert signing.read_key(path) == private
    assert signing.public_of(private) == public


@needs_cipher
def test_the_payload_covers_the_approver_and_the_key():
    """The bug this replaced: signing everything except the whole signature
    block left the name outside what was covered."""
    private, _ = signing.generate()
    signed = signing.sign({"schema": 1, "capabilities": {}}, private, "alice")
    covered = signing.payload(signed).decode()
    assert "alice" in covered
    assert signed["signature"]["public_key"] in covered
    assert signed["signature"]["value"] not in covered


@needs_cipher
def test_the_payload_is_stable_across_key_order():
    private, _ = signing.generate()
    a = signing.sign({"schema": 1, "script": "s", "capabilities": {}},
                     private, "alice")
    b = dict(reversed(list(a.items())))
    assert signing.payload(a) == signing.payload(b), \
        "a signature over a formatting choice would break on re-serialising"


# ------------------------------------------- the flake, and what caused it

def test_a_comment_marker_inside_quotes_is_not_a_comment():
    """The bug behind an intermittent CI failure nobody could reproduce.

    Policy comments start with `--`. An Ed25519 public key is urlsafe base64,
    so it contains `-`, so about one key in a hundred contains `--`. The line
    `require an approval signed by "kA--bQ..."` was truncated at the marker,
    leaving an unterminated quote and a policy that did not parse, and the run
    failed with exit 2 while the test was asserting about approvals.

    It failed roughly one CI run in five and never once locally, which is what
    a 1-in-100 event looks like across a matrix of jobs.
    """
    from frostlang.audit import parse_policy

    rules = parse_policy('require an approval signed by "kA--bQ_x-y"')
    assert rules[0].detail == ["kA--bQ_x-y"]

    rules = parse_policy('forbid running "my--tool"  -- and here is a comment')
    assert rules[0].subject == "my--tool"
    assert rules[0].hint == "and here is a comment"

    rules = parse_policy('forbid reading "/data/a#b"  # hash comments too')
    assert rules[0].subject == "/data/a#b"
    assert rules[0].hint == "hash comments too"


@needs_cipher
def test_every_generated_key_survives_the_policy_that_names_it():
    """The property, not one example. Generating keys until one contains the
    marker takes a few hundred tries, so the fixed value above is the
    regression test and this is the check that the generator and the parser
    agree at all."""
    from frostlang.audit import parse_policy

    for _ in range(200):
        _, public = signing.generate()
        rules = parse_policy(f'require an approval signed by "{public}"')
        assert rules[0].detail == [public], public
