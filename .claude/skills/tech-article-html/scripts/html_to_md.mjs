#!/usr/bin/env node
/** Convert an article page built by build_article.py back into a Markdown source.
 *
 * Usage:
 *   node html_to_md.mjs page.html [article.md]
 *
 * The output feeds md_to_article.mjs: converting a page and rebuilding from the
 * Markdown it produces gives back a byte-identical page (the article-meta
 * 'updated' date is carried over via the front matter). Use it to migrate
 * existing articles to the Markdown workflow, then verify with cmp. Panels with
 * hand-coloured source (spans inside the code) are carried through as `raw`
 * fences: their content is preserved byte-for-byte but is pre-escaped HTML, so
 * edit such fences knowing you are writing body-fragment code. Node standard
 * library only.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, "build_article.py");

function die(msg) {
  console.error(`html_to_md: ERROR: ${msg}`);
  process.exit(1);
}

// LANG / PROSE_LANGS stay owned by build_article.py — read them from its source
// so the label maps can never drift between the two scripts.
function readLangMaps() {
  const src = readFileSync(BUILD, "utf8");
  const lm = /LANG = (\{[\s\S]*?\})/.exec(src);
  const pm = /PROSE_LANGS = (\{[\s\S]*?\})/.exec(src);
  if (!lm || !pm) die("cannot find LANG / PROSE_LANGS in build_article.py");
  const LANG = JSON.parse(lm[1]);
  const PROSE = new Set([...pm[1].matchAll(/"([\w-]+)"/g)].map(m => m[1]));
  return { LANG, PROSE };
}
const { LANG, PROSE: PROSE_LANGS } = readLangMaps();
const REV = {};
for (const [k, v] of Object.entries(LANG)) if (!(v in REV)) REV[v] = k;

// single-pass entity decode (same semantics as python html.unescape) for the
// forms build_article's escaping can produce, plus numeric references
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
function unescape(s) {
  return s.replace(/&(?:amp|lt|gt|quot|apos|nbsp|#x[0-9a-fA-F]+|#\d+);/g, m => {
    const name = m.slice(1, -1);
    if (name.startsWith("#")) {
      const cp = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return String.fromCodePoint(cp);
    }
    return ENTITIES[name] ?? m;
  });
}

const MAIN_RE = /<main class="content" id="content">\n([\s\S]*?)\n  <\/main>/;
const SEC_RE = /<section class="sec">([\s\S]*?)<\/section>/g;
const INTRO_RE = /<section class="intro">([\s\S]*?)<\/section>/;
const ROW_RE = /<div class="row(?: row-only-text)?"><div class="row-text">([\s\S]*?)<\/div><div class="row-media">([\s\S]*?)<\/div><\/div>|<div class="row-wide">([\s\S]*?)<\/div>/g;
const TEXT_BLOCK_RE = /<(p|h3|blockquote|ul|ol)>([\s\S]*?)<\/\1>/g;
const FIG_RE = /<figure class="panel (panel-code|panel-prompt|panel-diagram (?:light|dark))(?: wide)?">([\s\S]*?)<\/figure>/g;
const POINTS_RE = /<li>([\s\S]*?)<\/li>/g;
const FN_CARD_RE = /^\s*<code class="fn-name">([\s\S]*?)<\/code><span>([\s\S]*?)<\/span>\s*$/;


function codeSpanMd(content) {
  // a code span that itself contains backticks needs `` `` `` delimiters,
  // padded with one space on each side (CommonMark rule)
  return content.includes("`") ? "`` " + content + " ``" : "`" + content + "`";
}


function mdInline(fragment) {
  /** inner HTML of a p/h/li/th/td → Markdown inline (code → `…`, strong → **…**,
   *  a → [t](u)). Literal backslashes and backticks in the prose are escaped so
   *  the forward converter does not read them as code spans / escapes; a real
   *  <br> tag is kept as a literal `<br>` in the Markdown. */
  const stash = [];
  let s = fragment.replace(/<br\s*\/?>/gi, "\x02"); // before unescape: &lt;br&gt; text stays text
  s = s.replace(/<code[^>]*>([\s\S]*?)<\/code>/g, (m, inner) => {
    stash.push(inner);
    return `\x00${stash.length - 1}\x00`;
  });
  // tags are converted / stripped while text is still escaped, so an escaped
  // &lt;x&gt; in the prose is not mistaken for a tag
  s = s.replace(/<strong[^>]*>([\s\S]*?)<\/strong>/g, (m, t) => `**${t}**`);
  s = s.replace(/<a href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g, (m, href, t) => `[${t}](${href})`);
  s = s.replace(/<[^>]+>/g, "");
  s = unescape(s);
  s = s.replace(/\\/g, () => "\\\\").replace(/`/g, () => "\\`");
  s = s.replace(/\x02/g, () => "<br>");
  return s.replace(/\x00(\d+)\x00/g, (m, i) => codeSpanMd(unescape(stash[+i]))).trim();
}


