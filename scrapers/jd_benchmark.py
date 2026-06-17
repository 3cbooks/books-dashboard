"""
当当 vs 京东 权益对标分析

输入：当当热卖榜里的书（带 perks 字段的子集）
输出：每本书的对标对比结构：
  {
    dangdang: {title, price, perks, rank},
    jd:       {available, sku, shop_name, price, comment_count, perks, is_self}
                ↑ available=False 表示京东根本没有同款
  }

关键：
- 用「核心书名 + 作者」去京东搜索（同 jd_compare 一样的策略）
- 找到的 SKU 取详情 + 评价数 + 识别京东侧权益（亲签/限量等）
- 对标时优先看自营版本，没有自营才看 POP
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote
from typing import Iterator

from .common import http_get, get_logger
from .jd_compare import extract_core_title, normalize_title, MOBILE_UA
from .jd import fetch_detail, fetch_sales_proxy

log = get_logger("jd_benchmark")


# 京东侧的权益识别（参考当当的规则）
JD_PERK_PATTERNS = [
    ("亲签",   ("亲签", "签名版", "作者签名")),
    ("限量",   ("限量", "限定", "编号版", "编码限量")),
    ("独家",   ("京东独家", "京东专属", "京东限定", "京东首发", "独家发售", "京东自营专享")),
    ("首发",   ("首发", "首版", "新书首发")),
    ("礼盒",   ("礼盒", "豪华版", "精装刷边", "藏书票")),
    ("赠品",   ("赠品", "赠送", "赠《", "随书赠", "附赠", "附送", "赠 ")),
]


# 对手品牌识别 — 标题/店铺名含这些词时，这本书是对手平台的卖家在京东 POP 卖，
# 不能算作"京东自己的对标版本"
RIVAL_BRANDS = (
    "当当", "新华书店", "凤凰新华", "博库", "京喜",
    "孔夫子", "天猫", "淘宝",
)


def is_rival_seller(title: str = "", shop_name: str = "") -> bool:
    """判断这本书的卖家/标题是否含对手品牌"""
    blob = f"{title} {shop_name}"
    return any(b in blob for b in RIVAL_BRANDS)


def detect_jd_perks(text: str) -> list[str]:
    """从京东商品标题/标签提取权益关键词。"""
    if not text:
        return []
    perks: list[str] = []
    for label, keywords in JD_PERK_PATTERNS:
        if any(k in text for k in keywords):
            perks.append(label)
    return perks


def query_jd_for_book(dangdang_book: dict, max_results: int = 12,
                     delay: float = 0.6) -> dict:
    """
    用一本当当书的核心标题，去京东搜索找对标版本。

    关键改进（v2）：
    - 不再只取"前一个匹配的 SKU"
    - 遍历所有候选 SKU，把所有版本的权益合并成"京东侧总权益"
      （比如京东可能有平装版无权益 + 亲签套装版有亲签礼盒，应合并）
    - best_match 仍然取销量最高的版本

    返回：
    {
      'available':       True/False,
      'best_match':      {...},          # 销量最高的同款版本
      'all_perks':       ['亲签','礼盒'], # 京东所有版本权益的并集
      'all_versions':    [{...}, ...],   # 所有同款版本的简要信息
      'all_skus_count':  int,
    }
    """
    title = dangdang_book.get("title", "")
    author = (dangdang_book.get("author") or "").strip()
    core = extract_core_title(title)
    if not core:
        return {"available": False, "reason": "no_core_title"}

    pop_core_normalized = normalize_title(core)

    author_clean = re.sub(r"[·・\.\(\)（）\[\]【】]", "", author).strip()
    author_short = author_clean[:6] if author_clean else ""

    # 搜索关键词扩展：
    # 1. "书名核心+作者"（精确）
    # 2. "书名核心"（兜底）
    # 3. "书名去尾数"（《人间小满3》→ "人间小满"，能搜到套装版）
    queries = []
    if author_short:
        queries.append(f"{core} {author_short}")
    queries.append(core)
    # 去掉书名末尾的数字（套装版通常不带"3""5"这种序号）
    core_stripped = re.sub(r"\s*\d+\s*$", "", core).strip()
    if core_stripped and core_stripped != core and len(core_stripped) >= 2:
        queries.append(core_stripped)

    # 收集所有 query 命中的 SKU 并集（之前只取第一个有结果的 query）
    all_found_skus: set[str] = set()
    for keyword in queries:
        url = f"https://so.m.jd.com/ware/search.action?keyword={quote(keyword)}&book=y"
        resp = http_get(url, timeout=15, extra_headers={"User-Agent": MOBILE_UA})
        if resp is None:
            continue
        skus = set(re.findall(r'item\.m\.jd\.com/product/(\d{6,14})\.html', resp.text))
        skus2 = set(re.findall(r'"sku(?:Id)?":\s*"?(\d{6,14})"?', resp.text))
        all_found_skus.update(skus | skus2)

    found_skus = list(all_found_skus)[:max_results]

    if not found_skus:
        return {"available": False, "reason": "no_search_result"}

    # 拿销量数据
    sales = fetch_sales_proxy(found_skus, batch_size=20)

    # 抓详情，过滤出"和当当书是同一本"的所有版本
    pop_core_stripped = re.sub(r"\s*\d+\s*$", "", core).strip()
    candidates: list[dict] = []
    skipped_rivals = 0
    for sku in found_skus:
        info = fetch_detail(sku)
        if not info:
            time.sleep(delay)
            continue

        # 关键：剔除对手品牌（当当/新华书店/...）开的 POP 店铺
        # 这种 SKU 是对手卖家在京东 POP 卖，把它的权益算给京东会误导
        if is_rival_seller(info.get("title", ""), info.get("shop_name", "")):
            skipped_rivals += 1
            time.sleep(delay)
            continue

        self_t = normalize_title(info.get("title", ""))
        if not self_t:
            time.sleep(delay)
            continue
        match_strict = pop_core_normalized in self_t
        match_loose = (
            len(pop_core_stripped) >= 3
            and pop_core_stripped in self_t
            and (not author_short or author_short in self_t)
        )
        if not (match_strict or match_loose):
            time.sleep(delay)
            continue
        sd = sales.get(sku, {})
        info["show_count"] = sd.get("show_count", 0)
        info["show_count_str"] = sd.get("show_count_str", "")
        info["comment_count_str"] = sd.get("comment_count_str", "")
        # 权益识别用 _perk_text（已经包含 title + salePropSeq 等变体字段）
        # 这样能识别到"主 SKU 标题没写但变体里有"的权益（如套装里的'亲签版'）
        perk_text = info.get("_perk_text") or info.get("title", "")
        info["perks"] = detect_jd_perks(perk_text)
        candidates.append(info)
        time.sleep(delay)

    if skipped_rivals:
        log.info("  ↪ 已剔除 %d 个对手品牌 SKU (当当/新华等)", skipped_rivals)

    if not candidates:
        return {"available": False, "reason": "no_match_after_detail"}

    # 销量降序
    candidates.sort(key=lambda b: b.get("show_count", 0), reverse=True)
    best_match = candidates[0]

    # 关键：合并所有版本的权益（京东可能在不同 SKU 里分散提供权益）
    all_perks = sorted(set(p for c in candidates for p in (c.get("perks") or [])))

    # 简化版的版本列表（前端可展开看"京东其他版本"）
    all_versions = [
        {
            "sku":              c.get("sku"),
            "title":            c.get("title"),
            "shop_name":        c.get("shop_name"),
            "is_self":          c.get("is_self"),
            "show_count_str":   c.get("show_count_str"),
            "comment_count_str": c.get("comment_count_str"),
            "perks":            c.get("perks", []),
            "detail_url":       c.get("detail_url"),
        }
        for c in candidates
    ]

    return {
        "available":      True,
        "best_match":     best_match,
        "all_perks":      all_perks,
        "all_versions":   all_versions,
        "all_skus_count": len(candidates),
    }


def benchmark_books(dangdang_books: list[dict],
                    only_with_perks: bool = True,
                    delay: float = 1.0) -> list[dict]:
    """
    主流程：对当当带权益的书逐一查京东对标。
    返回每本书的"对标条目"列表，前端可直接渲染为对比表。
    """
    targets = (
        [b for b in dangdang_books if b.get("perks")]
        if only_with_perks
        else dangdang_books
    )
    if not targets:
        log.warning("没有当当书需要对标")
        return []

    log.info("=== 对标分析: %d 本当当书 vs 京东 ===", len(targets))
    results = []
    for i, dd in enumerate(targets, 1):
        title = dd.get("title", "?")[:30]
        log.info("  [%d/%d] 《%s...》", i, len(targets), title)
        try:
            jd_data = query_jd_for_book(dd)
        except Exception as e:
            log.warning("   查询京东时异常: %s", e)
            jd_data = {"available": False, "reason": "error"}

        # 评估"对标缺口程度"
        # - 京东不卖 → 严重缺口
        # - 京东只有 POP，没自营 → 中度缺口
        # - 京东自营有但没权益 → 权益缺口
        # - 京东自营有且权益相同 → 不缺
        gap_level = _assess_gap(dd, jd_data)

        results.append({
            "dangdang": {
                "title": dd.get("title"),
                "author": dd.get("author"),
                "publisher": dd.get("publisher"),
                "price": dd.get("price"),
                "perks": dd.get("perks", []),
                "rank": dd.get("rank"),
                "category": dd.get("category"),
                "rating": dd.get("rating"),
                "url": dd.get("url"),
                "cover": dd.get("cover"),
            },
            "jd": jd_data,
            "gap_level": gap_level,
        })
        time.sleep(delay)

    log.info("=== 对标完成: %d 本 ===", len(results))
    counters = {"none": 0, "perk_gap": 0, "no_jd": 0}
    for r in results:
        counters[r["gap_level"]] = counters.get(r["gap_level"], 0) + 1
    log.info("  · 京东未在售 (no_jd):    %d 本", counters["no_jd"])
    log.info("  · 权益缺失 (perk_gap):   %d 本", counters["perk_gap"])
    log.info("  · 权益已对齐 (none):     %d 本", counters["none"])
    return results


def _assess_gap(dd: dict, jd: dict) -> str:
    """
    评估对标缺口：
      - no_jd     京东未在售
      - perk_gap  京东在售但当当独有权益更多（用京东所有版本的权益并集判断）
      - none      权益已对齐

    重要修复：用 jd['all_perks']（所有版本权益的并集）而不是 best_match.perks
    避免出现"京东其实有亲签套装版，只是销量最好的是平装版"导致漏判
    """
    if not jd.get("available"):
        return "no_jd"
    dd_perks = set(dd.get("perks", []))
    # 优先用 all_perks（所有 SKU 版本权益的并集），向后兼容老数据用 best_match.perks
    jd_perks = set(jd.get("all_perks") or
                   (jd.get("best_match") or {}).get("perks", []))
    distinctive = dd_perks - jd_perks
    distinctive_strong = distinctive - {"礼盒", "赠品", "首发"}
    if distinctive_strong:
        return "perk_gap"
    return "none"


# ============================================================
# 直接测试入口
# ============================================================

if __name__ == "__main__":
    import json
    books = json.load(open("data/books.json", encoding="utf-8"))
    perk_books = [b for b in books if b.get("perks")]
    print(f"当当带权益书 {len(perk_books)} 本，开始对标...\n")
    results = benchmark_books(perk_books, delay=1.5)

    # 打印结果
    print("\n=== 对标结果 ===")
    for r in results:
        dd = r["dangdang"]
        jd = r["jd"]
        gap = r["gap_level"]
        print(f"\n《{dd['title'][:40]}》 [{','.join(dd['perks'])}] gap={gap}")
        print(f"  当当: {dd.get('price','')} 排名 #{dd.get('rank','?')}")
        if jd.get("available"):
            best = jd["best_match"]
            shop_label = "自营" if best.get("is_self") else "POP"
            print(f"  京东 {shop_label}: {best.get('title','')[:35]}")
            print(f"        店铺: {best.get('shop_name','')[:30]}")
            print(f"        销量近期: {best.get('show_count_str','-')} | 价格: {best.get('price','-')}")
            print(f"        权益: {','.join(best.get('perks', [])) or '(无)'}")
        else:
            print(f"  京东: ✗ {jd.get('reason','未找到')}")

    # 保存
    json.dump(results, open("data/benchmark.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\n已保存到 data/benchmark.json")
