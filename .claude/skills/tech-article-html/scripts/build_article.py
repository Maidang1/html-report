#!/usr/bin/env python3
"""Render an article body fragment into a full-width, side-by-side HTML page.

Usage:
  python3 build_article.py body.html out.html --title "标题" [--kicker 源码拆解] \
      [--deck "一句话摘要"] [--meta "Claude Code v2.1.325"]... [--footer "源码出处：…"] [--plan]

The body is plain semantic HTML (see SKILL.md for the conventions). This script:
  * turns <pre data-lang data-title> into code / prompt panels and
    <figure data-diagram> into diagram panels,
  * splits every <h2> section into rows: prose on the left, the panels it
    explains on the right,
  * prints warnings (and the row plan with --plan) so pairing problems are
    visible before anyone opens the page.
Standard library only. Exit code 1 when there are errors.
"""
import argparse
import datetime
import html as _html
import json
import re
import sys
from html.parser import HTMLParser

LANG = {"js": "JavaScript", "javascript": "JavaScript", "ts": "TypeScript", "typescript": "TypeScript",
        "tsx": "TSX", "jsx": "JSX", "py": "Python", "python": "Python", "rust": "Rust", "rs": "Rust",
        "go": "Go", "toml": "TOML", "yaml": "YAML", "json": "JSON", "bash": "Shell", "sh": "Shell",
        "shell": "Shell", "sql": "SQL", "java": "Java", "kotlin": "Kotlin", "swift": "Swift",
        "c": "C", "cpp": "C++", "css": "CSS", "html": "HTML", "diff": "Diff",
        "md": "Markdown", "markdown": "Markdown", "prompt": "Prompt", "text": "Message", "message": "Message"}
PROSE_LANGS = {"prompt", "md", "markdown", "text", "message"}
VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base", "embed", "track"}

warnings, errors = [], []


# --------------------------------------------------------------------------- panels
def _attrs(s):
    return {m.group(1): _html.unescape(m.group(3) if m.group(3) is not None else "")
            for m in re.finditer(r'([\w:-]+)(\s*=\s*"([^"]*)")?', s or "")}


def code_panel(attrs, inner):
    lang = (attrs.get("data-lang") or "text").lower()
    title = attrs.get("data-title", "")
    if not title:
        warnings.append(f"code block ({lang}) has no data-title — say which file / prompt it is")
    m = re.fullmatch(r"\s*<code[^>]*>(.*)</code>\s*", inner, re.S)
    inner = m.group(1) if m else inner
    if re.search(r"<(?!/?(span|br|b|strong|em)\b)[a-zA-Z/]", inner):
        errors.append(f"code block '{title or lang}' contains raw HTML tags — escape < as &lt; and & as &amp;")
    prose = attrs.get("data-kind") == "prompt" or lang in PROSE_LANGS
    n_lines = inner.strip("\n").count("\n") + 1
    if prose and n_lines > 30 and "data-full" not in attrs:
        warnings.append(f"long prompt panel '{title or lang}' ({n_lines} lines) — split it by its own sections "
                        "and explain each beside it (SKILL.md: 大段 Prompt 的解读), or mark data-full")
    hand_tokenised = "<span" in inner
    code_cls = "" if prose or hand_tokenised else f' class="language-{lang}"'
    kind = "panel-prompt" if prose else "panel-code"
    safe_title = _html.escape(title, quote=True)
    return (f'<figure class="panel {kind}">'
            f'<figcaption class="panel-head"><span class="panel-title" title="{safe_title}">{_html.escape(title)}</span>'
            f'<span class="panel-lang">{LANG.get(lang, lang)}</span>'
            f'<button type="button" class="panel-btn" data-copy>复制</button></figcaption>'
            f'<div class="panel-body"><pre><code{code_cls}>{inner}</code></pre></div>'
            f'<button type="button" class="panel-expand" data-expand hidden>展开全部</button></figure>')


def diagram_panel(attrs, inner):
    if "<svg" not in inner and "<img" not in inner:
        errors.append(f"diagram '{attrs.get('data-title', '')}' has no <svg> or <img>")
    tone = attrs.get("data-tone", "light")
    wide = " wide" if "data-wide" in attrs else ""
    title = _html.escape(attrs.get("data-title", ""))
    return (f'<figure class="panel panel-diagram {tone}{wide}">'
            f'<figcaption class="panel-head"><span class="panel-title">{title}</span>'
            f'<button type="button" class="panel-btn" data-zoom>放大</button></figcaption>'
            f'<div class="panel-body">{inner.strip()}</div></figure>')


def convert_panels(body):
    body = re.sub(r"<pre(\s[^>]*)?>(.*?)</pre>", lambda m: code_panel(_attrs(m.group(1)), m.group(2)), body, flags=re.S)
    body = re.sub(r"<figure(\s[^>]*\bdata-diagram\b[^>]*)>(.*?)</figure>",
                  lambda m: diagram_panel(_attrs(m.group(1)), m.group(2)), body, flags=re.S)
    return body


