"""
出版商务周报（cnpubg.com）新闻抓取器 — 作为百度新闻的兜底源

百度新闻在 GitHub Actions IP 上经常被反爬（2026-06-22~23 实测连续多次后几个
关键词全部 0 条）。cnpubg.com 是出版业内媒体，对爬虫友好，URL 自带日期，
是最可靠的"中文出版业新闻"独立源。

抓取策略：
- 直接抓 /news/ 列表页（不需要登录、无反爬）
- URL 形如 /news/2026/0622/72358.shtml，日期从 URL 取
- 摘要从详情页 meta[name=description] 取
- 时间统一格式化为 ISO8601 +08:00 与 baidu_news 输出对齐
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from .common import http_get, get_logger

log = get_logger("cnpubg_news")

LIST_URL = "http://www.cnpubg.com/news/"

# 文章 URL 模式：/news/2026/0622/72358.shtml
_ARTICLE_RE = re.compile(
    r'<a[^>]+href="(http://www\.cnpubg\.com/news/(\d{4})/(\d{2})(\d{2})/\d+\.shtml)"[^>]*>([^<]+)</a>'
)
_DESC_RE = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]+)"')


def _clean_html_entities(s: str) -> str:
    """简单清理 HTML entity。"""
    if not s:
        return ""
    s = s.replace("&ldquo;", '"').replace("&rdquo;", '"')
    s = s.replace("&lsquo;", "'").replace("&rsquo;", "'")
    s = s.replace("&hellip;", "...").replace("&middot;", "·")
    s = s.replace("&nbsp;", " ").replace("&mdash;", "—").replace("&ndash;", "–")
    s = s.replace("&quot;", '"').replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _fetch_article_summary(url: str) -> str:
    """抓详情页 meta description 作为摘要。"""
    resp = http_get(url, timeout=8)
    if resp is None:
        return ""
    try:
        text = resp.content.decode("utf-8", errors="replace")
    except Exception:
        return ""
    m = _DESC_RE.search(text)
    if not m:
        return ""
    return _clean_html_entities(m.group(1))[:200]


def fetch(max_articles: int = 20) -> list[dict]:
    """抓取 cnpubg 列表页，最多返回 max_articles 篇近期文章。"""
    log.info("抓取出版商务周报 cnpubg.com/news/...")
    resp = http_get(LIST_URL, timeout=10)
    if resp is None:
        log.warning("✗ cnpubg 列表页请求失败")
        return []

    text = resp.content.decode("utf-8", errors="replace")
    matches = _ARTICLE_RE.findall(text)
    if not matches:
        log.warning("✗ cnpubg 列表页未匹配到任何文章 URL")
        return []

    # 去重（首页同一文章常被列在多个区块）
    seen = set()
    articles: list[dict] = []
    tz = timezone(timedelta(hours=8))
    for url, year, mm, dd, title in matches:
        if url in seen:
            continue
        seen.add(url)
        title = _clean_html_entities(title)
        if not title:
            continue
        try:
            # 列表页只到日，给个 09:00 占位时间（保证排序稳定）
            d = datetime(int(year), int(mm), int(dd), 9, 0, tzinfo=tz)
            published_at = d.isoformat(timespec="seconds")
        except ValueError:
            continue

        articles.append({
            "title": title,
            "url": url,
            "summary": "",  # 后面再补
            "source": "出版商务周报",
            "published_at": published_at,
        })

        if len(articles) >= max_articles:
            break

    log.info("cnpubg 列表解析: %d 篇文章", len(articles))

    # 补摘要 — 只对前 N 篇取，避免太慢
    SUMMARY_FETCH_LIMIT = min(10, len(articles))
    import time
    for i, art in enumerate(articles[:SUMMARY_FETCH_LIMIT]):
        summary = _fetch_article_summary(art["url"])
        if summary:
            art["summary"] = summary
        if i < SUMMARY_FETCH_LIMIT - 1:
            time.sleep(1)  # 客气一点

    log.info("cnpubg 摘要补全: %d 篇", SUMMARY_FETCH_LIMIT)
    return articles


if __name__ == "__main__":
    news = fetch()
    print(f"\n=== 抓到 {len(news)} 条 ===\n")
    for i, n in enumerate(news[:8], 1):
        print(f"{i}. [{n['source']}] {n['title']}")
        if n.get("summary"):
            print(f"   {n['summary'][:80]}")
        print(f"   {n['published_at']}  |  {n['url']}")
        print()
