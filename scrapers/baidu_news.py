"""
百度新闻搜索抓取器
原理：百度新闻搜索结果页面，把每条新闻的数据以
      <!--s-data:{"title":"...","titleUrl":"...","summary":"..."}-->
      的形式嵌入 HTML 注释。
      解析这些 JSON 比解析 CSS 类名稳定得多（不怕改版）。

抓取关键词（多关键词分别抓 → 合并去重）:
  "图书出版", "出版业", "新书", "书业", "实体书店"
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Comment

from .common import http_get, get_logger

log = get_logger("baidu_news")

# 抓哪些关键词
KEYWORDS = [
    "图书出版",
    "出版业",
    "新书",
    "书业",
    "实体书店",
]

URL = "https://news.baidu.com/ns"

# 用于剥掉百度搜索结果里的 <em>关键词高亮</em> 标签
_EM_RE = re.compile(r"</?em[^>]*>", re.I)
# 一些噪音域名/源（自媒体洗稿站，质量较低）
_BLACKLIST_DOMAINS = {
    "sohu.com",       # 大量低质聚合
}
_BLACKLIST_SOURCE_KEYWORDS = ("百家号", "搜狐网", "网易")  # 可按需调整


def _strip_em(s: str) -> str:
    return _EM_RE.sub("", s).strip() if s else ""


def _parse_relative_time(s: str) -> str | None:
    """
    把 '5小时前' / '2天前' / '2026-06-14 10:30' 转 ISO8601。
    解析失败返回 None（让上游决定是否兜底用'当前时间'）。
    """
    if not s:
        return None
    s = s.strip()
    tz = timezone(timedelta(hours=8))

    # 形如 2026-06-14 10:30:00
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{1,2})", s)
    if m:
        try:
            dt = datetime(*[int(x) for x in m.groups()], tzinfo=tz)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return None

    now = datetime.now(tz)
    m = re.match(r"(\d+)\s*分钟前", s)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.match(r"(\d+)\s*小时前", s)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.match(r"(\d+)\s*天前", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat(timespec="seconds")
    if "刚刚" in s:
        return now.isoformat(timespec="seconds")
    return None


def _is_blacklisted(source: str, url: str) -> bool:
    if not url:
        return True
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return True
    for d in _BLACKLIST_DOMAINS:
        if d in host:
            return True
    for k in _BLACKLIST_SOURCE_KEYWORDS:
        if k in (source or ""):
            return True
    return False


def _extract_one_keyword(keyword: str, limit: int = 20) -> list[dict]:
    """抓单个关键词的搜索结果。"""
    resp = http_get(
        URL,
        params={
            "word": keyword,
            "rn": str(limit),
            "tn": "news",
            "from": "news",
            "cl": "2",
            "ct": "0",  # 不限时间
        },
    )
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    results: list[dict] = []

    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        s = str(comment).strip()
        if not s.startswith("s-data:"):
            continue
        try:
            data = json.loads(s[len("s-data:"):])
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        title = _strip_em(data.get("title") or "")
        url = data.get("titleUrl") or ""
        summary = _strip_em(data.get("summary") or "")
        source = data.get("sourceName") or data.get("rtses") or ""

        if not title or not url:
            continue
        if _is_blacklisted(source, url):
            continue

        # 时间字段不一定有 — 解析失败就用当前时间兜底（标记为"今日抓取"）
        published = _parse_relative_time(
            data.get("newsTime") or data.get("time") or ""
        )

        results.append({
            "title": title,
            "url": url,
            "summary": summary[:140],   # 限长
            "source": source[:24],
            "published_at": published,  # 可能是 None
            "_keyword": keyword,        # 私有字段，便于调试 / 后续可能用
        })
    return results


def fetch() -> list[dict]:
    """聚合多关键词，去重 + 按时间倒序。"""
    seen_urls: set[str] = set()
    all_news: list[dict] = []

    for kw in KEYWORDS:
        log.info("抓取百度新闻 [%s]...", kw)
        items = _extract_one_keyword(kw, limit=15)
        new_count = 0
        for it in items:
            if it["url"] in seen_urls:
                continue
            seen_urls.add(it["url"])
            all_news.append(it)
            new_count += 1
        log.info("  └─ +%d 条（去重前 %d）", new_count, len(items))

    # 用兜底时间（北京时间 now）填充无时间数据，再按时间倒序
    from .common import now_iso
    fallback_time = now_iso()
    for n in all_news:
        if not n["published_at"]:
            n["published_at"] = fallback_time

    all_news.sort(key=lambda x: x["published_at"], reverse=True)

    # 移除内部字段
    for n in all_news:
        n.pop("_keyword", None)

    log.info("百度新闻总计: %d 条（去重后）", len(all_news))
    return all_news


# ============================================================
# 直接运行测试
# ============================================================

if __name__ == "__main__":
    news = fetch()
    print(f"\n=== 抓到 {len(news)} 条新闻 ===\n")
    for i, n in enumerate(news[:8], 1):
        print(f"{i}. [{n['source']}] {n['title']}")
        print(f"   {n['summary'][:60]}")
        print(f"   {n['published_at']}  |  {n['url'][:80]}")
        print()
