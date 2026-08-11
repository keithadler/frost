"""frost served to the thing that writes the script.

Two halves. `frost mcp` answers the review questions over JSON-RPC, and `frost
context` is what a model should read before writing any frost at all.

The property worth the most here is not the protocol plumbing: it is that
every snippet in the context document parses. A reference that teaches a form
the parser rejects is worse than no reference, because the model believes it
and spends its retries defending a line that was never going to work.
"""

import io
import json
import os
import subprocess
import sys

import pytest

from frostlang import context, mcp
from frostlang.parser import parse

from helpers import REPO


def talk(*messages):
    """Drive the server in-process and return the answers."""
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def call(name, **arguments):
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": arguments}})
    return answer["result"]


def body(result):
    return result["content"][0]["text"]


# ------------------------------------------------------------- the protocol

def test_the_handshake_answers_with_a_version_and_the_tools():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}})
    result = answer["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "frost"
    assert "tools" in result["capabilities"]


def test_an_older_client_gets_the_version_it_asked_for():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "2024-11-05"}})
    assert answer["result"]["protocolVersion"] == "2024-11-05"


def test_a_version_nobody_here_knows_gets_this_server_s_own():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"protocolVersion": "1999-01-01"}})
    assert answer["result"]["protocolVersion"] == mcp.PROTOCOL


def test_a_notification_is_answered_with_silence():
    """A response to a notification is a protocol violation, and the client
    that receives one is entitled to hang waiting for a matching request."""
    assert talk({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_every_tool_has_a_name_a_description_and_a_schema():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = answer["result"]["tools"]
    assert {t["name"] for t in tools} == set(mcp.HANDLERS)
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


def test_rubbish_on_the_wire_is_answered_rather_than_fatal():
    stdin = io.StringIO("not json at all\n")
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    [answer] = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert answer["error"]["code"] == -32700


def test_an_unknown_tool_is_an_error_not_a_crash():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "frost_rm_rf", "arguments": {}}})
    assert answer["error"]["code"] == -32602


def test_an_unknown_method_is_an_error():
    [answer] = talk({"jsonrpc": "2.0", "id": 1, "method": "sudo"})
    assert answer["error"]["code"] == -32601


def test_a_tool_that_raises_answers_instead_of_closing_the_pipe():
    """The agent can act on 'that failed'. It can do nothing with a dead
    server, and neither can the person watching it retry forever.

    The first version of this passed an argument the tool simply defaulted,
    so nothing raised and the handler under test never ran. An argument of
    the wrong type reaches the parser and genuinely throws.
    """
    result = call("frost_check", source=12345)
    assert result["isError"]
    text = result["content"][0]["text"]
    assert "Error" in text or "error" in text


def test_a_batch_is_answered_as_a_batch():
    """Several messages in one array, notifications dropped. A client that
    batches and then waits for an answer per element will hang otherwise."""
    stdin = io.StringIO(json.dumps([
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]) + "\n")
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    answers = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert [a["id"] for a in answers] == [1, 2]


def test_a_batch_of_nothing_but_notifications_is_answered_with_silence():
    stdin = io.StringIO(json.dumps([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]) + "\n")
    stdout = io.StringIO()
    assert mcp.serve(stdin, stdout) == 0
    assert stdout.getvalue() == ""


def test_blank_lines_between_frames_are_ignored():
    stdin = io.StringIO(
        "\n\n" + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        + "\n\n")
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    assert [json.loads(l)["id"] for l in stdout.getvalue().splitlines()] == [1]


def test_an_unknown_notification_is_dropped_rather_than_answered():
    assert talk({"jsonrpc": "2.0", "method": "notifications/whatever"}) == []


def test_the_stream_survives_a_bad_message_in_the_middle():
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
        "{ not json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    answers = [json.loads(l) for l in stdout.getvalue().splitlines()]
    assert [a.get("id") for a in answers] == [1, None, 2]


# ---------------------------------------------------------------- the tools

def test_check_reports_a_clean_script():
    found = json.loads(body(call("frost_check",
                                 source='run "echo" with "hi"\nput it\n')))
    assert found["parses"] and found["verdict"] == "clean"


def test_check_reports_a_danger_without_refusing_to_answer():
    found = json.loads(body(call("frost_check",
                                 source='run "rm" with "-rf", "/tmp/x"\n')))
    assert found["verdict"] == "dangerous"
    assert any("Recursive" in f["title"] for f in found["findings"])


def test_a_script_that_does_not_parse_comes_back_as_a_diagnostic():
    """`${name}` is the first thing a model writes, so this is the path that
    matters most. It used to fall through to the generic handler and return
    the words 'LexError: unexpected character', which is not something a
    model can repair from."""
    result = call("frost_check", source="put ${name} into x\n")
    assert result["isError"]
    report = json.loads(body(result))
    assert report["diagnostics"][0]["line"] == 1
    assert report["diagnostics"][0]["code"] == "unexpected-character"


def test_explain_names_the_programs_and_the_hosts():
    text = body(call("frost_explain",
                     source='run "curl" with "https://api.github.com/x" '
                            "within 5 seconds\n"))
    assert "curl" in text and "api.github.com" in text


