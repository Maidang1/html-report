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
  --bg: #fafaf8; --surface: #ffffff; --sunken: #eeede8;
  --ink: #1b1b19; --ink-2: #3a3a36; --muted: #72716b; --faint: #a3a29c;
  --line: #e6e5e0; --line-strong: #cfcec8;
  --accent: #2457d6; --accent-soft: rgb(36 87 214 / 0.08);
  --code-bg: #15161a; --code-head: #1d1e23; --code-ink: #e4e4e1; --code-line: #2a2b31;
  --prompt-bg: #f6f5f1; --prompt-ink: #2b2a27;
  --pr-h: #2457d6; --pr-b: #1b1b19;
  --hl: rgb(36 87 214 / 0.10); --hl-amber: rgb(217 119 6 / 0.14);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.04), 0 8px 24px rgb(0 0 0 / 0.05);
  --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    "Noto Sans SC", "Noto Sans CJK SC", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111110; --surface: #181817; --sunken: #1e1e1c;
    --ink: #ecebe7; --ink-2: #cdccc6; --muted: #98978f; --faint: #6b6a64;
    --line: #2a2a27; --line-strong: #3d3d39;
    --accent: #7ea2ff; --accent-soft: rgb(126 162 255 / 0.10);
    --code-bg: #0d0e10; --code-head: #16171a; --code-line: #24252a;
    --prompt-bg: #1a1a18; --prompt-ink: #dad9d3;
    --pr-h: #8fb0ff; --pr-b: #f1f0ec;
    --hl: rgb(126 162 255 / 0.14); --hl-amber: rgb(245 158 11 / 0.16);
    --shadow: 0 1px 2px rgb(0 0 0 / 0.3), 0 8px 24px rgb(0 0 0 / 0.25);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #111110; --surface: #181817; --sunken: #1e1e1c;
  --ink: #ecebe7; --ink-2: #cdccc6; --muted: #98978f; --faint: #6b6a64;
  --line: #2a2a27; --line-strong: #3d3d39;
  --accent: #7ea2ff; --accent-soft: rgb(126 162 255 / 0.10);
  --code-bg: #0d0e10; --code-head: #16171a; --code-line: #24252a;
  --prompt-bg: #1a1a18; --prompt-ink: #dad9d3;
  --pr-h: #8fb0ff; --pr-b: #f1f0ec;
  --hl: rgb(126 162 255 / 0.14); --hl-amber: rgb(245 158 11 / 0.16);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.3), 0 8px 24px rgb(0 0 0 / 0.25);
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
a:hover { text-decoration: underline; text-underline-offset: 3px; }
strong { font-weight: 650; color: var(--ink); }
:not(pre) > code {
  padding: 0.12em 0.38em; border-radius: 4px;
  background: var(--sunken); color: var(--ink);
  font: 0.86em/1 var(--mono); letter-spacing: 0;
  -webkit-box-decoration-break: clone; box-decoration-break: clone;
}

/* progress + section ticks */
.progress { position: fixed; inset: 0 0 auto 0; height: 2px; z-index: 60; background: var(--accent); transform-origin: 0 50%; transform: scaleX(0); }
.ticks { position: fixed; left: 0; top: 50%; z-index: 50; transform: translateY(-50%); padding: 16px 20px 16px 26px; }
.ticks-list { display: flex; flex-direction: column; gap: 2px; }
.ticks-list a {
  display: block; width: 16px; height: 10px; padding: 4px 0;
  background: var(--line-strong); background-clip: content-box; border-radius: 2px;
  transition: width .16s ease, background-color .16s ease;
}
.ticks-list a.lv3 { width: 9px; }
.ticks-list a:hover, .ticks-list a:focus-visible { width: 24px; background-color: var(--ink-2); outline: none; text-decoration: none; }
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
.page { padding: 0 56px 120px 96px; }
.masthead { padding: 56px 0 40px; border-bottom: 1px solid var(--line); }
.masthead-top { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.back { font-size: 13px; color: var(--muted); }
.back:hover { color: var(--accent); text-decoration: none; }
.masthead-left { display: flex; align-items: center; gap: 14px; }
.kicker { margin: 0; font: 600 12px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--accent); }
.theme-toggle {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--muted); font: 13px var(--sans); cursor: pointer;
}
.theme-toggle:hover { color: var(--ink); border-color: var(--line-strong); }
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
.sec + .sec { margin-top: 8px; }
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
.anchor { margin-left: 8px; color: var(--faint); font-weight: 400; opacity: 0; }
h2:hover .anchor, h3:hover .anchor { opacity: 1; text-decoration: none; }

