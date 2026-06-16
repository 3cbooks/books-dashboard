"""
京东图书 POP / 自营 抓取器（基于 requests + 移动版详情页）

策略发现:
  - 京东 PC 搜索页 / Playwright 抓取被反爬严重屏蔽（GitHub Actions 上 0 数据）
  - 但 京东图书榜单页（book.jd.com/booktop）是 SSR 渲染，能直接拿 SKU 列表
  - 京东移动版详情页（item.m.jd.com/product/{sku}.html）SSR 渲染所有字段
  - 两个都对 requests 友好，无需 Playwright

流程:
  1. fetch_sku_list()  从图书榜单页拿 ~150-200 个 SKU
  2. fetch_detail()    对每个 SKU 拉移动版详情页，提取 venderId/出版社/作者/书名
  3. fetch()           汇总 → 分类成 self / pop 两组
"""
from __future__ import annotations

import json
import re
import time
from typing import Iterator
from urllib.parse import quote

from .common import http_get, get_logger

log = get_logger("jd")

# 京东图书榜单入口（SSR、对 requests 友好）
# category 编码: 4 是图书一级分类，二级分类如下
BOOKTOP_URL = "https://book.jd.com/booktop/0-0-0.html"
CATEGORIES = [
    ("综合", "4-0-0"),
    ("小说", "4-90-0"),
    ("童书", "4-91-0"),
    ("文学", "4-92-0"),
    ("社科", "4-93-0"),
]

# 移动版详情页（带完整商品数据的 SSR 页面）
DETAIL_M_URL = "https://item.m.jd.com/product/{sku}.html"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ============================================================
# 第一步：拿 SKU 列表
# ============================================================

def fetch_sku_list() -> list[str]:
    """从京东图书榜单页抓 SKU 列表，去重返回。"""
    all_skus: set[str] = set()
    for label, cat_code in CATEGORIES:
        url = f"{BOOKTOP_URL}?category={cat_code}"
        resp = http_get(url, timeout=15, max_retries=2)
        if resp is None:
            log.warning("京东榜单 [%s] 失败", label)
            continue
        # SKU 在 "skuId":"xxxxxxxxx" 字段里
        skus = set(re.findall(r'"skuId":\s*"?(\d{6,12})"?', resp.text))
        log.info("京东榜单 [%s]: +%d 个 SKU", label, len(skus))
        all_skus.update(skus)
        time.sleep(2)

    # 也抓一个"新书榜"
    url = f"{BOOKTOP_URL}?orderType=1&category=4-0-0"
    resp = http_get(url, timeout=15)
    if resp:
        skus = set(re.findall(r'"skuId":\s*"?(\d{6,12})"?', resp.text))
        log.info("京东新书榜: +%d 个 SKU", len(skus))
        all_skus.update(skus)

    log.info("京东 SKU 总数（去重后）: %d", len(all_skus))
    return list(all_skus)


# ============================================================
# 第二步：抓详情页，提取关键字段
# ============================================================

# 详情页里的 JSON 数据是分散嵌入的，用一组 regex 提取
DETAIL_PATTERNS = {
    # "Author":"马伯庸"
    "author":     re.compile(r'"Author"\s*:\s*"([^"]+)"'),
    # "Publishers":"民主与建设出版社"
    "publisher":  re.compile(r'"Publishers"\s*:\s*"([^"]+)"'),
    # "venderId":"1000005647"
    "vender_id":  re.compile(r'"venderId"\s*:\s*"?(\d+)"?'),
    # "shopName":"博集天卷京东自营旗舰店"
    "shop_name":  re.compile(r'"shopName"\s*:\s*"([^"]+)"'),
    # 商品名（在 <title> 或 "name" 字段）
    "title":      re.compile(r'<title>([^<]+)</title>'),
    # "PublishingTime":"2025-08"  或 "出版时间"
    "pubdate":    re.compile(r'"(?:PublishingTime|出版时间)"\s*:\s*"([^"]+)"'),
    # ISBN: 在 detailParams 里 "ISBN":"9787510482427"
    "isbn":       re.compile(r'"ISBN"\s*:\s*"([^"]+)"'),
    # "skuStatus":"1" → 1 在售 / 0 下架
    "sku_status": re.compile(r'"skuStatus"\s*:\s*"?(\d+)"?'),
}


