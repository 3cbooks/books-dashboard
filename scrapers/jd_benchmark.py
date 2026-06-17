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
# （注意："京喜"是京东子品牌，不算对手；但它的权益质量差，best_match 选择时降级，详见 _shop_priority）
RIVAL_BRANDS = (
    "当当", "新华书店", "凤凰新华", "博库",
    "孔夫子", "天猫", "淘宝",
)


def _shop_priority(book: dict) -> int:
    """
    给京东每个候选 SKU 打优先级分（用于挑 best_match）：
      - 标准京东自营（果麦/博集天卷/中信等出版社的京东自营旗舰店）  → 100
      - POP 商家                                                    → 10
      - 京喜自营 (京东低价子品牌，权益少质量参差，业务上不愿对标)    → 5

    京喜被压到比 POP 还低 —— 业务方反馈：京喜自营官方店的权益质量不能代表京东侧
    实际能力，宁可对标普通 POP 店（一般是出版社直营的店）。
    """
    shop = book.get("shop_name", "") or ""
    if not shop:
        return 0
    # 标准京东自营（最高优先级）
    if ("京东自营" in shop or "自营旗舰店" in shop) and "京喜" not in shop:
        return 100
    # 京喜自营 — 业务降级
    if "京喜" in shop:
        return 5
    # 普通 POP
    return 10


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


def _tokenize_for_match(title: str) -> list[str]:
    """
    把当当书名拆成"用于匹配的关键词"列表。
    例：'大中华寻宝记城市系列 泉州寻宝记' →
        ['大中华寻宝记城市', '大中华寻宝记', '泉州寻宝记']
    比 'in' 字串匹配更宽松，能应对京东和当当书名"空格位置不一致"的情况。
    """
    if not title:
        return []
    norm = normalize_title(title)
    # 先按空格 + 常见分隔结构切（"系列""·"等）
    parts = re.split(r"[\s·•·:\-]+|系列", norm)
    tokens = [p.strip() for p in parts if p and len(p.strip()) >= 2]

    # 处理"X+修饰后缀"模式：去掉常见后缀拿到主干
    extra = []
    suffix_modifiers = ["城市", "儿童", "少儿", "亲子", "古代", "现代", "中国"]
    for t in tokens:
        for sfx in suffix_modifiers:
            if t.endswith(sfx) and len(t) > len(sfx) + 1:
                stripped = t[:-len(sfx)]
                if stripped not in tokens and stripped not in extra:
                    extra.append(stripped)
    tokens.extend(extra)

    if norm not in tokens:
        tokens.append(norm)
    return tokens


# 标志性"版本/纪念/特殊"词 — 用于做细颗粒度的标题相似度匹配
# 当当书名里如果出现这些词，京东 SKU 标题里也含相同词的优先选
_VERSION_KEYWORDS = [
    # 纪念/版本
    "十周年", "二十周年", "三十周年", "周年纪念", "纪念版", "纪念",
    "初版", "首版", "再版", "新版", "复刻", "复刻版",
    "典藏", "珍藏", "精装", "豪华", "限量",
    # 内容/赠品
    "亲签", "签名", "印签", "特签", "礼盒", "护身符",
    "全集", "套装", "全册", "全套", "选集",
    # 时代/作者关联
    "仙逝", "逝世", "诞辰", "百年", "九十年代",
    # 特殊版式
    "图文", "插图", "彩图", "彩绘", "绘本",
]


def _extract_version_keywords(title: str) -> set[str]:
    """从书名里提取出现的'版本/纪念/特殊词'集合（用于细粒度相似度匹配）"""
    if not title:
        return set()
    norm = normalize_title(title)
    found = set()
    for kw in _VERSION_KEYWORDS:
        if kw in norm:
            found.add(kw)
    return found