.intro { padding-top: 40px; }
.intro > p { max-width: 820px; margin: 0 0 20px; color: var(--ink-2); }
.points { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin: 8px 0 0; padding: 0; list-style: none; }
.points li { padding: 16px 18px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); color: var(--ink-2); font-size: 14.5px; line-height: 1.65; }
.points li > code.fn-name { display: inline-block; margin-bottom: 8px; padding: 0; background: none; color: var(--accent); font-size: 13px; font-weight: 600; }
.points li > span { display: block; }

/* text | media rows */
.row {
  display: grid; grid-template-columns: minmax(300px, 600px) minmax(0, 1fr);
  gap: 0 56px; align-items: start; margin: 0 0 40px;
}
.row-text > :first-child { margin-top: 0; }
.row-text > :last-child { margin-bottom: 0; }
.row-text p, .row-text ul, .row-text ol { margin: 0 0 16px; color: var(--ink-2); }
.row-text ul, .row-text ol { padding-left: 1.3em; }
.row-text li { margin: 6px 0; }
.row-text li::marker { color: var(--faint); }
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
  display: flex; align-items: center; gap: 10px; min-height: 38px; padding: 0 8px 0 14px;
  border-bottom: 1px solid var(--line); font: 12px/1.4 var(--mono); color: var(--muted);
}
.panel-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.panel-lang { flex: none; color: var(--faint); }
.panel-btn {
  flex: none; height: 26px; padding: 0 10px; border: 1px solid transparent; border-radius: 6px;
  background: none; color: inherit; font: 12px var(--sans); cursor: pointer;
}
.panel-btn:hover { border-color: var(--line); color: var(--ink); }
.panel-body { max-height: 560px; overflow: auto; }
.panel.expanded .panel-body { max-height: none; }
.panel pre { margin: 0; padding: 16px 18px; font: 12.5px/1.7 var(--mono); letter-spacing: 0; tab-size: 4; }
.panel pre code { font: inherit; }
.panel-expand {
  display: block; width: 100%; height: 34px; border: 0; border-top: 1px solid var(--line);
  background: var(--surface); color: var(--accent); font: 12.5px var(--sans); cursor: pointer;
}
.panel-expand[hidden] { display: none; }