function cover(fragment, matches, what) {
  /** Guards against silently dropping content: matches must tile the fragment. */
  let pos = 0;
  for (const m of matches) {
    if (fragment.slice(pos, m.index).trim()) {
      die(`unrecognised ${what} content: ${JSON.stringify(fragment.slice(pos, m.index).slice(0, 100))}`);
    }
    pos = m.index + m[0].length;
  }
  if (fragment.slice(pos).trim()) {
    die(`unrecognised ${what} content: ${JSON.stringify(fragment.slice(pos).slice(0, 100))}`);
  }
}


function textBlocks(htmlFragment) {
  const out = [];
  const matches = [...htmlFragment.matchAll(TEXT_BLOCK_RE)];
  cover(htmlFragment, matches, "row-text");
  for (const m of matches) {
    const tag = m[1], inner = m[2];
    if (tag === "p") out.push(["p", mdInline(inner)]);
    else if (tag === "h3") out.push(["h3", mdInline(inner)]);
    else if (tag === "blockquote") {
      const p = /^\s*<p>([\s\S]*?)<\/p>\s*$/.exec(inner);
      if (!p) die(`blockquote with unexpected content: ${inner.slice(0, 80)}`);
      out.push(["quote", mdInline(p[1])]);
    } else {
      out.push(["list", [tag, [...inner.matchAll(POINTS_RE)].map(x => mdInline(x[1]))]]);
    }
  }
  return out;
}


function introBlocks(htmlFragment) {
  const out = [];
  const pat = /<p>([\s\S]*?)<\/p>|<ul class="points">([\s\S]*?)<\/ul>|<(ul|ol)>([\s\S]*?)<\/\3>/g;
  const matches = [...htmlFragment.matchAll(pat)];
  cover(htmlFragment, matches, "intro");
  for (const m of matches) {
    if (m[1] !== undefined) {
      out.push(["p", mdInline(m[1])]);
    } else if (m[2] !== undefined) {
      const items = [];
      for (const li of m[2].matchAll(POINTS_RE)) {
        const fn = FN_CARD_RE.exec(li[1]);
        items.push(fn ? `${codeSpanMd(unescape(fn[1]))}：${mdInline(fn[2])}` : mdInline(li[1]));
      }
      out.push(["list", ["ul", items]]);
    } else {
      out.push(["list", [m[3], [...m[4].matchAll(POINTS_RE)].map(x => mdInline(x[1]))]]);
    }
  }
  return out;
}


function mediaBlocks(htmlFragment) {
  const out = [];
  const matches = [...htmlFragment.matchAll(FIG_RE)];
  cover(htmlFragment, matches, "row-media");
  for (const m of matches) {
    const cls = m[1], inner = m[2];
    if (cls.startsWith("panel-diagram")) {
      const tone = cls.split(" ")[1];
      const t = /<span class="panel-title">([\s\S]*?)<\/span>/.exec(inner);
      const svg = /(<svg\b[\s\S]*?<\/svg>|<img\b[^>]*>)/.exec(inner);
      if (!t || !svg) die("diagram panel without title or svg/img");
      // title kept in the escaped form the page carries: the forward converter
      // writes it straight back into the body attribute
      out.push(["svg", [t[1], tone, svg[1]]]);
    } else {
      const t = /<span class="panel-title"[^>]*\stitle="([^"]*)"/.exec(inner);
      const label = /<span class="panel-lang">([\s\S]*?)<\/span>/.exec(inner);
      const code = /<pre><code[^>]*>([\s\S]*?)<\/code><\/pre>/.exec(inner);
      if (!(t && label && code)) die("code panel without title, language label, or code content");
      const langM = /<code class="language-([\w-]+)"/.exec(inner);
      const lang = langM ? langM[1] : (REV[label[1]] ?? label[1].toLowerCase());
      // a paper panel carrying a code language was forced prose via
      // data-kind="prompt" — carry that attribute through
      const kind = cls === "panel-prompt" && !PROSE_LANGS.has(lang) ? ' kind="prompt"' : "";
      let content = code[1];
      // hand-coloured panels carry raw <span> markup: keep every byte and mark
      // the fence raw so md_to_article.mjs passes it through verbatim
      const raw = content.includes("<span");
      if (!raw) content = unescape(content);
      out.push(["code", [lang, t[1], content, raw, kind]]);
    }
  }
  return out;
}


