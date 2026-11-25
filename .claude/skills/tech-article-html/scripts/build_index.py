#!/usr/bin/env python3
"""Build index.html listing every article in a directory.

Usage:
  python3 build_index.py <dir> [--out index.html] [--title "技术文章"] [--back ../index.html]

Subfolders that hold an index.html built by this script are listed as
"专题" (topic) cards above the article grid. --back adds a link from a
topic index up to its parent index.

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
  --bg: #f5f4ed; --surface: #faf9f5; --sunken: #f0eee6;
  --ink: #141413; --ink-2: #2c2c29; --muted: #504e49; --faint: #6b6a64;
  --line: #e8e6dc; --line-strong: #d8d5c8;
  --accent: #1B365D; --accent-soft: #EEF2F7;
  --shadow: 0 1px 2px rgb(20 20 19 / 0.04), 0 8px 24px rgb(20 20 19 / 0.05);
  --sans: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
    "Noto Sans SC", "Noto Sans CJK SC", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #141413; --surface: #1e1d1b; --sunken: #282724;
    --ink: #f2f0ea; --ink-2: #e2ded3; --muted: #a8a49a; --faint: #7a776e;
    --line: #2e2d2a; --line-strong: #403f3c;
    --accent: #9bb8dc; --accent-soft: rgb(155 184 220 / 0.12);
    --shadow: 0 1px 2px rgb(0 0 0 / 0.32), 0 8px 24px rgb(0 0 0 / 0.26);
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #141413; --surface: #1e1d1b; --sunken: #282724;
  --ink: #f2f0ea; --ink-2: #e2ded3; --muted: #a8a49a; --faint: #7a776e;
  --line: #2e2d2a; --line-strong: #403f3c;
  --accent: #9bb8dc; --accent-soft: rgb(155 184 220 / 0.12);
  --shadow: 0 1px 2px rgb(0 0 0 / 0.32), 0 8px 24px rgb(0 0 0 / 0.26);
  color-scheme: dark;
}
* { box-sizing: border-box; }
html { scroll-padding-top: 96px; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.7 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration: none; }
code { font-family: var(--mono); font-size: 0.9em; }
.page { max-width: 1120px; margin: 0 auto; padding: 0 56px 96px; }
.masthead { padding: 64px 0 40px; }
.masthead-top { display: flex; justify-content: space-between; align-items: center; }
.kicker { margin: 0; font: 600 12px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--accent); }
.theme-toggle {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px;
  border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
  color: var(--muted); font: 13px var(--sans); cursor: pointer;
}
.theme-toggle:hover { color: var(--ink); border-color: var(--line-strong); }
h1 { margin: 20px 0 10px; font-size: clamp(32px, 4vw, 52px); line-height: 1.15; font-weight: 700; letter-spacing: -0.02em; }
.summary { margin: 0; color: var(--muted); font-size: 15px; font-variant-numeric: tabular-nums; }

.toolbar {
  position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px;
  margin: 0 0 8px; padding: 14px 0; background: color-mix(in srgb, var(--bg) 92%, transparent);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--ink);
}
.search {
  flex: 1 1 240px; max-width: 320px; height: 36px; padding: 0 14px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
  color: var(--ink); font: 14px var(--sans); outline: none;
}
.search:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag {
  height: 30px; padding: 0 12px; border: 1px solid transparent; border-radius: 999px;
  background: none; color: var(--muted); font: 13.5px var(--sans); cursor: pointer;
}
@media (hover: hover) { .tag:hover { color: var(--ink); } }
.tag:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.tag[aria-pressed="true"] { background: var(--ink); color: var(--bg); font-weight: 600; }
.sort { margin-left: auto; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink-2); font: 13px var(--sans); }

.grid { display: block; }
.card {
  display: grid; grid-template-columns: 148px minmax(0, 1fr); gap: 0 32px;
  padding: 32px 0; border-bottom: 1px solid var(--line); overflow-wrap: anywhere;
}
.card-side { padding-top: 6px; color: var(--muted); font: 12.5px/1.6 var(--mono); font-variant-numeric: tabular-nums; }
.card-kicker { display: block; margin-top: 4px; font: 600 12px/1.5 var(--sans); letter-spacing: .04em; color: var(--accent); }
.card-main { max-width: 760px; }
.card h2 { margin: 0 0 10px; font-size: 22px; line-height: 1.4; font-weight: 700; letter-spacing: -0.005em; text-wrap: balance; }
.card h2 a { transition: color .15s ease; }
@media (hover: hover) { .card h2 a:hover { color: var(--accent); } }
.card h2 a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.card h2 code { color: var(--accent); font-weight: 600; }
.card .deck { margin: 0 0 12px; color: var(--ink-2); font-size: 15px; line-height: 1.75; }
.card .info { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.7; font-variant-numeric: tabular-nums; }
.card .info span + span::before { content: "·"; margin: 0 8px; color: var(--faint); }
.card details { margin-top: 10px; }
.card summary { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; color: var(--accent); font-size: 13px; list-style: none; }
.card summary::-webkit-details-marker { display: none; }
.card summary::before { content: "+"; width: 1em; font-family: var(--mono); text-align: center; }
.card details[open] summary::before { content: "−"; }
.card summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.card ol { columns: 2 280px; column-gap: 32px; margin: 12px 0 0; padding: 12px 0 0 1.8em; border-top: 1px solid var(--line); font-size: 13.5px; line-height: 1.6; color: var(--ink-2); }
.card ol li { margin: 0 0 6px; break-inside: avoid; }
.card ol li::marker { color: var(--faint); font-family: var(--mono); font-size: 11.5px; }
@media (hover: hover) { .card ol a:hover { color: var(--accent); } }
.empty { padding: 60px 0; text-align: center; color: var(--muted); }
.empty p { margin: 0 0 14px; }
.empty button { height: 34px; padding: 0 14px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink); font: 14px var(--sans); cursor: pointer; }
.back { display: inline-block; margin-bottom: 14px; color: var(--muted); font-size: 13.5px; }
.back:hover { color: var(--accent); }
.topics { margin: 0 0 48px; }
.topics h2 { margin: 0 0 12px; font: 600 11px/1 var(--mono); letter-spacing: .14em; color: var(--faint); }
.topic-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr)); gap: 12px; }
.topic {
  display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 2px 16px;
  padding: 18px 22px; border-radius: 12px; background: var(--ink); color: var(--bg);
  transition: transform .15s ease;
}
@media (hover: hover) { .topic:hover { transform: translateY(-1px); } }
.topic:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.topic strong { font-size: 17px; }
.topic span { grid-column: 1; color: color-mix(in srgb, var(--bg) 72%, var(--ink)); font-size: 13px; font-variant-numeric: tabular-nums; }
.topic::after { content: "→"; grid-column: 2; grid-row: 1 / 3; font-size: 18px; }
.foot { margin-top: 56px; color: var(--muted); font-size: 12.5px; }
.foot code { padding: 1px 5px; border-radius: 4px; background: var(--sunken); color: var(--muted); }
mark { background: var(--accent-soft); color: inherit; border-radius: 3px; }

@media (max-width: 760px) {
  html { scroll-padding-top: 0; }
  .page { padding: 0 16px 64px; }
  .toolbar { position: static; margin: 0 -16px 20px; padding: 12px 16px; }
  .search, .sort { font-size: 16px; }
  .card { grid-template-columns: 1fr; padding: 24px 0; }
  .card-side { display: flex; gap: 10px; align-items: baseline; padding: 0 0 6px; }
  .card-kicker { margin: 0; }
  .card h2 { font-size: 19px; }
  .sort { margin-left: 0; }
}
</style>
</head>
<body>
<div class="page">
  <header class="masthead">
    __BACK__<div class="masthead-top">
      <p class="kicker">文章目录</p>
      <button type="button" class="theme-toggle" data-theme-toggle aria-label="切换明暗主题">◐ 主题</button>
    </div>
    <h1>__TITLE__</h1>
    <p class="summary" data-summary></p>
  </header>
  __TOPICS__<div class="toolbar">
    <input class="search" type="search" placeholder="搜索标题、摘要或章节…" aria-label="搜索文章" data-search>
    <div class="tags" data-tags></div>
    <select class="sort" data-sort aria-label="排序">
      <option value="created">最近创建</option>
      <option value="updated">最近更新</option>
      <option value="title">按标题</option>
      <option value="length">按篇幅</option>
    </select>
  </div>
  <main class="grid" data-grid></main>
  <div class="empty" data-empty hidden><p data-empty-msg></p><button type="button" data-clear>清除搜索和筛选</button></div>
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

  // articles without a created date fall back to their updated date
  function created(a) { return a.created || a.updated || ''; }
  function tagsOf(a) { return [a.kicker].filter(Boolean); }
  var tagCount = {};
  data.forEach(function (a) { tagsOf(a).forEach(function (t) { tagCount[t] = (tagCount[t] || 0) + 1; }); });
  var tags = Object.keys(tagCount).sort(function (x, y) { return tagCount[y] - tagCount[x]; });
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
      if (key === 'created') return created(y).localeCompare(created(x)) || (y.updated || '').localeCompare(x.updated || '');
      return (y.updated || '').localeCompare(x.updated || '');
    });
    grid.innerHTML = list.map(function (a) {
      var secMatch = q && (a.sections || []).some(function (s) { return s.toLowerCase().indexOf(q) >= 0; });
      var title = q ? hit(a.title, q) : (a.title_html || esc(a.title));
      var day = created(a).slice(0, 10);
      return '<article class="card"><div class="card-side">' + esc(day) +
          (a.kicker ? '<span class="card-kicker">' + esc(a.kicker) + '</span>' : '') + '</div>' +
        '<div class="card-main"><h2><a href="' + esc(a.file) + '">' + title + '</a></h2>' +
        (a.deck ? '<p class="deck">' + hit(a.deck, q) + '</p>' : '') +
        '<p class="info"><span>约 ' + a.minutes + ' 分钟</span>' +
          (a.updated && a.updated !== day ? '<span>更新于 ' + esc(a.updated) + '</span>' : '') +
          (a.meta || []).map(function (m) { return '<span>' + hit(m, q) + '</span>'; }).join('') + '</p>' +
        ((a.links || []).length ? '<details' + (secMatch ? ' open' : '') + '><summary>' + a.links.length + ' 个章节</summary><ol>' +
          a.links.map(function (l) { return '<li><a href="' + esc(a.file) + '#' + encodeURIComponent(l.id) + '">' + hit(l.text, q) + '</a></li>'; }).join('') +
        '</ol></details>' : '') + '</div></article>';
    }).join('');
    empty.hidden = list.length > 0;
    if (!list.length) empty.querySelector('[data-empty-msg]').textContent =
      (q ? '没有匹配“' + search.value.trim() + '”' : '没有') + (active ? '、标签为“' + active + '”' : '') + '的文章';
  }
  document.querySelector('[data-clear]').addEventListener('click', function () {
    search.value = ''; active = null;
    tagBox.querySelectorAll('.tag').forEach(function (x, i) { x.setAttribute('aria-pressed', i === 0 ? 'true' : 'false'); });
    render(); search.focus();
  });
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


def rem_fonts(page):
    """Font sizes in rem inside <style>, so the reader's browser font-size setting still applies."""
    to_rem = lambda m: f"{float(m.group(1)) / 16:g}rem"
    return re.sub(r"<style>.*?</style>", lambda s: re.sub(
        r"(?<![\w-])(font(?:-size)?\s*:[^;{}]*)",
        lambda d: re.sub(r"(\d*\.?\d+)px", to_rem, d.group(1)), s.group(0)), page, flags=re.S)


