"""The capability manifest: what --explain can see without running."""

from frostlang.audit import audit, describe

from helpers import caps_for


def test_manifest_lists_programs():
    c = caps_for('run "git" with "status"\nrun "make"')
    assert sorted(x.program for x in c.commands) == ["git", "make"]


def test_manifest_sees_pipe_stages():
    c = caps_for('''
    pipe
        run "cat" with "a.txt"
        run "wc" with "-l"
    end pipe
    ''')
    assert sorted(x.program for x in c.commands) == ["cat", "wc"]
    assert all(x.in_pipe for x in c.commands)


def test_manifest_sees_inside_loops_and_handlers():
    c = caps_for('''
    to helper
        run "hostname"
    end helper

    repeat 3 times
        run "uptime"
    end repeat
    ''')
    assert sorted(x.program for x in c.commands) == ["hostname", "uptime"]


def test_manifest_records_file_access():
    c = caps_for('''
    put file "in.txt" into data
    put data into file "out.txt"
    delete file "old.txt"
    ''')
    assert ("in.txt", 2) in c.reads
    assert ("out.txt", 3) in c.writes
    assert ("old.txt", 4) in c.deletes


def test_exists_check_is_not_double_counted():
    c = caps_for('if file "x.txt" exists then put "yes"')
    assert len(c.reads) == 1


def test_runtime_built_names_are_flagged_not_guessed():
    c = caps_for('''
    put item 1 of the arguments into program
    run program with "--help"
    ''')
    assert c.commands[0].program is None
    assert c.dynamic == 1


def test_concatenated_literals_are_resolved():
    c = caps_for('put "x" into file "logs/" & "run.txt"')
    assert c.writes[0][0] == "logs/run.txt"


def test_describe_is_readable():
    text = describe(caps_for('run "git" with "push"'))
    assert "Runs these programs" in text
    assert "git" in text
