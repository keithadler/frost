"""Cleanup blocks.

The gap this closes was the sharpest one in the language: abort-on-failure is
the headline default, and there was no way to release anything on the way out.
A lock file taken on line 3 outlived every script that failed on line 4.

`ensure` registers a block when execution reaches it. Registered blocks run
when the script ends: normally, on error, on `quit`, or on interrupt, most
recent first, so cleanup unwinds in the reverse of the order things were
acquired. A failure inside a cleanup block is reported but never replaces the
error that ended the script, because that error is what the reader needs.
"""

import pytest

from frostlang.parser import parse, ParseError
from frostlang.interp import FrostError

from helpers import out, run, err, run_failing, caps_for


# ------------------------------------------------------------ registration

def test_a_cleanup_block_runs_at_the_end():
    src = """
    ensure
        put "cleaned"
    end ensure
    put "working"
    """
    assert out(src) == "working\ncleaned"


def test_a_cleanup_block_does_not_run_where_it_is_written():
    """It registers at that point; it runs at the end."""
    src = """
    put "one"
    ensure
        put "last"
    end ensure
    put "two"
    """
    assert out(src) == "one\ntwo\nlast"


def test_a_block_that_is_never_reached_never_runs():
    src = """
    if false then
        ensure
            put "never"
        end ensure
    end if
    put "done"
    """
    assert out(src) == "done"


def test_blocks_run_in_reverse_order():
    """Cleanup unwinds in the reverse of acquisition, like nested resources."""
    src = """
    ensure
        put "release outer"
    end ensure
    ensure
        put "release inner"
    end ensure
    put "body"
    """
    assert out(src) == "body\nrelease inner\nrelease outer"


def test_registering_in_a_loop_registers_each_time():
    src = """
    repeat 3 times
        ensure
            put "tidy"
        end ensure
    end repeat
    put "body"
    """
    assert out(src) == "body\ntidy\ntidy\ntidy"


# ------------------------------------------------------------- on the way out

def test_cleanup_runs_when_the_script_fails():
    text, error = run_failing("""
    ensure
        put "cleaned"
    end ensure
    put nothing here
    """)
    assert "cleaned" in text
    assert "no variable named" in error.msg


def test_cleanup_runs_on_quit():
    src = """
    ensure
        put "cleaned"
    end ensure
    quit with status 3
    put "unreachable"
    """
    text, status = run(src)
    assert status == 3
    assert text.strip() == "cleaned"


def test_cleanup_runs_on_quit_with_zero():
    src = """
    ensure
        put "cleaned"
    end ensure
    quit with status 0
    """
    assert run(src) == ("cleaned\n", 0)


def test_the_original_error_survives_cleanup():
    """Cleanup must not become the error the reader sees."""
    src = """
    ensure
        put "cleaned"
    end ensure
    put missing variable
    """
    with pytest.raises(FrostError) as e:
        run(src)
    assert "no variable named" in e.value.msg


def test_a_failing_cleanup_does_not_mask_the_original_error():
    src = """
    ensure
        put also missing
    end ensure
    put missing variable
    """
    with pytest.raises(FrostError) as e:
        run(src)
    assert "missing variable" in e.value.msg


def test_a_failing_cleanup_does_not_stop_the_others():
    src = """
    ensure
        put "second"
    end ensure
    ensure
        put broken thing
    end ensure
    put "body"
    """
    assert out(src) == "body\nsecond"


def test_a_failing_cleanup_is_reported_on_standard_error():
    src = """
    ensure
        put broken thing
    end ensure
    put "body"
    """
    assert "cleanup failed" in err(src)


def test_quit_inside_a_cleanup_does_not_change_the_status():
    src = """
    ensure
        quit with status 9
    end ensure
    quit with status 2
    """
    assert run(src)[1] == 2


def test_cleanup_runs_only_once():
    src = """
    ensure
        put "once"
    end ensure
    put "body"
    """
    assert out(src) == "body\nonce"


# ---------------------------------------------------------- the real use

def test_the_motivating_case_a_lock_file_is_released_on_failure(tmp_path):
    lock = tmp_path / "run.lock"
    src = f"""
    put "held" into file "{lock}"
    ensure
        delete file "{lock}"
    end ensure
    put nothing here
    """
    with pytest.raises(FrostError):
        run(src)
    assert not lock.exists(), "the lock file outlived the failed script"


def test_a_temporary_file_is_removed_on_success(tmp_path):
    scratch = tmp_path / "scratch.txt"
    src = f"""
    put "work" into file "{scratch}"
    ensure
        delete file "{scratch}"
    end ensure
    put the number of lines in file "{scratch}"
    """
    assert out(src) == "1"
    assert not scratch.exists()


def test_cleanup_sees_variables_from_the_body(tmp_path):
    lock = tmp_path / "held.lock"
    src = f"""
    put "{lock}" into lock path
    put "x" into file (lock path)
    ensure
        delete file (lock path)
    end ensure
    """
    run(src)
    assert not lock.exists()


def test_cleanup_sees_a_value_assigned_after_registration(tmp_path):
    """The block is a closure over the script's state, not a snapshot."""
    src = """
    put "before" into stage
    ensure
        put stage
    end ensure
    put "after" into stage
    """
    assert out(src) == "after"


# --------------------------------------------------------------- parsing

def test_an_empty_ensure_block_is_rejected():
    with pytest.raises(ParseError) as e:
        parse("ensure\nend ensure")
    assert "cleans nothing up" in e.value.msg


def test_an_unclosed_ensure_block_is_rejected():
    with pytest.raises(ParseError):
        parse('ensure\n    put "x"')


def test_ensure_cannot_be_used_as_a_name():
    with pytest.raises(ParseError):
        parse('put 1 into ensure count')


def test_ensure_nests_inside_a_handler():
    src = """
    to work
        ensure
            put "handler cleanup"
        end ensure
        put "handler body"
    end work
    work
    put "main"
    """
    assert out(src) == "handler body\nmain\nhandler cleanup"


def test_loop_control_cannot_escape_through_an_ensure_block():
    with pytest.raises(ParseError):
        parse("ensure\n    exit repeat\nend ensure")


# ------------------------------------------------------------- the audit

def test_the_manifest_sees_inside_a_cleanup_block():
    """A delete hidden in cleanup is still a delete."""
    caps = caps_for("""
    ensure
        delete file "/tmp/scratch"
    end ensure
    put "x"
    """)
    assert ("/tmp/scratch", 3) in caps.deletes


def test_the_manifest_records_that_a_script_cleans_up():
    caps = caps_for('ensure\n    put "x"\nend ensure')
    assert caps.cleanups == [1]
