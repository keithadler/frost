#!/usr/bin/env python3
"""Boot play.html in a real browser and make it agree with the goldens.

Everything else about the WebAssembly panel is checked without a browser:
`tests/test_playground.py` proves the embedded Python is byte-identical to the
files on disk, and records what each sample answers in this process. Those
tests are fast and they run everywhere.

What they cannot see is whether the page *works*. Pyodide is fetched from a
CDN at a pinned version, the page writes modules into an emscripten
filesystem, and the JavaScript marshals arguments across a bridge. Any of that
can break with no commit to this repository: a CDN can drop a version, a
browser can change a rule about workers, a module I embed can grow an import
that CPython-on-WASM does not ship.

So this is the canary. It runs the real page in real Chromium, presses the
real buttons, and compares what appears against the answers recorded in
process. If the page and the goldens disagree, either the embedding is stale
or the browser path is broken, and both are worth failing a build over.

    python tools/canary_browser.py

Needs `playwright` and `playwright install chromium`. Skips with a clear
message rather than failing when they are absent, so a contributor without
them is not blocked; CI installs them and does not skip.
"""

import http.server
import json
import os
import re
import socketserver
import sys
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

GOLDEN = os.path.join(HERE, "tests", "golden")
BOOT_TIMEOUT_MS = 180_000        # a first-run Pyodide download is not quick

ACTIONS = ["check", "explain", "policy", "approve", "diagnose"]


def slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def samples_in_page():
    with open(os.path.join(HERE, "play.html")) as fh:
        page = fh.read()
    found = re.search(r"var SRC = \{.*?\}, SAMPLES = (\[.*?\]), POLICY = ",
                      page, re.S)
    if not found:
        raise SystemExit("play.html no longer carries its embedded samples")
    return json.loads(found.group(1))


def serve():
    """A local server, because file:// and workers do not mix."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def translate_path(self, path):
            return os.path.join(HERE, path.lstrip("/").split("?")[0] or
                                "play.html")

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; skipping the browser canary.")
        print("  pip install playwright && playwright install chromium")
        return 0

    samples = samples_in_page()
    httpd, port = serve()
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/play.html")

        page.click("#boot")
        page.wait_for_function(
            "() => document.getElementById('bootstate')"
            ".textContent.indexOf('running in this page') !== -1",
            timeout=BOOT_TIMEOUT_MS)
        version = page.evaluate(
            "() => document.getElementById('bootstate').textContent")
        print(f"booted:{version.strip()}")

        chips = page.query_selector_all("#samples button")
        if len(chips) != len(samples):
            raise SystemExit(f"page shows {len(chips)} samples, "
                             f"expected {len(samples)}")

        for index, sample in enumerate(samples):
            chips[index].click()
            for action in ACTIONS:
                page.click(f'.acts button[data-act="{action}"]')
                shown = page.evaluate(
                    "() => document.getElementById('rout').textContent")
                name = f"play.{slug(sample['title'])}.{action}.txt"
                with open(os.path.join(GOLDEN, name)) as fh:
                    expected = fh.read().rstrip("\n")
                if shown.rstrip("\n") != expected:
                    failures.append((name, expected, shown))
                    print(f"  MISMATCH {name}")
                else:
                    print(f"  ok       {name}")

        # The demo that carries the argument, driven end to end.
        poisoned = [i for i, s in enumerate(samples) if s.get("approved")]
        if poisoned:
            chips[poisoned[0]].click()
            page.click('.acts button[data-act="compare"]')
            shown = page.evaluate(
                "() => document.getElementById('rout').textContent")
            if "REFUSED: it can now reach telemetry.example" not in shown:
                failures.append(("compare", "a refusal naming the host", shown))
                print("  MISMATCH compare")
            else:
                print("  ok       compare")

        browser.close()
    httpd.shutdown()

    if errors:
        print("\nthe page raised:")
        for e in errors:
            print("  " + e)
    if failures:
        print(f"\n{len(failures)} disagreement(s) between the page and the "
              f"recorded answers:\n")
        for name, expected, shown in failures[:3]:
            print(f"--- {name}\nexpected:\n{expected[:500]}\n"
                  f"the page showed:\n{shown[:500]}\n")
        print("Either play.html is stale (run tools/build_play.py) or the "
              "browser path is broken.")
        return 1

    print("\nthe page agrees with every recorded answer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
