"""
京东 POP 有自营无 — 比对器（v2：主动验证版）

核心改进：
  v1 错误做法：用我抓到的 33 本自营 SKU 池做比对（自营总量是几十万本，覆盖不全 → 误判）
  v2 正确做法：对每本 POP 书，主动去京东搜索它的书名，看搜索结果里有没有自营版

匹配策略：
  1. 用 POP 书的核心书名 + 作者 去京东移动版搜索
  2. 解析前 N 个搜索结果，挨个看是否自营
  3. 只有"搜索结果里没有任何自营版"的书才算"POP 独家"
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

from .common import http_get, get_logger

log = get_logger("jd_compare")

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


# 营销词清洗（书名标准化用）
_STOP_PATTERNS = [
    r"【[^】]*】",                          # 【自营】【正版】等
    r"\[[^\]]*\]",                          # [现货]
    r"\([^)]*\)",                           # 括号内
    r"（[^）]*）",                          # 全角括号
    r"^\s*[a-zA-Z0-9 ]+出版[^\s]*\s*",      # 开头的"中信出版社"等店铺前缀
    r"^\s*[一-鿿]{2,8}出版社\s*",   # 中文"XX出版社"开头
    r"^\s*[一-鿿]{2,8}出版\s*",     # 中文"XX出版"开头
    r"\s*正版\s*", r"\s*现货\s*", r"\s*包邮\s*",
    r"\s*礼盒\s*", r"\s*精装版?\s*", r"\s*平装\s*",
    r"\s*典藏版?\s*", r"\s*纪念版\s*",
    r"^\s*【\s*", r"\s*】\s*",  # 残留的方括号
]


def normalize_title(title: str) -> str:
    """清洗书名."""
    if not title:
        return ""
    s = title.strip()
    # 多遍清洗（清掉一个营销词后可能露出下一个）
    for _ in range(3):
        for pat in _STOP_PATTERNS:
            s = re.sub(pat, "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_core_title(title: str) -> str:
    """
    提取书名的真正核心（去掉营销/店铺前缀）。
    《一句顶一万句 刘震云作品集...》  → '一句顶一万句'
    《中信出版【官方旗舰店】真希望我父母读过...》 → '真希望我父母读过这本书'
    """
    if not title:
        return ""
    norm = normalize_title(title)

    # 查找第一个"句末"标志（冒号、空格分隔的副标题）
    # 主标题通常是"X：副标题..." 或 "X X X 别的描述"
    # 取冒号前 / 顿号前 的内容
    for sep in ["：", ":", " "]:
        if sep in norm:
            # 但不能太短（避免把 "1+1" 这种切散）
            head = norm.split(sep)[0]
            if len(head) >= 3:
                norm = head
                break

    # 限长 12 字（搜索关键词太长反而搜不到）
    return norm[:12].strip()


# ============================================================
# 主动验证：用书名去京东搜索看有没有自营版
# ============================================================

def jd_has_self_version(pop_book: dict, max_results: int = 10,
                        delay: float = 0.5) -> tuple[bool, list[dict]]:
    """
    去京东搜索 POP 书的核心标题，
    判断结果里是否包含自营版本。
    返回 (是否有自营, 搜索到的所有自营结果)

    搜索策略：先用"书名+作者"搜，结果太少时降级为只用书名搜
    """
    title = pop_book.get("title", "")
    author = pop_book.get("author", "") or ""
    core = extract_core_title(title)
    if not core:
        return False, []

    # 清洗作者名（去掉外文名常见的"·" "・"等，避免精度过窄）
    author_clean = re.sub(r"[·・\.\(\)（）\[\]【】]", "", author).strip()
    # 只取中文作者名或前几个字符
    author_short = author_clean[:6] if author_clean else ""

    # 搜索策略：先用"书名+作者"，结果太少再降级
    queries = []
    if author_short:
        queries.append(f"{core} {author_short}")
    queries.append(core)  # 兜底：纯书名

    from .jd import fetch_detail
    pop_core_normalized = normalize_title(core)

    for keyword in queries:
        url = f"https://so.m.jd.com/ware/search.action?keyword={quote(keyword)}&book=y"
        resp = http_get(url, timeout=15, extra_headers={"User-Agent": MOBILE_UA})
        if resp is None:
            continue

        skus = set(re.findall(r'item\.m\.jd\.com/product/(\d{6,14})\.html', resp.text))
        skus2 = set(re.findall(r'"sku(?:Id)?":\s*"?(\d{6,14})"?', resp.text))
        all_skus = list((skus | skus2) - {pop_book.get("sku", "")})[:max_results]

        # 不再用 SKU 长度过滤（"14位=POP"是错的，自营也有长 SKU）
        # 而是查每个 SKU 的详情，用 shop_name 真实判定
        if not all_skus:
            log.debug("搜 '%s' 未找到任何 SKU，尝试下一个 query", keyword[:30])
            continue

        # 逐个查详情
        for sku in all_skus:
            info = fetch_detail(sku)
            if info and info.get("is_self"):
                self_title = normalize_title(info.get("title", ""))
                if self_title and pop_core_normalized and pop_core_normalized in self_title:
                    return True, [info]
            time.sleep(delay)

    return False, []


def find_pop_only(self_books: list[dict], pop_books: list[dict],
                  verify_each: bool = True, delay: float = 1.5) -> list[dict]:
    """
    返回所有"POP 在卖、自营没卖"的书。

    verify_each=True (默认): 对每本 POP 书去京东搜索验证（慢但准）
    verify_each=False: 只用本次抓到的自营池比对（快但容易误判）
    """
    if not pop_books:
        return []

    if not verify_each:
        # 旧逻辑（保留以备 debug）
        log.warning("⚠ 未启用主动验证，可能误判 POP 独家")
        return _find_pop_only_local(self_books, pop_books)

    log.info("=== 主动验证模式：每本 POP 书去京东搜索是否有自营版 ===")
    log.info("待验证 %d 本 POP 书（每本约 %.1f 秒）", len(pop_books), 1.5 + delay)

    pop_only = []
    for i, b in enumerate(pop_books, 1):
        title = b.get("title", "")[:40]
        has_self, self_books_found = jd_has_self_version(b, delay=delay)
        if has_self:
            log.info("  [%d/%d] ✗《%s...》自营版存在 (SKU %s)",
                     i, len(pop_books), title,
                     self_books_found[0].get('sku') if self_books_found else '?')
        else:
            log.info("  [%d/%d] ✓《%s...》自营版未找到 → 标记 POP 独家",
                     i, len(pop_books), title)
            pop_only.append(b)
        time.sleep(delay)

    log.info("→ POP 独家（自营无）: %d 本（共验证 %d 本）",
             len(pop_only), len(pop_books))
    return pop_only


def _find_pop_only_local(self_books: list[dict], pop_books: list[dict]) -> list[dict]:
    """旧的本地池比对（不准）— 仅做兜底."""
    self_titles = {normalize_title(b.get("title", "")) for b in self_books}
    pop_only = []
    for pb in pop_books:
        pt = normalize_title(pb.get("title", ""))
        if pt and not any(pt[:6] in st for st in self_titles if st):
            pop_only.append(pb)
    return pop_only


# ============================================================
# 调试入口
# ============================================================

if __name__ == "__main__":
    import json, sys
    pop = json.load(open("data/jd_pop.json", encoding="utf-8"))
    self = json.load(open("data/jd_self.json", encoding="utf-8"))
    print(f"现有 POP {len(pop)} 本，自营 {len(self)} 本")
    print("\n=== 用主动验证模式重跑 ===")
    pop_only = find_pop_only(self, pop, verify_each=True, delay=1.5)
    print(f"\n=== 真正的 POP 独家：{len(pop_only)} 本 ===")
    for b in pop_only:
        print(f"  · 《{b.get('title','?')[:50]}》")
