#!/usr/bin/env node
/** Build a tech-article page from a Markdown source file.
 *
 * Usage:
 *   node md_to_article.mjs article.md [out.html] [--plan] [--body-out body.html]
 *
 * The .md file starts with a small front matter block (--- delimited):
 *
 *   title:   page heading; backticks become <code> inside the <h1>
 *   (title / kicker / deck / meta items / footer are Markdown inline, like body text)
 *   kicker:  small label above the title
 *   deck:    one-sentence summary under the title
 *   meta:    chips under the title, one '- ' item each
 *   footer:  page footer line
 *   index:   relative link back to the article index, e.g. index.html
 *   updated: pin the article-meta updated date (YYYY-MM-DD); default: today
 *   created: creation date for the index sort (YYYY-MM-DD or "YYYY-MM-DD HH:MM"); default: none
 *
 * The body is the Markdown subset that maps onto the body fragments
 * build_article.py expects:
 *
 *   paragraphs (keep one paragraph per line), - lists, 1. ordered lists, > quotes,
 *   ## / ### headings, GFM pipe tables (\\| for a literal pipe in a cell),
 *   ```lang title="文件路径或出处"       code / prompt panels (prompt / md / text
 *                                       are the light paper panels),
 *   ```lang title="…" kind="prompt"     force the light paper panel for a code
 *                                       language,
 *   ```lang title="…" raw               hand-coloured source: the fence content is
 *                                       already-escaped HTML with <span> markup and
 *                                       is passed through verbatim,
 *   ```svg title="图 N：…" tone="light" diagram panels (raw svg / img markup),
 *   bare flags: wide, full.
 *
 * Conventions worth knowing: fence titles are carried in the HTML-escaped form
 * the page shows (a title containing " is written &quot;), a literal <br> in a
 * cell or paragraph stays a line break, and code spans that contain backticks
 * use `` `` `` delimiters with one space of padding.
 *
 * A list before the first ## becomes the intro point cards. Pairing follows the
 * same order rules as the HTML fragment path: write the explanation first, then
 * the panel it explains; a list or quote right after a panel stays in the same
 * row as its commentary.
 *
 * The rendered body is handed to build_article.py in this directory, which
 * provides the whole page chrome and keeps its WARN / ERROR checks. Front
 * matter travels to it via a temporary JSON file (--fm-json), never via
 * command-line arguments — article text must stay off the argv of child
 * processes. Node standard library only.
 */
