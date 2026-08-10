"""The browser panel has to be frost, not a copy of frost that used to be.

`play.html` carries an embedded snapshot of `frostlang/*.py`, because a page
cannot import from disk. That snapshot is the whole risk. Everything else
about running real CPython in the browser is sound precisely because there is
no second implementation, and an embedded copy quietly going stale would
reintroduce exactly the problem it was meant to remove: a page confidently
answering with last month's rules.

So two guarantees here, both checkable without a browser.

**Freshness.** Every embedded source is byte-identical to the file on disk.

**Answers.** For every sample the page ships, the answer computed in this
process is recorded as a golden file. The page runs the same functions over
the same bytes, so if the embedding is fresh the page's answers are these. A
headless browser then only has to agree with the goldens, which is what the
CI canary does.
"""

import json
import os
import re

import pytest

from frostlang import browser
from frostlang.audit import parse_policy

from helpers import REPO

PLAY = os.path.join(REPO, "play.html")
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def embedded():
    """The JSON blobs the builder wrote into the page."""
    with open(PLAY) as fh:
        page = fh.read()
    sources = re.search(r"var SRC = (\{.*?\}), SAMPLES = (\[.*?\]), "
                        r"POLICY = (\".*?\");", page, re.S)
    assert sources, "the page no longer carries its embedded payload"
    return (json.loads(sources.group(1)), json.loads(sources.group(2)),
            json.loads(sources.group(3)))


SRC, SAMPLES, POLICY = embedded()


# ------------------------------------------------------------- freshness

def test_the_page_carries_every_module_it_needs():
    from tools.build_play import BROWSER_MODULES
    assert set(SRC) == {m + ".py" for m in BROWSER_MODULES}


@pytest.mark.parametrize("name", sorted(SRC))
def test_each_embedded_module_matches_the_file_on_disk(name):
    """A stale copy would have the page answering with rules that no longer
    exist anywhere else, which is worse than having no page."""
    with open(os.path.join(REPO, "frostlang", name)) as fh:
        assert SRC[name] == fh.read(), (
            f"play.html carries a stale {name}; run tools/build_play.py")


def test_the_embedded_policy_is_the_shipped_one():
    with open(os.path.join(REPO, "examples", "production.policy")) as fh:
        assert POLICY == fh.read()
    assert parse_policy(POLICY), "the page ships a policy that does not parse"


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s["file"])
def test_each_sample_matches_its_example_file(sample):
    """Samples are read off disk so the demo cannot drift from the scripts the
    suite already runs, formats and audits."""
    path = os.path.join(REPO, "examples", sample["file"])
    if not os.path.exists(path):
        pytest.skip(f"{sample['file']} is written for the page, not shipped")
    with open(path) as fh:
        on_disk = fh.read()
    assert sample["source"].startswith(on_disk.rstrip()[:200])


def test_every_sample_says_what_to_try():
    """A demo without guidance is a toy. Each sample has to name the button
    worth pressing and what it will show."""
    for sample in SAMPLES:
        assert sample.get("try"), f"{sample['title']} has no guidance"
        assert len(sample["try"]) > 40


def test_the_pyodide_version_is_pinned():
    """An unpinned runtime means the page can break without a commit."""
    from tools.build_play import PYODIDE
    assert re.search(r"/v\d+\.\d+\.\d+/", PYODIDE), PYODIDE
    with open(PLAY) as fh:
        assert PYODIDE in fh.read()


# --------------------------------------------------------------- the oracle

ACTIONS = ["check", "explain", "policy", "approve", "diagnose"]


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def golden_name(sample, action):
    # Keyed on the sample's title, not its file: two samples share
    # apicheck.frost (the clean one and its poisoned regeneration), and keying
    # on the filename silently had one overwrite the other's answers.
    return f"play.{slug(sample['title'])}.{action}.txt"


def check_golden(name, actual):
    path = os.path.join(GOLDEN, name)
    if os.environ.get("FROST_UPDATE_GOLDEN"):
        with open(path, "w") as fh:
            fh.write(actual if actual.endswith("\n") else actual + "\n")
        return
    assert os.path.exists(path), (
        f"no golden file for {name}; regenerate with FROST_UPDATE_GOLDEN=1")
    with open(path) as fh:
        assert fh.read().rstrip("\n") == actual.rstrip("\n"), name


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s["file"])
def test_the_answer_for_every_sample_is_recorded(sample, action):
    """The oracle a headless browser is measured against.

    Recorded here rather than asserted inline because the value of these is
    that they are *identical* to what the page prints, not that any one of
    them reads a particular way.
    """
    extra = POLICY if action == "policy" else ""
    check_golden(golden_name(sample, action),
                 browser.run(action, sample["source"], extra))


def test_the_poisoned_sample_is_refused_against_its_own_approval():
    """The demo that carries the argument, pinned so it cannot quietly stop
    refusing."""
    poisoned = [s for s in SAMPLES if s.get("approved")]
    assert poisoned, "the page no longer ships a before-and-after pair"
    answer = browser.compare(poisoned[0]["source"], poisoned[0]["approved"])
    assert "REFUSED: it can now reach telemetry.example" in answer
    assert "it was not run" in answer


def test_the_broken_sample_teaches_both_halves_of_repair():
    """The sample is built to show the line frost draws, not just that repair
    works. High-confidence mistakes are fixed; the missing timeout unit is
    left, because `seconds` is a guess and a wrong repair costs an agent a
    whole round trip. A demo where everything is fixed teaches that frost
    guesses, which is the opposite of true."""
    broken = [s for s in SAMPLES if s["file"] == "broken.frost"]
    assert broken, "the page no longer ships a broken script"
    source = broken[0]["source"]
    assert "Syntax error" in browser.run("check", source)

    payload = json.loads(browser.run("repair", source))
    assert "repair(s) applied" in payload["note"]

    left = browser.run("check", payload["source"])
    assert "a timeout needs a unit" in left, (
        "the sample no longer demonstrates a repair frost declines to apply")

    diagnosed = browser.run("diagnose", payload["source"])
    assert "repair (likely)" in diagnosed, (
        "the leftover has no repair to show, so the demo just looks broken")


def test_the_guidance_explains_the_leftover():
    """Ending on an unexplained error would read as the tool failing."""
    broken = [s for s in SAMPLES if s["file"] == "broken.frost"][0]
    assert "likely" in broken["try"]
    assert "timeout" in broken["try"]
