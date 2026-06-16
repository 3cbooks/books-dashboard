"""
京东 POP 有自营无 — 比对器

输入：jd.fetch() 返回的 {self: [...], pop: [...]}
输出：jd_pop_only.json — 只在 POP 出现、自营没卖的书

匹配策略（按强度从高到低）：
  1. ISBN 精确匹配（如果 POP 和自营都有 ISBN）
  2. 书名模糊匹配（清洗营销词后比较核心标题）
  3. 作者 + 部分书名匹配（兜底）

注意：京东商品搜索页本身没有 ISBN 字段，需要点详情页才能拿。
为节省请求量，暂时用书名模糊匹配。
"""
from __future__ import annotations

import re
from typing import Iterable
from .common import get_logger

log = get_logger("jd_compare")


# 营销词清洗（书名标准化用）
_STOP_PATTERNS = [
    r"【[^】]*】",        # 【自营】【正版】【精装】等方括号词
    r"\[[^\]]*\]",        # [现货]
    r"\([^)]*\)",         # 括号内的解释
    r"（[^）]*）",        # 全角括号
    r"\s*\d+册\s*",       # "5 册""全 8 册"
    r"\s*全套\s*",
    r"\s*正版\s*",
    r"\s*现货\s*",
    r"\s*包邮\s*",
    r"\s*礼盒\s*",
    r"\s*精装版?\s*",
    r"\s*平装\s*",
    r"\s*典藏版?\s*",
    r"\s*纪念版\s*",
    r"\s*[A-Za-z0-9_\-]+\s*$",  # 末尾英文字符串（一般是营销 tag）
]


def normalize_title(title: str) -> str:
    """把书名清洗成可比对的核心字符串。"""
    if not title:
        return ""
    s = title.strip()
    for pat in _STOP_PATTERNS:
        s = re.sub(pat, "", s)
    # 去多余空白和全角空格
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[，。！？、,.!?:：]+$", "", s)
    return s.lower()


def core_keyword(title: str) -> str:
    """提取书名的核心 2-6 字关键词（用于模糊匹配）。"""
    norm = normalize_title(title)
    # 取前 6 个字符（中文书名标题往往在最前）
    return norm[:6]


def is_pop_unique(pop_book: dict, self_books: list[dict],
                  self_index: dict | None = None) -> bool:
    """
    判断一本 POP 书是否在自营池里找不到对应商品。
    """
    pop_title = pop_book.get("title", "")
    if not pop_title:
        return False

    pop_norm = normalize_title(pop_title)
    pop_core = core_keyword(pop_title)
    pop_author = (pop_book.get("author") or "").strip()

    # 已建好的 self_index 索引提速
    if self_index is None:
        self_index = build_index(self_books)

    # 1. 完全相同的清洗后书名 → 在自营池有
    if pop_norm in self_index["norm_set"]:
        return False

    # 2. 核心关键词匹配 + 作者匹配
    if pop_core and pop_author:
        candidates = self_index["by_core"].get(pop_core, [])
        for s in candidates:
            s_author = (s.get("author") or "").strip()
            if pop_author and s_author and pop_author == s_author:
                return False  # 同核心 + 同作者 → 视为同书

    # 3. 子串匹配（POP 书名包含某本自营书的核心字串）
    if pop_norm and len(pop_norm) >= 4:
        for s_norm in self_index["norm_set"]:
            if len(s_norm) >= 4 and (s_norm in pop_norm or pop_norm in s_norm):
                # 长度差异 <30% 才算同一本
                ratio = min(len(s_norm), len(pop_norm)) / max(len(s_norm), len(pop_norm))
                if ratio >= 0.7:
                    return False

    return True


def build_index(books: list[dict]) -> dict:
    """为自营书池建索引，加速比对。"""
    by_core: dict[str, list[dict]] = {}
    norm_set: set[str] = set()
    for b in books:
        norm = normalize_title(b.get("title", ""))
        core = core_keyword(b.get("title", ""))
        if norm:
            norm_set.add(norm)
        if core:
            by_core.setdefault(core, []).append(b)
    return {"by_core": by_core, "norm_set": norm_set}


def find_pop_only(self_books: list[dict], pop_books: list[dict]) -> list[dict]:
    """
    返回所有"POP 在卖、自营没卖"的书。
    """
    if not pop_books:
        return []

    self_index = build_index(self_books)
    log.info("自营索引: %d 个书名 | POP 候选: %d 本",
             len(self_index["norm_set"]), len(pop_books))

    pop_only = []
    for b in pop_books:
        if is_pop_unique(b, self_books, self_index):
            pop_only.append(b)

    log.info("→ POP 独家（自营无）: %d 本", len(pop_only))
    return pop_only


# ============================================================
# 直接测试
# ============================================================

if __name__ == "__main__":
    samples = [
        ("【官方正版】马伯庸小说作品集10册 长安十二时辰", "马伯庸小说作品集"),
        ("【正版】余华小说全集（精装版）", "余华小说全集"),
        ("龙族全套典藏版正版", "龙族全套"),
        ("【京东自营】活着 余华代表作", "活着"),
    ]
    for raw, expected in samples:
        n = normalize_title(raw)
        c = core_keyword(raw)
        print(f"原: {raw}")
        print(f"清洗: {n}  | 核心: {c}")
        print()
