"""
数据质量验证脚本 — 跑完抓取后自动检查
本意：每天自动跑时，输出一份"健康度报告"，发现退化立刻能看到。

检查点：
  1. 当当数据：≥30 本，含权益的 ≥10 本
  2. 京东对标：≥10 本，自营匹配率 ≥60%
  3. 新闻：近 30 天内 ≥10 条
  4. 关键书自营匹配（含具体书名清单 — 这些书今天确认过应该匹配自营）
"""
from __future__ import annotations

import json
from pathlib import Path
from .common import get_logger, load_json

log = get_logger("validate")


# 关键书的"期望 SKU 守护清单"
# 每天自动跑时如果某本书 best_match 不是这个期望 SKU，要警告
# 这是"业务确认过的对标关系"——不能让搜索波动覆盖
EXPECTED_KEY_SKUS = {
    "我是你的遗物":     "15371686",   # 博集天卷京东自营
    "陪安东尼":         "14671295",   # 中南天使京东自营（预订）
    "泥潭":             "14516547",   # 漓江出版社京东自营
    "真实之书":         "15385432",   # 京东自营（venderId=0 识别）
    "泉州寻宝记":       "15345754",   # 中信童书京东自营
    "白鹿原":           "15372704",   # 人民文学出版社京东自营（十周年纪念版）
    "人间小满3":        "14720705",   # 文通天下京东自营（单本）
}


# 已经验证过应该匹配到京东自营的关键书（书名片段）— 兼容旧逻辑
EXPECTED_JD_SELF_BOOKS = list(EXPECTED_KEY_SKUS.keys()) + [
    "肥志百科", "请和我门外的花", "伊加利亚", "万物有光",
]


def validate() -> dict:
    """跑完整体检，返回 dict 报告。"""
    report = {
        "ok": True,
        "warnings": [],
        "errors": [],
        "stats": {},
    }

    # ============ 检查当当 ============
    books = load_json("books.json", default=[]) or []
    perk_books = [b for b in books if b.get("perks")]
    report["stats"]["dangdang_total"] = len(books)
    report["stats"]["dangdang_perk"] = len(perk_books)
    if len(books) < 30:
        report["errors"].append(f"当当书数 {len(books)} < 30，可能反爬")
        report["ok"] = False
    if len(perk_books) < 8:
        report["warnings"].append(f"权益书数 {len(perk_books)} < 8")

    # ============ 检查新闻 ============
    news = load_json("news.json", default=[]) or []
    report["stats"]["news_total"] = len(news)

    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8))
    cutoff_30d = (datetime.now(tz) - timedelta(days=30)).isoformat(timespec="seconds")
    fresh_count = sum(1 for n in news if (n.get("published_at") or "") >= cutoff_30d)
    report["stats"]["news_fresh_30d"] = fresh_count

    if len(news) < 10:
        report["errors"].append(f"新闻总数 {len(news)} < 10，可能反爬")
        report["ok"] = False

    # ============ 检查对标 ============
    benchmark = load_json("benchmark.json", default=[]) or []
    report["stats"]["benchmark_total"] = len(benchmark)

    no_jd_count = 0
    self_count = 0
    pop_count = 0
    for r in benchmark:
        if not r.get("jd", {}).get("available"):
            no_jd_count += 1
            continue
        best = r.get("jd", {}).get("best_match", {}) or {}
        if best.get("is_self"):
            self_count += 1
        else:
            pop_count += 1
    report["stats"]["benchmark_self"] = self_count
    report["stats"]["benchmark_pop"] = pop_count
    report["stats"]["benchmark_no_jd"] = no_jd_count

    if len(benchmark) > 0:
        no_jd_rate = no_jd_count / len(benchmark)
        if no_jd_rate >= 0.5:
            report["errors"].append(
                f"京东未在售率 {no_jd_rate:.0%} ≥ 50%，可能反爬"
            )
            report["ok"] = False
        elif no_jd_rate >= 0.3:
            report["warnings"].append(f"京东未在售率 {no_jd_rate:.0%}")

        if len(benchmark) >= 10 and self_count / len(benchmark) < 0.5:
            report["warnings"].append(
                f"自营匹配率 {self_count}/{len(benchmark)} = {self_count/len(benchmark):.0%}"
            )

    # ============ 关键书自营匹配验证 ============
    book_to_status: dict[str, str] = {}
    for r in benchmark:
        title = r["dangdang"]["title"]
        if not r.get("jd", {}).get("available"):
            book_to_status[title] = "no_jd"
            continue
        best = r["jd"].get("best_match", {}) or {}
        book_to_status[title] = "self" if best.get("is_self") else "pop"

    degraded = []
    for expected in EXPECTED_JD_SELF_BOOKS:
        for title, status in book_to_status.items():
            if expected in title:
                if status != "self":
                    degraded.append(f"{title[:30]} → {status}")
                break

    if degraded:
        report["warnings"].append(
            f"关键书退化（应自营但不是）: {len(degraded)} 本"
        )
        for d in degraded:
            report["warnings"].append(f"  · {d}")

    return report


def main() -> int:
    """打印验证报告。退出码：ok=0, errors=1。"""
    rep = validate()

    log.info("═══ 数据质量报告 ═══")
    log.info("整体状态: %s", "✅ OK" if rep["ok"] else "❌ 有问题")
    log.info("")
    log.info("[统计]")
    for k, v in rep["stats"].items():
        log.info("  %-25s %s", k, v)
    if rep["errors"]:
        log.info("")
        log.info("[❌ 错误]")
        for e in rep["errors"]:
            log.info("  %s", e)
    if rep["warnings"]:
        log.info("")
        log.info("[⚠️ 警告]")
        for w in rep["warnings"]:
            log.info("  %s", w)
    log.info("═══════════════")

    # 写出 markdown 摘要给 GitHub Actions step summary
    summary = _format_markdown(rep)
    Path("data/_validate_report.md").write_text(summary, encoding="utf-8")
    return 0 if rep["ok"] else 1


def _format_markdown(rep: dict) -> str:
    icon = "✅" if rep["ok"] else "❌"
    lines = [f"## {icon} 数据质量报告\n"]
    lines.append("### 统计")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for k, v in rep["stats"].items():
        lines.append(f"| {k} | {v} |")
    if rep["errors"]:
        lines.append("\n### ❌ 错误")
        for e in rep["errors"]:
            lines.append(f"- {e}")
    if rep["warnings"]:
        lines.append("\n### ⚠️ 警告")
        for w in rep["warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.exit(main())
