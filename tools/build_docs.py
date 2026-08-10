#!/usr/bin/env python3
"""Render README.md and LANGUAGE.md into one browsable HTML page.

Keyword lists are pulled from the parser itself, so the syntax highlighting in
the docs cannot drift away from the language.
"""

import json
import os
import re
import sys

import markdown

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang.parser import HARD_WORDS, CHUNK_SINGULAR, CHUNK_PLURAL, ORDINALS

OUT = os.path.join(HERE, "docs.html")

KEYWORDS = sorted(HARD_WORDS)
NOUNS = sorted(set(CHUNK_SINGULAR) | set(CHUNK_PLURAL)
               | set(ORDINALS) | {"last", "middle", "any", "result",
                                  "arguments", "output", "error", "status",
                                  "current", "folder", "environment",
                                  "variable", "length", "number", "second",
                                  "seconds", "minute", "minutes",
                                  "millisecond", "milliseconds", "ms",
                                  "hour", "hours"})


def slug(text):
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


def convert(path):
    with open(os.path.join(HERE, path)) as fh:
        text = fh.read()
    # Drop the top H1 and the hand-written contents list; the sidebar replaces it.
    text = re.sub(r"\A#\s+.*?\n", "", text, count=1)
    text = re.sub(r"##\s+Contents\n(.*?)(?=\n---)", "", text, flags=re.S)
    md = markdown.Markdown(extensions=["fenced_code", "tables", "attr_list"])
    return md.convert(text)


def add_anchors(html, prefix):
    """Give every h2/h3 a stable id and collect the nav entries."""
    entries = []

    def repl(m):
        level, inner = m.group(1), m.group(2)
        label = re.sub(r"<.*?>", "", inner)
        anchor = f"{prefix}-{slug(label)}"
        if level == "2":
            entries.append({"id": anchor, "label": label})
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    html = re.sub(r"<h([23])>(.*?)</h\1>", repl, html, flags=re.S)
    return html, entries


def main():
    readme, readme_nav = add_anchors(convert("README.md"), "guide")
    reference, reference_nav = add_anchors(convert("LANGUAGE.md"), "ref")

    with open(os.path.join(HERE, "frostlang", "__init__.py")) as fh:
        version = re.search(r'__version__ = "([^"]+)"', fh.read()).group(1)

    page = TEMPLATE.format(
        version=version,
        readme=readme,
        reference=reference,
        nav=build_nav(readme_nav, reference_nav),
        keywords=json.dumps(KEYWORDS),
        nouns=json.dumps(NOUNS),
    )
    with open(OUT, "w") as fh:
        fh.write(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")


def build_nav(guide, ref):
    def group(title, entries):
        items = "".join(
            f'<li><a href="#{e["id"]}">{e["label"]}</a></li>' for e in entries)
        return f'<p class="nav-title">{title}</p><ul>{items}</ul>'
    return group("Guide", guide) + group("Reference", ref)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>frost {version} — language documentation</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --paper:#EAF0F4;
  --card:#FFFFFF;
  --ink:#0D1418;
  --muted:#5B6C77;
  --frost:#14657F;
  --frost-deep:#0B3F52;
  --ice:#CBDFE9;
  --ice-soft:#E2EDF3;
  --ember:#A6401B;
  --rule:#BFD3DE;
  --display:"Space Grotesk",system-ui,sans-serif;
  --body:"Newsreader",Georgia,serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}}

*{{box-sizing:border-box}}
html{{scroll-behavior:smooth;scroll-padding-top:1.5rem}}
body{{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:var(--body);font-size:17.5px;line-height:1.65;
  -webkit-font-smoothing:antialiased;
}}

/* ---------------------------------------------------------- structure */
.shell{{display:grid;grid-template-columns:255px minmax(0,1fr);gap:0;
  max-width:1240px;margin:0 auto;}}

aside{{
  position:sticky;top:0;height:100vh;overflow-y:auto;
  padding:2.2rem 1.4rem 3rem 1.6rem;
  border-right:1px solid var(--rule);
}}
.brand{{font-family:var(--display);font-weight:700;font-size:1.45rem;
  letter-spacing:-.03em;line-height:1;margin:0 0 .1rem;}}
.brand span{{color:var(--frost)}}
.brand-sub{{font-family:var(--mono);font-size:.68rem;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin:0 0 1.8rem}}
.nav-title{{font-family:var(--mono);font-size:.66rem;letter-spacing:.15em;
  text-transform:uppercase;color:var(--frost);margin:1.5rem 0 .5rem;
  padding-bottom:.35rem;border-bottom:1px solid var(--rule);}}
