"""
中国新闻出版广电网（chinaxwcb.com）— 业内权威新闻源

为什么挑这个：
- HTTPS 直连，GitHub Actions 也能访问（本地+海外双通）
- URL 自带日期 /2026/06/23/99880175.html，0 反爬风险
- 是国家新闻出版署官方报刊《中国新闻出版广电报》的电子版

抓取策略：
- 直接抓首页 https://www.chinaxwcb.com/
- regex 匹配文章链接（年/月/日/文章ID.html）
- 列表页只能拿到 title + 日期，不取详情页摘要（页面没 meta description）
- 时间统一格式化为 ISO8601 +08:00

cnpubg_news 备用：cnpubg.com 在 GitHub Actions IP 上偶尔超时
（实测 2026-06-23 ConnectTimeout 3 次）。chinaxwcb 是 HTTPS + 大流量
门户，连通性更稳定。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

from .common import http_get, get_logger

log = get_logger("chinaxwcb")

LIST_URL = "https://www.chinaxwcb.com/"

# 文章 URL 模式：/2026/06/23/99880175.html
_ARTICLE_RE = re.compile(
    r'<a[^>]+href="(https://www\.chinaxwcb\.com/(\d{4})/(\d{2})/(\d{2})/\d+\.html)"[^>]*>([^<]+)</a>'
)


def _clean(s: str) -> str:
    if not s:
        return ""
    s = s.replace("&ldquo;", '"').replace("&rdquo;", '"')
    s = s.replace("&lsquo;", "'").replace("&rsquo;", "'")
    s = s.replace("&hellip;", "...").replace("&middot;", "·")
    s = s.replace("&nbsp;", " ").replace("&mdash;", "—")
    s = s.replace("&quot;", '"').replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch(max_articles: int = 20) -> list[dict]:
    """抓取 chinaxwcb 首页，最多返回 max_articles 篇近期文章。"""
    log.info("抓取中国新闻出版广电网 chinaxwcb.com...")
    resp = http_get(LIST_URL, timeout=10)
    if resp is None:
        log.warning("✗ chinaxwcb 列表页请求失败")
        return []

    text = resp.content.decode("utf-8", errors="replace")
    matches = _ARTICLE_RE.findall(text)
    if not matches:
        log.warning("✗ chinaxwcb 列表页未匹配到任何文章 URL")
        return []

    # 60 天窗口（与 baidu_news / main 保护机制一致）
    tz = timezone(timedelta(hours=8))
    cutoff = datetime.now(tz) - timedelta(days=60)

    seen = set()
    articles: list[dict] = []
    for url, year, mm, dd, title in matches:
        if url in seen:
            continue
        seen.add(url)
        title = _clean(title)
        if not title or len(title) < 6:
            continue
        try:
            d = datetime(int(year), int(mm), int(dd), 9, 0, tzinfo=tz)
        except ValueError:
            continue
        if d < cutoff:
            continue

        articles.append({
            "title": title,
            "url": url,
            "summary": "",  # 列表页没足够摘要，前端展示只用 title 即可
            "source": "中国新闻出版广电报",
            "published_at": d.isoformat(timespec="seconds"),
        })

        if len(articles) >= max_articles:
            break

    log.info("chinaxwcb 列表解析: %d 篇文章", len(articles))
    return articles


if __name__ == "__main__":
    news = fetch()
    print(f"\n=== 抓到 {len(news)} 条 ===\n")
    for i, n in enumerate(news[:8], 1):
        print(f"{i}. [{n['source']}] {n['title']}")
        print(f"   {n['published_at']}  |  {n['url']}")
        print()
