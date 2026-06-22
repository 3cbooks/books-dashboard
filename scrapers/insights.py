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


def _insight(icon: str, title: str, body: str, tag: str,
             tone: str = "", anchor: str | None = None) -> dict:
    """构造一条洞察的标准结构。anchor 是页面内跳转目标的 section id。"""
    item = {"icon": icon, "title": title, "body": body, "tag": tag, "tone": tone}
    if anchor:
        item["anchor"] = anchor
    return item


# ============================================================
# 今日 vs 昨日差异（让每条洞察 body 都带"当日特征"）
# ============================================================

def _book_key(b: dict) -> str:
    """跨日识别同一本书的稳定 key（url 优先，回退 title）"""
    return b.get("url") or b.get("title") or ""


def compute_today_changes(today: list[dict], yesterday: list[dict]) -> dict:
    """
    返回一个 dict，描述今日相对昨日的变化。所有规则都可以从里面取词拼到 body 末尾。

    返回字段：
      new_titles:        今日新进入榜单的书名列表（按今日 rank 升序）
      rising_titles:     排名上升 ≥3 名的书名（带"升 N 名"）
      new_perk_titles:   今日比昨日多出权益的书名（如《XX》新增亲签）
      churn:             "X 进 Y 出" 字符串
      summary_line:      一行摘要，用作所有规则的兜底"今日特征"
    """
    if not yesterday:
        return {"new_titles": [], "rising_titles": [], "new_perk_titles": [],
                "churn": "", "summary_line": "今日为首日基线"}

    yest_map = {_book_key(b): b for b in yesterday if _book_key(b)}
    today_map = {_book_key(b): b for b in today if _book_key(b)}

    new_keys = [k for k in today_map if k not in yest_map]
    out_keys = [k for k in yest_map if k not in today_map]

    new_books_sorted = sorted(
        (today_map[k] for k in new_keys),
        key=lambda b: b.get("rank") or 99,
    )
    new_titles = [b.get("title", "") for b in new_books_sorted]

    rising = []
    for k in today_map.keys() & yest_map.keys():
        t_rank = today_map[k].get("rank") or 99
        y_rank = yest_map[k].get("rank") or 99
        if y_rank - t_rank >= 3:
            rising.append((today_map[k].get("title", ""), y_rank - t_rank))
    rising.sort(key=lambda x: x[1], reverse=True)
    rising_titles = [f"《{t[:14]}》(升 {n} 名)" for t, n in rising[:3]]

    new_perk_titles = []
    for k in today_map.keys() & yest_map.keys():
        t_perks = set(today_map[k].get("perks") or [])
        y_perks = set(yest_map[k].get("perks") or [])
        added = t_perks - y_perks
        if added:
            new_perk_titles.append(
                f"《{today_map[k].get('title','')[:12]}》+{'/'.join(added)}"
            )

    churn = f"{len(new_keys)} 进 {len(out_keys)} 出" if (new_keys or out_keys) else ""

    if new_titles:
        summary_line = f"今日新进 {len(new_titles)} 本：" + "、".join(
            f"《{t[:12]}》" for t in new_titles[:3]
        )
    elif rising_titles:
        summary_line = f"今日排名上升：{rising_titles[0]}"
    elif new_perk_titles:
        summary_line = f"今日新增权益：{new_perk_titles[0]}"
    elif churn:
        summary_line = f"今日榜单 {churn}"
    else:
        summary_line = "今日榜单与昨日持平"

    return {
        "new_titles": new_titles,
        "rising_titles": rising_titles,
        "new_perk_titles": new_perk_titles,
        "churn": churn,
        "summary_line": summary_line,
    }


# ============================================================
# 规则集
# ============================================================