aside ul{{list-style:none;margin:0;padding:0}}
aside li a{{
  display:block;padding:.2rem 0;color:var(--muted);text-decoration:none;
  font-family:var(--display);font-size:.83rem;font-weight:500;
  line-height:1.35;border-left:2px solid transparent;padding-left:.65rem;
  margin-left:-.65rem;transition:color .15s,border-color .15s;
}}
aside li a:hover{{color:var(--ink)}}
aside li a.here{{color:var(--frost-deep);border-left-color:var(--frost)}}

main{{padding:2.2rem 3.2rem 8rem;min-width:0}}
.doc{{max-width:74ch}}

/* ------------------------------------------------------------- hero */
.hero{{margin:0 0 3.5rem}}
.eyebrow{{font-family:var(--mono);font-size:.7rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--frost);margin:0 0 .9rem}}
.hero h1{{font-family:var(--display);font-weight:700;font-size:clamp(2.1rem,5vw,3.1rem);
  letter-spacing:-.035em;line-height:1.02;margin:0 0 .8rem}}
.hero .lede{{font-size:1.18rem;color:var(--muted);margin:0 0 2.2rem;max-width:52ch}}

/* signature: the nested chunk expression, decomposed */
.thesis{{
  background:var(--card);border:1px solid var(--rule);
  border-radius:3px;padding:1.7rem 1.6rem 1.4rem;
}}
.thesis-label{{font-family:var(--mono);font-size:.66rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted);margin:0 0 1.1rem}}
.expr{{font-family:var(--mono);font-size:clamp(.85rem,2.1vw,1.05rem);
  line-height:2.5;white-space:nowrap;overflow-x:auto;padding-bottom:.3rem}}
