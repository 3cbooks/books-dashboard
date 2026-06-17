"""
HTML 报告渲染
"""
from __future__ import annotations

import html as html_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))


def _esc(s: str) -> str:
    return html_lib.escape(s or "")


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return iso


def _fmt_relative(iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        delta = dt - now
        if delta.total_seconds() < 0:
            return "已过期"
        # 用日期相减（不含时分），避免"今天 21:00 → 明天 19:30"显示成"22 小时后"
        days = (dt.date() - now.date()).days
        if days == 0:
            hours = int(delta.total_seconds() / 3600)
            if hours == 0:
                return "<1 小时后"
            return f"今天 ({hours} 小时后)"
        if days == 1:
            return "明天"
        if days == 2:
            return "后天"
        return f"{days} 天后"
    except Exception:
        return ""


def render_report(posts: list[dict], failed_accounts: list[str], output_path: Path):
    """
    渲染 HTML 报告
    posts 已经包含 livestream 解析结果 + account_name + account_platform
    """
    now = datetime.now(TZ)

    # 分组：有预告 vs 无预告 vs 含书名（可能有用但未识别为预告）
    previews = [p for p in posts if p.get("livestream", {}).get("has_preview")]
    has_books_only = [
        p for p in posts
        if not p.get("livestream", {}).get("has_preview")
        and p.get("livestream", {}).get("books")
    ]
    others = [
        p for p in posts
        if not p.get("livestream", {}).get("has_preview")
        and not p.get("livestream", {}).get("books")
    ]

    # 预告按时间排序（有时间的优先 + 时间近的优先 / 没时间的最后）
    def _sort_key(p):
        iso = p.get("livestream", {}).get("scheduled_at")
        if iso:
            try:
                return (0, datetime.fromisoformat(iso))
            except Exception:
                pass
        return (1, datetime.max.replace(tzinfo=TZ))

    previews_sorted = sorted(previews, key=_sort_key)

    html = []
    html.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>图书直播预告 · 监控报告</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #f5f5f0; }
  .card { transition: all .2s; }
  .card:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,.08); }
  .badge-douyin { background: #fdebec; color: #d33; }
  .badge-xhs { background: #fff5e6; color: #f60; }
  .book-tag { background: #f0f9ff; color: #0369a1; }
  .time-chip { background: #ecfdf5; color: #047857; }
  .past-chip { background: #f3f4f6; color: #6b7280; }
</style>
</head>
<body class="text-gray-800">
<div class="max-w-5xl mx-auto p-6">
""")

    # 头部
    html.append(f"""
<header class="mb-6">
  <h1 class="text-2xl font-bold mb-1">图书直播预告监控</h1>
  <p class="text-sm text-gray-500">
    更新时间: {now.strftime('%Y-%m-%d %H:%M')} ·
    监控 {len(set((p['account_platform'], p['account_name']) for p in posts))} 个账号 ·
    抓取 {len(posts)} 条作品 ·
    识别预告 <span class="font-bold text-emerald-700">{len(previews)}</span> 条
""")
    if failed_accounts:
        html.append(f' · <span class="text-rose-600">失败 {len(failed_accounts)} 个</span>')
    html.append("</p></header>\n")

    # ===== 直播预告区 =====
    html.append('<section class="mb-8"><h2 class="text-lg font-semibold mb-3">📅 直播预告</h2>')
    if not previews_sorted:
        html.append('<div class="bg-white rounded-lg p-6 text-center text-gray-400">暂无识别到的直播预告</div>')
    else:
        for p in previews_sorted:
            html.append(_render_card(p, now, is_preview=True))
    html.append("</section>\n")

    # ===== 含书名但不确定是否预告 =====
    if has_books_only:
        html.append('<section class="mb-8"><h2 class="text-lg font-semibold mb-3">📚 含书名（未识别为直播预告）</h2>')
        html.append('<p class="text-xs text-gray-500 mb-3">这些作品里有书名号《X》但没有直播线索。可能是新书推荐 / 测评 / 已结束的直播复盘</p>')
        for p in has_books_only[:20]:
            html.append(_render_card(p, now, is_preview=False))
        html.append("</section>\n")

    # ===== 失败账号 =====
    if failed_accounts:
        html.append('<section class="mb-8"><h2 class="text-lg font-semibold mb-3 text-rose-700">⚠️ 抓取失败</h2>')
        html.append('<div class="bg-rose-50 border border-rose-200 rounded-lg p-4">')
        html.append('<ul class="list-disc pl-5 text-sm">')
        for acc in failed_accounts:
            html.append(f'<li>{_esc(acc)}</li>')
        html.append('</ul></div></section>\n')

    # ===== 其他作品 折叠 =====
    if others:
        html.append(f'<details class="mb-8"><summary class="cursor-pointer text-sm text-gray-500">其他 {len(others)} 条作品（无书名 + 无直播信号）</summary>')
        html.append('<div class="mt-3">')
        for p in others[:50]:
            html.append(_render_card(p, now, is_preview=False, compact=True))
        html.append('</div></details>')

    html.append("</div></body></html>")

    output_path.write_text("\n".join(html), encoding="utf-8")


def _render_card(post: dict, now: datetime, is_preview: bool, compact: bool = False) -> str:
    livestream = post.get("livestream", {}) or {}
    platform = post.get("account_platform", "")
    name = post.get("account_name", "")
    title = post.get("title", "")
    url = post.get("url", "#")
    scheduled = livestream.get("scheduled_at")
    scheduled_text = livestream.get("scheduled_text", "")
    books = livestream.get("books", []) or []

    badge_cls = "badge-douyin" if platform == "douyin" else "badge-xhs"
    plat_label = "抖音" if platform == "douyin" else "小红书"

    if compact:
        return f'''<div class="text-xs text-gray-500 mb-1">
  <span class="inline-block px-2 py-0.5 rounded {badge_cls}">{plat_label}</span>
  <span class="font-medium">{_esc(name)}</span> ·
  <a href="{_esc(url)}" target="_blank" class="hover:underline">{_esc(title[:80])}</a>
</div>'''

    time_chip = ""
    if scheduled:
        relative = _fmt_relative(scheduled, now)
        is_past = "已过期" in relative
        chip_cls = "past-chip" if is_past else "time-chip"
        time_chip = f'''
<div class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs {chip_cls} mb-2">
  <span>📅 {_fmt_time(scheduled)}</span>
  <span class="opacity-70">({relative})</span>
</div>'''

    books_html = ""
    if books:
        books_html = '<div class="flex flex-wrap gap-1.5 mt-2">'
        for b in books[:8]:
            books_html += f'<span class="book-tag inline-block px-2 py-0.5 rounded text-xs">📕 {_esc(b)}</span>'
        if len(books) > 8:
            books_html += f'<span class="text-xs text-gray-400">+{len(books)-8}</span>'
        books_html += "</div>"

    matched_text_html = ""
    if scheduled_text:
        matched_text_html = f'<div class="text-xs text-gray-400 mt-1">原文: "{_esc(scheduled_text)}"</div>'

    return f'''
<div class="card bg-white rounded-lg p-4 mb-3 shadow-sm">
  <div class="flex items-center gap-2 mb-1.5">
    <span class="inline-block px-2 py-0.5 rounded text-xs {badge_cls}">{plat_label}</span>
    <span class="font-medium text-sm">{_esc(name)}</span>
  </div>
  {time_chip}
  <a href="{_esc(url)}" target="_blank" class="block text-sm text-gray-700 hover:text-emerald-700 leading-relaxed">
    {_esc(title)}
  </a>
  {books_html}
  {matched_text_html}
</div>'''