function parseTable(htmlFragment) {
  // a literal | in a cell (prose or code span) is written \| so the row still splits
const cellMd = h => mdInline(h).replace(/\|/g, "\\|");
const ths = [...htmlFragment.matchAll(/<th>([\s\S]*?)<\/th>/g)].map(m => cellMd(m[1]));
  if (!ths.length) die("table without header cells");
  const rows = [];
  for (const tr of htmlFragment.matchAll(/<tr>([\s\S]*?)<\/tr>/g)) {
    const cells = [...tr[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map(m => cellMd(m[1]));
    if (cells.length) rows.push(cells);
  }
  return [ths, rows];
}


function fenceMarks(content) {
  /** 3 backticks, or one more than the longest backtick run at a content line
   *  start, so fences whose content itself carries markdown fences still close. */
  let mx = 2;
  for (const line of content.split("\n")) {
    const m = /^`+/.exec(line);
    if (m) mx = Math.max(mx, m[0].length);
  }
  return mx + 1;
}


function renderMd(fm, blocks) {
  const L = ["---"];
  for (const k of ["title", "kicker", "deck"]) if (fm[k]) L.push(`${k}: ${fm[k]}`);
  if (fm.meta?.length) {
    L.push("meta:");
    L.push(...fm.meta.map(c => "  - " + c));
  }
  for (const k of ["footer", "index", "updated"]) if (fm[k]) L.push(`${k}: ${fm[k]}`);
  L.push("---", "");
  for (const [kind, payload] of blocks) {
    if (kind === "p") L.push(payload, "");
    else if (kind === "h2") L.push(`## ${payload}`, "");
    else if (kind === "h3") L.push(`### ${payload}`, "");
    else if (kind === "list") {
      const [tag, items] = payload;
      if (tag === "ol") L.push(...items.map((it, i) => `${i + 1}. ${it}`), "");
      else L.push(...items.map(it => `- ${it}`), "");
    } else if (kind === "quote") L.push(`> ${payload}`, "");
    else if (kind === "table") {
      const [hdr, rows] = payload;
      L.push("| " + hdr.join(" | ") + " |");
      L.push("| " + hdr.map(() => "---").join(" | ") + " |");
      for (const r of rows) L.push("| " + r.join(" | ") + " |");
      L.push("");
    } else if (kind === "code") {
      const [lang, title, content, raw, kindAttr] = payload;
      const marks = "`".repeat(fenceMarks(content));
      L.push(`${marks}${lang} title="${title}"${kindAttr}${raw ? " raw" : ""}`, content, marks, "");
    } else if (kind === "svg") {
      const [title, tone, svg] = payload;
      const marks = "`".repeat(fenceMarks(svg));
      L.push(`${marks}svg title="${title}" tone="${tone}"`, svg, marks, "");
    }
  }
  return L.join("\n").replace(/\n+$/, "") + "\n";
}


// --------------------------------------------------------------------------- main
const [page, outArg] = process.argv.slice(2);
if (!page) die("usage: node html_to_md.mjs page.html [article.md]");

const text = readFileSync(page, "utf8");
const mainM = MAIN_RE.exec(text);
if (!mainM) die('no <main class="content"> — is this a page built by build_article.py?');
const mainHtml = mainM[1];

const metaM = /<script type="application\/json" id="article-meta">([\s\S]*?)<\/script>/.exec(text);
let meta = null;
if (metaM) {
  try { meta = JSON.parse(metaM[1]); } catch { /* fall through */ }
}
if (!meta) die("no readable article-meta block");
const title = mdInline(/<h1>([\s\S]*?)<\/h1>/.exec(text)?.[1] ?? "");
// header fields are HTML on the page and Markdown inline in the source
const kicker = mdInline(/<p class="kicker">([\s\S]*?)<\/p>/.exec(text)?.[1] ?? "");
const deck = mdInline(/<p class="deck">([\s\S]*?)<\/p>/.exec(text)?.[1] ?? "");
const footer = mdInline(/<footer class="foot">([\s\S]*?)<\/footer>/.exec(text)?.[1] ?? "");
const metaDiv = /<div class="meta">([\s\S]*?)<\/div>/.exec(text);
const chips = metaDiv ? [...metaDiv[1].matchAll(/<span>([\s\S]*?)<\/span>/g)].map(m => mdInline(m[1])) : [];
const backM = /<a class="back" href="([^"]*)"/.exec(text);

const fm = {
  title, kicker, deck, meta: chips, footer,
  index: backM ? backM[1] : "", updated: meta.updated ?? "",
};

const blocks = [];
const introM = INTRO_RE.exec(mainHtml);
if (introM) blocks.push(...introBlocks(introM[1]));
for (const sec of mainHtml.matchAll(SEC_RE)) {
  const inner = sec[1];
  const h2 = /^<h2>([\s\S]*?)<\/h2>/.exec(inner);
  if (!h2) die('a <section class="sec"> does not start with <h2>');
  blocks.push(["h2", mdInline(h2[1])]);
  for (const row of inner.slice(h2[0].length).matchAll(ROW_RE)) {
    if (row[3] !== undefined) blocks.push(["table", parseTable(row[3])]);
    else {
      blocks.push(...textBlocks(row[1]));
      blocks.push(...mediaBlocks(row[2]));
    }
  }
}

const out = outArg || page.replace(/\.[^.]+$/, "") + ".md";
writeFileSync(out, renderMd(fm, blocks));
let nCode = 0, nSvg = 0, nTables = 0, nH2 = 0;
for (const [k] of blocks) {
  if (k === "code") nCode++;
  else if (k === "svg") nSvg++;
  else if (k === "table") nTables++;
  else if (k === "h2") nH2++;
}
console.log(`wrote ${out}: ${nH2} sections, ${nCode} code panels, ${nSvg} diagrams, ${nTables} tables`);
