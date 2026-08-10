"""Reading, writing, appending and deleting files."""

import pytest

from frostlang.interp import FrostError

from helpers import out, run


def test_write_and_read_file(tmp_path):
    p = tmp_path / "out.txt"
    src = f'''
    put "hello" into file "{p}"
    put file "{p}"
    '''
    assert out(src) == "hello"


def test_append_to_file(tmp_path):
    p = tmp_path / "log.txt"
    src = f'''
    put "one" into file "{p}"
    put "two" after file "{p}"
    put the number of lines in file "{p}"
    '''
    assert out(src) == "2"


def test_file_exists(tmp_path):
    p = tmp_path / "here.txt"
    p.write_text("x")
    assert out(f'put file "{p}" exists') == "true"
    assert out(f'put file "{tmp_path}/nope.txt" exists') == "false"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FrostError) as e:
        run(f'put file "{tmp_path}/nope.txt"')
    assert "no file at" in e.value.msg


def test_delete_file(tmp_path):
    p = tmp_path / "gone.txt"
    p.write_text("x")
    run(f'delete file "{p}"')
    assert not p.exists()