PAGE = rem_fonts(PAGE)


def read_topics(root, out_name):
    """Subfolders whose index page was built by this script, as (dir, title, count, latest)."""
    topics = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, out_name)
        if d.startswith(".") or not os.path.isfile(p):
            continue
        raw = open(p, encoding="utf-8").read()
        m = re.search(r'<script id="articles" type="application/json">(.*?)</script>', raw, re.S)
        t = re.search(r"<title>(.*?)</title>", raw, re.S)
        if not m or not t:
            continue
        items = json.loads(m.group(1).replace("<\\/", "</"))
        latest = max((x.get("updated", "") for x in items), default="")
        topics.append((d, plain(t.group(1)), len(items), latest))
    return topics


def topics_html(topics, out_name):
    if not topics:
        return ""
    e = _html.escape
    cards = "".join(
        f'<a class="topic" href="{e(d)}/{e(out_name)}"><strong>{e(title)}</strong>'
        f'<span>{n} 篇' + (f" · 最近更新 {e(latest)}" if latest else "") + "</span></a>"
        for d, title, n, latest in topics)
    return f'<section class="topics" aria-label="专题"><h2>专题</h2><div class="topic-grid">{cards}</div></section>\n  '


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir")
    ap.add_argument("--out", default="index.html")
    ap.add_argument("--title", default="技术文章")
    ap.add_argument("--back", help="link to the parent index, e.g. ../index.html")
    a = ap.parse_args()
    out_name = os.path.basename(a.out)
    files = sorted(f for f in os.listdir(a.dir) if f.endswith(".html") and f != out_name)
    articles = [read_article(os.path.join(a.dir, f)) for f in files]
    data = json.dumps(articles, ensure_ascii=False).replace("</", "<\\/")
    topics = read_topics(a.dir, out_name)
    back = f'<a class="back" href="{_html.escape(a.back)}">← 上级目录</a>\n    ' if a.back else ""
    page = (PAGE.replace("__BACK__", back).replace("__TOPICS__", topics_html(topics, out_name))
            .replace("__TITLE__", _html.escape(a.title)).replace("__COUNT__", str(len(articles)))
            .replace("__DATE__", datetime.date.today().isoformat()).replace("__DATA__", data))
    out = a.out if os.path.isabs(a.out) or os.path.dirname(a.out) else os.path.join(a.dir, a.out)
    open(out, "w", encoding="utf-8").write(page)
    for m in articles:
        print(f"  {m['file']}: {m['title']}  ({len(m['links'])} 个章节)")
    for d, title, n, _ in topics:
        print(f"  [专题] {d}/: {title}  ({n} 篇)")
    print(f"wrote {out} with {len(articles)} article(s)" + (f", {len(topics)} topic(s)" if topics else ""))


if __name__ == "__main__":
    main()
