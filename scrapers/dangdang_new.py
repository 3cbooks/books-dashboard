"""
当当真新书抓取器（按出版日期排序，区别于销量榜）

URL 模板:
  http://category.dangdang.com/cp{分类码}-srsort_pubdate_desc-0-0-1-1.html
  → 商品列表（每页 60 本，已按出版日倒序）
  → 但列表页不含'出版日期'字段，需要去详情页拿

策略:
  1. 抓 N 个分类的列表页（每页 60 本，取前 K 本最新的）
  2. 对每本去抓详情页，提取'出版时间'
  3. 标记是否在'近 7 天 / 近 30 天'内出版
  4. 输出独立的 books_new.json 给前端
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterator
from bs4 import BeautifulSoup, Tag

from .common import http_get, get_logger, normalize_category
from .dangdang import detect_perks, _clean_title
from . import douban_verify

log = get_logger("dangdang_new")

# 抓哪几类（每类抓多少本）
CATEGORIES = [
    ("总榜",   "01.00.00.00.00.00", "全部"),
    ("小说",   "01.03.00.00.00.00", "小说"),
    ("文学",   "01.05.00.00.00.00", "文学"),
    ("童书",   "01.21.00.00.00.00", "童书"),
    ("社科",   "01.09.00.00.00.00", "社科"),
    ("经管",   "01.41.00.00.00.00", "经管"),
    ("科普",   "01.25.00.00.00.00", "科技"),
    ("历史",   "01.07.00.00.00.00", "人文"),
]

LIST_URL = (
    "http://category.dangdang.com/cp{code}-"
    "srsort_pubdate_desc-0-0-1-{page}.html"
)


def _is_credible_pubdate(date_str: str | None, today: datetime) -> bool:
    """
    判断出版日期是否"合理"。当当不少书把出版日期填成 2030+ 来卡排序，
    这些是垃圾数据；真正合理的范围：今天往前 5 年，往后 6 个月。
    """
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        days = (today.date() - d).days
        return -180 <= days <= 365 * 5    # 上市前 6 个月 ~ 已出版 5 年
    except ValueError:
        return False

PUBDATE_RE = re.compile(r"出版时间[：:]?\s*(\d{4})\s*[-年]?\s*(\d{1,2})\s*[-月]?\s*(\d{1,2})?")


def _parse_list_item(li: Tag, fallback_category: str) -> dict | None:
    """从分类列表页的 <li> 里提取一本书的基础信息（不含出版日期）。"""
    try:
        name_a = li.select_one("p.name a") or li.select_one("a.pic")
        if not name_a:
            return None
        raw_title = (name_a.get("title") or "").strip()
        if not raw_title:
            return None
        title = _clean_title(raw_title)

        url = name_a.get("href", "")
        if url.startswith("//"):
            url = "http:" + url

        # 封面图
        img = li.select_one("a.pic img")
        cover = ""
        if img:
            cover = img.get("data-original") or img.get("src") or ""
            if cover.startswith("//"):
                cover = "http:" + cover

        # 价格
        price_el = li.select_one("span.search_now_price")
        price = price_el.get_text(strip=True) if price_el else ""

        # 出版社 + 作者：用 dd_name 属性区分（最可靠）
        publisher = ""
        authors = []
        author_root = li.select_one("p.search_book_author") or li.select_one("div.search_book_author")
        if author_root:
            for el in author_root.find_all("a"):
                dd_name = el.get("dd_name", "")
                text = el.get_text(strip=True)
                if not text:
                    continue
                if dd_name == "单品作者":
                    authors.append(text)
                elif dd_name == "单品出版社" or "出版社" in text:
                    publisher = publisher or text
        # 作者可能有多个（合著），用 / 拼起来；豆瓣搜索用第一作者就够
        author = " ".join(authors[:3])  # 最多 3 个，避免 query 过长

        # 评分
        rating = None
        star = li.select_one("span.search_star_black > span")
        if star and star.get("style"):
            try:
                pct = float(star["style"].replace("width:", "").replace("%;", "").replace("%", "").strip())
                if pct > 0:
                    rating = round(pct / 20, 1)
            except (ValueError, KeyError):
                pass

        return {
            "title": title,
            "raw_title": raw_title,
            "url": url,
            "cover": cover,
            "price": price,
            "publisher": publisher,
            "author": author,
            "rating": rating,
            "category": fallback_category,
            "perks": detect_perks(raw_title),
            "source": "当当",
        }
    except Exception as e:
        log.warning("列表条目解析失败: %s", e)
        return None


def _fetch_pubdate(detail_url: str) -> str | None:
    """从商品详情页抓出版日期，返回 ISO8601 (YYYY-MM-DD) 或 None。"""
    if not detail_url:
        return None
    resp = http_get(detail_url, encoding="gb2312", timeout=10, max_retries=2)
    if resp is None:
        return None

    m = PUBDATE_RE.search(resp.text)
    if not m:
        return None
    year, month, day = m.group(1), m.group(2), m.group(3)
    try:
        y = int(year)
        mn = max(1, min(12, int(month)))
        d = int(day) if day else 1  # 只精确到月时用 1 号兜底
        d = max(1, min(28, d))       # 防越界
        return f"{y:04d}-{mn:02d}-{d:02d}"
    except (ValueError, TypeError):
        return None


def fetch(per_category: int = 4, max_pages: int = 3) -> list[dict]:
    """
    抓多个分类按出版日排序的列表，跨多页扫描，
    过滤掉离谱的未来日期（>6 个月后）和过老的（>5 年前），
    最后保留每类前 N 本"合理日期"的书。
    """
    today = datetime.now(timezone(timedelta(hours=8)))
    seen_urls: set[str] = set()
    books: list[dict] = []

    for label, code, std in CATEGORIES:
        log.info("抓取当当真新书 [%s]...", label)
        kept = 0

        # 翻页：从第 1 页开始抓，每页 60 本，找到合理日期的书就纳入
        for page in range(1, max_pages + 1):
            if kept >= per_category:
                break
            url = LIST_URL.format(code=code, page=page)
            resp = http_get(url, encoding="gb2312", timeout=15)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select("ul.bigimg li")
            if not items:
                break

            for li in items:
                if kept >= per_category:
                    break
                item = _parse_list_item(li, std)
                if not item or item["url"] in seen_urls:
                    continue

                # 抓详情页，确认出版日期
                pub = _fetch_pubdate(item["url"])
                if not _is_credible_pubdate(pub, today):
                    continue  # 日期不合理（占位/垃圾），跳过

                # 计算 days_since_pub
                pub_date = datetime.strptime(pub, "%Y-%m-%d").date()
                item["pubdate"] = pub
                item["days_since_pub"] = (today.date() - pub_date).days
                seen_urls.add(item["url"])
                books.append(item)
                kept += 1

        log.info("  └─ 取到 %d 本（合理出版日期）", kept)

    # 按出版日期倒序（未来日期排最前 — 它们是预售/待上市的上游信号）
    books.sort(key=lambda x: x.get("pubdate") or "", reverse=True)

    # 先按当当的出版日期临时打个 status（校验阶段会修正）
    for b in books:
        days = b.get("days_since_pub")
        if days is None:
            b["pub_status"] = "unknown"
        elif days < 0:
            b["pub_status"] = "preorder"
        elif days <= 7:
            b["pub_status"] = "fresh"
        elif days <= 30:
            b["pub_status"] = "recent"
        else:
            b["pub_status"] = "older"

    # ============ 豆瓣校验 ============
    # 当当的"出版时间"和 ISBN 经常错乱（再版日期、占位数据），
    # 用豆瓣按"书名+作者"二次校准。
    # 限流敏感：只校验当当判定为'preorder'的书（最需要验证的"虚假新书"嫌疑）
    preorder_books = [b for b in books if b.get("pub_status") == "preorder"]
    if preorder_books:
        log.info("豆瓣只校验当当判定为预售的 %d 本（节省限流配额）", len(preorder_books))
        douban_verify.cross_check_books(preorder_books, today)

    # 统计
    stats = {
        "preorder": sum(1 for b in books if b["pub_status"] == "preorder"),
        "fresh":    sum(1 for b in books if b["pub_status"] == "fresh"),
        "recent":   sum(1 for b in books if b["pub_status"] == "recent"),
        "older":    sum(1 for b in books if b["pub_status"] == "older"),
        "unknown":  sum(1 for b in books if b["pub_status"] == "unknown"),
    }
    log.info("完成: 共 %d 本", len(books))
    log.info("  ├─ 预售/待上市: %d 本（上游押注信号）", stats["preorder"])
    log.info("  ├─ 真新书 ≤7天: %d 本", stats["fresh"])
    log.info("  ├─ 较新 8-30天: %d 本", stats["recent"])
    log.info("  ├─ 较旧 >30天:  %d 本", stats["older"])
    log.info("  └─ 无日期:      %d 本", stats["unknown"])
    return books


# ============================================================
# 直接运行测试
# ============================================================

if __name__ == "__main__":
    books = fetch(per_category=3)
    print(f"\n=== 抓到 {len(books)} 本（按出版日倒序）===\n")
    for i, b in enumerate(books[:15], 1):
        days = b.get("days_since_pub")
        status = b.get("pub_status", "unknown")
        flag = {
            "preorder": "🔮", "fresh": "🆕", "recent": "📚",
            "older": "  ", "unknown": "❓",
        }.get(status, "  ")
        if days is None:
            days_str = "(无日期)"
        elif days < 0:
            days_str = f"距上市 {-days} 天"
        else:
            days_str = f"已出版 {days} 天"
        perks = " ".join(f"[{p}]" for p in (b.get("perks") or []))
        print(f"{flag} #{i:>2} [{b['category']:<3}] 《{b['title'][:30]}》")
        print(f"     {b.get('pubdate') or '?'}  ({days_str}) | {b.get('publisher') or ''}")
        if perks:
            print(f"     {perks}")
        print()

    print("\n========== 关键人群 ==========")

    preorder = [b for b in books if b.get("pub_status") == "preorder"]
    print(f"\n🔮 即将上市预售书: {len(preorder)} 本")
    for b in preorder[:5]:
        print(f"  · 《{b['title'][:30]}》 — {b.get('pubdate')} (距上市 {-b.get('days_since_pub', 0)} 天)")

    fresh = [b for b in books if b.get("pub_status") == "fresh"]
    print(f"\n🆕 真本周新书 (≤7 天): {len(fresh)} 本")
    for b in fresh:
        print(f"  · 《{b['title'][:30]}》 — {b.get('pubdate')}")

    recent = [b for b in books if b.get("pub_status") == "recent"]
    print(f"\n📚 近 30 天新书: {len(recent)} 本")
    for b in recent[:5]:
        print(f"  · 《{b['title'][:30]}》 — {b.get('pubdate')}")
