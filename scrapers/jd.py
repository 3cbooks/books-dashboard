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

# 京东 m 站搜索（用来广搜 POP 书）
SEARCH_URL = "https://so.m.jd.com/ware/search.action"

# 销量代理 API（核心）— 评价数 API 公开，1 次能查 100 个 SKU
COMMENT_API = "https://club.jd.com/comment/productCommentSummaries.action"

# 移动版详情页（带完整商品数据的 SSR 页面）
DETAIL_M_URL = "https://item.m.jd.com/product/{sku}.html"
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# ============================================================
# 第一步：拿 SKU 列表
# ============================================================

# 用于在京东搜索找 POP 商家书的关键词集合
# 一些"POP 商家更喜欢挂"的关键词更容易出 POP 结果
SEARCH_KEYWORDS = [
    "图书 正版", "新华书店", "出版社旗舰店",
    "小说", "儿童读物", "教辅", "畅销书",
    "经管 励志", "心理学",
]


def fetch_pop_skus_via_search(max_per_query: int = 20) -> set[str]:
    """
    通过京东 m 站搜索广搜疑似 POP 的 SKU。

    注意：以前误以为"14 位 SKU = POP"。实际上少数自营店（比如"XX京东自营旗舰店"）
    也用 14 位 SKU。所以这里返回'疑似 POP'集合，最终判定要靠详情页 shop_name 检查。
    """
    pop_skus: set[str] = set()
    for kw in SEARCH_KEYWORDS:
        url = SEARCH_URL
        params = {"keyword": kw, "book": "y", "page": "1"}
        resp = http_get(
            url, params=params, timeout=15, max_retries=2,
            extra_headers={
                "User-Agent": MOBILE_UA,
                "Referer": "https://so.m.jd.com/",
            },
        )
        if resp is None:
            continue
        # 取所有 SKU（14+ 位优先，因为 8 位短 SKU 多半是自营）
        # 自营误混情况让 fetch_detail 阶段的 is_self 来甄别
        skus = set(re.findall(r'(?:item\.m\.jd\.com/product/|"sku(?:Id)?":\s*"?)(\d{11,14})', resp.text))
        before = len(pop_skus)
        pop_skus.update(s for s in skus if len(s) >= 11)
        log.info("[POP 搜] '%s': +%d (累计 %d)", kw, len(pop_skus) - before, len(pop_skus))
        time.sleep(2)
    return pop_skus


def fetch_self_skus_via_search(max_per_query: int = 20) -> set[str]:
    """
    通过京东搜索 + 榜单页广搜京东自营图书 SKU。
    自营 SKU 通常是 7-9 位。
    """
    self_skus: set[str] = set()

    # 1. 榜单页（已知能拿到 SKU）
    for label, cat in CATEGORIES:
        url = f"{BOOKTOP_URL}?category={cat}"
        resp = http_get(url, timeout=15, max_retries=2)
        if resp:
            skus = set(re.findall(r'"skuId":\s*"?(\d{6,12})"?', resp.text))
            self_skus.update(s for s in skus if 6 <= len(s) <= 10)  # 自营特征
        time.sleep(2)

    # 2. 自营专搜 — 加 ev= 参数限定 京东图书 品牌
    for kw in ["小说", "童书", "文学", "经管", "历史", "心理"]:
        params = {"keyword": kw, "book": "y", "ev": "exbrand_京东图书"}
        resp = http_get(
            SEARCH_URL, params=params, timeout=15,
            extra_headers={"User-Agent": MOBILE_UA, "Referer": "https://so.m.jd.com/"},
        )
        if resp:
            skus = set(re.findall(r'item\.m\.jd\.com/product/(\d{6,10})\.html', resp.text))
            self_skus.update(skus)
        time.sleep(2)

    log.info("[自营 SKU 池] 总计 %d 个", len(self_skus))
    return self_skus


# ============================================================
# 销量过滤（核心：通过评价数 API 拿"近期销量"代理）
# ============================================================

def parse_show_count(s: str) -> int:
    """
    把京东的 ShowCountStr 转成最小销量数值：
      "1万+" → 10000
      "5000+" → 5000
      "1000+" → 1000
      "100+" → 100
      "0" / "" / None → 0
    """
    if not s:
        return 0
    s = str(s).strip()
    if "万+" in s:
        m = re.match(r"(\d+)万\+", s)
        return int(m.group(1)) * 10000 if m else 10000
    m = re.match(r"(\d+)\+", s)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1))
    return 0