def test_policy_reports_the_refusal_and_the_rule():
    found = json.loads(body(call(
        "frost_policy",
        source='run "curl" with "https://evil.example/x" within 5 seconds\n',
        policy='require reaching only "api.github.com"\n')))
    assert found["allowed"] is False
    assert "evil.example" in found["findings"][0]["what"]


def test_policy_refuses_to_draft_its_own_widening():
    """Every call here is a machine asking. An agent handed the exact edit
    that clears its own refusal has been handed the instructions for widening
    its own bounds, which is what --automated exists to prevent."""
    found = json.loads(body(call(
        "frost_policy",
        source='run "curl" with "https://evil.example/x" within 5 seconds\n',
        policy='require reaching only "api.github.com"\n')))
    advice = found["what_would_have_to_change"]
    assert "decision for a person" in advice
    assert "require reaching only" not in advice


def test_a_policy_that_does_not_parse_says_so():
    result = call("frost_policy", source='put "x"\n',
                  policy="forbid everything everywhere\n")
    assert result["isError"]
    assert "does not parse" in body(result)


def test_diff_reports_a_widening():
    found = json.loads(body(call(
        "frost_diff",
        before='run "echo" with "hi"\n',
        after='run "echo" with "hi"\nrun "curl" with "https://x.example" '
              "within 5 seconds\n")))
    assert found["wider"] is True
    assert any("curl" in g for g in found["gained"])


def test_diff_reports_no_change_for_the_same_script():
    same = 'run "echo" with "hi"\n'
    found = json.loads(body(call("frost_diff", before=same, after=same)))
    assert found["wider"] is False
    assert found["gained"] == [] and found["lost"] == []


# ------------------------------------------------------------- what it wont

def test_there_is_no_tool_that_runs_anything():
    """The design, not an omission. A server that executes on request moves
    the decision to run back to the machine, and the decision sitting with a
    person is the whole reason frost exists."""
    for name in mcp.HANDLERS:
        assert "run" not in name.replace("frost_", "")
    listed = json.dumps(mcp.TOOLS).lower()
    assert "executes the script" not in listed

    from frostlang import interp
    source = open(os.path.join(REPO, "frostlang", "mcp.py")).read()
    assert "Interpreter(" not in source, \
        "the server must not be able to construct an interpreter"
    assert interp.Interpreter                       # the real one still exists


def test_no_tool_takes_a_path():
    """A path argument would let whatever holds the other end of this pipe
    read any file the process can reach."""
    for tool in mcp.TOOLS:
        for name, schema in tool["inputSchema"].get("properties", {}).items():
            assert "path" not in name.lower(), tool["name"]
            assert "file" not in name.lower(), tool["name"]
            assert "path" not in schema.get("description", "").lower() or \
                "not a path" in schema["description"].lower()


# ------------------------------------------------------- the model context

def test_every_snippet_in_the_context_document_parses():
    """The property this file exists for.

    A reference that teaches a form the parser rejects is worse than none:
    the model believes it, and spends its retries defending a line that was
    never going to work.
    """
    text = context.model_context()
    blocks = []
    inside, current = False, []
    for line in text.split("\n"):
        if line.startswith("```frost"):
            inside, current = True, []
        elif line.startswith("```") and inside:
            blocks.append("\n".join(current))
            inside = False
        elif inside:
            current.append(line)

    assert blocks, "no frost blocks found; the extractor is broken"
    for block in blocks:
        parse(block)                # raises if the document teaches a lie


def test_the_reserved_words_come_from_the_parser():
    """Typed out once is stale forever, and the value of this document is
    that a model can trust it."""
    from frostlang.parser import HARD_WORDS
    assert set(context.keywords()) == set(HARD_WORDS)
    text = context.model_context()
    assert f"All {len(HARD_WORDS)} of them" in text
    for word in HARD_WORDS:
        assert word in text


def test_the_document_names_the_mistakes_a_model_actually_makes():
    text = context.model_context()
    for wrong in ("${name}", "$(cmd)", "eval", "globbing", "let"):
        assert wrong in text, f"{wrong} is not warned about"


def test_the_document_stays_small_enough_to_paste():
    """It competes with the script for the context window. LANGUAGE.md is
    thousands of lines and is the reason this exists at all."""
    text = context.model_context()
    assert len(text) < 8000, f"{len(text)} characters is too long to paste"
    assert len(text) > 1500, "suspiciously short; is it generating at all?"


def test_the_command_prints_it():
    env = {**os.environ, "PYTHONPATH": REPO}
    done = subprocess.run([sys.executable, os.path.join(REPO, "frost"),
                           "context"], capture_output=True, text=True,
                          env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout == context.model_context()


def test_the_server_serves_the_same_document():
    assert body(call("frost_grammar")) == context.model_context()


def test_the_checked_in_copy_matches_the_generator():
    """MODEL-CONTEXT.md is fetched from the repository by things that never
    run frost, so a stale copy is a document that teaches yesterday's
    grammar. CI rebuilds it and fails if the tree changed."""
    on_disk = open(os.path.join(REPO, "MODEL-CONTEXT.md")).read()
    assert on_disk == context.model_context(), \
        "run tools/build_context.py and commit the result"
