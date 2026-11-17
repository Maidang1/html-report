#!/usr/bin/env python3
"""Build index.html listing every article in a directory.

Usage:
  python3 build_index.py <dir> [--out index.html] [--title "技术文章"]

Reads the <script id="article-meta"> block that build_article.py embeds
(title, deck, kicker, meta chips, sections, updated date). Pages without it
fall back to <title>, <meta name="description"> and their <h2> headings.
Re-run after adding, renaming or deleting an article. Standard library only.
"""
import argparse
import datetime
import html as _html
import json
import os
import re
import unicodedata


def plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", _html.unescape(s or ""))).strip()


def slug(text, used):
    """Mirror the article page's heading-id function so section links resolve."""
    s = re.sub(r"[\s　]+", "-", text.strip().lower())
    s = "".join(ch for ch in s if ch in "-_" or unicodedata.category(ch)[0] in "LN")
    s = re.sub(r"-+", "-", s).strip("-") or "section"
    base, i = s, 2
    while s in used:
        s = f"{base}-{i}"
        i += 1
    used.add(s)
    return s


def read_article(path):
    raw = open(path, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="article-meta">(.*?)</script>', raw, re.S)
    if m:
        meta = json.loads(m.group(1))
    else:
        title = re.search(r"<title>(.*?)</title>", raw, re.S)
        desc = re.search(r'<meta name="description" content="([^"]*)"', raw)
        meta = {
            "title": plain(title.group(1)) if title else os.path.basename(path),
            "deck": _html.unescape(desc.group(1)) if desc else "",
            "kicker": "", "meta": [],
            "sections": [plain(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", raw, re.S)],
            "chars": len(re.findall(r"[㐀-鿿]", re.sub(r"<[^>]+>", "", raw))),
        }
    if not meta.get("updated"):
        meta["updated"] = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    # Ids are assigned over h2 and h3 together on the page; collect both to keep dedupe identical.
    heads = [plain(h) for _, h in re.findall(r"<h([23])[^>]*>(.*?)</h\1>", raw, re.S)]
    used, ids = set(), {}
    for h in heads:
        sid = slug(h, used)
        ids.setdefault(h, sid)
    meta["links"] = [{"text": s, "id": ids.get(s) or slug(s, set())} for s in meta.get("sections", [])]
    meta["file"] = os.path.basename(path)
    meta["minutes"] = meta.get("minutes") or max(1, round(meta.get("chars", 0) / 420))
    return meta


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__COUNT__ 篇技术文章的目录">
<script>(function(){try{var t=localStorage.getItem('tech-article-theme');if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t)}catch(e){}})();</script>
<style>
:root {
  --bg: #fafaf8; --surface: #ffffff; --sunken: #eeede8;
  --ink: #1b1b19; --ink-2: #3a3a36; --muted: #72716b; --faint: #a3a29c;
  --line: #e6e5e0; --line-strong: #cfcec8;
  --accent: #2457d6; --accent-soft: rgb(36 87 214 / 0.08);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.04), 0 8px 24px rgb(0 0 0 / 0.05);
  --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    "Noto Sans SC", "Noto Sans CJK SC", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111110; --surface: #181817; --sunken: #1e1e1c;
    --ink: #ecebe7; --ink-2: #cdccc6; --muted: #98978f; --faint: #6b6a64;
    --line: #2a2a27; --line-strong: #3d3d39;
    --accent: #7ea2ff; --accent-soft: rgb(126 162 255 / 0.10);
    --shadow: 0 1px 2px rgb(0 0 0 / 0.3), 0 8px 24px rgb(0 0 0 / 0.25);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #111110; --surface: #181817; --sunken: #1e1e1c;
  --ink: #ecebe7; --ink-2: #cdccc6; --muted: #98978f; --faint: #6b6a64;
  --line: #2a2a27; --line-strong: #3d3d39;
  --accent: #7ea2ff; --accent-soft: rgb(126 162 255 / 0.10);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.3), 0 8px 24px rgb(0 0 0 / 0.25);
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.7 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration: none; }
code { font-family: var(--mono); font-size: 0.9em; }
.page { max-width: 1440px; margin: 0 auto; padding: 0 56px 96px; }
.masthead { padding: 56px 0 28px; }
.masthead-top { display: flex; justify-content: space-between; align-items: center; }
.kicker { margin: 0; font: 600 12px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--accent); }
.theme-toggle {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--muted); font: 13px var(--sans); cursor: pointer;
}
.theme-toggle:hover { color: var(--ink); border-color: var(--line-strong); }
h1 { margin: 18px 0 8px; font-size: clamp(28px, 3vw, 40px); line-height: 1.2; font-weight: 700; letter-spacing: -0.01em; }
.summary { margin: 0; color: var(--muted); font-size: 15px; }