# --------------------------------------------------------------------------- rows
class _Top(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.lines = [0] + [m.end() for m in re.finditer("\n", text)]
        self.depth, self.blocks, self.cur = 0, [], None

    def _pos(self):
        ln, col = self.getpos()
        return self.lines[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            self.cur = [tag, dict(attrs), self._pos()]
        if tag not in VOID:
            self.depth += 1
        elif self.depth == 0:
            self.blocks.append((tag, dict(attrs), self.cur[2], self.rawdata.index(">", self._pos()) + 1))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        self.depth -= 1
        if self.depth == 0 and self.cur:
            self.blocks.append((self.cur[0], self.cur[1], self.cur[2], self.rawdata.index(">", self._pos()) + 1))
            self.cur = None


def top_blocks(body):
    p = _Top(body)
    p.feed(body)
    p.close()
    return [(t, a, body[s:e]) for t, a, s, e in p.blocks]


def kind(tag, attrs):
    cls = set((attrs.get("class") or "").split())
    if tag in ("h2", "h3"):
        return tag
    if tag == "table" or "data-wide-row" in attrs:
        return "wide"
    if tag == "figure" and "panel" in cls:
        return "media"
    if tag in ("blockquote", "ul", "ol"):
        return "aside"  # may trail the panel it explains
    return "prose"


def build_rows(body):
    sections = [{"h2": None, "rows": []}]
    row = None

    def new_row(k="split"):
        nonlocal row
        row = {"kind": k, "text": [], "media": []}
        sections[-1]["rows"].append(row)

    for tag, attrs, html in top_blocks(body):
        k = kind(tag, attrs)
        if k == "h2":
            sections.append({"h2": html, "rows": []})
            row = None
        elif k == "h3":
            new_row()
            row["text"].append(html)
        elif k == "wide":
            new_row("wide")
            row["text"].append(html)
            row = None
        elif k == "media":
            if row is None or row["kind"] == "wide":
                new_row()
            row["media"].append(html)
        else:
            # prose after a panel opens a new row; a list or quote right after it stays as its commentary
            if row is None or row["kind"] == "wide" or (row["media"] and k == "prose"):
                new_row()
            row["text"].append(html)
    return sections


def intro_html(texts):
    out = []
    for h in texts:
        if re.match(r"\s*<ul[^>]*\bdata-points\b", h):
            cards = []
            for it in re.findall(r"<li>(.*?)</li>", h, re.S):
                m = re.match(r"\s*<code>(.*?)</code>\s*[：:]\s*(.*)", it, re.S)
                cards.append(f'<li><code class="fn-name">{m.group(1)}</code><span>{m.group(2)}</span></li>' if m else f"<li>{it}</li>")
            out.append('<ul class="points">' + "".join(cards) + "</ul>")
        else:
            out.append(h)
    return "\n".join(out)


def render_rows(body):
    out = []
    for sec in build_rows(body):
        if sec["h2"] is None:
            texts = [x for r in sec["rows"] for x in r["text"]]
            if any(r["media"] for r in sec["rows"]):
                warnings.append("panels before the first <h2> are dropped from the intro — move them into a section")
            if texts:
                out.append('<section class="intro">' + intro_html(texts) + "</section>")
            continue
        out.append('<section class="sec">' + sec["h2"])
        for r in sec["rows"]:
            if r["kind"] == "wide":
                out.append('<div class="row-wide">' + "\n".join(r["text"]) + "</div>")
                continue
            cls = "row" if r["media"] else "row row-only-text"
            out.append(f'<div class="{cls}"><div class="row-text">' + "\n".join(r["text"]) +
                       '</div><div class="row-media">' + "\n".join(r["media"]) + "</div></div>")
        out.append("</section>")
    return "\n".join(out)


def plain(s, n=26):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", _html.unescape(s))).strip()
    return t[:n] + ("…" if len(t) > n else "")


def lint_and_plan(body, show_plan):
    secs = build_rows(body)
    if len(secs) == 1:
        errors.append("no <h2> sections")
    for i, sec in enumerate(secs):
        if show_plan:
            print(f"§{i} {plain(sec['h2'] or '(intro)', 40)}")
        for r in sec["rows"]:
            if show_plan:
                media = [plain(re.search(r'class="panel-title"[^>]*>(.*?)</span>', m).group(1), 30) for m in r["media"]]
                print(f"   [{r['kind']}] {' | '.join(plain(t) for t in r['text'])}  →  {media}")
            if sec["h2"] and r["kind"] == "split":
                text_len = sum(len(plain(t, 10**6)) for t in r["text"])
                if r["media"] and not r["text"]:
                    warnings.append(f"§{i}: panel with no explanation beside it — add a sentence before it")
                if len(r["media"]) >= 4:
                    warnings.append(f"§{i}: one row holds {len(r['media'])} panels — split with <h3> or prose")
                if r["media"] and text_len > 900:
                    warnings.append(f"§{i}: {text_len} chars of prose beside one panel group — split the row")
    if "<style" in body or "data-hw" in body:
        warnings.append("body carries its own styling — the page style is fixed by this script")


# --------------------------------------------------------------------------- page
CSS = r"""
:root {
  --bg: #f5f4ed; --surface: #faf9f5; --sunken: #f0eee6;
  --ink: #141413; --ink-2: #3d3d3a; --muted: #504e49; --faint: #6b6a64; --tick: #85837b;
  --line: #e8e6dc; --line-strong: #d8d5c8;
  --accent: #1B365D; --accent-soft: #EEF2F7;
  --code-bg: #181816; --code-head: #22221f; --code-ink: #ece8da; --code-line: #30302e;
  --prompt-bg: #faf9f5; --prompt-ink: #141413; --diagram-bg: #f5f4ed;
  --pr-h: #1B365D; --pr-b: #141413;
  --hl: #E4ECF5; --hl-amber: #f0e0d8;
  --shadow: 0 1px 2px rgb(20 20 19 / 0.04), 0 8px 24px rgb(20 20 19 / 0.05);
  --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    "Noto Sans SC", "Noto Sans CJK SC", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --surface: #1e1d1b; --sunken: #282724;
    --ink: #f2f0ea; --ink-2: #dad6ca; --muted: #a8a49a; --faint: #7a776e; --tick: #6e6c64;
    --line: #2e2d2a; --line-strong: #403f3c;
    --accent: #9bb8dc; --accent-soft: rgb(155 184 220 / 0.12);
    --code-bg: #10100f; --code-head: #191917; --code-line: #2a2926;
    --prompt-bg: #1a1917; --prompt-ink: #dedad0; --diagram-bg: #f5f4ed;
    --pr-h: #9bb8dc; --pr-b: #f2f0ea;
    --hl: rgb(155 184 220 / 0.15); --hl-amber: rgb(217 180 138 / 0.16);
    --shadow: 0 1px 2px rgb(0 0 0 / 0.32), 0 8px 24px rgb(0 0 0 / 0.26);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --surface: #1e1d1b; --sunken: #282724;
  --ink: #f2f0ea; --ink-2: #dad6ca; --muted: #a8a49a; --faint: #7a776e; --tick: #6e6c64;
  --line: #2e2d2a; --line-strong: #403f3c;
  --accent: #9bb8dc; --accent-soft: rgb(155 184 220 / 0.12);
  --code-bg: #10100f; --code-head: #191917; --code-line: #2a2926;
  --prompt-bg: #1a1917; --prompt-ink: #dedad0; --diagram-bg: #f5f4ed;
  --pr-h: #9bb8dc; --pr-b: #f2f0ea;
  --hl: rgb(155 184 220 / 0.15); --hl-amber: rgb(217 180 138 / 0.16);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.32), 0 8px 24px rgb(0 0 0 / 0.26);
  color-scheme: dark;
}

* { box-sizing: border-box; }
html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.75 var(--sans); -webkit-font-smoothing: antialiased;
  text-autospace: ideograph-alpha ideograph-numeric;
}
a { color: var(--accent); text-decoration: none; }
@media (hover: hover) { a:hover { text-decoration: underline; text-underline-offset: 3px; } }
strong { font-weight: 650; color: var(--ink); }
:not(pre) > code {
  padding: 0.12em 0.38em; border-radius: 4px;
  background: var(--sunken); color: var(--ink);
  font: 0.86em/1 var(--mono); letter-spacing: 0;
  -webkit-box-decoration-break: clone; box-decoration-break: clone;
}

.skip {
  position: absolute; left: 16px; top: -48px; z-index: 90; padding: 8px 14px; border-radius: 8px;
  background: var(--surface); color: var(--ink); box-shadow: var(--shadow); font-size: 14px;
}
.skip:focus { top: 12px; }
.sr-only { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* progress + section ticks */
.progress { position: fixed; inset: 0 0 auto 0; height: 2px; z-index: 60; background: var(--accent); transform-origin: 0 50%; transform: scaleX(0); }
.ticks { position: fixed; left: 0; top: 50%; z-index: 50; transform: translateY(-50%); padding: 16px 20px 16px 26px; }
.ticks-list { display: flex; flex-direction: column; gap: 2px; }
.ticks-list a {
  display: block; width: 16px; height: 10px; padding: 4px 0;
  background: var(--tick); background-clip: content-box; border-radius: 2px;
  transition: width .16s ease, background-color .16s ease;
}
.ticks-list a.lv3 { width: 9px; }
.ticks-list a:focus-visible, .ticks-list a.is-hover { width: 24px; background-color: var(--ink-2); outline: none; text-decoration: none; }
.ticks-list a.is-active { width: 28px; background-color: var(--ink); }
.ticks-card {
  position: absolute; left: 66px; width: 320px; transform: translateY(-50%);
  padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px;
  background: var(--surface); box-shadow: var(--shadow); pointer-events: none;
}
.ticks-card .num { font: 600 11px/1 var(--mono); color: var(--accent); letter-spacing: .06em; }
.ticks-card strong { display: block; margin-top: 4px; font-size: 14px; line-height: 1.45; }
.ticks-card p { margin: 6px 0 0; color: var(--muted); font-size: 13px; line-height: 1.55;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ticks-card p:empty { display: none; }

/* page */
.page { max-width: 1760px; margin: 0 auto; padding: 0 56px 120px 96px; }
.masthead { padding: 56px 0 40px; border-bottom: 1px solid var(--line); }
.masthead-top { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.back { font-size: 13px; color: var(--muted); }
@media (hover: hover) { .back:hover { color: var(--accent); text-decoration: none; } }
.masthead-left { display: flex; align-items: center; gap: 14px; }
.kicker { margin: 0; font: 600 12px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--accent); }
.theme-toggle {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--muted); font: 13px var(--sans); cursor: pointer;
}
@media (hover: hover) { .theme-toggle:hover { color: var(--ink); border-color: var(--line-strong); } }
h1 {
  max-width: 1040px; margin: 18px 0 14px;
  font-size: clamp(28px, 3.2vw, 44px); line-height: 1.18; font-weight: 700; letter-spacing: -0.01em;
  text-wrap: balance;
}
h1 code { padding: 0; background: none; font: inherit; font-family: var(--mono); font-size: 0.92em; color: var(--accent); }
.deck { max-width: 820px; margin: 0; color: var(--ink-2); font-size: 18px; line-height: 1.65; }
.meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px; }
.meta span {
  padding: 3px 10px; border: 1px solid var(--line); border-radius: 999px;
  color: var(--muted); font: 12.5px/1.6 var(--sans);
}

/* sections */
.content { counter-reset: sec; }
.sec { padding-top: 64px; }
.sec + .sec { margin-top: 24px; border-top: 1px solid var(--line); }
.sec > h2 {
  counter-increment: sec; position: relative;
  max-width: 1040px; margin: 0 0 28px;
  font-size: clamp(22px, 1.9vw, 28px); line-height: 1.35; font-weight: 700; letter-spacing: -0.005em;
  text-wrap: balance; scroll-margin-top: 28px;
}
.sec > h2::before {
  content: counter(sec, decimal-leading-zero); display: block; margin-bottom: 10px;
  font: 600 12px/1 var(--mono); letter-spacing: .1em; color: var(--accent);
}
h2 code, h3 code { padding: 0; background: none; color: var(--accent); font-size: 0.86em; font-weight: 600; }
h3 { margin: 0 0 10px; font-size: 18px; line-height: 1.45; font-weight: 650; scroll-margin-top: 28px; }
.anchor { margin-left: 8px; color: var(--muted); font-weight: 400; opacity: 0; }
@media (hover: hover) { h2:hover .anchor, h3:hover .anchor { opacity: 1; text-decoration: none; } }
.anchor:focus-visible { opacity: 1; text-decoration: none; }

.intro { padding-top: 40px; }
.intro > p { max-width: 760px; margin: 0 0 20px; color: var(--ink-2); }
.points { display: grid; max-width: 1040px; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin: 8px 0 0; padding: 0; list-style: none; }
.points li { padding: 16px 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--ink-2); font-size: 14.5px; line-height: 1.65; }
.points li > code.fn-name { display: inline-block; margin-bottom: 8px; padding: 0; background: none; color: var(--accent); font-size: 13px; font-weight: 600; }
.points li > span { display: block; }

/* text | media rows */
.row {
  display: grid; grid-template-columns: minmax(340px, min(40%, 600px)) minmax(0, 1fr);
  gap: 0 clamp(32px, 3.5vw, 56px); align-items: start; margin: 0 0 40px;
}
.row + .row:has(> .row-text > h3:first-child) { margin-top: 8px; padding-top: 28px; border-top: 1px dashed var(--line); }
.row-text > :first-child { margin-top: 0; }
.row-text > :last-child { margin-bottom: 0; }
.row-text p, .row-text ul, .row-text ol { margin: 0 0 16px; color: var(--ink-2); }
.row-text ul, .row-text ol { padding-left: 1.3em; }
.row-text li { margin: 6px 0; }
.row-text li::marker { color: var(--muted); }
.row-text blockquote {
  margin: 0 0 16px; padding: 12px 16px; border-left: 2px solid var(--accent);
  border-radius: 0 8px 8px 0; background: var(--accent-soft); color: var(--ink-2); font-size: 15px;
}
.row-text blockquote p { margin: 0; }
.row-media { position: sticky; top: 24px; display: grid; gap: 20px; min-width: 0; }
.row-only-text { grid-template-columns: minmax(0, 760px); }
.row-only-text > .row-media { display: none; }
.row-wide { display: block; margin: 0 0 48px; }

/* panels */
.panel { margin: 0; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--surface); box-shadow: var(--shadow); position: relative; }
.panel-head {
  display: flex; align-items: center; gap: 10px; min-height: 38px; padding: 6px 8px 6px 14px;
  border-bottom: 1px solid var(--line); font: 12px/1.4 var(--mono); color: var(--muted);
}
.panel-title { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.panel-lang { flex: none; color: var(--muted); }
.panel-btn {
  flex: none; height: 26px; padding: 0 10px; border: 1px solid transparent; border-radius: 6px;
  background: none; color: inherit; font: 12px var(--sans); cursor: pointer;
}
@media (hover: hover) { .panel-btn:hover { border-color: var(--line); color: var(--ink); } }
.panel-body { max-height: 560px; overflow: auto; }
.panel-body:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.panel.is-collapsible:not(.expanded) .panel-body {
  -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 36px), transparent 100%);
  mask-image: linear-gradient(to bottom, #000 calc(100% - 36px), transparent 100%);
}
.panel.expanded .panel-body { max-height: none; }
.panel pre { margin: 0; padding: 16px 18px; font: 12.5px/1.7 var(--mono); letter-spacing: 0; tab-size: 4; }
.panel pre code { font: inherit; }
.panel-expand {
  display: block; width: 100%; height: 34px; border: 0; border-top: 1px solid var(--line);
  background: var(--surface); color: var(--accent); font: 12.5px var(--sans); cursor: pointer;
}
.panel-expand[hidden] { display: none; }

.panel-code { background: var(--code-bg); border-color: var(--code-line); }
.panel-code .panel-head { background: var(--code-head); border-color: var(--code-line); color: #a8a49a; }
.panel-code .panel-lang { color: #a8a49a; }
@media (hover: hover) { .panel-code .panel-btn:hover { border-color: #403f3c; color: #ece8da; } }
.panel-code .panel-body:focus-visible { outline-color: #9bb8dc; }
.panel-code .panel-expand { background: var(--code-head); border-color: var(--code-line); color: #9bb8dc; }
.panel-code pre { color: var(--code-ink); white-space: pre; }
.panel-code .kw { color: #ff7b72; }
.panel-code .fn { color: #d2a8ff; }
.panel-code .str { color: #a5d6ff; }
.panel-code :is(.cm, .cmt) { color: #a8a49a; }
.panel-code .num { color: #79c0ff; }
.panel-code :is(.ty, .typ) { color: #ffa657; }
.panel-code :is(.hl-line, .hl-amber) { display: inline-block; min-width: 100%; margin: 0 -18px; padding: 0 18px; box-sizing: content-box; }
.panel-code .hl-line { background: rgb(56 139 253 / 0.16); box-shadow: inset 2px 0 0 #58a6ff; }
.panel-code .hl-amber { background: rgb(210 153 34 / 0.18); box-shadow: inset 2px 0 0 #d29922; }

/* highlight.js tokens for plain code (hand-tokenised code keeps its own spans) */
.panel-code .hljs-keyword, .panel-code .hljs-selector-tag, .panel-code .hljs-doctag { color: #ff7b72; }
.panel-code .hljs-title, .panel-code .hljs-title.function_, .panel-code .hljs-section { color: #d2a8ff; }
.panel-code .hljs-string, .panel-code .hljs-regexp, .panel-code .hljs-template-variable { color: #a5d6ff; }
.panel-code .hljs-comment, .panel-code .hljs-quote, .panel-code .hljs-meta { color: #a8a49a; }
.panel-code .hljs-number, .panel-code .hljs-literal, .panel-code .hljs-attr, .panel-code .hljs-variable, .panel-code .hljs-property, .panel-code .hljs-symbol { color: #79c0ff; }
.panel-code .hljs-type, .panel-code .hljs-built_in, .panel-code .hljs-title.class_, .panel-code .hljs-params { color: #ffa657; }
.panel-code .hljs-addition { color: #aff5b4; background: rgb(46 160 67 / 0.15); }
.panel-code .hljs-deletion { color: #ffdcd7; background: rgb(248 81 73 / 0.15); }

.panel-prompt { background: var(--prompt-bg); }
.panel-prompt .panel-head { background: var(--sunken); }
.panel-prompt pre { color: var(--prompt-ink); white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12.5px; line-height: 1.75; }
.panel-prompt .pr-h { color: var(--pr-h); font-weight: 700; }
.panel-prompt .pr-b { color: var(--pr-b); font-weight: 650; }
.panel-prompt :is(.cm, .cmt) { color: var(--muted); }
.panel-prompt .kw, .panel-prompt .fn, .panel-prompt .num, .panel-prompt :is(.ty, .typ) { color: inherit; }
.panel-prompt .str { color: inherit; }
.panel-prompt :is(.hl-line, .hl-amber) { display: inline; padding: 1px 0; border-radius: 3px; -webkit-box-decoration-break: clone; box-decoration-break: clone; }
.panel-prompt .hl-line { background: var(--hl); }
.panel-prompt .hl-amber { background: var(--hl-amber); }

.panel-diagram .panel-body { max-height: none; padding: 16px; cursor: zoom-in; }
.panel-diagram.light .panel-body {
  background:
    linear-gradient(to right, var(--diagram-bg) 30%, transparent) left / 32px 100% no-repeat local,
    linear-gradient(to left, var(--diagram-bg) 30%, transparent) right / 32px 100% no-repeat local,
    radial-gradient(farthest-side at 0 50%, rgb(0 0 0 / 0.14), transparent) left / 14px 100% no-repeat scroll,
    radial-gradient(farthest-side at 100% 50%, rgb(0 0 0 / 0.14), transparent) right / 14px 100% no-repeat scroll,
    var(--diagram-bg);
}
.panel-diagram.dark { background: #0c1220; border-color: #1e293b; }
.panel-diagram.dark .panel-head { background: #0f172a; border-color: #1e293b; color: #94a3b8; }
.panel-diagram svg { display: block; width: 100%; height: auto; min-width: 600px; letter-spacing: 0; }
.panel-diagram.light svg { font-family: var(--sans); }
.panel-diagram.wide svg { min-width: 920px; }
.zoom-dialog { width: calc(100% - 48px); max-width: none; height: calc(100% - 48px); max-height: none; padding: 0; color: var(--ink); }
.zoom-dialog[open] { display: flex; flex-direction: column; }
.zoom-dialog::backdrop { background: rgb(0 0 0 / 0.55); }
.zoom-dialog .panel-body { flex: 1; min-height: 0; overflow: auto; overscroll-behavior: contain; cursor: zoom-out; }
.zoom-dialog :is(svg, img) { display: block; width: 100%; height: 100%; min-width: 0; object-fit: contain; }

/* table */
.row-wide .table-wrap {
  overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);
  /* edge shadows appear only while more columns sit past the scroll edge */
  background:
    linear-gradient(to right, var(--surface) 30%, transparent) left / 40px 100% no-repeat local,
    linear-gradient(to left, var(--surface) 30%, transparent) right / 40px 100% no-repeat local,
    radial-gradient(farthest-side at 0 50%, color-mix(in srgb, var(--ink) 30%, transparent), transparent) left / 16px 100% no-repeat scroll,
    radial-gradient(farthest-side at 100% 50%, color-mix(in srgb, var(--ink) 30%, transparent), transparent) right / 16px 100% no-repeat scroll,
    var(--surface);
}
table { width: 100%; min-width: 880px; border-collapse: collapse; font-size: 13.5px; line-height: 1.6; }
th, td { padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; color: var(--ink-2); }
th { position: sticky; top: 0; background: var(--sunken); color: var(--ink); font-weight: 650; font-size: 13px; }
tr:last-child td { border-bottom: 0; }
td:first-child { position: sticky; left: 0; z-index: 1; background: var(--surface); color: var(--ink); white-space: nowrap; box-shadow: 1px 0 0 var(--line); }
th:first-child { position: sticky; left: 0; z-index: 2; box-shadow: 1px 0 0 var(--line); }
td code, th code { white-space: normal; }

.foot { margin-top: 80px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }

/* stack once the media column would drop below ~634px: a 600px-min diagram plus its padding */
@media (max-width: 1320px) {
  .row { display: block; }
  .row-media { position: static; margin-top: 18px; }
}
@media (max-width: 1100px) {
  .ticks { padding-left: 16px; padding-right: 12px; }
  .page { padding: 0 28px 96px 72px; }
}
@media (max-width: 640px) {
  .ticks { display: none; }
  table { min-width: 680px; }
  td:first-child, th:first-child { position: static; box-shadow: none; }
  td:first-child { white-space: normal; }
  .page { padding: 0 16px 72px; }
  .masthead { padding-top: 32px; }
  .deck { font-size: 16px; }
  .panel-diagram svg { min-width: 560px; }
}
@media print {
  .ticks, .progress, .theme-toggle, .panel-btn, .panel-expand { display: none !important; }
  .page { padding: 0; }
  .row { display: block; }
  .row-media { position: static; }
  .panel-body { max-height: none; }
}
@media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } * { transition: none !important; } }
"""


def rem_fonts(css):
    """Font sizes in rem, so the reader's browser font-size setting still applies."""
    to_rem = lambda m: f"{float(m.group(1)) / 16:g}rem"
    return re.sub(r"(?<![\w-])(font(?:-size)?\s*:[^;{}]*)",
                  lambda d: re.sub(r"(\d*\.?\d+)px", to_rem, d.group(1)), css)


CSS = rem_fonts(CSS)

JS = r"""
(function () {
  var root = document.documentElement;
  // theme
  var tg = document.querySelector('[data-theme-toggle]');
  if (tg) tg.addEventListener('click', function () {
    var cur = root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('tech-article-theme', next); } catch (e) {}
  });

  // heading ids + anchors
  var used = {};
  function slug(t) {
    var s = t.trim().toLowerCase().replace(/[\s　]+/g, '-').replace(/[^\p{L}\p{N}\-_]/gu, '').replace(/-+/g, '-').replace(/^-|-$/g, '') || 'section';
    var b = s, i = 2; while (used[s]) s = b + '-' + i++; used[s] = 1; return s;
  }
  var heads = Array.prototype.slice.call(document.querySelectorAll('.content h2, .content h3'));
  var labels = heads.map(function (h) { return h.textContent.trim(); });
  heads.forEach(function (h, i) {
    h.id = h.id || slug(labels[i]);
    var a = document.createElement('a'); a.className = 'anchor'; a.href = '#' + h.id; a.textContent = '#'; a.setAttribute('aria-label', '链接到本节');
    h.appendChild(a);
  });

  // reading time + section count
  var content = document.querySelector('.content');
  var text = content.innerText || '';
  var cjk = (text.match(/[㐀-鿿]/g) || []).length;
  var words = (text.replace(/[㐀-鿿]/g, ' ').match(/[A-Za-z0-9_]+/g) || []).length;
  var rt = document.querySelector('[data-reading-time]');
  var preset = null;
  try { preset = JSON.parse(document.getElementById('article-meta').textContent).minutes; } catch (e) {}
  if (rt) rt.textContent = '约 ' + (preset || Math.max(1, Math.round(cjk / 400 + words / 220))) + ' 分钟';
  var sc = document.querySelector('[data-sections]');
  if (sc) sc.textContent = document.querySelectorAll('.content .sec > h2').length + ' 个章节';

  // tables scroll inside a frame
  content.querySelectorAll('table').forEach(function (t) {
    var w = document.createElement('div'); w.className = 'table-wrap';
    t.parentNode.insertBefore(w, t); w.appendChild(t);
  });

  // panels: copy, expand, zoom
  var status = document.querySelector('[data-status]'), dlg = document.querySelector('.zoom-dialog');
  document.querySelectorAll('.panel').forEach(function (p) {
    var body = p.querySelector('.panel-body'), more = p.querySelector('[data-expand]');
    // a code panel that scrolls needs a keyboard stop, or its hidden lines are pointer-only
    if (p.querySelector('pre') && (body.scrollWidth > body.clientWidth || body.scrollHeight > body.clientHeight)) {
      body.tabIndex = 0; body.setAttribute('role', 'region');
      body.setAttribute('aria-label', p.querySelector('.panel-title').textContent);
    }
    if (more && body.scrollHeight > body.clientHeight + 8) {
      p.classList.add('is-collapsible');
      more.hidden = false;
      more.addEventListener('click', function () {
        var on = p.classList.toggle('expanded');
        more.textContent = on ? '收起' : '展开全部';
        if (!on) p.scrollIntoView({ block: 'nearest' });
      });
    }
    var copy = p.querySelector('[data-copy]');
    if (copy) copy.addEventListener('click', function () {
      var txt = p.querySelector('pre').innerText;
      var report = function (ok) {
        var msg = ok ? '已复制' : '复制失败，请手动选中复制';
        copy.textContent = msg; status.textContent = msg;
        setTimeout(function () { copy.textContent = '复制'; }, ok ? 1400 : 4000);
      };
      var legacy = function () {
        var ta = document.createElement('textarea'), ok = false;
        ta.value = txt; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { ok = document.execCommand('copy'); } catch (e) {}
        ta.remove(); copy.focus(); return ok;
      };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(txt).then(function () { report(true); }, function () { report(legacy()); });
      else report(legacy());
    });
    var zoom = p.querySelector('[data-zoom]');
    if (zoom) {
      var open = function () {
        dlg.className = 'panel panel-diagram zoom-dialog ' + (p.classList.contains('dark') ? 'dark' : 'light');
        dlg.querySelector('.panel-title').textContent = p.querySelector('.panel-title').textContent;
        dlg.querySelector('.panel-body').innerHTML = body.innerHTML;
        dlg.showModal();
      };
      zoom.addEventListener('click', open);
      body.addEventListener('click', open);
    }
  });
  dlg.querySelector('[data-zoom-close]').addEventListener('click', function () { dlg.close(); });
  dlg.querySelector('.panel-body').addEventListener('click', function () { dlg.close(); });

  // syntax colours for plain code blocks
  if (window.hljs) document.querySelectorAll('.panel-code code[class^="language-"]').forEach(function (el) {
    try { window.hljs.highlightElement(el); } catch (e) {}
  });

  // section ticks with hover card
  var list = document.querySelector('.ticks-list'), card = document.querySelector('.ticks-card');
  var canHover = matchMedia('(hover: hover)').matches;
  var secNo = 0, subNo = 0, nums = heads.map(function (h) {
    if (h.tagName === 'H2') { subNo = 0; return ('0' + (++secNo)).slice(-2); }
    return ('0' + secNo).slice(-2) + '.' + (++subNo);
  });
  function snippet(h) {
    var scope = h.tagName === 'H2' ? h.parentElement : h.closest('.row-text');
    var p = scope && scope.querySelector('p, li');
    var t = p ? p.textContent.replace(/\s+/g, ' ').trim() : '';
    return t.length > 120 ? t.slice(0, 120) + '…' : t;
  }
  var links = heads.map(function (h, i) {
    var a = document.createElement('a');
    a.href = '#' + h.id; a.className = h.tagName === 'H3' ? 'lv3' : 'lv2'; a.setAttribute('aria-label', labels[i]);
    var show = function () {
      card.querySelector('.num').textContent = nums[i];
      card.querySelector('strong').textContent = labels[i];
      card.querySelector('p').textContent = snippet(h);
      card.style.top = (a.offsetTop + a.offsetHeight / 2) + 'px';
      card.hidden = false;
    };
    a.addEventListener('mouseenter', function () { if (canHover) a.classList.add('is-hover'); show(); });
    a.addEventListener('mouseleave', function () { a.classList.remove('is-hover'); });
    a.addEventListener('focus', show);
    a.addEventListener('blur', function () { card.hidden = true; });
    list.appendChild(a); return a;
  });
  list.addEventListener('mouseleave', function () { card.hidden = true; });
  var bar = document.querySelector('.progress');
  function onScroll() {
    var y = innerHeight * 0.25, act = 0;
    heads.forEach(function (h, i) { if (h.getBoundingClientRect().top < y) act = i; });
    links.forEach(function (a, i) { a.classList.toggle('is-active', i === act); });
    var max = document.documentElement.scrollHeight - innerHeight;
    bar.style.transform = 'scaleX(' + (max > 0 ? scrollY / max : 0) + ')';
  }
  addEventListener('scroll', onScroll, { passive: true }); onScroll();
})();
"""


def page(*, title, kicker, deck, meta, footer, rows, lang, index, sections, chars, minutes):
    plain_title = re.sub(r"<[^>]+>", "", title)
    chips = "".join(f"<span>{m}</span>" for m in meta) + "<span data-reading-time></span><span data-sections></span>"
    head_kicker = f'<p class="kicker">{kicker}</p>' if kicker else ""
    back = f'<a class="back" href="{_html.escape(index, quote=True)}">← 全部文章</a>' if index else ""
    meta_json = json.dumps({
        "title": plain_title, "title_html": title, "kicker": kicker, "deck": plain(deck, 10**6) if deck else "",
        "meta": [plain(m, 10**6) for m in meta], "sections": sections, "chars": chars, "minutes": minutes,
        "updated": datetime.date.today().isoformat(),
    }, ensure_ascii=False).replace("</", "<\\/")
    deck_html = f'<p class="deck">{deck}</p>' if deck else ""
    desc = _html.escape(plain(deck, 160) if deck else plain_title, quote=True)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{plain_title}</title>
<meta name="description" content="{desc}">
<script type="application/json" id="article-meta">{meta_json}</script>
<script>(function(){{try{{var t=localStorage.getItem('tech-article-theme');if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t)}}catch(e){{}}}})();</script>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#content">跳到正文</a>
<div class="progress" aria-hidden="true"></div>
<nav class="ticks" aria-label="目录">
  <div class="ticks-list"></div>
  <div class="ticks-card" role="tooltip" hidden><span class="num"></span><strong></strong><p></p></div>
</nav>
<div class="page">
  <header class="masthead">
    <div class="masthead-top">
      <div class="masthead-left">{back}{head_kicker}</div>
      <button type="button" class="theme-toggle" data-theme-toggle aria-label="切换明暗主题">◐ 主题</button>
    </div>
    <h1>{title}</h1>
    {deck_html}
    <div class="meta">{chips}</div>
  </header>
  <main class="content" id="content">
{rows}
  </main>
  <footer class="foot">{footer}</footer>
</div>
<dialog class="panel panel-diagram zoom-dialog" aria-labelledby="zoom-title">
  <div class="panel-head"><span class="panel-title" id="zoom-title"></span><button type="button" class="panel-btn" data-zoom-close>关闭</button></div>
  <div class="panel-body"></div>
</dialog>
<div class="sr-only" role="status" data-status></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>{JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("body")
    ap.add_argument("out")
    ap.add_argument("--title", default="", help="may contain <code>")
    ap.add_argument("--fm-json", default="",
                    help="read title/kicker/deck/meta/footer/index from a JSON file; keeps article text "
                         "off the command line (some machines kill processes over argv contents)")
    ap.add_argument("--kicker", default="")
    ap.add_argument("--deck", default="", help="one-sentence summary under the title")
    ap.add_argument("--meta", action="append", default=[], help="chip under the title; repeatable")
    ap.add_argument("--footer", default="")
    ap.add_argument("--lang", default="zh-CN")
    ap.add_argument("--plan", action="store_true", help="print the section/row plan")
    ap.add_argument("--index", default="", help="relative link to the article index, e.g. index.html")
    a = ap.parse_args()
    if a.fm_json:
        with open(a.fm_json, encoding="utf-8") as f:
            fmj = json.load(f)
        a.title = fmj.get("title") or a.title
        a.kicker = fmj.get("kicker") or a.kicker
        a.deck = fmj.get("deck") or a.deck
        a.meta = fmj.get("meta") or a.meta
        a.footer = fmj.get("footer") or a.footer
        a.index = fmj.get("index") or a.index
    if not a.title:
        ap.error("--title is required (or give --fm-json with a title)")

    body = convert_panels(open(a.body, encoding="utf-8").read())
    lint_and_plan(body, a.plan)
    sections = [plain(sec["h2"], 10**6) for sec in build_rows(body) if sec["h2"]]
    chars = len(re.sub(r"\s+", "", plain(re.sub(r"<pre.*?</pre>|<svg.*?</svg>", "", body, flags=re.S), 10**7)))
    # same estimate as the page script: ~400 CJK chars or ~220 latin words per minute, code included
    full = plain(re.sub(r"<svg.*?</svg>", "", body, flags=re.S), 10**7)
    cjk = len(re.findall(r"[\u3400-\u9fff]", full))
    words = len(re.findall(r"[A-Za-z0-9_]+", re.sub(r"[\u3400-\u9fff]", " ", full)))
    minutes = max(1, round(cjk / 400 + words / 220))
    html = page(title=a.title, kicker=a.kicker, deck=a.deck, meta=a.meta, footer=a.footer,
                rows=render_rows(body), lang=a.lang, index=a.index, sections=sections, chars=chars, minutes=minutes)
    open(a.out, "w", encoding="utf-8").write(html)
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    print(f"wrote {a.out}" + (f" with {len(errors)} error(s)" if errors else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
