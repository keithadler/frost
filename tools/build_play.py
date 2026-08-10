#!/usr/bin/env python3
"""Build play.html — a live scratchpad for chunk expressions.

The evaluator is web/chunks.js, which tools/verify_chunks.py checks against the
Python implementation on every build. This script refuses to write the page if
that check has not passed.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang.parser import HARD_WORDS, CHUNK_SINGULAR, CHUNK_PLURAL, ORDINALS

OUT = os.path.join(HERE, "play.html")

SUBJECT = """10.0.0.1 GET /index.html 200 0.014
10.0.0.2 POST /api/login 500 1.221
10.0.0.3 GET /about.html 200 0.031
10.0.0.2 POST /api/login 500 0.998
10.0.0.9 GET /missing 404 0.008
10.0.0.4 GET /index.html 200 0.019
10.0.0.2 POST /api/pay 500 2.404"""

EXAMPLES = [
    ("Start here", [
        ("the first line of it", "A chunk by ordinal."),
        ("the third word of it", "Words split on whitespace, across the whole text."),
        ("the last line of it", "last, middle and any work like ordinals."),
    ]),
    ("Nesting", [
        ("the third word of line 2 of it",
         "Chunks compose. In bash this is sed piped into awk."),
        ("the last word of the first line of it", "Read it outside-in."),
        ("the first character of the fourth word of line 5 of it",
         "Three levels deep and still one grammar."),
    ]),
    ("Counting", [
        ("the number of lines in it", "No wc, no piping."),
        ("the number of words in line 2 of it", "Count inside a chunk."),
        ("the length of the first word of it", "Characters, not chunks."),
    ]),
    ("Ranges", [
        ("lines 2 to 4 of it", "Rejoined with the natural separator."),
        ("words 2 to 3 of the last line of it", "Ranges nest too."),
        ("characters 1 to 8 of the first line of it", "Substrings, readably."),
        ("word -1 of the first line of it", "Negative counts from the end."),
    ]),
    ("Patterns", [
        (r'every match of "\d+\.\d+\.\d+\.\d+" in it', "Every client address."),
        (r'the first line of it matches "^(\S+) (\w+) (\S+) (\d+)"',
         "True, and it records the capture groups."),
        ("match 2", "Group 2 of the match you just ran. Try match 4."),
        ("the whole match", "The entire matched text."),
        ('"report.tmp" is like "*.tmp"', "Globs, for when regex is overkill."),
    ]),
    ("Building text", [
        ('"client: " & the first word of it', "& joins."),
        ("the first word of it && the last word of it", "&& joins with a space."),
        ("the number of lines in it * 2", "Arithmetic works as expected."),
    ]),
    ("Lists", [
        ("the words of the first line of it",
         "A plural noun with no index is the whole set."),
        ('the lines of it joined by " | "', "Back to text, any separator."),
        ('the first line of it split by "."',
         "For delimiters the chunk nouns do not cover."),
        ("the number of items in the words of it", "Lists count like anything else."),
    ]),
    ("Sorting", [
        ("the sorted the words of the last line of it", "Leaves the original alone."),
        ('the unique (the words of "b a b c a") joined by ","',
         "Duplicates dropped, order kept."),
        ('the sorted (the words of "10 9 100 2") joined by ","',
         "Numeric when every value is a number."),
        ("the reversed the lines of it", "The other direction."),
    ]),
    ("Text and numbers", [
        ("the uppercase the third word of it", "Also lowercase and trimmed."),
        ('the trimmed "   ragged   "', "Surrounding whitespace only."),
        ('the sum of the words of "1 2 3 4"', "Also largest, smallest, average."),
        ("the rounded 2.6", "And the absolute of a negative."),
    ]),
]


def check_agreement():
    proc = subprocess.run([sys.executable,
                           os.path.join(HERE, "tools", "verify_chunks.py")],
                          capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("refusing to build play.html: the browser evaluator "
                         "disagrees with the interpreter")


def main():
    check_agreement()

    with open(os.path.join(HERE, "web", "chunks.js")) as fh:
        evaluator = fh.read()

    nouns = sorted(set(CHUNK_SINGULAR) | set(CHUNK_PLURAL) | set(ORDINALS) |
                   {"last", "middle", "any", "matches", "whole", "match",
                    "length", "number", "every"})

    page = (TEMPLATE
            .replace("__EVALUATOR__", evaluator)
            .replace("__SUBJECT__", json.dumps(SUBJECT))
            .replace("__EXAMPLES__", json.dumps(EXAMPLES))
            .replace("__KEYWORDS__", json.dumps(sorted(HARD_WORDS)))
            .replace("__NOUNS__", json.dumps(nouns)))
    with open(OUT, "w") as fh:
        fh.write(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>frost — chunk expression scratchpad</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EAF0F4; --card:#FFFFFF; --ink:#0D1418; --muted:#5B6C77;
  --frost:#14657F; --frost-deep:#0B3F52; --ice:#CBDFE9; --ice-soft:#E2EDF3;
  --rule:#BFD3DE; --ember:#A6401B; --ok:#1C6B4B; --ok-bg:#E7F2EC;
  --display:"Space Grotesk",system-ui,sans-serif;
  --body:"Newsreader",Georgia,serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--body);
  font-size:17px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:2.4rem 1.4rem 5rem}
.eyebrow{font-family:var(--mono);font-size:.7rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--frost);margin:0 0 .8rem}
h1{font-family:var(--display);font-weight:700;font-size:clamp(1.9rem,4.4vw,2.7rem);
  letter-spacing:-.035em;line-height:1.05;margin:0 0 .7rem}
.lede{font-size:1.1rem;color:var(--muted);max-width:60ch;margin:0 0 2rem}
.lede a{color:var(--frost)}

.bar{display:flex;gap:.6rem;align-items:stretch;margin-bottom:.5rem}
#expr{flex:1;font-family:var(--mono);font-size:1rem;padding:.85rem .9rem;
  border:1px solid var(--rule);border-left:3px solid var(--frost);
  border-radius:2px;background:var(--card);color:var(--ink)}
#expr:focus{outline:2px solid var(--frost);outline-offset:1px}
.result{background:var(--card);border:1px solid var(--rule);border-radius:2px;
  padding:.9rem 1rem;min-height:3.4rem;margin-bottom:1.6rem}
.result .val{font-family:var(--mono);font-size:1rem;white-space:pre-wrap;
  word-break:break-word;margin:0}
.result.ok{border-left:3px solid var(--ok);background:var(--ok-bg)}
.result.err{border-left:3px solid var(--ember);background:#FBEDE9}
.result .err-msg{font-family:var(--body);color:var(--ember);margin:0}
.result .hint{font-size:.9rem;color:var(--muted);margin:.35rem 0 0}
.result .meta{font-family:var(--mono);font-size:.68rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--muted);margin:0 0 .35rem}

.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.4rem;
  align-items:start}
.panel{background:var(--card);border:1px solid var(--rule);border-radius:2px;
  overflow:hidden}
.panel h2{font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--frost);margin:0;padding:.7rem .95rem;
  border-bottom:1px solid var(--rule);background:var(--ice-soft)}
.panel .body{padding:.9rem}
#subject{width:100%;font-family:var(--mono);font-size:.76rem;line-height:1.8;
  border:none;resize:vertical;min-height:200px;color:var(--ink);background:none}
#subject:focus{outline:none}
.gutter{font-family:var(--mono);font-size:.7rem;color:var(--muted);
  margin:.6rem 0 0;padding-top:.6rem;border-top:1px solid var(--rule)}

.group{margin-bottom:1.1rem}
.group:last-child{margin-bottom:0}
.group h3{font-family:var(--display);font-weight:500;font-size:.92rem;
  margin:0 0 .45rem;color:var(--frost-deep)}
.ex{display:block;width:100%;text-align:left;background:none;
  border:1px solid transparent;border-left:2px solid var(--ice);
  border-radius:2px;padding:.35rem .55rem;margin-bottom:.3rem;cursor:pointer;
  font-family:inherit;color:inherit}
.ex:hover{background:var(--ice-soft);border-left-color:var(--frost)}
.ex code{font-family:var(--mono);font-size:.76rem;color:var(--frost-deep);
  display:block}
/* The direct child only: the highlight spans live inside <code>, and a bare
   `.ex span` made every token in the example its own line. */
.ex > span{font-size:.82rem;color:var(--muted);display:block;margin-top:.1rem}

.tk-kw{color:var(--frost);font-weight:600}
.tk-noun{color:var(--frost-deep)}
.tk-str{color:var(--ember)}
.tk-num{color:#6B3FA0}
footer{margin-top:2.4rem;padding-top:1.2rem;border-top:1px solid var(--rule);
  font-size:.9rem;color:var(--muted)}
footer code{font-family:var(--mono);font-size:.85em}
@media (max-width:820px){.cols{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="wrap">

<p class="eyebrow">frost — scratchpad</p>
<h1>Chunk expressions, live.</h1>
<p class="lede">Type an expression and see the result against the text on the
left. <code>it</code> holds that text. This is the notation that replaces
<code>cut</code>, <code>awk</code>, <code>sed -n</code>, <code>head</code> and
<code>tail</code> with one grammar.</p>

<div class="bar">
  <input id="expr" spellcheck="false" autocomplete="off"
         value="the third word of line 2 of it"
         aria-label="chunk expression">
</div>
<div class="result" id="result"><p class="val"></p></div>

<div class="cols">
  <div class="panel">
    <h2>The text — edit it freely</h2>
    <div class="body">
      <textarea id="subject" spellcheck="false"></textarea>
      <p class="gutter" id="gutter"></p>
    </div>
  </div>

  <div class="panel">
    <h2>Try these</h2>
    <div class="body" id="examples"></div>
  </div>
</div>

<footer>
  This page evaluates expressions with <code>web/chunks.js</code>, a second
  implementation of the chunk grammar. <code>tools/verify_chunks.py</code>
  runs 1,288 expressions through both it and the real interpreter on every
  build, and the page is not written if they disagree.
  For the full language, run <code>frost --try</code> in a terminal.
</footer>
</div>

<script>
__EVALUATOR__
</script>

<script>
const SUBJECT = __SUBJECT__;
const EXAMPLES = __EXAMPLES__;
const KEYWORDS = new Set(__KEYWORDS__);
const NOUNS = new Set(__NOUNS__);

const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function highlight(code){
  const out=[]; let i=0;
  while(i<code.length){
    const rest=code.slice(i);
    let m=rest.match(/^"(\\.|[^"\\])*"?/);
    if(m){out.push(['str',m[0]]);i+=m[0].length;continue;}
    m=rest.match(/^\d+(\.\d+)?/);
    if(m){out.push(['num',m[0]]);i+=m[0].length;continue;}
    m=rest.match(/^[A-Za-z_][A-Za-z0-9_]*/);
    if(m){const w=m[0].toLowerCase();
      out.push([KEYWORDS.has(w)?'kw':(NOUNS.has(w)?'noun':null),m[0]]);
      i+=m[0].length;continue;}
    out.push([null,code[i]]);i+=1;
  }
  return out.map(([c,t])=>c?`<span class="tk-${c}">${esc(t)}</span>`:esc(t)).join('');
}

const exprEl = document.getElementById('expr');
const subjEl = document.getElementById('subject');
const resEl  = document.getElementById('result');
const gutEl  = document.getElementById('gutter');

subjEl.value = SUBJECT;

document.getElementById('examples').innerHTML = EXAMPLES.map(([title, items]) =>
  `<div class="group"><h3>${esc(title)}</h3>${
    items.map(([code, why]) =>
      `<button class="ex" data-expr="${esc(code)}">
         <code>${highlight(code)}</code><span>${esc(why)}</span>
       </button>`).join('')
  }</div>`).join('');

document.querySelectorAll('.ex').forEach(b =>
  b.addEventListener('click', () => {
    exprEl.value = b.dataset.expr;
    exprEl.focus();
    run();
  }));

function stats(){
  const t = subjEl.value;
  const lines = t === '' ? 0 : t.split('\n').length;
  const words = t.split(/\s+/).filter(Boolean).length;
  gutEl.textContent =
    `${lines} lines · ${words} words · ${t.length} characters`;
}

function run(){
  const src = exprEl.value.trim();
  stats();
  if(!src){ resEl.className='result'; resEl.innerHTML='<p class="val"></p>'; return; }

  let value;
  try {
    value = frostChunks.evaluate(src, subjEl.value);
  } catch (e) {
    resEl.className = 'result err';
    resEl.innerHTML =
      `<p class="meta">error</p><p class="err-msg">${esc(e.msg || String(e))}</p>` +
      (e.hint ? `<p class="hint">hint: ${esc(e.hint)}</p>` : '');
    return;
  }

  resEl.className = 'result ok';
  let label, body;
  if (Array.isArray(value)) {
    label = value.length === 1 ? '1 item' : value.length + ' items';
    body = value.length ? value.join('\n') : '(no matches)';
  } else if (typeof value === 'boolean') {
    label = 'true or false';
    body = String(value);
  } else if (typeof value === 'number') {
    label = 'number';
    body = frostChunks.text(value);
  } else {
    const t = frostChunks.text(value);
    label = t === '' ? 'empty' : t.length + (t.length === 1 ? ' character' : ' characters');
    body = t === '' ? '(empty)' : t;
  }
  resEl.innerHTML = `<p class="meta">${esc(label)}</p><p class="val">${esc(body)}</p>`;
}

exprEl.addEventListener('input', run);
subjEl.addEventListener('input', run);
run();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