def rule_dangdang_perks(books: list[dict], changes: dict | None = None) -> list[dict]:
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

    # 今日"权益变化"加塞到 body 末尾（让卡片每天都不一样）
    perk_change_line = ""
    if changes and changes.get("new_perk_titles"):
        perk_change_line = "今日新增权益：" + "、".join(
            changes["new_perk_titles"][:2]
        ) + "。"

    if perk_ratio >= 0.25:
        # 高占比 → 强信号
        out.append(_insight(
            "🎯",
            f"当当 Top 榜 {len(with_perks)}/{len(dd)} 本带差异化权益",
            f"占比 {perk_ratio*100:.0f}%。其中 {top_perk_str}。"
            f"这些权益版京东自营多数缺货 — 是当当用户黏性的核心抓手。"
            + (f" {perk_change_line}" if perk_change_line else ""),
            "当当对标",
            "warn",
            anchor="benchmark-section",
        ))

    # 亲签上榜书：单独拎出来，因为是最直接的"作者×渠道"绑定
    qianqian_books = [b for b in dd if "亲签" in b.get("perks", [])]
    if qianqian_books:
        avg_rank = sum(b.get("rank", 99) for b in qianqian_books) / len(qianqian_books)
        names = "、".join(f"《{b['title'][:14]}》" for b in qianqian_books[:3])
        # 亲签今日变化：从 changes.new_titles 里筛出带亲签的新进入者
        qianqian_today = ""
        if changes and changes.get("new_titles"):
            new_set = set(changes["new_titles"])
            new_qianqian = [b for b in qianqian_books if b.get("title") in new_set]
            if new_qianqian:
                qianqian_today = (
                    f"今日 {len(new_qianqian)} 本亲签新进榜："
                    + "、".join(f"《{b['title'][:12]}》" for b in new_qianqian[:2])
                    + "。"
                )
        out.append(_insight(
            "✍️",
            f"当当 24 小时榜 {len(qianqian_books)} 本亲签版上榜",
            f"平均榜位 #{avg_rank:.1f}，含 {names}。"
            f"亲签是作者×渠道的强绑定，京东自营对标空白。"
            + (f" {qianqian_today}" if qianqian_today else ""),
            "亲签机会",
            "warn",
            anchor="benchmark-section",
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
                anchor="benchmark-section",
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
            anchor="books-section",
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
    # 只取最热的一条，避免占满洞察板块
    for theme, cnt, hits in matches[:1]:
        # 拼一条"今日头条新闻"让 body 每天都不一样
        # hits 顺序与 news 一致（news 已按时间倒序），取第 1 条作为今日代表
        head = f"今日代表：《{hits[0][:24]}》。" if hits else ""
        out.append(_insight(
            "📰",
            f"行业话题聚焦：{theme}（{cnt} 条）",
            f"今日 {cnt} 条相关新闻进入议程，是当下出版业舆论的核心。"
            f"{head}建议关联选题或营销叙事时同步把握热度。",
            "新闻热点",
            "info",
            anchor="news-section",
        ))
    return out


def rule_high_rated(books: list[dict], changes: dict | None = None) -> list[dict]:
    """高分上榜书：评分 ≥ 4.7 的书单独拎出来，作为"质量信号"。"""
    out = []
    high = [b for b in books if (b.get("rating") or 0) >= 4.7]
    if len(high) >= 3:
        names = "、".join(f"《{b['title'][:12]}》" for b in high[:3])
        # 今日新进榜的高分书 → 加到 body 末尾
        new_high_line = ""
        if changes and changes.get("new_titles"):
            new_set = set(changes["new_titles"])
            new_high = [b for b in high if b.get("title") in new_set]
            if new_high:
                new_high_line = (
                    f" 今日新进 {len(new_high)} 本高分书："
                    + "、".join(f"《{b['title'][:12]}》" for b in new_high[:2])
                    + "。"
                )
        out.append(_insight(
            "⭐",
            f"高分上榜书集中：{len(high)} 本评分 ≥ 4.7",
            f"包含 {names}。读者口碑端正反馈强烈，可作为大客户/会员推荐重点。"
            + new_high_line,
            "口碑信号",
            "info",
            anchor="books-section",
        ))
    return out


def rule_no_perk_top(books: list[dict], changes: dict | None = None) -> list[dict]:
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
        # 今日"排名上升"信号 → 拼到 body 末尾
        rising_line = ""
        if changes and changes.get("rising_titles"):
            rising_line = " 今日排名上升：" + "、".join(changes["rising_titles"][:2]) + "。"
        out.append(_insight(
            "🔥",
            f"{len(pure_top3)} 本无权益版上榜书登品类前 3",
            f"占无权益版上榜书的 {ratio:.0f}%，含 {names}。"
            f"这类书靠纯内容力出圈，是京东自营对标销售的高优先级候选。"
            + rising_line,
            "纯内容信号",
            anchor="books-section",
        ))
    return out


def rule_upcoming_books(new_books: list[dict]) -> list[dict]:
    """
    上游押注信号：当当数据按"出版日倒序"显示的预售书，
    是出版社未来 6 个月计划上市的图书。
    它领先销量榜 1-3 个月，是商业分析师的关键先行指标。
    """
    out = []
    if not new_books:
        return out

    preorder = [b for b in new_books if b.get("pub_status") == "preorder"]
    if len(preorder) < 3:
        return out

    # 按品类分组统计
    cat_counter = Counter(
        b.get("category", "其他") for b in preorder
    )
    top_cats = cat_counter.most_common(3)
    cats_str = "、".join(f"{c}({n})" for c, n in top_cats)

    out.append(_insight(
        "🔮",
        f"出版社未来 6 个月将上市 {len(preorder)} 本预售书",
        f"主投品类：{cats_str}（共 {len(preorder)} 本预售）。"
        f"这是销量榜的 1-3 个月先行指标，可提前规划自营选品。",
        "上游信号",
        "info",
        anchor="upcoming-section",
    ))

    # 找出预售书里有权益的（出版社+渠道双押注）
    preorder_perks = [b for b in preorder if b.get("perks")]
    if preorder_perks:
        names = "、".join(f"《{b['title'][:14]}》" for b in preorder_perks[:3])
        out.append(_insight(
            "🎁",
            f"{len(preorder_perks)} 本预售书已锁定权益版",
            f"含 {names}。上市前即'独家/限量'锁定，是出版社×当当的渠道深度合作。"
            f"重点候选：尽早判断是否能跟进自营版本。",
            "渠道前哨",
            "warn",
            anchor="upcoming-section",
        ))

    return out


def rule_freshly_published(new_books: list[dict]) -> list[dict]:
    """近期出版的书：fresh + recent"""
    out = []
    if not new_books:
        return out

    fresh = [b for b in new_books
             if b.get("pub_status") in ("fresh", "recent")]
    if len(fresh) < 3:
        return out

    cat_counter = Counter(b.get("category", "其他") for b in fresh)
    top_cats = cat_counter.most_common(3)
    cats_str = "、".join(f"{c}({n})" for c, n in top_cats)

    out.append(_insight(
        "🆕",
        f"近 30 日新出版 {len(fresh)} 本",
        f"主要品类：{cats_str}。这是真正的'近期上市'图书，"
        f"区别于销量榜上的长红畅销书。",
        "新出版",
        "info",
        anchor="upcoming-section",
    ))
    return out


def rule_douban_verification(books: list[dict], new_books: list[dict],
                              changes: dict | None = None) -> list[dict]:
    """
    豆瓣校验相关洞察：
    - 当当热卖榜里被豆瓣确认是"5 年以上老书"的，是常销长红信号
    - 豆瓣查不到的书，往往是当当独家版（信号偏弱但有意义）
    - 当当判定为预售但豆瓣无记录的，提示"占位数据嫌疑"
    """
    out = []
    today_year = 2026

    # ① 老书重热（豆瓣校验显示出版于 5 年前但仍在热卖榜前 10）
    old_hot = []
    for b in books:
        if b.get("verify_status") != "verified":
            continue
        douban_pubdate = b.get("douban_pubdate")
        if not douban_pubdate:
            continue
        try:
            pub_year = int(douban_pubdate[:4])
            age = today_year - pub_year
            if age >= 5 and (b.get("rank") or 99) <= 10:
                old_hot.append((b, age))
        except (ValueError, TypeError):
            pass

    if old_hot:
        names_ages = "、".join(
            f"《{b['title'][:14]}》({age}年前)" for b, age in old_hot[:3]
        )
        # 今日老书重热的具体书名兜底（防止只剩 "1 本'老书重热'入榜前 10" 没具体差异）
        # 用 changes 兜底加 churn 信号（X 进 Y 出）
        churn_line = ""
        if changes and changes.get("churn"):
            churn_line = f" 今日榜单流转：{changes['churn']}。"
        out.append(_insight(
            "📜",
            f"{len(old_hot)} 本'老书重热'入榜前 10",
            f"豆瓣校验显示这些书出版 ≥5 年仍在热卖榜：{names_ages}。"
            f"长尾内容力强，是经典常销 / 新版重发的典型，自营若有同款值得长期备货。"
            + churn_line,
            "常销信号",
            anchor="books-section",
        ))

    # ② 当当判定预售但豆瓣无记录（占位数据嫌疑）
    if new_books:
        preorder = [b for b in new_books if b.get("pub_status") == "preorder"]
        unverified_preorder = [b for b in preorder
                               if b.get("verify_status") == "unverified"]
        if preorder and len(unverified_preorder) / len(preorder) >= 0.7:
            out.append(_insight(
                "🚧",
                f"{len(unverified_preorder)}/{len(preorder)} 本预售书豆瓣未收录",
                f"占比 {len(unverified_preorder)/len(preorder)*100:.0f}%，"
                f"豆瓣无对应条目 — 这些'预售书'可能是当当后台占位 / 长尾再版数据，"
                f"建议谨慎当作真实'上游信号'解读。",
                "数据质量",
                "warn",
                anchor="upcoming-section",
            ))

    return out
    """新近出版（≤30 天）的书 — 真'新书'信号，区别于销量榜上的长红书。"""
    out = []
    if not new_books:
        return out

    fresh = [b for b in new_books
             if b.get("pub_status") in ("fresh", "recent")]
    if len(fresh) < 3:
        return out

    cat_counter = Counter(b.get("category", "其他") for b in fresh)
    top_cats = cat_counter.most_common(3)
    cats_str = "、".join(f"{c}({n})" for c, n in top_cats)

    out.append(_insight(
        "🆕",
        f"近 30 日新出版 {len(fresh)} 本",
        f"主要品类：{cats_str}。这是真正的'近期上市'图书，"
        f"区别于销量榜上的长红畅销书。",
        "新出版",
        "info",
        anchor="upcoming-section",
    ))
    return out


# ============================================================
# 主流程
# ============================================================

def rule_jd_pop_gap(jd_pop_only: list[dict]) -> list[dict]:
    """京东 POP 在卖、自营没卖 → 自营选品的内部缺口洞察"""
    out = []
    if len(jd_pop_only) < 2:
        return out

    # 统计是哪些出版社 / 店铺类型在 POP 卖但自营没
    publishers = Counter(
        b.get("publisher", "未知") for b in jd_pop_only if b.get("publisher")
    )
    top_pubs = publishers.most_common(3)
    pubs_str = "、".join(f"{p}" for p, _ in top_pubs)

    names = "、".join(f"《{b.get('title','?')[:14]}》" for b in jd_pop_only[:3])

    out.append(_insight(
        "🛒",
        f"京东 POP 在售 {len(jd_pop_only)} 本，自营未上架",
        f"含 {names}。出版社方包括 {pubs_str}。"
        f"自营选品的潜在缺口，建议优先评估上架。",
        "POP 缺口",
        "warn",
        anchor="jd-pop-section",
    ))
    return out


def generate(books: list[dict], news: list[dict],
             new_books: list[dict] | None = None,
             jd_pop_only: list[dict] | None = None,
             yesterday_books: list[dict] | None = None) -> list[dict]:
    """跑所有规则，按"信号强度"挑出 5-10 条。"""
    new_books = new_books or []
    jd_pop_only = jd_pop_only or []
    yesterday_books = yesterday_books or []

    # 计算"今日 vs 昨日"差异，让每条规则都能拼一句"当日特征"到 body
    changes = compute_today_changes(books, yesterday_books)
    log.info("今日变化: %s", changes.get("summary_line", ""))

    candidates: list[dict] = []
    candidates.extend(rule_dangdang_perks(books, changes))
    candidates.extend(rule_douban_verification(books, new_books, changes))
    candidates.extend(rule_category_distribution(books))
    candidates.extend(rule_high_rated(books, changes))
    candidates.extend(rule_no_perk_top(books, changes))
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

    # 限制数量（前 6 条 — 2 行 × 3 列正好）
    return candidates[:6]


def main() -> int:
    log.info("═══ 开始生成洞察 ═══")
    books = load_json("books.json", default=[]) or []
    news = load_json("news.json", default=[]) or []
    new_books = load_json("books_new.json", default=[]) or []
    insights = generate(books, news, new_books=new_books)
    save_json("insights.json", insights)
    log.info("═══ 完成: 生成 %d 条洞察 ═══", len(insights))
    for it in insights:
        log.info("  %s %s", it["icon"], it["title"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
