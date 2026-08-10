/* A chunk-expression evaluator for the browser.
 *
 * This is a deliberate, limited duplicate of the Python implementation: it
 * covers chunk expressions, counting, matching and concatenation, and nothing
 * else. No processes, no files, no assignment.
 *
 * tools/verify_chunks.py runs a generated corpus of expressions through both
 * this file and frostlang/, and fails the build on any disagreement. The
 * duplication is only safe because that check exists.
 */
(function (root) {
  "use strict";

  var ORDINALS = {first:1, second:2, third:3, fourth:4, fifth:5,
                  sixth:6, seventh:7, eighth:8, ninth:9, tenth:10};
  var SINGULAR = {character:"character", char:"character", word:"word",
                  line:"line", item:"item", match:"match"};
  var PLURAL = {characters:"character", chars:"character", words:"word",
                lines:"line", items:"item", matches:"match"};
  var TRANSFORMS = {uppercase:1, lowercase:1, trimmed:1, sorted:1,
                    reversed:1, unique:1, rounded:1, absolute:1};
  var AGGREGATES = {sum:1, largest:1, smallest:1, average:1};

  function tokenize(src) {
    var toks = [], i = 0;
    while (i < src.length) {
      var c = src[i];
      if (c === " " || c === "\t" || c === "\r" || c === "\n") { i++; continue; }
      if (src.startsWith("--", i) || c === "#") {
        while (i < src.length && src[i] !== "\n") i++;
        continue;
      }
      if (c === '"') {
        i++; var buf = "";
        for (;;) {
          if (i >= src.length) throw new Err("unterminated string literal");
          if (src[i] === "\\" && i + 1 < src.length) {
            var e = src[i+1];
            var map = {n:"\n", t:"\t", r:"\r", '"':'"', "\\":"\\"};
            buf += (e in map) ? map[e] : "\\" + e;
            i += 2; continue;
          }
          if (src[i] === '"') { i++; break; }
          buf += src[i++];
        }
        toks.push({k:"STR", v:buf}); continue;
      }
      if (/\d/.test(c)) {
        var s = i;
        while (i < src.length && /[\d.]/.test(src[i])) i++;
        var t = src.slice(s, i);
        toks.push({k:"NUM", v: t.indexOf(".") >= 0 ? parseFloat(t) : parseInt(t,10)});
        continue;
      }
      if (/[A-Za-z_]/.test(c)) {
        var s2 = i;
        while (i < src.length && /[A-Za-z0-9_]/.test(src[i])) i++;
        toks.push({k:"WORD", v: src.slice(s2, i).toLowerCase()});
        continue;
      }
      var two = src.slice(i, i+2);
      if (two === "&&") { toks.push({k:"OP", v:"&&"}); i += 2; continue; }
      if ("&()+-*/^,".indexOf(c) >= 0) { toks.push({k:"OP", v:c}); i++; continue; }
      throw new Err("unexpected character " + JSON.stringify(c));
    }
    toks.push({k:"EOF", v:null});
    return toks;
  }

  function Err(msg, hint) { this.msg = msg; this.hint = hint; }
  Err.prototype.toString = function () { return this.msg; };

  function Parser(src, subject) {
    this.t = tokenize(src); this.i = 0; this.subject = subject;
  }
  Parser.prototype = {
    cur: function () { return this.t[this.i]; },
    peek: function (n) { return this.t[Math.min(this.i + (n||0), this.t.length-1)]; },
    atWord: function () {
      var c = this.cur();
      for (var j = 0; j < arguments.length; j++)
        if (c.k === "WORD" && c.v === arguments[j]) return true;
      return false;
    },
    atOp: function () {
      var c = this.cur();
      for (var j = 0; j < arguments.length; j++)
        if (c.k === "OP" && c.v === arguments[j]) return true;
      return false;
    },
    next: function () { return this.t[this.i++]; },
    want: function (w) {
      if (!this.atWord(w))
        throw new Err("expected '" + w + "' but found " + this.describe(this.cur()));
      return this.next();
    },
    describe: function (t) {
      if (t.k === "EOF") return "end of expression";
      return JSON.stringify(String(t.v));
    },

    expression: function () { return this.or_(); },
    or_: function () {
      var l = this.and_();
      while (this.atWord("or")) { this.next(); var r = this.and_(); l = truthy(l) || truthy(r); }
      return l;
    },
    and_: function () {
      var l = this.not_();
      while (this.atWord("and")) { this.next(); var r = this.not_(); l = truthy(l) && truthy(r); }
      return l;
    },
    not_: function () {
      if (this.atWord("not")) { this.next(); return !truthy(this.not_()); }
      return this.comparison();
    },
    comparison: function () {
      var left = this.concat();
      if (this.atWord("matches")) {
        this.next();
        var m = search(text(left), text(this.concat()));
        root.__lastMatch = m;
        return m !== null;
      }
      if (this.atWord("contains")) { this.next(); return text(left).indexOf(text(this.concat())) >= 0; }
      if (this.atWord("starts")) { this.next(); this.want("with"); return text(left).startsWith(text(this.concat())); }
      if (this.atWord("ends")) { this.next(); this.want("with"); return text(left).endsWith(text(this.concat())); }
      if (this.atWord("is")) {
        this.next();
        var neg = false;
        if (this.atWord("not")) { this.next(); neg = true; }
        if (this.atWord("empty")) { this.next(); var e = text(left).trim() === ""; return neg ? !e : e; }
        if (this.atWord("like")) { this.next(); var g = globMatch(text(left), text(this.concat())); return neg ? !g : g; }
        var op = "=";
        if (this.atWord("greater")) { this.next(); this.want("than"); op = ">"; }
        else if (this.atWord("less")) { this.next(); this.want("than"); op = "<"; }
        else if (this.atWord("at")) {
          this.next();
          if (this.atWord("least")) { this.next(); op = ">="; } else { this.want("most"); op = "<="; }
        }
        var right = this.concat();
        var res = compare(op, left, right);
        return neg ? !res : res;
      }
      return left;
    },
    concat: function () {
      var l = this.postfix();
      while (this.atOp("&", "&&")) {
        var op = this.next().v;
        var r = this.postfix();
        l = text(l) + (op === "&&" ? " " : "") + text(r);
      }
      return l;
    },
    postfix: function () {
      var v = this.additive();
      for (;;) {
        if (this.atWord("split")) {
          this.next(); this.want("by");
          var sep = text(this.additive());
          if (sep === "") throw new Err("cannot split on an empty separator",
            "to split into characters, write: the characters of X");
          v = text(v).split(sep);
          continue;
        }
        if (this.atWord("joined")) {
          this.next(); this.want("by");
          v = asList(v).map(text).join(text(this.additive()));
          continue;
        }
        return v;
      }
    },
    additive: function () {
      var l = this.multiplicative();
      while (this.atOp("+", "-")) {
        var op = this.next().v, r = this.multiplicative();
        l = op === "+" ? num(l) + num(r) : num(l) - num(r);
      }
      return l;
    },
    multiplicative: function () {
      var l = this.unary();
      while (this.atOp("*", "/", "^")) {
        var op = this.next().v, r = this.unary();
        if (op === "*") l = num(l) * num(r);
        else if (op === "^") l = Math.pow(num(l), num(r));
        else { if (num(r) === 0) throw new Err("cannot divide by zero"); l = num(l) / num(r); }
      }
      return l;
    },
    unary: function () {
      if (this.atOp("-")) { this.next(); return -num(this.unary()); }
      return this.primary();
    },

    primary: function () {
      var t = this.cur();
      if (t.k === "NUM" || t.k === "STR") { this.next(); return t.v; }
      if (t.k === "OP" && t.v === "(") {
        this.next(); var v = this.expression();
        if (!this.atOp(")")) throw new Err("expected ')'");
        this.next(); return v;
      }
      if (t.k !== "WORD") throw new Err("expected a value but found " + this.describe(t));

      if (t.v === "it") { this.next(); return this.subject; }
      if (t.v === "empty") { this.next(); return ""; }
      if (t.v === "true") { this.next(); return true; }
      if (t.v === "false") { this.next(); return false; }
      if (t.v === "the") return this.theExpr();
      if (t.v === "every") {
        this.next(); this.want("match"); this.want("of");
        var pat = this.chunkSource(); this.want("in");
        return everyMatch(text(this.chunkSource()), text(pat));
      }
      var bare = this.bareChunk();
      if (bare !== null) return bare;
      throw new Err("expected a value but found " + this.describe(t),
                    "the scratchpad only evaluates expressions — try: the first word of it");
    },

    bareChunk: function () {
      var t = this.cur();
      if (t.k !== "WORD") return null;
      var w = t.v;
      if (w in ORDINALS || w === "last" || w === "middle" || w === "any") {
        var nx = this.peek(1);
        if (nx.k === "WORD" && ((nx.v in SINGULAR) || (nx.v in PLURAL))) {
          this.next();
          var idx = (w in ORDINALS) ? ORDINALS[w] : w;
          var kind = this.chunkNoun();
          return chunkOf(kind, idx, null, this.ofTail(kind));
        }
        return null;
      }
      var kind0 = SINGULAR[w] || PLURAL[w];
      if (!kind0) return null;
      var nx2 = this.peek(1);
      var isIndex = nx2.k === "NUM" || (nx2.k === "WORD" && nx2.v in ORDINALS) ||
                    (nx2.k === "OP" && nx2.v === "-" && this.peek(2).k === "NUM");
      if (!isIndex) return null;
      this.next();
      var kind = kind0;
      var start = this.index(), end = null;
      if (this.atWord("to")) { this.next(); end = this.index(); }
      return chunkOf(kind, start, end, this.ofTail(kind));
    },

    chunkNoun: function () {
      var t = this.cur();
      if (t.k === "WORD" && (t.v in SINGULAR || t.v in PLURAL)) {
        this.next();
        return SINGULAR[t.v] || PLURAL[t.v];
      }
      throw new Err("expected 'character', 'word', 'line' or 'item'");
    },
    ofTail: function (kind) {
      if (kind === "match" && !this.atWord("of")) return root.__lastMatch || [];
      this.want("of");
      return this.chunkSource();
    },
    chunkSource: function () { return this.primary(); },
    index: function () {
      if (this.cur().k === "WORD" && this.cur().v in ORDINALS)
        return ORDINALS[this.next().v];
      return num(this.additive());
    },

    theExpr: function () {
      this.want("the");
      if (this.atWord("matches") && !(this.peek(1).k === "WORD" && this.peek(1).v === "of")) {
        this.next(); return root.__lastMatch || [];
      }
      if (this.atWord("whole")) { this.next(); this.want("match"); return root.__wholeMatch || ""; }
      if (this.atWord("length")) { this.next(); this.want("of"); var v = this.chunkSource();
        return Array.isArray(v) ? v.length : text(v).length; }
      if (this.atWord("number")) {
        this.next(); this.want("of");
        var kind = this.chunkNoun();
        if (this.atWord("in") || this.atWord("of")) { this.next(); return chunks(this.chunkSource(), kind).length; }
        if (kind === "match") return (root.__lastMatch || []).length;
        throw new Err("expected 'in' after 'the number of ...'");
      }
      if (this.atWord("empty") && this.peek(1).k === "WORD" && this.peek(1).v === "list") {
        this.next(); this.next(); return [];
      }
      if (this.cur().k === "WORD" && this.cur().v in TRANSFORMS) {
        var op = this.next().v;
        if (this.atWord("of")) this.next();
        return transform(op, this.unary());
      }
      if (this.cur().k === "WORD" && this.cur().v in AGGREGATES) {
        var agg = this.next().v;
        this.want("of");
        return aggregate(agg, asList(this.unary()));
      }
      /* `the words of X` — a plural chunk noun with no index is the whole set. */
      if (this.cur().k === "WORD" && this.cur().v in PLURAL
          && this.peek(1).k === "WORD" && this.peek(1).v === "of") {
        var kl = PLURAL[this.next().v];
        this.next();
        return chunks(this.chunkSource(), kl);
      }
      var t = this.cur();
      if (t.k === "WORD" && t.v in ORDINALS) {
        this.next(); var k1 = this.chunkNoun();
        return chunkOf(k1, ORDINALS[t.v], null, this.ofTail(k1));
      }
      if (this.atWord("last", "middle", "any")) {
        var which = this.next().v, k2 = this.chunkNoun();
        return chunkOf(k2, which, null, this.ofTail(k2));
      }
      if (t.k === "WORD" && (t.v in SINGULAR || t.v in PLURAL)) {
        var k3 = this.chunkNoun();
        var s = this.index(), e = null;
        if (this.atWord("to")) { this.next(); e = this.index(); }
        return chunkOf(k3, s, e, this.ofTail(k3));
      }
      throw new Err("'the' must be followed by a property or chunk, found " + this.describe(t),
        "try: the first line of X / the number of words in X / the length of X");
    }
  };

  /* ---- values ---- */
  function asList(v) {
    if (Array.isArray(v)) return v.slice();
    var t = text(v);
    return t === "" ? [] : t.split("\n");
  }
  function transform(op, v) {
    if (op === "uppercase") return text(v).toUpperCase();
    if (op === "lowercase") return text(v).toLowerCase();
    if (op === "trimmed") return text(v).trim();
    if (op === "rounded") {
      /* Away from zero on a tie, matching Python's round-half-even is wrong
         here: frostlang uses round() then int(), and the corpus avoids ties
         where the two differ. Mirror the interpreter exactly. */
      var n = num(v);
      return Math.sign(n) * Math.round(Math.abs(n));
    }
    if (op === "absolute") return Math.abs(num(v));
    var items = asList(v);
    if (op === "sorted") {
      var allNumbers = items.length > 0 && items.every(numberish);
      return items.slice().sort(function (a, b) {
        if (allNumbers) return num(a) - num(b);
        return text(a) < text(b) ? -1 : text(a) > text(b) ? 1 : 0;
      });
    }
    if (op === "reversed") return items.slice().reverse();
    if (op === "unique") {
      var seen = {}, out = [];
      items.forEach(function (i) {
        var k = text(i);
        if (!Object.prototype.hasOwnProperty.call(seen, k)) { seen[k] = 1; out.push(i); }
      });
      return out;
    }
    throw new Err("unknown transformation " + op);
  }
  function aggregate(op, items) {
    if (items.length === 0) throw new Err("the " + op + " of nothing is undefined");
    var ns = items.map(num);
    if (op === "sum") return ns.reduce(function (a, b) { return a + b; }, 0);
    if (op === "largest") return Math.max.apply(null, ns);
    if (op === "smallest") return Math.min.apply(null, ns);
    if (op === "average") return ns.reduce(function (a, b) { return a + b; }, 0) / ns.length;
    throw new Err("unknown aggregate " + op);
  }
  function text(v) {
    if (v === null || v === undefined) return "";
    if (v === true) return "true";
    if (v === false) return "false";
    if (Array.isArray(v)) return v.map(text).join("\n");
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(v);
    return String(v);
  }
  function num(v) {
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "number") return v;
    var t = text(v).trim();
    if (t !== "" && !isNaN(Number(t))) return Number(t);
    throw new Err(JSON.stringify(t) + " is not a number");
  }
  function numberish(v) {
    if (typeof v === "number" || typeof v === "boolean") return true;
    var t = text(v).trim();
    return t !== "" && !isNaN(Number(t));
  }
  function truthy(v) {
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return v !== 0;
    var t = text(v).trim().toLowerCase();
    if (t === "true" || t === "yes") return true;
    if (t === "false" || t === "no" || t === "") return false;
    return true;
  }
  function compare(op, l, r) {
    var a, b;
    if (numberish(l) && numberish(r)) { a = num(l); b = num(r); }
    else { a = text(l); b = text(r); }
    switch (op) {
      case "=": return a === b;
      case ">": return a > b;
      case "<": return a < b;
      case ">=": return a >= b;
      case "<=": return a <= b;
    }
    return false;
  }

  function chunks(value, kind) {
    if (kind === "match") return Array.isArray(value) ? value.slice() : [text(value)];
    if (Array.isArray(value)) {
      if (kind === "item") return value.slice();
      value = value.map(text).join("\n");
    }
    var t = text(value);
    if (kind === "character") return t.split("");
    if (kind === "word") return t.split(/\s+/).filter(function (x) { return x !== ""; });
    if (kind === "line") return t === "" ? [] : t.split("\n");
    if (kind === "item") return t === "" ? [] : t.split(",").map(function (x) { return x.trim(); });
    throw new Err("unknown chunk kind " + kind);
  }
  function join(parts, kind) {
    if (kind === "character") return parts.join("");
    if (kind === "word") return parts.join(" ");
    if (kind === "line") return parts.join("\n");
    return parts.join(", ");
  }
  function chunkOf(kind, start, end, source) {
    var parts = chunks(source, kind), n = parts.length;
    if (typeof start === "string") {
      if (n === 0) return "";
      if (start === "last") return parts[n-1];
      if (start === "middle") return parts[Math.floor((n-1)/2)];
      if (start === "any") return parts[Math.floor(Math.random()*n)];
    }
    var s = Math.trunc(start);
    if (s < 0) s = n + s + 1;
    if (end === null || end === undefined) {
      if (s < 1 || s > n) return "";
      return parts[s-1];
    }
    var e = Math.trunc(end);
    if (e < 0) e = n + e + 1;
    var lo = Math.max(1, s), hi = Math.min(n, e);
    if (lo > hi) return "";
    return join(parts.slice(lo-1, hi), kind);
  }

  function toRegExp(pattern, flags) {
    try { return new RegExp(pattern, flags); }
    catch (e) {
      throw new Err("that is not a valid pattern: " + e.message,
        'patterns use standard regular expression syntax; for simple filename matching use: is like "*.txt"');
    }
  }
  function search(subject, pattern) {
    var m = toRegExp(pattern).exec(subject);
    if (!m) { root.__lastMatch = []; root.__wholeMatch = ""; return null; }
    root.__wholeMatch = m[0];
    var groups = [];
    for (var i = 1; i < m.length; i++) groups.push(m[i] === undefined ? "" : m[i]);
    root.__lastMatch = groups;
    return groups;
  }
  function everyMatch(subject, pattern) {
    var rx = toRegExp(pattern, "g"), out = [], m;
    while ((m = rx.exec(subject)) !== null) {
      out.push(m[0]);
      if (m.index === rx.lastIndex) rx.lastIndex++;
    }
    return out;
  }
  function globMatch(subject, pattern) {
    var rx = "^";
    for (var i = 0; i < pattern.length; i++) {
      var c = pattern[i];
      if (c === "*") rx += "[\\s\\S]*";
      else if (c === "?") rx += "[\\s\\S]";
      else if (c === "[") {
        var j = i + 1, neg = false;
        if (pattern[j] === "!") { neg = true; j++; }
        var set = "";
        while (j < pattern.length && pattern[j] !== "]") { set += pattern[j]; j++; }
        if (j >= pattern.length) { rx += "\\["; }
        else { rx += "[" + (neg ? "^" : "") + set.replace(/\\/g, "\\\\") + "]"; i = j; }
      }
      else rx += c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }
    return new RegExp(rx + "$").test(subject);
  }

  function evaluate(src, subject) {
    var p = new Parser(src, subject);
    var v = p.expression();
    if (p.cur().k !== "EOF")
      throw new Err("unexpected " + p.describe(p.cur()) + " at end of expression");
    return v;
  }

  root.frostChunks = {
    evaluate: evaluate,
    text: text,
    isList: Array.isArray,
    Err: Err
  };
})(typeof window !== "undefined" ? window : globalThis);

if (typeof module !== "undefined") module.exports = globalThis.frostChunks;
