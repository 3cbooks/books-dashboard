"""
Google News RSS — 终极兜底新闻源

为什么挑这个：
- Google 全球 CDN，GitHub Actions runner 100% 可达
  （cnpubg.com / chinaxwcb.com 都对 GitHub Actions IP 超时
   2026-06-23 实测 timeout * 3 retry × 两个域名）
- RSS 标准格式，pubDate 是 RFC822
- 支持中文搜索，多关键词聚合后量大（100 篇/查询）
- source tag 直接告诉你"哪家媒体"，比百度更干净

抓取策略：
- 多关键词分别请求 → 合并去重
- title 通常带" - 媒体名"后缀，剥掉
- pubDate RFC822 → ISO8601 +08:00
- url 是 Google 跳转链接，但前端展示 url 也无所谓（点了仍能跳到原文）
"""
from __future__ import annotations

import re
import time
from email.utils import parsedate_to_datetime
from datetime import timezone, timedelta

from .common import http_get, get_logger

log = get_logger("google_news")

KEYWORDS = [
    "图书出版",
    "新书发布",
    "童书",
    "图书博览会",
    "出版业",
]

# Google News RSS 接口（中文）
URL = "https://news.google.com/rss/search"


def _strip_source_suffix(title: str) -> str:
    """Google News title 常带 ' - 媒体名' 后缀，剥掉以保持干净"""
    if not title:
        return ""
    m = re.match(r"^(.+?)\s+-\s+[^-]{2,30}$", title.strip())
    return m.group(1).strip() if m else title.strip()


def _parse_rfc822(s: str) -> str | None:
    """'Tue, 23 Jun 2026 01:56:11 GMT' → '2026-06-23T09:56:11+08:00'"""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s.strip())
        if dt is None:
            return None
        # 转北京时间
        bj = timezone(timedelta(hours=8))
        return dt.astimezone(bj).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return None


def _extract_one_keyword(keyword: str) -> list[dict]:
    """抓单个关键词的 RSS 结果"""
    resp = http_get(
        URL,
        params={
            "q": keyword,
            "hl": "zh-CN",
            "gl": "CN",
            "ceid": "CN:zh-Hans",
        },
        timeout=15,
    )
    if resp is None:
        return []

    text = resp.content.decode("utf-8", errors="replace")
    items = re.findall(r"<item>(.*?)</item>", text, re.S)

    out: list[dict] = []
    for it in items:
        title_m = re.search(r"<title>(.*?)</title>", it, re.S)
        link_m = re.search(r"<link>(.*?)</link>", it, re.S)
        pub_m = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        src_m = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)

        if not title_m or not link_m:
            continue

        title_raw = title_m.group(1).strip()
        title = _strip_source_suffix(title_raw)
        if not title or len(title) < 6:
            continue

        published = _parse_rfc822(pub_m.group(1) if pub_m else "")
        # 没日期的 Google 条目极少，但保险起见跟其他源一致丢弃
        if not published:
            continue

        out.append({
            "title": title,
            "url": link_m.group(1).strip(),
            "summary": "",  # Google News description 含大量 HTML，留空避免污染
            "source": src_m.group(1).strip() if src_m else "Google News",
            "published_at": published,
        })

    return out


def fetch() -> list[dict]:
    """聚合 KEYWORDS 关键词，去重 + 60 天窗口过滤后返回"""
    all_news: list[dict] = []
    seen_urls = set()
    seen_titles = set()

    for kw in KEYWORDS:
        log.info("抓取 Google News [%s]...", kw)
        items = _extract_one_keyword(kw)
        added = 0
        for it in items:
            u, t = it.get("url"), it.get("title")
            if u in seen_urls or t in seen_titles:
                continue
            all_news.append(it)
            seen_urls.add(u)
            seen_titles.add(t)
            added += 1
        log.info("  └─ +%d 条（去重前 %d）", added, len(items))
        # 客气一点
        time.sleep(2)

    # 60 天窗口（与其他源一致）
    from datetime import datetime as _dt
    bj = timezone(timedelta(hours=8))
    cutoff = (_dt.now(bj) - timedelta(days=60)).isoformat(timespec="seconds")
    fresh = [n for n in all_news if n.get("published_at", "") >= cutoff]

    fresh.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    log.info("Google News 总计: %d 条（去重 + 60 天后）", len(fresh))
    return fresh


if __name__ == "__main__":
    news = fetch()
    print(f"\n=== 抓到 {len(news)} 条 ===\n")
    for i, n in enumerate(news[:10], 1):
        print(f"{i}. [{n['source']}] {n['title']}")
        print(f"   {n['published_at']}  |  {n['url'][:80]}")
        print()