def fetch_sales_proxy(skus: list[str], batch_size: int = 50) -> dict[str, dict]:
    """
    批量拉评价数 API，为每个 SKU 取销量代理。
    返回 {sku: {show_count: 100, comment_count: '1万+', avg_score: 5}}
    每次 API 调用最多 100 个 SKU，我们每批 50 比较安全。
    """
    out: dict[str, dict] = {}
    for i in range(0, len(skus), batch_size):
        batch = skus[i:i+batch_size]
        params = {"referenceIds": ",".join(batch)}
        resp = http_get(COMMENT_API, params=params, timeout=15, max_retries=2)
        if resp is None:
            continue
        try:
            data = resp.json()
        except Exception:
            # 偶尔是 JSONP 格式
            text = resp.text.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start > 0:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    continue
            else:
                continue

        for item in data.get("CommentsCount", []):
            sku = str(item.get("SkuId") or item.get("ProductId") or "")
            if not sku:
                continue
            out[sku] = {
                "show_count_str": item.get("ShowCountStr") or "",
                "comment_count_str": item.get("CommentCountStr") or "",
                "show_count": parse_show_count(item.get("ShowCountStr")),
                "comment_count": parse_show_count(item.get("CommentCountStr")),
                "avg_score": item.get("AverageScore", 0),
                "good_rate": item.get("GoodRateShow", 0),
            }
        log.info("[销量查询] %d-%d / %d", i, i+len(batch), len(skus))
        time.sleep(1.5)
    return out


# ============================================================
# 旧的 SKU 列表函数（保留以备）
# ============================================================

def fetch_sku_list() -> list[str]:
    """从京东图书榜单页抓 SKU 列表，去重返回。（旧接口，保留）"""
    all_skus: set[str] = set()
    for label, cat_code in CATEGORIES:
        url = f"{BOOKTOP_URL}?category={cat_code}"
        resp = http_get(url, timeout=15, max_retries=2)
        if resp is None:
            log.warning("京东榜单 [%s] 失败", label)
            continue
        skus = set(re.findall(r'"skuId":\s*"?(\d{6,12})"?', resp.text))
        log.info("京东榜单 [%s]: +%d 个 SKU", label, len(skus))
        all_skus.update(skus)
        time.sleep(2)

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


# 从京东商品页"上榜信息"提取细分品类（京东详情页里散布的榜单字段）
# - "name":"家教方法图书热卖榜第3名"  → 家教方法
# - "longTitle":"文学动漫金榜"        → 文学动漫
# - "shortTitle":"中国当代小说"       → 中国当代小说
_CAT_RANK_RE = re.compile(r'"(?:name|longTitle|shortTitle|channelEntryTitle)"\s*:\s*"([^"]+)"')
_CAT_TRIM_RE = re.compile(r'(?:图书)?(?:热卖)?(?:总)?榜(?:第\d+名)?$|^榜单|金榜?$')

# 太宽泛的品类标签，过滤掉
_CAT_BLACKLIST = {"图书", "热卖", "京东", "新书", "总榜", "畅销榜", "新书榜",
                  "热卖榜", "图书热卖榜", "图书榜", "畅销", "新品"}


def _extract_jd_category(text: str) -> str | None:
    """
    从京东商品 HTML 里抽取细分品类。

    京东商品页里的"榜单"字段是细分品类的最稳信号：
      "name":"家教方法图书热卖榜第3名"     → 家教方法
      "longTitle":"中国当代小说图书榜"      → 中国当代小说
      "channelEntryTitle":"心理学图书热卖榜"→ 心理学

    严格只抓"X榜"模式，避免误抓售后承诺/书名。
    """
    cats: list[tuple[str, int]] = []  # (品类名, 优先级)

    # 优先级 1: "name":"X图书热卖榜第N名" — 最稳，只在榜单字段出现
    for m in re.finditer(r'"name"\s*:\s*"([^"]+?(?:图书|童书)?(?:热卖)?榜(?:第\d+名)?)"', text):
        s = m.group(1)
        cleaned = re.sub(r'(?:图书|童书)?(?:热卖)?榜(?:第\d+名)?$', '', s).strip()
        if cleaned and cleaned not in _CAT_BLACKLIST and 2 <= len(cleaned) <= 12:
            cats.append((cleaned, 1))

    # 优先级 2: "longTitle":"XXX图书榜" / "channelEntryTitle":"XXX图书热卖榜"
    for key in ["longTitle", "channelEntryTitle", "shortTitle"]:
        for m in re.finditer(rf'"{key}"\s*:\s*"([^"]+?)(?:图书|童书)?(?:热卖)?(?:金)?榜"', text):
            cleaned = m.group(1).strip()
            if cleaned and cleaned not in _CAT_BLACKLIST and 2 <= len(cleaned) <= 12:
                cats.append((cleaned, 2))

    if not cats:
        return None

    # 选优先级最高 + 最频繁出现的
    cats.sort(key=lambda x: x[1])
    return cats[0][0]


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

    # 提取细分品类（前端展示用）
    out["category"] = _extract_jd_category(text)

    return out


# ============================================================
# 第三步：综合
# ============================================================

