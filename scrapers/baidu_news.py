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


def _strip_prefix(s: str, prefixes: list[str]) -> str:
    """剥掉字符串开头的固定前缀（'发布于：xxx' → 'xxx'）。"""
    if not s:
        return ""
    for p in prefixes:
        if s.startswith(p):
            return s[len(p):].strip()
    return s.strip()


def _parse_relative_time(s: str) -> str | None:
    """
    把百度返回的发布时间字符串转 ISO8601。支持的格式：
      'X分钟前' / 'X小时前' / 'X天前'   → 计算相对时间
      '刚刚'                             → 现在
      '昨天14:14' / '昨天'               → 昨天
      '前天09:33'                        → 前天
      '2024年5月23日' / '2024-05-23'    → 那一天
      '2024年5月23日 10:30'              → 带时分
    解析失败返回 None。
    """
    if not s:
        return None
    s = s.strip()
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)

    # 1. 'X分钟前' / 'X小时前' / 'X天前' / '刚刚'
    m = re.match(r"(\d+)\s*分钟前", s)
    if m:
        return (now - timedelta(minutes=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.match(r"(\d+)\s*小时前", s)
    if m:
        return (now - timedelta(hours=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.match(r"(\d+)\s*天前", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat(timespec="seconds")
    if s == "刚刚":
        return now.isoformat(timespec="seconds")

    # 2. '昨天HH:MM' / '昨天'
    m = re.match(r"昨天(?:\s*(\d{1,2})[:：](\d{1,2}))?", s)
    if m:
        d = (now - timedelta(days=1))
        hh = int(m.group(1)) if m.group(1) else d.hour
        mm = int(m.group(2)) if m.group(2) else d.minute
        return d.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat(timespec="seconds")

    # 3. '前天HH:MM' / '前天'
    m = re.match(r"前天(?:\s*(\d{1,2})[:：](\d{1,2}))?", s)
    if m:
        d = (now - timedelta(days=2))
        hh = int(m.group(1)) if m.group(1) else d.hour
        mm = int(m.group(2)) if m.group(2) else d.minute
        return d.replace(hour=hh, minute=mm, second=0, microsecond=0).isoformat(timespec="seconds")

    # 4. 完整日期：2024年5月23日 / 2024-5-23 / 2024/5/23 / (可选 时分)
    m = re.match(
        r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?"
        r"(?:[\sT]+(\d{1,2})[:：](\d{1,2}))?",
        s,
    )
    if m:
        try:
            y, mn, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4)) if m.group(4) else 0
            mm = int(m.group(5)) if m.group(5) else 0
            return datetime(y, mn, d, hh, mm, tzinfo=tz).isoformat(timespec="seconds")
        except ValueError:
            return None

    # 5. 无年份的"5月23日 10:30" / "5-23"（默认本年；若 > 今天则视为去年）
    m = re.match(r"(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?(?:[\s]+(\d{1,2})[:：](\d{1,2}))?", s)
    if m:
        try:
            mn, d = int(m.group(1)), int(m.group(2))
            hh = int(m.group(3)) if m.group(3) else 0
            mm = int(m.group(4)) if m.group(4) else 0
            year = now.year
            dt = datetime(year, mn, d, hh, mm, tzinfo=tz)
            if dt > now:
                dt = dt.replace(year=year - 1)
            return dt.isoformat(timespec="seconds")
        except ValueError:
            return None

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
            "ct": "1",     # 1 = 最近一周；2 = 一月；3 = 一年；0 = 全部
            "rsv_dl": "ns_pc",
            "ie": "utf-8",
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

        # 时间字段：百度新闻把发布日期放在多个字段里，按优先级取
        # - dispTime: '2024年5月23日'（最稳定，绝大多数新闻都有）
        # - accessibilityData.timeAriaLabel: '发布于：2024年5月23日'（兜底）
        # - newsTime / time: 旧字段（基本不存在）
        time_str = (
            data.get("dispTime")
            or _strip_prefix(((data.get("accessibilityData") or {}).get("timeAriaLabel") or ""),
                             ["发布于：", "发布于:"])
            or data.get("newsTime")
            or data.get("time")
            or ""
        )
        published = _parse_relative_time(time_str)

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
        items = _extract_one_keyword(kw, limit=30)
        new_count = 0
        for it in items:
            if it["url"] in seen_urls:
                continue
            seen_urls.add(it["url"])
            all_news.append(it)
            new_count += 1
        log.info("  └─ +%d 条（去重前 %d）", new_count, len(items))

    # 不再用"当前时间"兜底 — 这会让所有新闻显示成"刚刚"，误导用户
    # 没有日期的新闻保持 published_at = None，前端展示成 '—'

    # 过滤掉过老的新闻（>30 天）和无日期的（行业动态要保持时效）
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    cutoff = datetime.now(tz) - timedelta(days=30)
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    fresh_news = []
    too_old = 0
    no_date = 0
    for n in all_news:
        pub = n.get("published_at")
        if not pub:
            # 无日期的多是产业调研报告等 SEO 内容，不是真新闻
            no_date += 1
            continue
        if pub >= cutoff_iso:
            fresh_news.append(n)
        else:
            too_old += 1
    log.info(
        "新闻时效过滤: 保留 %d 条 | 剔除 %d 条 >30天 | 剔除 %d 条无日期",
        len(fresh_news), too_old, no_date,
    )

    # 按时间倒序
    fresh_news.sort(key=lambda x: x["published_at"], reverse=True)
    all_news = fresh_news

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