.panel-code { background: var(--code-bg); border-color: var(--code-line); }
.panel-code .panel-head { background: var(--code-head); border-color: var(--code-line); color: #9b9ca3; }
.panel-code .panel-lang { color: #6c6d74; }
.panel-code .panel-btn:hover { border-color: #3a3b42; color: #e4e4e1; }
.panel-code .panel-expand { background: var(--code-head); border-color: var(--code-line); color: #9db8ff; }
.panel-code pre { color: var(--code-ink); white-space: pre; }
.panel-code .kw { color: #ff7b72; }
.panel-code .fn { color: #d2a8ff; }
.panel-code .str { color: #a5d6ff; }
.panel-code :is(.cm, .cmt) { color: #8b949e; }
.panel-code .num { color: #79c0ff; }
.panel-code :is(.ty, .typ) { color: #ffa657; }
.panel-code :is(.hl-line, .hl-amber) { display: inline-block; min-width: 100%; margin: 0 -18px; padding: 0 18px; box-sizing: content-box; }
.panel-code .hl-line { background: rgb(56 139 253 / 0.16); box-shadow: inset 2px 0 0 #58a6ff; }
.panel-code .hl-amber { background: rgb(210 153 34 / 0.18); box-shadow: inset 2px 0 0 #d29922; }

/* highlight.js tokens for plain code (hand-tokenised code keeps its own spans) */
.panel-code .hljs-keyword, .panel-code .hljs-selector-tag, .panel-code .hljs-doctag { color: #ff7b72; }
.panel-code .hljs-title, .panel-code .hljs-title.function_, .panel-code .hljs-section { color: #d2a8ff; }
.panel-code .hljs-string, .panel-code .hljs-regexp, .panel-code .hljs-template-variable { color: #a5d6ff; }
.panel-code .hljs-comment, .panel-code .hljs-quote, .panel-code .hljs-meta { color: #8b949e; }
.panel-code .hljs-number, .panel-code .hljs-literal, .panel-code .hljs-attr, .panel-code .hljs-variable, .panel-code .hljs-property, .panel-code .hljs-symbol { color: #79c0ff; }
.panel-code .hljs-type, .panel-code .hljs-built_in, .panel-code .hljs-title.class_, .panel-code .hljs-params { color: #ffa657; }
.panel-code .hljs-addition { color: #aff5b4; background: rgb(46 160 67 / 0.15); }
.panel-code .hljs-deletion { color: #ffdcd7; background: rgb(248 81 73 / 0.15); }

.panel-prompt { background: var(--prompt-bg); }
.panel-prompt .panel-head { background: var(--surface); }
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
.panel-diagram.light .panel-body { background: #fff; }
.panel-diagram.dark { background: #0c1220; border-color: #1e293b; }
.panel-diagram.dark .panel-head { background: #0f172a; border-color: #1e293b; color: #94a3b8; }
.panel-diagram svg { display: block; width: 100%; height: auto; min-width: 600px; letter-spacing: 0; }
.panel-diagram.light svg { font-family: var(--sans); }
.panel-diagram.wide svg { min-width: 920px; }
.panel-diagram.zoomed {
  position: fixed; inset: 24px; z-index: 80; display: flex; flex-direction: column;
  box-shadow: 0 0 0 100vmax rgb(0 0 0 / 0.55), var(--shadow);
}
.panel-diagram.zoomed .panel-body { flex: 1; overflow: auto; cursor: zoom-out; display: grid; place-items: center; }
.panel-diagram.zoomed svg { width: min(1600px, 100%); min-width: 0; }

/* table */
.row-wide .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); box-shadow: var(--shadow); }
table { width: 100%; border-collapse: collapse; font-size: 14px; line-height: 1.6; }
th, td { padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; color: var(--ink-2); }
th { position: sticky; top: 0; background: var(--sunken); color: var(--ink); font-weight: 650; font-size: 13px; }
tr:last-child td { border-bottom: 0; }
td:first-child { color: var(--ink); white-space: nowrap; }
td code, th code { white-space: normal; }

.foot { margin-top: 80px; padding-top: 20px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }

@media (max-width: 1100px) {
  .page { padding: 0 28px 96px 64px; }
  .row { display: block; }
  .row-media { position: static; margin-top: 18px; }
}
@media (max-width: 640px) {
  .ticks { display: none; }
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
  document.querySelectorAll('.panel').forEach(function (p) {
    var body = p.querySelector('.panel-body'), more = p.querySelector('[data-expand]');
    if (more && body.scrollHeight > body.clientHeight + 8) {
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
      var done = function () { copy.textContent = '已复制'; setTimeout(function () { copy.textContent = '复制'; }, 1400); };
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(done, function () {}); else done();
    });
    var zoom = p.querySelector('[data-zoom]');
    if (zoom) {
      var toggle = function () { var on = p.classList.toggle('zoomed'); zoom.textContent = on ? '关闭' : '放大'; };
      zoom.addEventListener('click', toggle);
      body.addEventListener('click', toggle);
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.panel.zoomed').forEach(function (p) { p.classList.remove('zoomed'); p.querySelector('[data-zoom]').textContent = '放大'; });
  });

  // syntax colours for plain code blocks
  if (window.hljs) document.querySelectorAll('.panel-code code[class^="language-"]').forEach(function (el) {
    try { window.hljs.highlightElement(el); } catch (e) {}
  });

  // section ticks with hover card
  var list = document.querySelector('.ticks-list'), card = document.querySelector('.ticks-card');
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
    a.addEventListener('mouseenter', show); a.addEventListener('focus', show);
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
  <main class="content">
{rows}
  </main>
  <footer class="foot">{footer}</footer>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>{JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("body")
    ap.add_argument("out")
    ap.add_argument("--title", required=True, help="may contain <code>")
    ap.add_argument("--kicker", default="")
    ap.add_argument("--deck", default="", help="one-sentence summary under the title")
    ap.add_argument("--meta", action="append", default=[], help="chip under the title; repeatable")
    ap.add_argument("--footer", default="")
    ap.add_argument("--lang", default="zh-CN")
    ap.add_argument("--plan", action="store_true", help="print the section/row plan")
    ap.add_argument("--index", default="", help="relative link to the article index, e.g. index.html")
    a = ap.parse_args()

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