import { existsSync, readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const HERE = dirname(fileURLToPath(import.meta.url));
const BUILD = join(HERE, "build_article.py");

const FM_KEYS = new Set(["title", "kicker", "deck", "meta", "footer", "index", "updated", "created"]);

function die(msg) {
  console.error(`md_to_article: ERROR: ${msg}`);
  process.exit(1);
}

// --------------------------------------------------------------------------- front matter
function splitFront(text) {
  const t = text.replace(/^\uFEFF/, "");
  const m = /^---[ \t]*\n([\s\S]*?)\n---[ \t]*\n/.exec(t);
  if (!m) die("file must start with a '---' front matter block, closed by another '---' line");
  return [m[1], t.slice(m[0].length)];
}

function unquote(v) {
  v = v.trim();
  if (v.length >= 2 && v[0] === v[v.length - 1] && (v[0] === '"' || v[0] === "'")) return v.slice(1, -1);
  return v;
}

function parseFront(block) {
  const fm = {};
  let key = null;
  for (const raw of block.split("\n")) {
    if (!raw.trim()) continue;
    const m = /^([A-Za-z][\w-]*)\s*:\s*(.*)$/.exec(raw);
    if (m) {
      key = m[1];
      if (!FM_KEYS.has(key)) die(`unknown front matter key '${key}' (known keys: ${[...FM_KEYS].sort().join(", ")})`);
      const val = m[2].trim();
      fm[key] = val ? unquote(val) : [];
    } else if (/^\s*-\s+/.test(raw)) {
      if (!key || !Array.isArray(fm[key])) die(`list item under '${key}' — give the key an empty value before its '- ' items`);
      fm[key].push(unquote(/^\s*-\s+(.*)$/.exec(raw)[1]));
    } else {
      die(`cannot parse front matter line: ${JSON.stringify(raw.trim())}`);
    }
  }
  if (!fm.title) die("front matter needs a title");
  if (typeof fm.meta === "string") fm.meta = [fm.meta];
  if (fm.updated && !/^\d{4}-\d{2}-\d{2}$/.test(String(fm.updated))) die("updated must look like 2026-11-19");
  if (fm.created && !/^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$/.test(String(fm.created))) die("created must look like 2026-11-19 or 2026-11-19 14:30");
  return fm;
}

// --------------------------------------------------------------------------- inline
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(text) {
  /** `code` → <code>, `` `code` `` → <code>, **bold** → <strong>, [text](url) →
   *  <a>; \` and \\ stay literal; a literal <br> stays a line break; everything
   *  else escaped. */
  const escStash = [];
  text = text.replace(/\\([`\\])/g, (m, c) => {
    escStash.push(c);
    return `\x01${escStash.length - 1}\x01`;
  });
  text = text.replace(/<br\s*\/?>/gi, "\x02");
  const codeStash = [];
  text = text.replace(/(`+)([\s\S]*?)\1/g, (m, marks, content) => {
    // a code span opens with N backticks and closes with the same run; the
    // `` `` content `` form pads with one space on each side
    let c = content;
    if (c.length >= 2 && c.startsWith(" ") && c.endsWith(" ") && c.trim()) c = c.slice(1, -1);
    codeStash.push(c);
    return `\x00${codeStash.length - 1}\x00`;
  });
  text = esc(text);
  text = text.replace(/\[([^\]]+)\]\(([^()\s]+)\)/g, (m, t, u) => `<a href="${u}">${t}</a>`);
  text = text.replace(/\*\*(.+?)\*\*/g, (m, t) => `<strong>${t}</strong>`);
  text = text.replace(/\x02/g, () => "<br>");
  text = text.replace(/\x01(\d+)\x01/g, (m, i) => esc(escStash[+i]));
  return text.replace(/\x00(\d+)\x00/g, (m, i) => `<code>${esc(codeStash[+i])}</code>`);
}

// --------------------------------------------------------------------------- blocks
function splitRow(line) {
  // cells split on | ; \| is a literal pipe (also inside code spans, as in GFM)
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells = [];
  let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; }
    else if (s[i] === "|") { cells.push(cur.trim()); cur = ""; }
    else cur += s[i];
  }
  cells.push(cur.trim());
  return cells;
}

function isBlockStart(line) {
  const s = line.replace(/^\s+/, "");
  return s.startsWith("```") || s.startsWith("#") || s.startsWith(">") || s.startsWith("|") ||
         /^\s*[-*]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line);
}