.layer{{
  padding:.3em .45em;border-radius:2px;cursor:default;
  transition:background .18s ease, color .18s ease;
  border-bottom:2px solid transparent;
}}
.layer[data-on="1"]{{background:var(--ice-soft);border-bottom-color:var(--ice)}}
.layer[data-on="2"]{{background:var(--ice);border-bottom-color:#9FC3D4}}
.layer[data-on="3"]{{background:var(--frost);color:#fff;border-bottom-color:var(--frost-deep)}}
.sample{{
  margin-top:1.2rem;padding-top:1.1rem;border-top:1px solid var(--rule);
  font-family:var(--mono);font-size:.76rem;line-height:1.95;color:var(--muted);
  white-space:pre;overflow-x:auto;
}}
.sample b{{font-weight:400;color:var(--muted)}}
.sample .row.lit{{color:var(--ink)}}
.sample .row.lit .hit{{background:var(--frost);color:#fff;padding:.1em .25em;border-radius:2px}}
.thesis-note{{margin:1.1rem 0 0;font-size:.9rem;color:var(--muted)}}
.thesis-note code{{background:none;border:none;padding:0;color:var(--ember);font-size:.85rem}}

/* ---------------------------------------------------------- typography */
main h2{{
  font-family:var(--display);font-weight:700;font-size:1.62rem;
  letter-spacing:-.022em;line-height:1.15;
  margin:3.6rem 0 1rem;padding-top:1.5rem;border-top:1px solid var(--rule);
}}
main h3{{font-family:var(--display);font-weight:500;font-size:1.13rem;
  letter-spacing:-.01em;margin:2.2rem 0 .6rem}}
main p{{margin:0 0 1.05rem}}
main strong{{font-weight:600}}
main ul,main ol{{margin:0 0 1.15rem;padding-left:1.25rem}}
main li{{margin-bottom:.42rem}}
main hr{{display:none}}
a{{color:var(--frost);text-underline-offset:3px}}

/* inline code */
main p code,main li code,main td code{{
  font-family:var(--mono);font-size:.83em;background:var(--ice-soft);
  border:1px solid var(--ice);border-radius:2px;padding:.08em .34em;
  color:var(--frost-deep);
}}

/* code blocks */
pre{{
  background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--frost);
  border-radius:2px;padding:1.05rem 1.15rem;margin:0 0 1.35rem;
  overflow-x:auto;font-size:.83rem;line-height:1.72;
}}
pre code{{font-family:var(--mono);background:none;border:none;padding:0;color:var(--ink)}}
pre.shell{{border-left-color:var(--muted);background:#0D1418;color:#C6D6DE}}
pre.shell code{{color:#C6D6DE}}
pre.diagnostic{{border-left-color:var(--ember);background:#FBF2EE}}
pre.grammar{{border-left-color:var(--ice);font-size:.78rem}}
pre.transcript{{border-left-color:var(--muted);background:#F5F8FA;color:#33454F}}
pre.transcript code{{color:#33454F}}
pre.policy{{border-left-color:#8A6212;background:#FAF6EC}}

/* frost syntax colours */
.tk-kw{{color:var(--frost);font-weight:600}}
.tk-noun{{color:var(--frost-deep)}}
.tk-str{{color:var(--ember)}}
.tk-num{{color:#6B3FA0}}
.tk-com{{color:#8A9AA5;font-style:italic}}

/* tables */
table{{border-collapse:collapse;width:100%;margin:0 0 1.4rem;font-size:.92rem}}
th,td{{text-align:left;padding:.5rem .7rem;border-bottom:1px solid var(--rule);
  vertical-align:top}}
th{{font-family:var(--display);font-weight:500;font-size:.78rem;
  letter-spacing:.05em;text-transform:uppercase;color:var(--frost);
  border-bottom:1px solid var(--frost)}}
tbody tr:last-child td{{border-bottom:none}}

/* ------------------------------------------------------------ mobile */
.menu-btn{{display:none}}
@media (max-width:860px){{
  .shell{{grid-template-columns:1fr}}
  aside{{position:static;height:auto;border-right:none;
    border-bottom:1px solid var(--rule);padding:1.4rem 1.4rem 1.8rem}}
  aside .nav-body{{display:none}}
  aside.open .nav-body{{display:block}}
  .menu-btn{{display:block;font-family:var(--mono);font-size:.72rem;
    letter-spacing:.12em;text-transform:uppercase;background:none;
    border:1px solid var(--rule);border-radius:2px;padding:.4rem .7rem;
    color:var(--frost);cursor:pointer}}
  main{{padding:1.8rem 1.3rem 5rem}}
  .expr{{white-space:normal;line-height:2.7}}
}}

@media (prefers-reduced-motion:reduce){{
  *{{transition:none!important;animation:none!important}}
  html{{scroll-behavior:auto}}
}}

.layer{{animation:settle .5s ease both}}
@keyframes settle{{from{{opacity:0;transform:translateY(3px)}}to{{opacity:1;transform:none}}}}
</style>
</head>
<body>
<div class="shell">

<aside id="side">
  <p class="brand">fr<span>o</span>st</p>
  <p class="brand-sub">v{version} · language docs</p>
  <button class="menu-btn" id="menu">Contents</button>
  <div class="nav-body">{nav}</div>
</aside>

<main>
  <header class="hero">
    <p class="eyebrow">A scripting language for readable shell scripts</p>
    <h1>Scripts are written once<br>and read at 3am.</h1>
    <p class="lede">A shell scripting language for the era when machines write
    the scripts and humans only get to review them — readable by default,
    structurally immune to injection, and auditable before a single process
    starts.</p>
    <p class="lede" style="font-size:1rem"><a href="audit.html">See a live audit report &rarr;</a></p>

    <div class="thesis">
      <p class="thesis-label">Chunk expressions — hover a layer</p>
      <div class="expr" id="expr">
        <span class="layer" data-depth="1">the third word of<span class="layer" data-depth="2"> line 7 of<span class="layer" data-depth="3"> file "access.log"</span></span></span>
      </div>
      <div class="sample" id="sample"></div>
      <p class="thesis-note">In bash: <code>sed -n '7p' access.log | awk '{{print $3}}'</code> — two tool dialects instead of one grammar.</p>
    </div>
  </header>

  <div class="doc">{readme}</div>
  <div class="doc">{reference}</div>
</main>
</div>

<script>
const KEYWORDS = new Set({keywords});
const NOUNS = new Set({nouns});

/* ---- frost syntax highlighting ------------------------------------- */
const NOT_FROST = /^\s*(\$|#\s|Error at|Syntax error at|frost\s+\S+\.frost|frost\s{{2,}})/;

function highlight(code) {{
  const out = [];
  let i = 0;
  while (i < code.length) {{
    const rest = code.slice(i);

    let m = rest.match(/^(--|#)[^\n]*/);
    if (m) {{ out.push(['com', m[0]]); i += m[0].length; continue; }}

    m = rest.match(/^"(\\.|[^"\\])*"?/);
    if (m) {{ out.push(['str', m[0]]); i += m[0].length; continue; }}

    m = rest.match(/^\d+(\.\d+)?/);
    if (m) {{ out.push(['num', m[0]]); i += m[0].length; continue; }}

    m = rest.match(/^[A-Za-z_][A-Za-z0-9_]*/);
    if (m) {{
      const w = m[0].toLowerCase();
      out.push([KEYWORDS.has(w) ? 'kw' : (NOUNS.has(w) ? 'noun' : null), m[0]]);
      i += m[0].length; continue;
    }}
    out.push([null, code[i]]); i += 1;
  }}
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  return out.map(([c, t]) => c ? `<span class="tk-${{c}}">${{esc(t)}}</span>` : esc(t)).join('');
}}

document.querySelectorAll('pre > code').forEach(code => {{
  const pre = code.parentElement;
  const cls = code.className || '';
  const text = code.textContent;

  if (/language-(bash|sh)/.test(cls)) {{ pre.classList.add('shell'); return; }}
  if (/language-text/.test(cls)) {{ pre.classList.add('transcript'); return; }}
  if (/language-policy/.test(cls)) {{ pre.classList.add('policy'); return; }}
  if (/language-ebnf/.test(cls)) {{ pre.classList.add('grammar'); return; }}
  if (/^(Error at|Syntax error at|REFUSED|\s*\[DANGER)/m.test(text) || /Verdict: dangerous/.test(text)) {{
    pre.classList.add('diagnostic'); return;
  }}
  if (NOT_FROST.test(text)) {{ pre.classList.add('shell'); return; }}

  code.innerHTML = highlight(text);
}});

/* ---- the hero demo -------------------------------------------------- */
const LOG = [
  '10.0.0.1  GET   /index.html   200',
  '10.0.0.2  POST  /api/login    500',
  '10.0.0.3  GET   /about.html   200',
  '10.0.0.2  POST  /api/login    500',
  '10.0.0.9  GET   /missing      404',
  '10.0.0.4  GET   /index.html   200',
  '10.0.0.2  POST  /api/pay      500'
];

const sample = document.getElementById('sample');

function paint(depth) {{
  sample.innerHTML = LOG.map((line, idx) => {{
    const n = idx + 1;
    const lit = depth >= 2 && n === 7;
    if (!lit) return `<div class="row"><b>${{String(n).padStart(2)}} </b>${{line}}</div>`;
    if (depth < 3 && depth >= 2) {{
      return `<div class="row lit"><b>${{String(n).padStart(2)}} </b>${{line}}</div>`;
    }}
    const parts = line.split(/(\s+)/);
    let wordNo = 0;
    const marked = parts.map(p => {{
      if (/^\s+$/.test(p)) return p;
      wordNo += 1;
      return wordNo === 3 ? `<span class="hit">${{p}}</span>` : p;
    }}).join('');
    return `<div class="row lit"><b>${{String(n).padStart(2)}} </b>${{marked}}</div>`;
  }}).join('');
}}

/* Depth 3 = file, 2 = line 7, 1 = the third word of that line. */
function setDepth(d) {{
  document.querySelectorAll('.layer').forEach(el => {{
    const own = Number(el.dataset.depth);
    el.dataset.on = (d && own >= d) ? String(4 - own) : '';
  }});
  paint(d ? (4 - d) : 0);
}}

document.querySelectorAll('.layer').forEach(el => {{
  el.addEventListener('mouseenter', e => {{
    e.stopPropagation();
    setDepth(Number(el.dataset.depth));
  }});
}});
document.getElementById('expr').addEventListener('mouseleave', () => setDepth(0));
paint(3);
setTimeout(() => setDepth(0), 2200);

/* ---- nav ------------------------------------------------------------ */
const links = [...document.querySelectorAll('aside a')];
const targets = links.map(a => document.getElementById(a.hash.slice(1))).filter(Boolean);
const spy = new IntersectionObserver(entries => {{
  entries.forEach(en => {{
    if (!en.isIntersecting) return;
    links.forEach(a => a.classList.toggle('here', a.hash === '#' + en.target.id));
  }});
}}, {{rootMargin: '0px 0px -78% 0px'}});
targets.forEach(t => spy.observe(t));

const side = document.getElementById('side');
document.getElementById('menu').addEventListener('click', () => side.classList.toggle('open'));
links.forEach(a => a.addEventListener('click', () => side.classList.remove('open')));
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
