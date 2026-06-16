"""
洞察规则引擎
- 读取 data/books.json + data/news.json
- 应用一系列规则，生成 3-8 条短洞察（每条 100 字以内）
- 写入 data/insights.json

设计原则：
1. 每条规则只在"信号显著"时触发，不为了凑数硬生
2. 优先突出"当当差异化权益"线索（业务对标场景）
3. 配套数字（百分比、排名、件数）让洞察可信
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .common import get_logger, load_json, save_json, normalize_category

log = get_logger("insights")


def _insight(icon: str, title: str, body: str, tag: str, tone: str = "") -> dict:
    """构造一条洞察的标准结构。"""
    return {"icon": icon, "title": title, "body": body, "tag": tag, "tone": tone}


# ============================================================
# 规则集
# ============================================================

def rule_dangdang_perks(books: list[dict]) -> list[dict]:
    """
    当当权益专题：识别亲签/限量/独家/首发等差异化权益，
    评估它们在热卖榜里的占比和排名表现 —— 直接对标京东自营的潜在缺口。
    """
    out = []
    dd = [b for b in books if b.get("source") == "当当"]
    if not dd:
        return out

    # 总体权益占比
    with_perks = [b for b in dd if b.get("perks")]
    perk_ratio = len(with_perks) / len(dd) if dd else 0

    perk_counter = Counter(p for b in dd for p in b.get("perks", []))
    top_perk_str = "、".join(
        f"{p}({c})" for p, c in perk_counter.most_common(4)
    )

    if perk_ratio >= 0.25:
        # 高占比 → 强信号
        out.append(_insight(
            "🎯",
            f"当当 Top 榜 {len(with_perks)}/{len(dd)} 本带差异化权益",
            f"占比 {perk_ratio*100:.0f}%。其中 {top_perk_str}。"
            f"这些权益版京东自营多数缺货 — 是当当用户黏性的核心抓手。",
            "当当对标",
            "warn",
        ))

    # 亲签上榜书：单独拎出来，因为是最直接的"作者×渠道"绑定
    qianqian_books = [b for b in dd if "亲签" in b.get("perks", [])]
    if qianqian_books:
        avg_rank = sum(b.get("rank", 99) for b in qianqian_books) / len(qianqian_books)
        names = "、".join(f"《{b['title'][:14]}》" for b in qianqian_books[:3])
        out.append(_insight(
            "✍️",
            f"当当近 7 日 {len(qianqian_books)} 本亲签版上榜",
            f"平均榜位 #{avg_rank:.1f}，含 {names}。"
            f"亲签是作者×渠道的强绑定，京东自营对标空白。",
            "亲签机会",
            "warn",
        ))

    # 限量版分析：稀缺感对销量的拉动
    limit_books = [b for b in dd if "限量" in b.get("perks", [])]
    if len(limit_books) >= 3:
        avg_rank_limit = sum(b.get("rank", 99) for b in limit_books) / len(limit_books)
        avg_rank_normal = (
            sum(b.get("rank", 99) for b in dd if not b.get("perks")) /
            max(1, len([b for b in dd if not b.get("perks")]))
        )
        diff = avg_rank_normal - avg_rank_limit
        if diff > 0:
            out.append(_insight(
                "💎",
                f"限量版图书排名比普通版高 {diff:.1f} 名",
                f"限量版平均 #{avg_rank_limit:.1f}，普通版平均 #{avg_rank_normal:.1f}。"
                f"稀缺感对销量的拉动作用清晰，可在自营侧复用'编号限量'机制。",
                "限量策略",
            ))

    return out


def rule_category_distribution(books: list[dict]) -> list[dict]:
    """品类分布：找出本周表现突出的品类。"""
    out = []
    if not books:
        return out

    # 排除"热销"这种非品类的标签
    real_cats = [b for b in books if b.get("category") not in ("热销", "其他", "")]
    if not real_cats:
        return out

    counter = Counter(b["category"] for b in real_cats)
    total = sum(counter.values())
    top1 = counter.most_common(1)[0]
    if top1[1] / total >= 0.30 and top1[1] >= 3:
        out.append(_insight(
            "📚",
            f"{top1[0]}品类本周强势，占比 {top1[1]/total*100:.0f}%",
            f"{top1[1]} 本上榜书属此品类，远超其他品类。"
            f"建议在选品/陈列侧加强对应栏目权重。",
            "品类信号",
        ))
    return out


def rule_news_themes(news: list[dict]) -> list[dict]:
    """新闻主题：在标题/摘要里找高频关键词，推断行业当下的关注重心。"""
    out = []
    if not news:
        return out

    themes = [
        ("AI 与出版",        ("AI", "人工智能", "大模型", "智能创作")),
        ("电商博弈",         ("618", "抵制", "京东", "拼多多", "电商", "促销")),
        ("出版业转型",       ("转型", "破局", "洗牌", "数字化", "数字出版")),
        ("实体书店",         ("实体书店", "独立书店", "书店")),
        ("少儿/童书",        ("少儿", "童书", "儿童阅读", "绘本")),
        ("政策与监管",       ("政府奖", "新闻出版署", "条例", "监管", "政策")),
    ]

    matches: list[tuple[str, int, list[str]]] = []
    for theme, kws in themes:
        # 在标题 + 摘要里找命中（覆盖更广）
        hits = []
        for n in news:
            text = (n.get("title", "") + " " + n.get("summary", ""))
            if any(kw in text for kw in kws):
                hits.append(n["title"])
        if len(hits) >= 3:
            matches.append((theme, len(hits), hits))

    matches.sort(key=lambda x: x[1], reverse=True)
    for theme, cnt, _hits in matches[:2]:
        out.append(_insight(
            "📰",
            f"行业话题聚焦：{theme}（{cnt} 条）",
            f"今日 {cnt} 条相关新闻进入议程，是当下出版业舆论的核心。"
            f"建议关联选题或营销叙事时同步把握热度。",
            "新闻热点",
            "info",
        ))
    return out


def rule_high_rated(books: list[dict]) -> list[dict]:
    """高分上榜书：评分 ≥ 4.7 的书单独拎出来，作为"质量信号"。"""
    out = []
    high = [b for b in books if (b.get("rating") or 0) >= 4.7]
    if len(high) >= 3:
        names = "、".join(f"《{b['title'][:12]}》" for b in high[:3])
        out.append(_insight(
            "⭐",
            f"高分上榜书集中：{len(high)} 本评分 ≥ 4.7",
            f"包含 {names}。读者口碑端正反馈强烈，可作为大客户/会员推荐重点。",
            "口碑信号",
            "info",
        ))
    return out


def rule_no_perk_top(books: list[dict]) -> list[dict]:
    """
    没有权益的书也排在前列 —— 反向信号：它们靠纯"内容力"上榜。
    （rank 是品类内排名，前 3 名内才算"前列"）
    """
    out = []
    dd = [b for b in books if b.get("source") == "当当"]
    if not dd:
        return out

    pure_top3 = [b for b in dd if not b.get("perks") and b.get("rank", 99) <= 3]
    no_perk_total = [b for b in dd if not b.get("perks")]

    if len(pure_top3) >= 3:
        names = "、".join(f"《{b['title'][:14]}》" for b in pure_top3[:3])
        ratio = len(pure_top3) / max(1, len(no_perk_total)) * 100
        out.append(_insight(
            "🔥",
            f"{len(pure_top3)} 本无权益版上榜书登品类前 3",
            f"占无权益版上榜书的 {ratio:.0f}%，含 {names}。"
            f"这类书靠纯内容力出圈，是京东自营对标销售的高优先级候选。",
            "纯内容信号",
        ))
    return out


# ============================================================
# 主流程
# ============================================================

def generate(books: list[dict], news: list[dict]) -> list[dict]:
    """跑所有规则，按"信号强度"挑出 5-8 条。"""
    candidates: list[dict] = []
    candidates.extend(rule_dangdang_perks(books))
    candidates.extend(rule_category_distribution(books))
    candidates.extend(rule_high_rated(books))
    candidates.extend(rule_no_perk_top(books))
    candidates.extend(rule_news_themes(news))

    # 兜底：哪怕一条都没生成，也给一个总览
    if not candidates:
        candidates.append(_insight(
            "📊",
            "今日数据已就位",
            f"抓取到当当上榜书 {len(books)} 本、行业新闻 {len(news)} 条。"
            f"待积累跨日数据后将生成更多趋势洞察。",
            "数据状态",
        ))

    # 限制数量（前 8 条）
    return candidates[:8]


def main() -> int:
    log.info("═══ 开始生成洞察 ═══")
    books = load_json("books.json", default=[]) or []
    news = load_json("news.json", default=[]) or []
    insights = generate(books, news)
    save_json("insights.json", insights)
    log.info("═══ 完成: 生成 %d 条洞察 ═══", len(insights))
    for it in insights:
        log.info("  %s %s", it["icon"], it["title"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
