#!/usr/bin/env python3
"""Build audit.html, a visual audit report for three example scripts.

Every report on the page is produced by running the real static analyzer at
build time and embedding its JSON. Nothing on the page is simulated in
JavaScript; the browser only renders what the analyzer already decided.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang.parser import parse, HARD_WORDS, CHUNK_SINGULAR, CHUNK_PLURAL, ORDINALS
from frostlang.audit import (audit, find_dangers, summarise, verdict,
                             parse_policy, check)
from frostlang.cli import audit_json

OUT = os.path.join(HERE, "audit.html")

DEMOS = [
    ("exfiltrate.frost", "\u201cDotfile backup\u201d",
     "Sold as a backup helper. It reads your keys and sends them to a "
     "stranger. Every line is individually plausible."),
    ("danger.frost", "Cleanup script",
     "Looks like routine maintenance \u2014 and quietly does four dangerous "
     "things."),
    ("healthcheck.frost", "Health check",
     "Polls endpoints, parses responses, reports failures. Passes clean."),
    ("logreport.frost", "Log analysis",
     "Reads a log, counts errors, ranks the busiest clients. Passes clean."),
]

KEYWORDS = sorted(HARD_WORDS)
NOUNS = sorted(set(CHUNK_SINGULAR) | set(CHUNK_PLURAL) | set(ORDINALS) | {
    "last", "middle", "any", "result", "arguments", "output", "error",
    "status", "current", "folder", "environment", "variable", "length",
    "number", "second", "seconds", "minute", "minutes", "millisecond",
    "milliseconds", "ms", "hour", "hours"})


def build():
    with open(os.path.join(HERE, "examples", "production.policy")) as fh:
        policy_text = fh.read()
    rules = parse_policy(policy_text)

    reports = []
    for filename, title, blurb in DEMOS:
        path = os.path.join(HERE, "examples", filename)
        with open(path) as fh:
            source = fh.read()
        lines = source.splitlines()
        tree = parse(source)
        caps = audit(tree)
        findings = find_dangers(caps)
        policy_hits = check(caps, rules)

        data = audit_json(filename, caps, findings, lines)
        data.update({
            "title": title,
            "blurb": blurb,
            "source": source,
            "policy": [
                {"severity": hit.severity, "what": hit.what, "line": hit.line,
                 "hint": hit.hint,
                 "source": lines[hit.line - 1].strip()
                 if 0 < hit.line <= len(lines) else ""}
                for hit in policy_hits],
            "verdict": verdict(findings, policy_hits),
            "counts": {
                "danger": sum(1 for f in findings if f.severity == "danger"),
                "caution": sum(1 for f in findings if f.severity == "caution"),
                "note": sum(1 for f in findings if f.severity == "note"),
                "refused": sum(1 for hit in policy_hits if hit.severity == "forbid"),
            },
        })
        reports.append(data)

    def embed(obj):
        # `</script` inside any string would close the tag early.
        return json.dumps(obj).replace("</", "<\\/")

    page = TEMPLATE.replace("__REPORTS__", embed(reports)) \
                   .replace("__POLICY__", embed(policy_text)) \
                   .replace("__KEYWORDS__", embed(KEYWORDS)) \
                   .replace("__NOUNS__", embed(NOUNS))
    with open(OUT, "w") as fh:
        fh.write(page)
    print(f"wrote {OUT} ({len(page):,} bytes)")
    for r in reports:
        print(f"  {r['script']:22} {r['verdict']:10} "
              f"{r['counts']['danger']}d {r['counts']['caution']}c "
              f"{r['counts']['refused']} refused")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>frost, audit report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Newsreader:opsz,wght@6..72,400;6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#EAF0F4; --card:#FFFFFF; --ink:#0D1418; --muted:#5B6C77;
  --frost:#14657F; --frost-deep:#0B3F52; --ice:#CBDFE9; --ice-soft:#E2EDF3;
  --rule:#BFD3DE;
  --danger:#A6301B; --danger-bg:#FBEDE9; --danger-line:#E0B4A8;
  --caution:#8A6212; --caution-bg:#FAF3E4; --caution-line:#DFCB9C;
  --ok:#1C6B4B; --ok-bg:#E7F2EC; --ok-line:#A9CDBB;
  --display:"Space Grotesk",system-ui,sans-serif;
  --body:"Newsreader",Georgia,serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:var(--body);font-size:17px;line-height:1.6;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:2.6rem 1.5rem 6rem}

header .eyebrow{font-family:var(--mono);font-size:.7rem;letter-spacing:.16em;
  text-transform:uppercase;color:var(--frost);margin:0 0 .8rem}
header h1{font-family:var(--display);font-weight:700;
  font-size:clamp(2rem,4.6vw,2.9rem);letter-spacing:-.035em;line-height:1.03;
  margin:0 0 .7rem}
header p.lede{font-size:1.13rem;color:var(--muted);max-width:60ch;margin:0 0 .6rem}
header p.note{font-size:.95rem;color:var(--muted);max-width:62ch;margin:0}
header a{color:var(--frost)}

/* ---- picker ---- */
.kicker{font-family:var(--display);font-weight:500;font-size:1.35rem;
  line-height:1.3;letter-spacing:-.02em;color:var(--frost-deep);
  margin:1.8rem 0 0;padding:1.1rem 0 0;border-top:2px solid var(--frost)}
.kicker span{color:var(--danger);font-weight:700}
.picker{display:grid;grid-template-columns:repeat(4,1fr);gap:.7rem;
  margin:1.8rem 0 1.6rem}
@media (max-width:840px){.kicker{font-size:1.15rem}}
.pick{text-align:left;background:var(--card);border:1px solid var(--rule);
  border-left:3px solid var(--rule);border-radius:2px;padding:.85rem .95rem;
  cursor:pointer;font-family:inherit;color:inherit;transition:border-color .15s,
  box-shadow .15s;display:block}
.pick:hover{border-color:var(--frost)}
.pick[aria-pressed="true"]{border-left-color:var(--frost);
  box-shadow:0 1px 0 var(--frost)}
.pick .name{font-family:var(--display);font-weight:500;font-size:1rem;
  display:block;margin-bottom:.15rem}
.pick .file{font-family:var(--mono);font-size:.68rem;color:var(--muted);
  display:block;margin-bottom:.5rem}
.pick .chip{font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;
  text-transform:uppercase;padding:.16rem .42rem;border-radius:2px;
  border:1px solid}
.chip.clean{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.chip.caution{color:var(--caution);background:var(--caution-bg);
  border-color:var(--caution-line)}
.chip.dangerous,.chip.blocked{color:var(--danger);background:var(--danger-bg);
  border-color:var(--danger-line)}

/* ---- verdict banner ---- */
.verdict{border:1px solid;border-left-width:4px;border-radius:2px;
  padding:1.1rem 1.2rem;margin:0 0 1.5rem}
.verdict.clean{background:var(--ok-bg);border-color:var(--ok-line);
  border-left-color:var(--ok)}
.verdict.caution{background:var(--caution-bg);border-color:var(--caution-line);
  border-left-color:var(--caution)}
.verdict.dangerous,.verdict.blocked{background:var(--danger-bg);
  border-color:var(--danger-line);border-left-color:var(--danger)}
.verdict h2{font-family:var(--display);font-weight:700;font-size:1.25rem;
  margin:0 0 .3rem;letter-spacing:-.02em}
.verdict.clean h2{color:var(--ok)}
.verdict.caution h2{color:var(--caution)}
.verdict.dangerous h2,.verdict.blocked h2{color:var(--danger)}
.verdict p{margin:0;font-size:1.02rem}
.tally{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;
  color:var(--muted);margin-top:.6rem!important}

/* ---- layout ---- */
.cols{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);
  gap:1.5rem;align-items:start}
.panel{background:var(--card);border:1px solid var(--rule);border-radius:2px;
  overflow:hidden}
.panel h3{font-family:var(--mono);font-size:.68rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--frost);margin:0;
  padding:.7rem .95rem;border-bottom:1px solid var(--rule);
  background:var(--ice-soft)}
.panel .body{padding:.95rem}

/* ---- source listing ---- */
.code{font-family:var(--mono);font-size:.75rem;line-height:1.75;
  margin:0;overflow-x:auto}
.code .ln{display:flex;padding:.02rem 0}
.code .n{flex:0 0 2.6em;text-align:right;padding-right:.9em;color:#9BAEB9;
  user-select:none}
.code .t{white-space:pre;flex:1}
.code .ln.danger{background:var(--danger-bg);
  box-shadow:inset 3px 0 0 var(--danger)}
.code .ln.caution{background:var(--caution-bg);
  box-shadow:inset 3px 0 0 var(--caution)}
.code .ln.note{background:var(--ice-soft);box-shadow:inset 3px 0 0 var(--ice)}
.code .ln.focus{outline:1px solid var(--frost);outline-offset:-1px}
.tk-kw{color:var(--frost);font-weight:600}
.tk-noun{color:var(--frost-deep)}
.tk-str{color:#A6401B}
.tk-num{color:#6B3FA0}
.tk-com{color:#8A9AA5;font-style:italic}

/* ---- findings ---- */
.finding{border-left:3px solid;padding:.6rem .8rem;margin-bottom:.6rem;
  border-radius:2px;cursor:default}
.finding:last-child{margin-bottom:0}
.finding.danger{border-color:var(--danger);background:var(--danger-bg)}
.finding.caution{border-color:var(--caution);background:var(--caution-bg)}
.finding.note{border-color:var(--ice);background:var(--ice-soft)}
.finding.refused{border-color:var(--danger);background:var(--danger-bg)}
.finding .top{display:flex;justify-content:space-between;gap:1rem;
  align-items:baseline}
.finding .title{font-family:var(--display);font-weight:500;font-size:.95rem}
.finding.danger .title,.finding.refused .title{color:var(--danger)}
.finding.caution .title{color:var(--caution)}
.finding .at{font-family:var(--mono);font-size:.68rem;color:var(--muted);
  white-space:nowrap}
.finding .why{font-size:.92rem;color:var(--muted);margin:.2rem 0 0}
.finding .src{font-family:var(--mono);font-size:.7rem;margin-top:.4rem;
  color:var(--ink);background:rgba(255,255,255,.65);padding:.28rem .45rem;
  border-radius:2px;overflow-x:auto;white-space:pre}
.empty{color:var(--muted);font-size:.95rem;margin:0}

/* ---- effects table ---- */
.eff{width:100%;border-collapse:collapse;font-size:.85rem}
.eff th{font-family:var(--mono);font-size:.63rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--frost);text-align:left;
  padding:.35rem .5rem .35rem 0;border-bottom:1px solid var(--frost)}
.eff td{padding:.35rem .5rem .35rem 0;border-bottom:1px solid var(--rule);
  vertical-align:top}
.eff td.mono{font-family:var(--mono);font-size:.75rem}
.eff tr:last-child td{border-bottom:none}
.pill{font-family:var(--mono);font-size:.62rem;letter-spacing:.07em;
  text-transform:uppercase;padding:.1rem .35rem;border-radius:2px;
  border:1px solid var(--rule);color:var(--muted);white-space:nowrap}
.pill.bad{color:var(--danger);border-color:var(--danger-line);
  background:var(--danger-bg)}
.pill.good{color:var(--ok);border-color:var(--ok-line);background:var(--ok-bg)}

.policy-src{font-family:var(--mono);font-size:.73rem;white-space:pre;
  overflow-x:auto;color:var(--muted);margin:0}
footer{margin-top:3rem;padding-top:1.4rem;border-top:1px solid var(--rule);
  font-size:.92rem;color:var(--muted)}

@media (max-width:840px){
  .cols{grid-template-columns:1fr}
  .picker{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <p class="eyebrow">frost, static audit</p>
  <h1>Read the script before you trust it.</h1>
  <p class="lede">A frost script is parsed, not assembled from strings, so what
  it can do is visible before anything runs. These three reports were produced
  by the analyzer itself.</p>
  <p class="note">In bash, <code>rm -rf "$DIR"</code> is just text until the
  moment it executes. There is nothing to inspect. Here the program and each
  argument are separate nodes in a tree.</p>

  <p class="kicker">Before you run it, you can read exactly what it is allowed
  to do. <span>Bash cannot do this.</span></p>
</header>

<div class="picker" id="picker"></div>

<div id="verdict"></div>

<div class="cols">
  <div>
    <div class="panel">
      <h3>The script</h3>
      <div class="body"><div class="code" id="code"></div></div>
    </div>
  </div>

  <div>
    <div class="panel" style="margin-bottom:1.5rem">
      <h3>Findings</h3>
      <div class="body" id="findings"></div>
    </div>

    <div class="panel" style="margin-bottom:1.5rem">
      <h3>Effects</h3>
      <div class="body" id="effects"></div>
    </div>

    <div class="panel">
      <h3>Policy, production.policy</h3>
      <div class="body">
        <div id="policy-result" style="margin-bottom:.9rem"></div>
        <pre class="policy-src" id="policy-src"></pre>
      </div>
    </div>
  </div>
</div>

<footer>
  Generated by <code>tools/build_audit.py</code>, which runs the same analyzer
  as <code>frost --explain</code> and <code>frost --policy</code>. No result on
  this page is written by hand.
</footer>
</div>

<script>
const REPORTS = __REPORTS__;
const POLICY  = __POLICY__;
const KEYWORDS = new Set(__KEYWORDS__);
const NOUNS = new Set(__NOUNS__);

const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function highlight(code){
  const out=[]; let i=0;
  while(i<code.length){
    const rest=code.slice(i);
    let m=rest.match(/^(--|#)[^\n]*/);
    if(m){out.push(['com',m[0]]);i+=m[0].length;continue;}
    m=rest.match(/^"(\\.|[^"\\])*"?/);
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

const RANK = {danger:3, caution:2, note:1};

function render(idx){
  const r = REPORTS[idx];

  document.querySelectorAll('.pick').forEach((b,i)=>
    b.setAttribute('aria-pressed', String(i===idx)));

  /* --- verdict --- */
  const worst = {
    clean:['Passes audit','Nothing here reaches outside what the script says it does.'],
    caution:['Passes with cautions','Nothing dangerous, but some failures could go unnoticed.'],
    dangerous:['Dangerous','This script can do lasting damage. Read every finding before running it.'],
    blocked:['Refused by policy','One or more rules were broken. frost would not run this script.']
  }[r.verdict];
  const c = r.counts;
  document.getElementById('verdict').innerHTML =
    `<div class="verdict ${r.verdict}">
       <h2>${worst[0]}</h2>
       <p>${esc(r.summary)}</p>
       <p class="tally">${c.danger} danger · ${c.caution} caution · ${c.note} note${
         c.refused ? ' · ' + c.refused + ' refused by policy' : ''}</p>
     </div>`;

  /* --- source, with the worst finding per line deciding the tint --- */
  const tint = {};
  r.findings.forEach(f=>{
    if(!tint[f.line] || RANK[f.severity] > RANK[tint[f.line]]) tint[f.line]=f.severity;
  });
  r.policy.filter(p=>p.severity==='forbid').forEach(p=>tint[p.line]='danger');

  document.getElementById('code').innerHTML = r.source.split('\n').map((line,i)=>{
    const n=i+1;
    const cls = tint[n] ? ' ' + tint[n] : '';
    return `<div class="ln${cls}" data-line="${n}"><span class="n">${n}</span><span class="t">${highlight(line)||' '}</span></div>`;
  }).join('');

  /* --- findings --- */
  const box = document.getElementById('findings');
  if(!r.findings.length){
    box.innerHTML = '<p class="empty">Nothing flagged. Every command is checked, every deadline is set, and nothing touches a protected location.</p>';
  } else {
    box.innerHTML = r.findings.map(f=>
      `<div class="finding ${f.severity}" data-line="${f.line}">
         <div class="top"><span class="title">${esc(f.title)}</span>
         <span class="at">line ${f.line}</span></div>
         <p class="why">${esc(f.detail)}</p>
         <div class="src">${highlight(f.source)}</div>
       </div>`).join('');
  }

  /* --- effects --- */
  const rows = [];
  r.commands.forEach(c=>{
    const flags=[];
    if(!c.checked) flags.push('<span class="pill bad">failure ignored</span>');
    if(!c.timeout) flags.push('<span class="pill">no timeout</span>');
    if(c.timeout) flags.push('<span class="pill good">timeout set</span>');
    if(c.in_pipe) flags.push('<span class="pill">pipe stage</span>');
    rows.push(['runs', c.program===null?'(built at runtime)':c.program, c.line, flags.join(' ')]);
  });
  r.reads.forEach(x=>rows.push(['reads', x.path===null?'(path built at runtime)':x.path, x.line,'']));
  r.writes.forEach(x=>rows.push(['writes', x.path===null?'(path built at runtime)':x.path, x.line,'']));
  r.deletes.forEach(x=>rows.push(['deletes', x.path===null?'(path built at runtime)':x.path, x.line,'']));
  r.environment.forEach(x=>rows.push(['env', x.name, x.line,'']));

  rows.sort((a,b)=>a[2]-b[2]);
  document.getElementById('effects').innerHTML =
    `<table class="eff"><thead><tr><th>Effect</th><th>Target</th><th>Line</th><th></th></tr></thead>
     <tbody>${rows.map(([k,v,l,f])=>
       `<tr data-line="${l}"><td>${k}</td><td class="mono">${esc(v)}</td><td class="mono">${l}</td><td>${f}</td></tr>`
      ).join('')}</tbody></table>` +
    (r.exits.length ? `<p class="empty" style="margin-top:.7rem">Can exit with status ${r.exits.join(', ')}.</p>` : '');

  /* --- policy --- */
  const pr = document.getElementById('policy-result');
  const refused = r.policy.filter(p=>p.severity==='forbid');
  if(!r.policy.length){
    pr.innerHTML = '<div class="finding note"><div class="top"><span class="title">No rule broken</span></div><p class="why">This script may run under the policy below.</p></div>';
  } else {
    pr.innerHTML = r.policy.map(p=>
      `<div class="finding ${p.severity==='forbid'?'refused':'caution'}" data-line="${p.line}">
        <div class="top"><span class="title">${p.severity==='forbid'?'REFUSED':'warning'}: ${esc(p.what)}</span>
        <span class="at">line ${p.line}</span></div>
        <div class="src">${highlight(p.source)}</div></div>`).join('')
      + (refused.length
         ? `<p class="empty" style="margin-top:.6rem"><strong>${refused.length} violation(s): frost exits 3 and the script never starts.</strong></p>`
         : '<p class="empty" style="margin-top:.6rem">Warnings only; the script would run.</p>');
  }
  document.getElementById('policy-src').textContent = POLICY.trim();

  wireHover();
}

function wireHover(){
  const lines = [...document.querySelectorAll('.code .ln')];
  const byLine = n => lines.find(l => l.dataset.line === String(n));
  document.querySelectorAll('[data-line]').forEach(el=>{
    if(el.classList.contains('ln')) return;
    el.addEventListener('mouseenter',()=>{
      const t = byLine(el.dataset.line);
      if(t){t.classList.add('focus');
        t.scrollIntoView({block:'nearest',behavior:'smooth'});}
    });
    el.addEventListener('mouseleave',()=>{
      const t = byLine(el.dataset.line);
      if(t) t.classList.remove('focus');
    });
  });
}

document.getElementById('picker').innerHTML = REPORTS.map((r,i)=>
  `<button class="pick" data-i="${i}" aria-pressed="${i===0}">
     <span class="name">${esc(r.title)}</span>
     <span class="file">${esc(r.script)}</span>
     <span class="chip ${r.verdict}">${r.verdict}</span>
   </button>`).join('');

document.querySelectorAll('.pick').forEach(b=>
  b.addEventListener('click',()=>render(Number(b.dataset.i))));

render(0);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