def fetch_detail(sku: str) -> dict | None:
    """抓单个 SKU 的移动版详情页，返回结构化数据。"""
    url = DETAIL_M_URL.format(sku=sku)
    resp = http_get(
        url, timeout=10, max_retries=2,
        extra_headers={"User-Agent": MOBILE_UA},
    )
    if resp is None:
        return None

    text = resp.text
    if len(text) < 5000:
        # 太小通常是被反爬返回了占位页
        return None

    out: dict = {"sku": sku, "url": url, "detail_url": f"https://item.jd.com/{sku}.html"}
    for key, pat in DETAIL_PATTERNS.items():
        m = pat.search(text)
        if m:
            out[key] = m.group(1).strip()

    # 清理标题（移除噪音）
    title = out.get("title", "")
    if title:
        # 标题格式："xxxxx【图片 价格 品牌 评论】-京东"
        title = re.sub(r"【[^】]*】[-\s]*京东\s*$", "", title)
        title = re.sub(r"-京东\s*$", "", title)
        title = re.sub(r"【[^】]*】[-\s]*京东图书\s*$", "", title)
        title = re.sub(r"-\s*京东图书\s*$", "", title)
        out["title"] = title.strip()

    # 排除占位 / 已下架 SKU
    title = out.get("title", "")
    if not title or title in ("京东购物-商品详情", "京东", "京东(JD.COM)"):
        return None
    if "京东(JD.COM)" in title or len(title) < 4:
        return None

    # 排除非图书（榜单里偶尔混入家电/家居等非图书 SKU）
    NON_BOOK_KW = ["机", "家具", "电器", "电视", "冰箱", "洗衣", "空调", "笔记本",
                   "手机", "电脑", "镜头", "厨具", "床", "柜", "灶"]
    pub = out.get("publisher", "")
    # 没有出版社字段 + 标题含家电关键词 → 非图书
    if not pub and any(k in title for k in NON_BOOK_KW):
        return None

    # 判断是否京东自营：shopName 包含"京东自营"或"自营旗舰店"
    shop = out.get("shop_name", "")
    out["is_self"] = bool(
        shop and ("京东自营" in shop or "自营旗舰店" in shop)
    )

    return out


# ============================================================
# 第三步：综合
# ============================================================

def fetch(max_skus: int = 80) -> dict:
    """
    完整流程：抓榜单 → 抓详情 → 分组成 self / pop。
    max_skus: 限制详情页抓取数量，避免触发反爬（每个 SKU 一次请求）。
    """
    log.info("=== 京东图书抓取 (max_skus=%d) ===", max_skus)
    skus = fetch_sku_list()
    if not skus:
        log.error("✗ 未拿到任何 SKU")
        return {"self": [], "pop": []}

    # 限量
    skus = skus[:max_skus]
    log.info("将抓取 %d 个 SKU 的详情（每个停 1 秒）...", len(skus))

    self_books: list[dict] = []
    pop_books: list[dict] = []

    for i, sku in enumerate(skus, 1):
        info = fetch_detail(sku)
        if info:
            (self_books if info.get("is_self") else pop_books).append(info)
        if i % 10 == 0:
            log.info("  进度 %d/%d (自营 %d / POP %d)",
                     i, len(skus), len(self_books), len(pop_books))
        # 礼貌延迟（详情页比较多，慢一点稳）
        time.sleep(1)

    log.info("=== 完成 ===")
    log.info("  自营 (jd_self): %d 本", len(self_books))
    log.info("  POP  (jd_pop):  %d 本", len(pop_books))
    return {"self": self_books, "pop": pop_books}


# ============================================================
# 直接运行测试
# ============================================================

if __name__ == "__main__":
    # 小量测试：每个分类抓 20 个 SKU 的详情
    result = fetch(max_skus=20)
    print(f"\n=== 自营 ({len(result['self'])} 本) ===")
    for b in result["self"][:3]:
        print(f"  · {b.get('title', '?')[:50]}")
        print(f"    {b.get('author','-')} | {b.get('publisher','-')} | {b.get('pubdate','-')}")
        print(f"    [{b.get('shop_name','-')}]")
    print(f"\n=== POP ({len(result['pop'])} 本) ===")
    for b in result["pop"][:5]:
        print(f"  · {b.get('title', '?')[:50]}")
        print(f"    {b.get('author','-')} | {b.get('publisher','-')} | {b.get('pubdate','-')}")
        print(f"    [{b.get('shop_name','-')}]")