function parseBlocks(body) {
  const lines = body.split("\n");
  const blocks = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fm = /^(`{3,})\s*(.*)$/.exec(line);
    if (fm) {
      // fences may be longer than ``` when their content itself carries markdown
      // fences; the closer must be backticks-only, >= this length
      const marks = fm[1].length;
      const info = fm[2].trim();
      const close = new RegExp("^`{" + marks + ",}\\s*$");
      let j = i + 1;
      while (j < n && !close.test(lines[j])) j++;
      if (j >= n) die(`fence opened on line ${i + 1} is never closed`);
      blocks.push(["fence", [info, lines.slice(i + 1, j)]]);
      i = j + 1;
      continue;
    }
    if (line.startsWith("### ")) { blocks.push(["h3", line.slice(4).trim()]); i++; continue; }
    if (line.startsWith("## ")) { blocks.push(["h2", line.slice(3).trim()]); i++; continue; }
    if (/^#+\s/.test(line)) die("only ## and ### exist in the body — the page title belongs in front matter");
    if (line.startsWith(">")) {
      let j = i;
      while (j < n && lines[j].startsWith(">")) j++;
      const quote = lines.slice(i, j).map(l => l.replace(/^>\s?/, "").trim()).join(" ");
      blocks.push(["quote", quote]);
      i = j;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      let j = i;
      while (j < n && /^\s*[-*]\s+/.test(lines[j])) j++;
      const items = lines.slice(i, j).map(l => /^\s*[-*]\s+(.*)$/.exec(l)[1].trim());
      blocks.push(["list", ["ul", items]]);
      i = j;
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      let j = i;
      while (j < n && /^\s*\d+[.)]\s+/.test(lines[j])) j++;
      const items = lines.slice(i, j).map(l => /^\s*\d+[.)]\s+(.*)$/.exec(l)[1].trim());
      blocks.push(["list", ["ol", items]]);
      i = j;
      continue;
    }
    if (line.replace(/^\s+/, "").startsWith("|")) {
      let j = i;
      while (j < n && lines[j].replace(/^\s+/, "").startsWith("|")) j++;
      blocks.push(["table", lines.slice(i, j).map(splitRow)]);
      i = j;
      continue;
    }
    let j = i;
    while (j < n && lines[j].trim() && !isBlockStart(lines[j])) j++;
    blocks.push(["p", lines.slice(i, j).map(l => l.trim()).join(" ")]);
    i = j;
  }
  return blocks;
}