def fetch(min_show_count: int = 100, max_pop_to_check: int = 100) -> dict:
    """
    新流程（按你的业务逻辑）：

    1. 广搜京东 POP 书 SKU（不限榜单，14 位 SKU 特征）
    2. 批量查询销量代理（评价数 API），过滤 ShowCount >= min_show_count
    3. 也抓一份京东自营 SKU 池作为参考
    4. 对每本"销量过关"的 POP 书，主动验证自营有没有同款

    参数:
      min_show_count: 销量过滤阈值，默认 100（你说的）
      max_pop_to_check: 详情页查询上限（每个详情 1 次请求，限制成本）
    """
    log.info("=== 京东 POP 缺口分析 (销量阈值=%d) ===", min_show_count)

    # ============ Step 1: 广搜 POP SKU ============
    log.info("Step 1: 广搜 POP 书 SKU...")
    pop_skus = fetch_pop_skus_via_search()
    if not pop_skus:
        log.error("✗ 未拿到任何 POP SKU")
        return {"self": [], "pop": [], "stats": {"reason": "no_pop_skus"}}
    log.info("  → 共抓到 %d 个 POP 候选 SKU", len(pop_skus))

    # ============ Step 2: 销量过滤 ============
    log.info("Step 2: 查销量代理（评价数 API）...")
    pop_skus_list = list(pop_skus)
    sales_data = fetch_sales_proxy(pop_skus_list)
    log.info("  → 拿到 %d 个 SKU 的销量数据", len(sales_data))

    qualified_pop_skus = [
        sku for sku in pop_skus_list
        if sales_data.get(sku, {}).get("show_count", 0) >= min_show_count
    ]
    log.info("  → 销量 ≥ %d 的 POP SKU: %d 个", min_show_count, len(qualified_pop_skus))
    # 限量抓详情
    qualified_pop_skus = qualified_pop_skus[:max_pop_to_check]

    # ============ Step 3: 自营 SKU 池（用于快速排除） ============
    log.info("Step 3: 抓京东自营 SKU 池...")
    self_skus = fetch_self_skus_via_search()

    # ============ Step 4: 抓详情 + 真实分组 ============
    # 注意：Step 1 用 SKU 长度筛 POP，但有些自营店铺也用长 SKU
    # （如"时光学文具京东自营旗舰店"的 SKU 是 14 位但实际是自营）
    # 所以这里要用 fetch_detail 返回的 is_self 重新分组
    log.info("Step 4: 抓 %d 本疑似 POP 书的详情，并按 shop_name 真实分组...",
             len(qualified_pop_skus))
    pop_books: list[dict] = []
    misclassified_self: list[dict] = []  # 错分到 POP 池的自营书
    for i, sku in enumerate(qualified_pop_skus, 1):
        info = fetch_detail(sku)
        if info:
            sd = sales_data.get(sku, {})
            info["show_count"] = sd.get("show_count", 0)
            info["show_count_str"] = sd.get("show_count_str", "")
            info["comment_count_str"] = sd.get("comment_count_str", "")
            info["avg_score"] = sd.get("avg_score", 0)
            # 关键：用详情页的 shop_name 重新判断
            if info.get("is_self"):
                misclassified_self.append(info)
                log.debug("  ↪ SKU %s 实际是自营 (店铺: %s)", sku, info.get("shop_name", ""))
            else:
                pop_books.append(info)
        if i % 10 == 0:
            log.info("  详情进度 %d/%d (POP %d, 误分自营 %d)",
                     i, len(qualified_pop_skus), len(pop_books), len(misclassified_self))
        time.sleep(1)

    if misclassified_self:
        log.info("⚠ %d 个'14位 SKU'其实是自营（按店铺名识别），已剔除出 POP 池",
                 len(misclassified_self))

    # 抓自营详情（少量，只用来作为快速排除池）
    log.info("Step 5: 抓自营详情池（最多 60 个）...")
    self_books: list[dict] = list(misclassified_self)  # 把上一步误分到 POP 池里的自营也并入
    for i, sku in enumerate(list(self_skus)[:60], 1):
        info = fetch_detail(sku)
        if info and info.get("is_self"):
            self_books.append(info)
        time.sleep(1)
        if i % 20 == 0:
            log.info("  自营详情进度 %d", i)

    log.info("=== 完成 ===")
    log.info("  POP（销量过关）: %d 本", len(pop_books))
    log.info("  自营池: %d 本", len(self_books))

    return {
        "self": self_books,
        "pop": pop_books,
        "stats": {
            "pop_skus_found": len(pop_skus),
            "pop_with_sales": len(sales_data),
            "pop_qualified": len([s for s in pop_skus_list
                                  if sales_data.get(s, {}).get("show_count", 0) >= min_show_count]),
            "min_show_count": min_show_count,
        },
    }


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
