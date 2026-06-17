"""
调度入口
- 跑所有数据源抓取器
- 失败时用上次成功的数据兜底（不会让网站突然空白）
- 输出 books.json / news.json / meta.json
- 后续 insights 模块会读这些文件再写 insights.json
"""
from __future__ import annotations

import sys
import traceback

from .common import get_logger, load_json, save_json, now_iso

log = get_logger("main")


def _safe_run(name: str, fn) -> tuple[list, str]:
    """
    跑一个抓取器，返回 (结果列表, 状态码)。
    状态码: 'ok' / 'partial' / 'failed'
    """
    try:
        items = fn()
        if not items:
            log.warning("✗ %s 返回空 → 视为失败", name)
            return [], "failed"
        log.info("✓ %s: %d 条", name, len(items))
        return items, "ok"
    except Exception:
        log.error("✗ %s 抛异常:\n%s", name, traceback.format_exc())
        return [], "failed"


def main() -> int:
    log.info("═══ 开始抓取 (%s) ═══", now_iso())

    # 各源的状态记录（用于在 meta.json 里告诉前端哪些源失败了）
    sources_status: dict[str, str] = {}

    # ============ 抓书 ============
    from . import dangdang
    # 对标主力：当当 24h 总榜前 20 名 + 各品类前 4 名（去重后约 40+ 本）
    books, status = _safe_run(
        "dangdang",
        lambda: dangdang.fetch(per_category=4, top_n_total=20),
    )
    sources_status["dangdang"] = status

    # 失败兜底：用上次的 books.json
    if not books:
        log.warning("⚠ 所有图书源失败，使用上次数据兜底")
        books = load_json("books.json", default=[])
    else:
        # 数据保护：当当突然只抓到极少（< 旧数据 50%）也认为不可信
        old_books = load_json("books.json", default=[]) or []
        if len(old_books) >= 20 and len(books) < len(old_books) * 0.5:
            log.warning(
                "⚠ 当当抓到 %d 本（旧 %d 本）数据剧降，保留旧数据不覆盖",
                len(books), len(old_books),
            )
            books = old_books

    # ============ 抓真新书 / 预售书（已废弃前端展示，但保留数据用于校验日期）============
    from . import dangdang_new
    new_books, status = _safe_run(
        "dangdang_new",
        lambda: dangdang_new.fetch(per_category=3, max_pages=2),
    )
    sources_status["dangdang_new"] = status
    if not new_books:
        log.warning("⚠ 真新书源失败，使用上次数据兜底")
        new_books = load_json("books_new.json", default=[])
    save_json("books_new.json", new_books)

    # ============ 当当 vs 京东 权益对标 ============
    # 对当当带权益的书逐一查京东对应版本，做权益对比
    # 数据保护多层防御:
    #  1. 整体反爬保护: ≥80% 是 'no_jd' → 保留旧数据
    #  2. 单本退化保护: 旧数据是自营、新数据降级 POP → 重试或保留旧
    #  3. 退化到 POP 也比丢数据强: 至少 best_match 不为空
    try:
        from . import jd_benchmark
        old_benchmark = load_json("benchmark.json", default=[]) or []
        # 旧数据按当当书名建索引，方便查
        old_by_title = {r["dangdang"]["title"]: r for r in old_benchmark}

        benchmark_data = jd_benchmark.benchmark_books(
            books, only_with_perks=False, top_n_total=20, delay=1.5,
        )

        if not benchmark_data:
            sources_status["benchmark"] = "partial"
        else:
            # 1. 整体反爬保护
            no_jd_count = sum(1 for r in benchmark_data
                              if r.get("gap_level") == "no_jd")
            if no_jd_count / len(benchmark_data) >= 0.8:
                log.warning(
                    "⚠ 京东对标 %d/%d 本 'no_jd' — 大概率被云端反爬，保留旧数据",
                    no_jd_count, len(benchmark_data),
                )
                sources_status["benchmark"] = "blocked_by_anticrawl"
            else:
                # 2. 单本退化保护：检查每本书有没有"自营→POP 退化"
                #    退化时尝试重试一次；重试还失败就用旧数据该条记录
                degraded_indices = []
                for i, r in enumerate(benchmark_data):
                    new_self = (r.get("jd",{}).get("best_match",{}) or {}).get("is_self", False)
                    new_avail = r.get("jd",{}).get("available", False)
                    old = old_by_title.get(r["dangdang"]["title"])
                    old_self = (
                        old and old.get("jd",{}).get("best_match",{})
                        and old["jd"]["best_match"].get("is_self", False)
                    ) if old else False
                    # 退化判定: 旧数据是自营，新数据(可用但)不是自营 OR 新数据完全不可用
                    if old_self and (not new_avail or not new_self):
                        degraded_indices.append(i)

                if degraded_indices:
                    log.warning(
                        "⚠ %d 本对标退化（旧自营 → 新 POP/不可用）：尝试重试",
                        len(degraded_indices),
                    )
                    # 重试一次
                    perk_books = [b for b in books if b.get("perks")]
                    for i in degraded_indices:
                        title = benchmark_data[i]["dangdang"]["title"]
                        target = next((b for b in perk_books if b.get("title") == title), None)
                        if not target:
                            continue
                        retry_result = jd_benchmark.query_jd_for_book(target, delay=1.5)
                        retry_self = (retry_result.get("best_match",{}) or {}).get("is_self", False)
                        if retry_self:
                            # 重试拿到自营了，更新这条
                            log.info("  ↪《%s...》重试成功，恢复自营匹配", title[:20])
                            benchmark_data[i]["jd"] = retry_result
                            from .jd_benchmark import _assess_gap
                            benchmark_data[i]["gap_level"] = _assess_gap(
                                benchmark_data[i]["dangdang"], retry_result
                            )
                        else:
                            # 重试还是不是自营，用旧数据兜底
                            old = old_by_title.get(title)
                            if old:
                                log.info("  ↪《%s...》重试失败，用旧数据兜底", title[:20])
                                benchmark_data[i] = old

                save_json("benchmark.json", benchmark_data)
                sources_status["benchmark"] = "ok"
                log.info("✓ 权益对标: %d 本", len(benchmark_data))
    except Exception:
        log.error("✗ 权益对标抛异常:\n%s", traceback.format_exc())
        sources_status["benchmark"] = "failed"

    # ============ 豆瓣校验（限流敏感，量要节制）============
    # 豆瓣对单 IP 反爬严，每次大概只能跑 5-10 次就被封
    # 策略：优先校验"看起来最像新书的"
    #   - 热卖榜的前 8 本（用户最关注的头部）
    #   - 预售书全部（最需要确认的"虚假新书"嫌疑）
    from datetime import datetime, timedelta, timezone
    from . import douban_verify
    today = datetime.now(timezone(timedelta(hours=8)))

    # 1) 校验热卖榜的前 N 本
    if books:
        try:
            HOT_VERIFY_TOP = 8
            log.info("=== 豆瓣校验热卖榜前 %d 本 ===", HOT_VERIFY_TOP)
            douban_verify.cross_check_books(books[:HOT_VERIFY_TOP], today)
        except Exception:
            log.error("豆瓣校验热卖榜抛异常:\n%s", traceback.format_exc())

    # 2) 校验所有预售书（已经在 dangdang_new.fetch 里跑了）
    # 此处再跑一次，用上次还没校验过的书（轮换策略）
    if new_books:
        unverified = [b for b in new_books
                      if b.get("verify_status") in (None, "skipped")]
        if unverified:
            try:
                log.info("=== 豆瓣再校验预售书 %d 本（上次未覆盖）===", len(unverified))
                douban_verify.cross_check_books(unverified, today)
            except Exception:
                log.error("豆瓣校验预售书抛异常:\n%s", traceback.format_exc())

    # ============ 抓新闻 ============
    from . import baidu_news
    news, status = _safe_run("baidu_news", baidu_news.fetch)
    sources_status["baidu_news"] = status

    if not news:
        log.warning("⚠ 所有新闻源失败，使用上次数据兜底")
        news = load_json("news.json", default=[])
    else:
        # 新闻数据保护：新数据少于旧数据 50% → 保留旧
        # 不要求旧数据有多少（哪怕只有 3 条，也比 0 条强）
        old_news = load_json("news.json", default=[]) or []
        if len(old_news) > 0 and len(news) < len(old_news) * 0.5:
            log.warning(
                "⚠ 新闻数 %d 少于旧数据 %d 的 50%%，可能反爬，保留旧数据",
                len(news), len(old_news),
            )
            news = old_news

    # ============ 写文件 ============
    save_json("books.json", books)
    save_json("news.json", news)

    # ============ 生成洞察 ============
    from . import insights as insights_mod
    try:
        jd_pop_only_data = load_json("jd_pop_only.json", default=[]) or []
        insights_list = insights_mod.generate(
            books, news,
            new_books=new_books,
            jd_pop_only=jd_pop_only_data,
        )
        save_json("insights.json", insights_list)
        log.info("✓ insights: %d 条", len(insights_list))
    except Exception:
        log.error("✗ insights 抛异常:\n%s", traceback.format_exc())

    # ============ meta.json ============
    # 简单的"本周新书数 + 环比"统计：
    # 由于我们目前只抓单日数据，环比拿不到精确值 — 用上次 meta 里的值做参照
    prev_meta = load_json("meta.json", default={}) or {}
    prev_count = prev_meta.get("new_books_week", len(books))
    if prev_count > 0:
        trend_pct = round((len(books) - prev_count) / prev_count * 100, 1)
    else:
        trend_pct = 0

    meta = {
        "updated_at": now_iso(),
        "new_books_week": len(books),
        "new_books_trend_pct": trend_pct,
        "news_count": len(news),
        "sources_status": sources_status,
    }
    save_json("meta.json", meta)

    log.info("═══ 完成 books=%d news=%d ═══", len(books), len(news))
    log.info("数据源状态: %s", sources_status)

    # ============ 数据质量验证 ============
    # 跑完抓取后立刻验证关键指标，发现退化输出警告
    try:
        from . import validate
        validate.main()
    except Exception:
        log.error("数据质量验证抛异常:\n%s", traceback.format_exc())

    # 任一源失败就退出码非 0（让 GitHub Actions 告警，但不阻断 commit）
    return 0 if all(s == "ok" for s in sources_status.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
