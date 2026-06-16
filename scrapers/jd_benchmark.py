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
    ("赠品",   ("赠送", "赠《", "随书赠", "附赠", "附送", "赠 ")),
]


def detect_jd_perks(text: str) -> list[str]:
    """从京东商品标题/标签提取权益关键词。"""
    if not text:
        return []
    perks: list[str] = []
    for label, keywords in JD_PERK_PATTERNS:
        if any(k in text for k in keywords):
            perks.append(label)
    return perks


def query_jd_for_book(dangdang_book: dict, max_results: int = 6,
                     delay: float = 0.6) -> dict:
    """
    用一本当当书的核心标题，去京东搜索找对标版本。

    返回：
    {
      'available': True/False,           # 京东是否在售（含自营/POP 任一）
      'best_match': {...},               # 最匹配的 SKU 详情（优先自营，其次销量最高的 POP）
      'all_skus_count': int,             # 京东搜出的全部相关 SKU 数
      'self_count': int,                 # 其中自营 SKU 数
      'pop_count':  int,
    }
    """
    title = dangdang_book.get("title", "")
    author = (dangdang_book.get("author") or "").strip()
    core = extract_core_title(title)
    if not core:
        return {"available": False, "reason": "no_core_title"}

    pop_core_normalized = normalize_title(core)

    # 作者名清洗（去掉特殊字符避免精度过窄）
    author_clean = re.sub(r"[·・\.\(\)（）\[\]【】]", "", author).strip()
    author_short = author_clean[:6] if author_clean else ""

    # 搜索策略：先用"书名+作者"，没有结果再降级用纯书名
    queries = []
    if author_short:
        queries.append(f"{core} {author_short}")
    queries.append(core)

    found_skus: list[str] = []
    for keyword in queries:
        url = f"https://so.m.jd.com/ware/search.action?keyword={quote(keyword)}&book=y"
        resp = http_get(url, timeout=15, extra_headers={"User-Agent": MOBILE_UA})
        if resp is None:
            continue

        skus = set(re.findall(r'item\.m\.jd\.com/product/(\d{6,14})\.html', resp.text))
        skus2 = set(re.findall(r'"sku(?:Id)?":\s*"?(\d{6,14})"?', resp.text))
        all_skus = list((skus | skus2))[:max_results]
        if all_skus:
            found_skus = all_skus
            break

    if not found_skus:
        return {"available": False, "reason": "no_search_result"}

    # 拿销量数据（一次 API 拿所有候选 SKU 的评价数）
    sales = fetch_sales_proxy(found_skus, batch_size=20)

    # 抓详情，过滤出"和当当书是同一本"的（不再区分自营/POP）
    candidates: list[dict] = []
    for sku in found_skus:
        info = fetch_detail(sku)
        if not info:
            time.sleep(delay)
            continue
        # 名字必须重叠
        self_t = normalize_title(info.get("title", ""))
        if not self_t or pop_core_normalized not in self_t:
            time.sleep(delay)
            continue
        # 把销量并入
        sd = sales.get(sku, {})
        info["show_count"] = sd.get("show_count", 0)
        info["show_count_str"] = sd.get("show_count_str", "")
        info["comment_count_str"] = sd.get("comment_count_str", "")
        # 识别京东侧权益
        info["perks"] = detect_jd_perks(info.get("title", ""))
        candidates.append(info)
        time.sleep(delay)

    # 选最佳匹配：按销量排
    candidates.sort(key=lambda b: b.get("show_count", 0), reverse=True)
    best_match = candidates[0] if candidates else None

    return {
        "available": best_match is not None,
        "best_match": best_match,
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
    评估对标缺口（不再区分自营/POP — 只看京东这本书在没在售 + 权益对比）：
      - no_jd     京东未在售
      - perk_gap  京东在售但当当独有权益更多
      - none      权益已对齐
    """
    if not jd.get("available"):
        return "no_jd"
    best = jd.get("best_match") or {}
    dd_perks = set(dd.get("perks", []))
    jd_perks = set(best.get("perks", []))
    distinctive = dd_perks - jd_perks
    # 排除"礼盒""赠品""首发"这种通用权益（对标信号弱）
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