def _recheck_for_self(core: str, dangdang_book: dict,
                      existing_candidates: list[dict],
                      dd_serial: str | None,
                      dd_serial_prefix: str | None,
                      pop_core_stripped: str,
                      keyword_tokens: list[str],
                      author_short: str,
                      max_results: int = 20,
                      delay: float = 0.6) -> dict | None:
    """
    复查：当初始对标 best_match 是 POP 时，主动用更宽的搜索找自营 SKU。

    目的：防止"营销词污染初始搜索"导致漏掉自营版。
    例：当当《真实之书 印签版 …》初始搜 12 个 SKU 没命中自营 15385432，
        本函数用纯 core 多召一些 SKU 专门挑自营。

    返回：找到的"自营且匹配同款"的 SKU info；没找到返回 None。
    """
    existing_skus = {c.get("sku") for c in existing_candidates}
    pop_core_normalized = normalize_title(core)
    title = dangdang_book.get("title", "") or ""

    # 用更宽的查询：纯核心标题 + 去尾数版 + 丛书拆词 + 分隔符尾段（不带作者，召回更广）
    queries = [core]
    core_no_serial = re.sub(r"\s*\d+\s*$", "", core).strip()
    if core_no_serial and core_no_serial != core and len(core_no_serial) >= 2:
        queries.append(core_no_serial)
    # 丛书前缀拆解：《财之道丛书・经营十二条》→ "经营十二条"
    m_sub = re.search(r"[一-鿿]{2,8}丛书[\s·・\-]*([一-鿿]{2,12})", title)
    if m_sub:
        sub = m_sub.group(1).strip()
        if sub and sub not in queries:
            queries.append(sub)
    # 分隔符尾段：《X・Y》→ Y
    parts = re.split(r"[・·:：]", title)
    if len(parts) >= 2:
        tail = parts[-1].strip()
        tail = re.sub(r"[（(][^）)]*[)）]", "", tail).strip()
        if tail and 2 <= len(tail) <= 12 and tail not in queries:
            queries.append(tail)

    new_skus: list[str] = []
    for keyword in queries:
        url = f"https://so.m.jd.com/ware/search.action?keyword={quote(keyword)}&book=y"
        resp = http_get(url, timeout=15, extra_headers={"User-Agent": MOBILE_UA})
        if resp is None:
            continue
        skus_a = set(re.findall(r'item\.m\.jd\.com/product/(\d{6,14})\.html', resp.text))
        skus_b = set(re.findall(r'"sku(?:Id)?":\s*"?(\d{6,14})"?', resp.text))
        for s in (skus_a | skus_b):
            if s not in existing_skus and s not in new_skus:
                new_skus.append(s)
        if len(new_skus) >= max_results:
            break

    if not new_skus:
        return None

    # 只查这些"新出现的 SKU"详情，找第一个自营且匹配的
    for sku in new_skus[:max_results]:
        info = fetch_detail(sku)
        if not info:
            time.sleep(delay)
            continue
        if not info.get("is_self"):
            time.sleep(delay)
            continue
        if is_rival_seller(info.get("title", ""), info.get("shop_name", "")):
            time.sleep(delay)
            continue

        self_t = normalize_title(info.get("title", ""))
        if not self_t:
            time.sleep(delay)
            continue

        # 序号守护
        if dd_serial:
            jd_t_full = info.get("title", "")
            has_serial = (
                dd_serial in jd_t_full
                or re.search(rf"全\s*\d+\s*册|套装\s*\d|1\s*\+\s*2", jd_t_full)
            )
            if not has_serial:
                time.sleep(delay)
                continue

        # 同款匹配（与主流程一致的三重判定）
        match_strict = pop_core_normalized in self_t
        match_loose = (
            len(pop_core_stripped) >= 3
            and pop_core_stripped in self_t
            and (not author_short or author_short in self_t)
        )
        match_token = (
            len(keyword_tokens) >= 2
            and sum(1 for t in keyword_tokens if t in self_t) >= 2
        )
        if not (match_strict or match_loose or match_token):
            time.sleep(delay)
            continue

        # 命中：补全权益识别
        info["show_count"] = 0  # 复查时不要求销量数据
        info["show_count_str"] = ""
        info["comment_count_str"] = ""
        perk_text = info.get("_perk_text") or info.get("title", "")
        info["perks"] = detect_jd_perks(perk_text)
        return info

    return None


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
    # 4. 标题里"系列"/"丛书"等分隔后的副标题部分
    #    《大中华寻宝记城市系列 泉州寻宝记》→ "泉州寻宝记"
    #    《财之道丛书・经营十二条》→ "经营十二条"
    queries = []
    if author_short:
        queries.append(f"{core} {author_short}")
    queries.append(core)
    # 去掉书名末尾的数字（套装版通常不带"3""5"这种序号）
    core_stripped = re.sub(r"\s*\d+\s*$", "", core).strip()
    if core_stripped and core_stripped != core and len(core_stripped) >= 2:
        queries.append(core_stripped)
    # 取标题里的副词条（"系列 X" "X 系列" 后的子标题），如"泉州寻宝记"
    full_norm = normalize_title(title)
    # 找形如 "X系列 Y" 或 "X 系列 Y"，取 Y
    m_sub = re.search(r"系列\s*([一-鿿]{2,10})$", full_norm)
    if m_sub:
        sub = m_sub.group(1).strip()
        if sub and sub not in queries:
            queries.append(sub)
    # 找形如 "X丛书Y" 或 "X丛书·Y" 或 "X丛书・Y"，取 Y（如《财之道丛书・经营十二条》）
    m_sub2 = re.search(r"[一-鿿]{2,8}丛书[\s·・\-]*([一-鿿]{2,12})", title)
    if m_sub2:
        sub = m_sub2.group(1).strip()
        if sub and sub not in queries:
            queries.append(sub)
    # 找标题里被"・"或"·"或":"分隔的最后一段，如"财之道丛书・经营十二条" → "经营十二条"
    parts = re.split(r"[・·:：]", title)
    if len(parts) >= 2:
        tail = parts[-1].strip()
        # 清理一下，去掉括号内容
        tail = re.sub(r"[（(][^）)]*[)）]", "", tail).strip()
        if tail and 2 <= len(tail) <= 12 and tail not in queries:
            queries.append(tail)

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

    # 提取当当书名末尾的"序号"（用于序号守护 + 后续排序）
    # 例：《人间小满3》→ serial="3", prefix="人间小满"
    #      《肥志百科17》→ serial="17", prefix="肥志百科"
    dd_serial_match = re.search(r"([一-鿿]{2,8})(\d+)\s*$", core or "")
    dd_serial = dd_serial_match.group(2) if dd_serial_match else None
    dd_serial_prefix = dd_serial_match.group(1) if dd_serial_match else None

    # 抓详情，过滤出"和当当书是同一本"的所有版本
    # 业务要求：京东侧优先自营，没自营时降级到销量最高的 POP（不再硬卡只看自营）
    pop_core_stripped = re.sub(r"\s*\d+\s*$", "", core).strip()
    # 关键词分词（用于宽松匹配，解决"大中华寻宝记城市系列" vs "大中华寻宝记 城市系列"这种因空格匹配失败的问题）
    keyword_tokens = _tokenize_for_match(dangdang_book.get("title", ""))

    candidates: list[dict] = []
    skipped_rivals = 0
    for sku in found_skus:
        info = fetch_detail(sku)
        if not info:
            time.sleep(delay)
            continue

        # 关键：剔除对手品牌（当当/新华书店/...）开的 POP 店铺
        if is_rival_seller(info.get("title", ""), info.get("shop_name", "")):
            skipped_rivals += 1
            time.sleep(delay)
            continue

        self_t = normalize_title(info.get("title", ""))
        if not self_t:
            time.sleep(delay)
            continue

        # 系列序号守护：当当书名带数字（如"人间小满3""肥志百科17"）
        # → 京东 SKU 标题必须含相同数字（防止选到"人间小满 1+2 套装"）
        # 用 dd_serial_match 在前面已经提取过
        if dd_serial:
            # 京东标题里要么含相同数字，要么是含"全 N 册"等明显套装标识
            jd_t_full = info.get("title", "")  # 不 normalize 避免数字被吃掉
            has_serial = (
                dd_serial in jd_t_full
                # 套装也算（虽然包含 1+2+...+N，但里面有这本书）
                or re.search(rf"全\s*\d+\s*册|套装\s*\d|1\s*\+\s*2", jd_t_full)
            )
            if not has_serial:
                time.sleep(delay)
                continue

        # 三重匹配（任一通过即视为同款）：
        #  1. strict: 整段核心字串出现在京东标题
        #  2. loose:  去尾数核心字串 + 作者
        #  3. token:  关键词分词 ≥2 个出现（最宽松，解决空格/顺序差异）
        match_strict = pop_core_normalized in self_t
        match_loose = (
            len(pop_core_stripped) >= 3
            and pop_core_stripped in self_t
            and (not author_short or author_short in self_t)
        )
        match_token = (
            len(keyword_tokens) >= 2
            and sum(1 for t in keyword_tokens if t in self_t) >= 2
        )
        if not (match_strict or match_loose or match_token):
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

    # 选最佳匹配多级排序：
    # 1. 店铺优先级（标准自营 > POP > 京喜）
    # 2. 系列序号严格匹配（《人间小满3》必须选含"3"的，不能选"1+2 套装"）
    # 3. 版本关键词相似度（纪念/初版/精装/亲签等版本词重合度）
    # 4. 销量
    dd_version_kws = _extract_version_keywords(dangdang_book.get("title", ""))

    def _serial_match(book: dict) -> int:
        """京东 SKU 是否含相同序号 — 严格单本=3，含序号但是套装=2，套装匹配=1，其他=0"""
        if not dd_serial:
            return 0  # 当当书没序号，所有 SKU 平等
        jd_t = normalize_title(book.get("title", ""))
        if not jd_t:
            return 0
        # 是否套装（标题含"套装""全X册""1+2"等）
        is_bundle = bool(re.search(r"套装|全\s*\d+\s*册|全套|1\+2", jd_t))
        # 严格匹配："X+序号"连续出现（如"人间小满3"）
        if dd_serial_prefix and (
            f"{dd_serial_prefix}{dd_serial}" in jd_t
            or f"{dd_serial_prefix} {dd_serial}" in jd_t
        ):
            # 含序号但是套装 → 2（套装含的不止这一本）
            # 含序号且是单本 → 3（最匹配）
            return 2 if is_bundle else 3
        # 不含明确序号但是套装匹配
        if dd_serial_prefix and dd_serial_prefix in jd_t and is_bundle:
            return 1
        return 0

    def _similarity(book: dict) -> int:
        """版本关键词重合度"""
        if not dd_version_kws:
            return 0
        jd_kws = _extract_version_keywords(book.get("title", ""))
        return len(dd_version_kws & jd_kws)

    candidates.sort(
        key=lambda b: (
            -_shop_priority(b),
            -_serial_match(b),
            -_similarity(b),
            -b.get("show_count", 0),
        )
    )
    best_match = candidates[0]

    # ===== 复查机制（最后防线，无需人工兜底）=====
    # 当 best_match 不是京东自营时，主动用"裸核心标题"再做一轮专门找自营的搜索
    # 防止"印签版/精装版"等营销词污染初始搜索，导致自营 SKU 没进 found_skus
    # 例：当当《真实之书 印签版 ...》初始搜 12 个 SKU 没命中自营 15385432
    #     复查时用纯"真实之书"搜更多结果 → 抓到自营版
    if not best_match.get("is_self"):
        recheck_self = _recheck_for_self(
            core, dangdang_book, candidates, dd_serial,
            dd_serial_prefix, pop_core_stripped, keyword_tokens,
            author_short, max_results=20, delay=delay,
        )
        if recheck_self:
            log.info("  ↪ 复查找到京东自营 SKU=%s（替换原 best_match=%s）",
                     recheck_self.get("sku"), best_match.get("sku"))
            # 把找到的自营加进 candidates 重新排序
            candidates.append(recheck_self)
            candidates.sort(
                key=lambda b: (
                    -_shop_priority(b),
                    -_serial_match(b),
                    -_similarity(b),
                    -b.get("show_count", 0),
                )
            )
            best_match = candidates[0]

    # 关键：合并所有版本的权益（京东可能在不同 SKU 里分散提供权益）
    # 但只统计"标准京东自营"的权益（京喜质量参差，不算入正式对标）
    standard_self_books = [c for c in candidates if _shop_priority(c) >= 100]
    perk_pool = standard_self_books if standard_self_books else candidates
    all_perks = sorted(set(p for c in perk_pool for p in (c.get("perks") or [])))

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
                    only_with_perks: bool = False,
                    top_n_total: int = 20,
                    delay: float = 1.0) -> list[dict]:
    """
    主流程：对当当 24h 总榜 Top N 的书逐一查京东对标。

    Args:
      only_with_perks: True 时只对标带权益的书；False（默认）对标所有 Top N
      top_n_total: "全部"总榜对标前 N 名（默认 20）

    返回每本书的"对标条目"列表，前端可直接渲染为对比表。
    """
    # 取"全部"分类（即 24h 总榜）的前 N 本
    # category="热销" 是当当总榜的标识
    top_books = [b for b in dangdang_books if b.get("category") == "热销"][:top_n_total]
    log.info("当当 24h 总榜前 %d 本（实际 %d 本）", top_n_total, len(top_books))

    if only_with_perks:
        targets = [b for b in top_books if b.get("perks")]
    else:
        targets = top_books

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

        # 把"权益差异"拆好直接给前端 — 前端不再需要自己计算
        # 这样前端展示和后端 gap 判定永远一致
        dd_perks_set = set(dd.get("perks", []))
        jd_perks_set = set(
            jd_data.get("all_perks") or
            (jd_data.get("best_match") or {}).get("perks", []) or []
        )
        distinctive = dd_perks_set - jd_perks_set
        distinctive_strong = sorted(distinctive - WEAK_PERKS)  # 真正的对标缺口
        distinctive_weak = sorted(distinctive & WEAK_PERKS)    # 弱权益差异（仅展示，不计入缺口）

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
            "distinctive_strong": distinctive_strong,
            "distinctive_weak": distinctive_weak,
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


# 弱权益：营销噱头大但实际差异小，不算"对标缺口"
# 共享给 _assess_gap + 前端展示用
# 前端通过 benchmark.json 里的 distinctive_strong / distinctive_weak 字段区分
WEAK_PERKS = {"礼盒", "赠品", "首发"}


def _assess_gap(dd: dict, jd: dict) -> str:
    """
    评估对标缺口：
      - no_jd     京东未在售
      - perk_gap  京东在售但当当独有强权益（亲签/限量/独家）
      - none      权益已对齐（含'仅当当独有弱权益'的情况）

    重要修复：用 jd['all_perks']（所有版本权益的并集）而不是 best_match.perks
    避免出现"京东其实有亲签套装版，只是销量最好的是平装版"导致漏判
    """
    if not jd.get("available"):
        return "no_jd"
    dd_perks = set(dd.get("perks", []))
    jd_perks = set(jd.get("all_perks") or
                   (jd.get("best_match") or {}).get("perks", []))
    distinctive = dd_perks - jd_perks
    distinctive_strong = distinctive - WEAK_PERKS
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