.toolbar {
  position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px;
  margin: 0 -56px 28px; padding: 14px 56px; background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: blur(8px); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
}
.search {
  flex: 1 1 280px; max-width: 440px; height: 38px; padding: 0 14px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
  color: var(--ink); font: 14px var(--sans); outline: none;
}
.search:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag {
  height: 30px; padding: 0 12px; border: 1px solid var(--line); border-radius: 999px;
  background: var(--surface); color: var(--muted); font: 13px var(--sans); cursor: pointer;
}
.tag:hover { color: var(--ink); }
.tag[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.sort { margin-left: auto; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink-2); font: 13px var(--sans); }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 18px; }
.card {
  display: flex; flex-direction: column; padding: 22px 24px 18px;
  border: 1px solid var(--line); border-radius: 14px; background: var(--surface); box-shadow: var(--shadow);
  transition: border-color .15s ease, transform .15s ease;
}
.card:hover { border-color: var(--line-strong); transform: translateY(-1px); }
.card-kicker { font: 600 11.5px/1 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--accent); }
.card h2 { margin: 10px 0 8px; font-size: 20px; line-height: 1.4; font-weight: 700; }
.card h2 a:hover { color: var(--accent); }
.card h2 code { color: var(--accent); font-weight: 600; }
.card .deck { margin: 0 0 14px; color: var(--ink-2); font-size: 14.5px; line-height: 1.7; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.chips span { padding: 2px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: 12px; }
.card details { margin-top: auto; border-top: 1px solid var(--line); padding-top: 10px; }
.card summary { display: flex; align-items: center; gap: 10px; cursor: pointer; color: var(--muted); font-size: 13px; list-style: none; }
.card summary::-webkit-details-marker { display: none; }
.card summary::after { content: "展开章节"; margin-left: auto; color: var(--accent); }
.card details[open] summary::after { content: "收起"; }
.card ol { margin: 10px 0 4px; padding-left: 1.6em; font-size: 13.5px; line-height: 1.6; color: var(--ink-2); }
.card ol li { margin: 4px 0; }
.card ol li::marker { color: var(--faint); font-family: var(--mono); font-size: 12px; }
.card ol a:hover { color: var(--accent); }
.empty { padding: 60px 0; text-align: center; color: var(--muted); }
.foot { margin-top: 56px; color: var(--faint); font-size: 12.5px; }
.foot code { padding: 1px 5px; border-radius: 4px; background: var(--sunken); color: var(--muted); }
mark { background: var(--accent-soft); color: inherit; border-radius: 3px; }

@media (max-width: 760px) {
  .page { padding: 0 16px 64px; }
  .toolbar { margin: 0 -16px 20px; padding: 12px 16px; }
  .grid { grid-template-columns: 1fr; }
  .sort { margin-left: 0; }
}
</style>
</head>
<body>
<div class="page">
  <header class="masthead">
    <div class="masthead-top">
      <p class="kicker">文章目录</p>
      <button type="button" class="theme-toggle" data-theme-toggle aria-label="切换明暗主题">◐ 主题</button>
    </div>
    <h1>__TITLE__</h1>
    <p class="summary" data-summary></p>
  </header>
  <div class="toolbar">
    <input class="search" type="search" placeholder="搜索标题、摘要或章节…" aria-label="搜索文章" data-search>
    <div class="tags" data-tags></div>
    <select class="sort" data-sort aria-label="排序">
      <option value="updated">最近更新</option>
      <option value="title">按标题</option>
      <option value="length">按篇幅</option>
    </select>
  </div>
  <main class="grid" data-grid></main>
  <p class="empty" data-empty hidden>没有匹配的文章</p>
  <footer class="foot">由 <code>build_index.py</code> 生成于 __DATE__。新增或删除文章后重新运行即可更新。</footer>
</div>
<script id="articles" type="application/json">__DATA__</script>
<script>
(function () {
  var root = document.documentElement;
  document.querySelector('[data-theme-toggle]').addEventListener('click', function () {
    var cur = root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('tech-article-theme', next); } catch (e) {}
  });

  var data = JSON.parse(document.getElementById('articles').textContent);
  var grid = document.querySelector('[data-grid]'), empty = document.querySelector('[data-empty]');
  var search = document.querySelector('[data-search]'), sort = document.querySelector('[data-sort]');
  var tagBox = document.querySelector('[data-tags]');
  var active = null;

  var latest = data.map(function (a) { return a.updated; }).sort().pop() || '';
  document.querySelector('[data-summary]').textContent = '共 ' + data.length + ' 篇' + (latest ? ' · 最近更新 ' + latest : '');

  function tagsOf(a) { return [a.kicker].concat(a.meta || []).filter(Boolean); }
  var tagCount = {};
  data.forEach(function (a) { tagsOf(a).forEach(function (t) { tagCount[t] = (tagCount[t] || 0) + 1; }); });
  var tags = Object.keys(tagCount).filter(function (t) { return tagCount[t] > 1 || data.length <= 3; });
  ['全部'].concat(tags).forEach(function (t, i) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'tag'; b.textContent = t; b.setAttribute('aria-pressed', i === 0 ? 'true' : 'false');
    b.addEventListener('click', function () {
      active = i === 0 ? null : t;
      tagBox.querySelectorAll('.tag').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      render();
    });
    tagBox.appendChild(b);
  });

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function hit(text, q) {
    var t = esc(text);
    if (!q) return t;
    var i = text.toLowerCase().indexOf(q);
    return i < 0 ? t : esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + esc(text.slice(i + q.length));
  }

  function render() {
    var q = search.value.trim().toLowerCase();
    var list = data.filter(function (a) {
      if (active && tagsOf(a).indexOf(active) < 0) return false;
      if (!q) return true;
      return [a.title, a.deck, a.kicker].concat(a.meta || [], a.sections || []).join('\n').toLowerCase().indexOf(q) >= 0;
    });
    var key = sort.value;
    list.sort(function (x, y) {
      if (key === 'title') return x.title.localeCompare(y.title, 'zh');
      if (key === 'length') return (y.chars || 0) - (x.chars || 0);
      return (y.updated || '').localeCompare(x.updated || '');
    });
    grid.innerHTML = list.map(function (a) {
      var secMatch = q && (a.sections || []).some(function (s) { return s.toLowerCase().indexOf(q) >= 0; });
      var title = q ? hit(a.title, q) : (a.title_html || esc(a.title));
      return '<article class="card">' +
        (a.kicker ? '<div class="card-kicker">' + esc(a.kicker) + '</div>' : '') +
        '<h2><a href="' + esc(a.file) + '">' + title + '</a></h2>' +
        (a.deck ? '<p class="deck">' + hit(a.deck, q) + '</p>' : '') +
        '<div class="chips">' + (a.meta || []).map(function (m) { return '<span>' + esc(m) + '</span>'; }).join('') +
          '<span>约 ' + a.minutes + ' 分钟</span><span>更新于 ' + esc(a.updated) + '</span></div>' +
        '<details' + (secMatch ? ' open' : '') + '><summary>' + (a.links || []).length + ' 个章节 · ' + esc(a.file) + '</summary><ol>' +
          (a.links || []).map(function (l) { return '<li><a href="' + esc(a.file) + '#' + encodeURIComponent(l.id) + '">' + hit(l.text, q) + '</a></li>'; }).join('') +
        '</ol></details></article>';
    }).join('');
    empty.hidden = list.length > 0;
  }
  search.addEventListener('input', render);
  sort.addEventListener('change', render);
  addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== search) { e.preventDefault(); search.focus(); }
  });
  render();
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--title", default="技术文章")
    a = ap.parse_args()
    out_name = os.path.basename(a.out)
    files = sorted(f for f in os.listdir(a.dir) if f.endswith(".html") and f != out_name)
    articles = [read_article(os.path.join(a.dir, f)) for f in files]
    data = json.dumps(articles, ensure_ascii=False).replace("</", "<\\/")
    page = (PAGE.replace("__TITLE__", _html.escape(a.title)).replace("__COUNT__", str(len(articles)))
            .replace("__DATE__", datetime.date.today().isoformat()).replace("__DATA__", data))
    out = a.out if os.path.isabs(a.out) or os.path.dirname(a.out) else os.path.join(a.dir, a.out)
    open(out, "w", encoding="utf-8").write(page)
    for m in articles:
        print(f"  {m['file']}: {m['title']}  ({len(m['links'])} 个章节)")
    print(f"wrote {out} with {len(articles)} article(s)")


if __name__ == "__main__":
    main()