// --------------------------------------------------------------------------- emit
function parseInfo(info) {
  if (!info) die('a fence needs a language and a title, e.g. ```swift title="src/retry.ts"');
  const attrs = {};
  for (const m of info.matchAll(/([\w-]+)\s*=\s*"([^"]*)"/g)) attrs[m[1]] = m[2];
  const flags = new Set(info.replace(/"[^"]*"/g, '""').split(/\s+/).filter(Boolean));
  return [info.trim().split(/\s+/)[0].toLowerCase(), attrs, flags];
}

function emit(blocks) {
  const parts = [];
  let seenH2 = false;
  for (const [kind, payload] of blocks) {
    if (kind === "h2") {
      seenH2 = true;
      parts.push(`<h2>${inline(payload)}</h2>`);
    } else if (kind === "h3") {
      parts.push(`<h3>${inline(payload)}</h3>`);
    } else if (kind === "p") {
      parts.push(`<p>${inline(payload)}</p>`);
    } else if (kind === "list") {
      const [tag, items] = payload;
      const attr = (seenH2 || tag !== "ul") ? "" : " data-points";
      const body = items.map(it => `  <li>${inline(it)}</li>`).join("\n");
      parts.push(`<${tag}${attr}>\n${body}\n</${tag}>`);
    } else if (kind === "quote") {
      parts.push(`<blockquote><p>${inline(payload)}</p></blockquote>`);
    } else if (kind === "table") {
      const header = payload[0], rest = payload.slice(1);
      if (!rest.length || !rest[0].every(c => /^:?-{3,}:?$/.test(c))) {
        die("a table needs a GFM separator row like | --- | --- | right under the header");
      }
      const rows = rest.slice(1);
      if (rows.some(r => r.length !== header.length)) die("a table row has a different number of cells than the header");
      const th = header.map(c => `<th>${inline(c)}</th>`).join("");
      const trs = rows.map(r => "    <tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>").join("\n");
      parts.push(`<table>\n  <thead><tr>${th}</tr></thead>\n  <tbody>\n${trs}\n  </tbody>\n</table>`);
    } else if (kind === "fence") {
      const [info, flines] = payload;
      const [lang, attrs, flags] = parseInfo(info);
      const title = attrs.title ?? "";
      if (!title) die(`fence '${lang}' needs title="…" — the file path it is from, or 图 N：…`);
      if (attrs.kind && attrs.kind !== "prompt") {
        die(`fence '${lang}': only kind="prompt" exists (forces the light paper panel for a code language)`);
      }
      const kindAttr = attrs.kind ? ` data-kind="${attrs.kind}"` : "";
      // titles travel in their HTML-escaped form: write them straight back into
      // the body attribute; only a literal quote needs escaping for the parser
      const titleAttr = title.replace(/"/g, "&quot;");
      const content = flines.join("\n");
      if (lang === "svg") {
        if (!content.includes("<svg") && !content.includes("<img")) {
          die(`svg fence '${title}' contains no <svg> or <img> markup`);
        }
        const wide = flags.has("wide") ? " data-wide" : "";
        const tone = attrs.tone ?? "light";
        parts.push(`<figure data-diagram data-title="${titleAttr}" data-tone="${tone}"${wide}>${content}</figure>`);
      } else {
        const full = flags.has("full") ? " data-full" : "";
        const inner = flags.has("raw") ? content : esc(content);
        parts.push(`<pre data-lang="${lang}"${kindAttr} data-title="${titleAttr}"${full}><code>${inner}</code></pre>`);
      }
    }
  }
  return parts.join("\n");
}

// --------------------------------------------------------------------------- run
function pinDates(path, updated, created) {
  const src = readFileSync(path, "utf8");
  let out = src;
  if (updated) out = out.replace(/("updated":\s*")[^"]*"/, (m, p) => p + updated + '"');
  if (created) out = out.replace(/("updated":\s*"[^"]*")/, (m) => m + ', "created": "' + created + '"');
  if (out !== src) writeFileSync(path, out);
}

// --------------------------------------------------------------------------- main
const positional = [];
let plan = false, bodyOut = "";
const args = process.argv.slice(2);
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--plan") plan = true;
  else if (args[i] === "--body-out") bodyOut = args[++i];
  else positional.push(args[i]);
}
const [md, outArg] = positional;
if (!md) die("usage: node md_to_article.mjs article.md [out.html] [--plan] [--body-out PATH]");
if (!existsSync(BUILD)) die(`build_article.py not found next to this script: ${BUILD}`);

const text = readFileSync(md, "utf8");
const [fmBlock, bodyMd] = splitFront(text);
const fm = parseFront(fmBlock);
const body = emit(parseBlocks(bodyMd));

const out = outArg || md.replace(/\.[^.]+$/, "") + ".html";
const tmpDir = mkdtempSync(join(tmpdir(), "md2a-"));
let bodyPath = bodyOut;
if (bodyOut) {
  writeFileSync(bodyOut, body);
} else {
  bodyPath = join(tmpDir, "body.html");
  writeFileSync(bodyPath, body);
}

// front matter goes via a JSON file: article text (which can mention anything,
// including strings local process monitors kill python for) must never appear on
// a child process's command line
// header fields are Markdown inline like the body: build_article.py inserts
// them as HTML, so they are converted (and escaped) here
const fmJson = { title: inline(fm.title) };
if (fm.kicker) fmJson.kicker = inline(fm.kicker);
if (fm.deck) fmJson.deck = inline(fm.deck);
if (Array.isArray(fm.meta) && fm.meta.length) fmJson.meta = fm.meta.map(inline);
if (fm.footer) fmJson.footer = inline(fm.footer);
if (fm.index) fmJson.index = fm.index;
const fmPath = join(tmpDir, "fm.json");
writeFileSync(fmPath, JSON.stringify(fmJson));

const cmd = ["python3", BUILD, bodyPath, out, "--fm-json", fmPath];
if (plan) cmd.push("--plan");
const r = spawnSync(cmd[0], cmd.slice(1), { stdio: "inherit" });
rmSync(tmpDir, { recursive: true, force: true });
const status = r.status ?? 1;
if (status === 0 && (fm.updated || fm.created)) pinDates(out, fm.updated && String(fm.updated), fm.created && String(fm.created));
process.exitCode = status;
