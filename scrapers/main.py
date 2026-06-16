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
    books, status = _safe_run("dangdang", lambda: dangdang.fetch(per_category=4))
    sources_status["dangdang"] = status

    # 失败兜底：用上次的 books.json
    if not books:
        log.warning("⚠ 所有图书源失败，使用上次数据兜底")
        books = load_json("books.json", default=[])

    # ============ 抓真新书 / 预售书 ============
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

    # ============ 抓京东 POP 自营对比 ============
    # 用 requests 抓京东榜单页拿 SKU + 移动版详情页拿字段
    # （之前 Playwright 方案被反爬屏蔽，这版用 SSR 友好的入口绕开了）
    try:
        from . import jd as jd_mod, jd_compare
        jd_data = jd_mod.fetch(max_skus=80)  # 80 个 SKU 是配额节制
        save_json("jd_self.json", jd_data["self"])
        save_json("jd_pop.json", jd_data["pop"])
        sources_status["jd"] = "ok" if (jd_data["self"] or jd_data["pop"]) else "partial"

        # 比对：找 POP 在卖、自营没卖
        pop_only = jd_compare.find_pop_only(jd_data["self"], jd_data["pop"])
        save_json("jd_pop_only.json", pop_only)
        log.info("✓ 京东 POP 独家（自营无）: %d 本", len(pop_only))
    except Exception:
        log.error("✗ 京东抓取/比对抛异常:\n%s", traceback.format_exc())
        sources_status["jd"] = "failed"

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

    # 任一源失败就退出码非 0（让 GitHub Actions 告警，但不阻断 commit）
    return 0 if all(s == "ok" for s in sources_status.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
