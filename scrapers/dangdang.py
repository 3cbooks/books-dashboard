"""
当当网新书榜抓取器
URL 模板:
  http://bang.dangdang.com/books/newhotsales/<分类码>-recent7-0-0-1-<页码>

分类码（dangdang 官方分类）:
  01.00.00.00.00.00  全部
  01.03.00.00.00.00  小说
  01.05.00.00.00.00  文学
  01.07.00.00.00.00  历史
  01.09.00.00.00.00  社科
  01.21.00.00.00.00  童书
  01.22.00.00.00.00  少儿
  01.25.00.00.00.00  科普
  01.41.00.00.00.00  经管
  01.43.00.00.00.00  计算机
"""
from __future__ import annotations

import re
from typing import Iterator
from bs4 import BeautifulSoup, Tag

from .common import http_get, get_logger, normalize_category


def _clean_title(raw: str) -> str:
    """
    去掉书名里的营销文案噪音：
      "XX全新小说 升维突破之作 限量礼盒版"  → "XX全新小说"
    规则：保留主标题 + 第一个空格前的副标题；后面有"专属/限量/赠/亲签..."的截断。
    """
    if not raw:
        return ""
    # 先做一些常见营销词截断
    cut_words = [
        "（", "(",
        " 当当", " 京东", " 限量", " 亲签", " 套装", " 礼盒",
        " 全新", " 现货", " 专属", " 赠", " 精装刷边",
        "亲签", "专享", "随机", " 升维", " 精装",
    ]
    title = raw.strip()
    # 取最早出现的截断点
    cut_pos = len(title)
    for w in cut_words:
        i = title.find(w)
        if 0 < i < cut_pos:
            cut_pos = i
    title = title[:cut_pos].strip()
    # 去掉重复空白
    title = re.sub(r"\s+", " ", title)
    # 兜底：如果清理后过短，回退到原标题前 30 字
    return title if len(title) >= 2 else raw[:30]

log = get_logger("dangdang")

# 抓哪几类（每类抓 1 页 = 20 本）
CATEGORIES = [
    ("全部", "01.00.00.00.00.00", "全部"),
    ("小说", "01.03.00.00.00.00", "小说"),
    ("文学", "01.05.00.00.00.00", "文学"),
    ("童书", "01.21.00.00.00.00", "童书"),
    ("社科", "01.09.00.00.00.00", "社科"),
    ("经管", "01.41.00.00.00.00", "经管"),
    ("科普", "01.25.00.00.00.00", "科技"),
    ("历史", "01.07.00.00.00.00", "人文"),
]

URL_TMPL = (
    "http://bang.dangdang.com/books/newhotsales/"
    "{code}-recent7-0-0-1-1"
)


def _parse_item(li: Tag, fallback_category: str) -> dict | None:
    """从单个 <li> 元素提取一本书的信息。"""
    try:
        # 书名 + 详情链接（在 .name > a）
        name_a = li.select_one("div.name a")
        if not name_a:
            return None
        # title 属性是完整书名（不会被截断）
        raw_title = name_a.get("title", "").strip() or name_a.get_text(strip=True)
        title = _clean_title(raw_title)
        url = name_a.get("href", "")

        # 封面图
        img = li.select_one("div.pic img")
        cover = ""
        if img:
            # data-original 是懒加载真实图，src 是占位（fallback 看 src）
            cover = img.get("data-original") or img.get("src") or ""

        # 作者（.publisher_info 里的 a — 可能有多个，第一个一般是作者）
        author = ""
        pub_info = li.select_one("div.publisher_info")
        if pub_info:
            a = pub_info.find("a")
            if a:
                author = a.get_text(strip=True)

        # 评分（.tuijian 里有"%好评" 或 .star 里 width 百分比）
        rating = None
        star = li.select_one("div.star span.level span")
        if star and star.get("style"):
            # style="width: 92.5%;" → 4.6 星（5 星制）
            try:
                pct = float(star["style"].replace("width:", "").replace("%;", "").replace("%", "").strip())
                if pct > 0:
                    rating = round(pct / 20, 1)  # 100% → 5.0
            except (ValueError, KeyError):
                pass

        # 价格（折扣价更代表市场行情）
        price = ""
        price_el = li.select_one("div.price span.price_n")
        if price_el:
            price = price_el.get_text(strip=True)

        if not title:
            return None

        return {
            "title": title,
            "author": author,
            "category": fallback_category,
            "rating": rating,
            "cover": cover,
            "url": url,
            "price": price,
            "source": "当当",
        }
    except Exception as e:
        log.warning("条目解析失败: %s", e)
        return None


def _fetch_category(code: str, std_category: str) -> Iterator[dict]:
    """抓单个分类的新书榜（默认前 20 本）。"""
    url = URL_TMPL.format(code=code)
    resp = http_get(url, encoding="gb2312")
    if resp is None:
        return

    soup = BeautifulSoup(resp.text, "lxml")
    ul = soup.find("ul", class_="bang_list")
    if not ul:
        log.warning("分类 %s 未找到榜单列表", std_category)
        return

    for li in ul.find_all("li", recursive=False):
        item = _parse_item(li, std_category)
        if item:
            # "全部"分类的书没有原始分类，用书名 + 作者 + URL 做智能归类
            if item["category"] == "全部":
                hint = f"{item['title']} {item.get('author', '')} {item.get('url', '')}"
                guessed = normalize_category(hint)
                # 智能归类失败 → 标"热销新书"（比"其他"更有信息量）
                item["category"] = guessed if guessed != "其他" else "热销"
            yield item


def fetch(per_category: int = 5) -> list[dict]:
    """
    抓所有分类的新书。
    per_category: 每个分类保留前几名（默认 5）；
                  "全部"分类作为兜底，多保留 10 本。
    """
    seen_urls: set[str] = set()  # 跨分类去重
    all_books: list[dict] = []

    for label, code, std in CATEGORIES:
        log.info("抓取当当 [%s]...", label)
        keep = 10 if label == "全部" else per_category
        count = 0
        for item in _fetch_category(code, std):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            all_books.append(item)
            count += 1
            if count >= keep:
                break
        log.info("  └─ +%d 本", count)

    log.info("当当总计: %d 本（去重后）", len(all_books))
    return all_books


# ============================================================
# 直接运行测试
# ============================================================

if __name__ == "__main__":
    import json
    books = fetch(per_category=3)
    print(f"\n=== 抓到 {len(books)} 本书 ===\n")
    for i, b in enumerate(books[:5], 1):
        print(f"{i}. [{b['category']}] {b['title'][:40]}")
        print(f"   作者: {b['author']} | 评分: {b['rating']} | 价格: {b['price']}")
    print(f"\n... 还有 {max(0, len(books)-5)} 本")
