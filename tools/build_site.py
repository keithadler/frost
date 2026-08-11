#!/usr/bin/env python3
"""Assemble the published site.

The generated pages live in the repository, which is the right place for them
and the wrong place to read them: GitHub renders a committed `.html` file as
source code, not as a page, so every link to `play.html` in the README showed
somebody a wall of markup instead of the scratchpad. That is a bad first
impression from a project whose entire pitch is legibility.

So the same files are published to Pages, with a landing page in front of
them. Nothing here is authored twice; this copies what the other builders
produced and writes an index that links to it.
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang import __version__

OUT = os.path.join(HERE, "site")

PAGES = [
    ("play.html", "Scratchpad",
     "Chunk expressions live, and below that the real interpreter compiled to "
     "WebAssembly: manifests, policy refusals and approvals, answering exactly "
     "as they would in a terminal."),
    ("docs.html", "Reference",
     "The whole language, browsable, generated from LANGUAGE.md."),
    ("audit.html", "Audit report",
     "Four scripts read by the analyser: a fake dotfile backup that steals "
     "keys and a cleanup script that quietly does four dangerous things, both "
     "refused, beside two that pass."),
]

INDEX = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>frost, a shell scripting language you can check before you run it</title>
<style>
:root{{--ink:#1a1d24;--muted:#5c6b7f;--rule:#e2e8f0;--bg:#fff;--accent:#1d3557}}
@media (prefers-color-scheme:dark){{
  :root{{--ink:#dbe3f0;--muted:#8496ad;--rule:#1e2738;--bg:#0b0e14;--accent:#7cc4ff}}
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:17px/1.65 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}}
.wrap{{max-width:44rem;margin:0 auto;padding:4rem 1.5rem 5rem}}
h1{{font-size:2.6rem;letter-spacing:-.03em;margin:0 0 .4rem}}
.lede{{font-size:1.2rem;color:var(--muted);margin:0 0 2.5rem}}
a{{color:var(--accent)}}
.card{{display:block;border:1px solid var(--rule);border-radius:12px;
  padding:1.2rem 1.4rem;margin:0 0 1rem;text-decoration:none;color:inherit}}
.card:hover{{border-color:var(--accent)}}
.card h2{{margin:0 0 .3rem;font-size:1.15rem;color:var(--accent)}}
.card p{{margin:0;color:var(--muted);font-size:.97rem}}
pre{{background:#05070b;color:#dbe3f0;padding:1rem 1.2rem;border-radius:10px;
  overflow-x:auto;font-family:ui-monospace,Menlo,monospace;font-size:.86rem;
  line-height:1.6}}
footer{{margin-top:3rem;padding-top:1.4rem;border-top:1px solid var(--rule);
  color:var(--muted);font-size:.92rem}}
</style></head><body><div class="wrap">

<h1>frost</h1>
<p class="lede">A shell scripting language for when machines write the scripts
and humans only get to review them. Readable by default, structurally immune
to injection, and auditable before a single process starts.</p>

<pre>put the number of lines in file "access.log" into request count
put "processing" &amp;&amp; request count &amp;&amp; "requests"

run "curl" with "-fsS", url within 30 seconds
put the json of it into build
if the "status" of build is not "green" then quit with status 1</pre>

{cards}

<footer>
  <p>Version {version}. <a href="https://github.com/keithadler/frost">Source
  on GitHub</a>, MIT licensed.</p>
  <p>Named for Robert Frost, born in San Francisco in 1874, whose poems can be
  read on first pass and not exhausted on the tenth. Plain surface, real weight
  underneath. That is the trick a script written by a machine and reviewed by a
  person at 3am has to pull off.</p>
</footer>
</div></body></html>
"""


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    cards = []
    for filename, title, blurb in PAGES:
        source = os.path.join(HERE, filename)
        if not os.path.exists(source):
            raise SystemExit(
                f"{filename} has not been built; run its builder first")
        shutil.copy(source, os.path.join(OUT, filename))
        cards.append(f'<a class="card" href="{filename}">'
                     f"<h2>{title}</h2><p>{blurb}</p></a>")

    with open(os.path.join(OUT, "index.html"), "w") as fh:
        fh.write(INDEX.format(cards="\n".join(cards), version=__version__))

    # Without this, Pages runs the files through Jekyll, which will not serve
    # anything it decides looks like a template.
    open(os.path.join(OUT, ".nojekyll"), "w").close()

    print(f"wrote {OUT} ({len(PAGES) + 1} pages)")


if __name__ == "__main__":
    main()
