"""
豆瓣书目校验器
- 输入: 书名 + 作者
- 输出: 豆瓣记录的真实出版日期（YYYY-MM-DD 或 YYYY-MM）+ 评分 + 出版社 + subject_id
- 用于校准当当抓取的"出版时间"（当当字段经常错乱）

策略:
1. 用「书名 + 作者」搜豆瓣
2. 解析嵌在 HTML 里的 window.__DATA__ JSON
3. 取首个 search_subject 类型的结果
4. 提取 abstract 字段（"作者 / 出版社 / 出版日 / 价格"）解析出版日

反爬：
- 限流是豆瓣的硬性约束（约 30-60 次/小时 IP 会被冷却）
- 我们用 1.5-3 秒间隔 + 失败立即停止后续请求
- 如果豆瓣不可用，所有书标 'unverified' 而不是中断流程
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from urllib.parse import quote
from typing import Any

from .common import http_get, get_logger

log = get_logger("douban_verify")

DOUBAN_SEARCH_URL = "https://search.douban.com/book/subject_search"

# 限流策略：连续 N 次空结果就提早退出（豆瓣大概率封了）
_CONSECUTIVE_EMPTY_THRESHOLD = 5

# abstract 里的出版日期格式：'2025-6-1' 或 '2025-6' 或 '2025/6/1'
PUBDATE_RE = re.compile(
    r"\b(\d{4})\s*[-/年]\s*(\d{1,2})(?:\s*[-/月]\s*(\d{1,2}))?"
)


def _parse_abstract(abstract: str) -> dict:
    """
    解析豆瓣搜索结果的 abstract 字段，例如:
      "刘楚昕 / 漓江出版社 / 2025-6-1 / 42"
      "张嘉佳 / 湖南文艺出版社 / 2026-5 / 56元"
    返回 {publisher, pubdate}
    """
    out = {"publisher": "", "pubdate": None}
    if not abstract:
        return out

    parts = [p.strip() for p in abstract.split("/")]
    # 第二段一般是出版社（包含"出版社"或"书"等关键词）
    for p in parts:
        if "出版社" in p or "出版" in p or "书" in p:
            out["publisher"] = p
            break

    # 找出版日期
    m = PUBDATE_RE.search(abstract)
    if m:
        y, mn, d = m.group(1), m.group(2), m.group(3)
        try:
            yi = int(y)
            mi = max(1, min(12, int(mn)))
            di = int(d) if d else 1
            di = max(1, min(28, di))
            out["pubdate"] = f"{yi:04d}-{mi:02d}-{di:02d}"
        except (ValueError, TypeError):
            pass

    return out


def verify(title: str, author: str = "", timeout: int = 10) -> dict | None:
    """
    在豆瓣搜书名+作者，返回首个最匹配的结果。
    返回 dict: { matched: bool, douban_title, douban_pubdate, douban_publisher,
                 douban_rating, douban_id }
    完全无匹配时返回 None。
    """
    if not title:
        return None

    query = f"{title} {author}".strip()
    resp = http_get(
        DOUBAN_SEARCH_URL,
        params={"search_text": query},
        timeout=timeout,
        max_retries=2,
    )
    if resp is None:
        return None

    m = re.search(r"window\.__DATA__\s*=\s*({.+?});", resp.text, re.DOTALL)
    if not m:
        return None

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    items = data.get("items", []) or []
    # 只看真书目（不要"搜索更多""相关人物"等模板）
    real = [it for it in items if it.get("tpl_name") == "search_subject"]
    if not real:
        return None

    first = real[0]
    db_title = (first.get("title") or "").strip()
    abstract = first.get("abstract") or ""
    parsed = _parse_abstract(abstract)
    rating = ((first.get("rating") or {}).get("value")) or None
    rating_count = ((first.get("rating") or {}).get("count")) or 0

    return {
        "matched": True,
        "douban_title": db_title,
        "douban_pubdate": parsed["pubdate"],
        "douban_publisher": parsed["publisher"],
        "douban_rating": rating,
        "douban_rating_count": rating_count,
        "douban_id": first.get("id"),
    }


def cross_check_books(books: list[dict], today: datetime) -> list[dict]:
    """
    对一批书做豆瓣校验，给每本书加上 verify_* 字段。
    并根据豆瓣数据修正 pub_status:
      - 豆瓣有真实出版日 + 在过去  → pub_status 改为 'older' / 'recent' / 'fresh'
      - 豆瓣有出版日 + 在未来      → 保持 'preorder'
      - 豆瓣查不到                 → 保持当当原值，但标 verify_status='unverified'

    限流处理：连续 N 次拿不到结果就放弃后续校验（豆瓣大概率限流了）。
    """
    log.info("开始豆瓣校验 %d 本书...", len(books))
    success = 0
    rewritten = 0
    consecutive_empty = 0
    aborted = False

    for i, b in enumerate(books, 1):
        if aborted:
            b["verify_status"] = "skipped"
            continue

        result = verify(b.get("title", ""), b.get("author", ""))

        # 友好延迟：成功 1.5-2.5s，失败也 0.5s（避免打死豆瓣）
        time.sleep(1.5 + random.random())

        if not result:
            b["verify_status"] = "unverified"
            consecutive_empty += 1
            if consecutive_empty >= _CONSECUTIVE_EMPTY_THRESHOLD:
                log.warning(
                    "连续 %d 次豆瓣返回空 → 大概率被限流，放弃后续校验",
                    consecutive_empty,
                )
                aborted = True
            continue

        # 拿到结果 — 重置连续空计数
        consecutive_empty = 0

        # 写入豆瓣字段（前端可展示）
        b["douban_title"] = result["douban_title"]
        b["douban_pubdate"] = result["douban_pubdate"]
        b["douban_publisher"] = result["douban_publisher"]
        b["douban_rating"] = result["douban_rating"]
        b["douban_id"] = result["douban_id"]
        b["verify_status"] = "verified"
        success += 1

        # 用豆瓣的出版日期修正 pub_status
        if result["douban_pubdate"]:
            try:
                d = datetime.strptime(result["douban_pubdate"], "%Y-%m-%d").date()
                days = (today.date() - d).days
                old_status = b.get("pub_status")
                # 重新分类
                if days < 0:
                    new_status = "preorder"
                elif days <= 7:
                    new_status = "fresh"
                elif days <= 30:
                    new_status = "recent"
                else:
                    new_status = "older"

                # 重写关键字段
                if new_status != old_status:
                    rewritten += 1
                    b["pub_status_dangdang"] = old_status   # 保留当当原始判断
                    b["pub_status"] = new_status
                    b["days_since_pub"] = days
                    b["pubdate_verified"] = result["douban_pubdate"]
            except ValueError:
                pass

        if i % 5 == 0:
            log.info("  ... %d/%d (校验通过 %d, 修正 %d)", i, len(books), success, rewritten)

    log.info(
        "完成: %d/%d 校验通过 (%.0f%%)，其中 %d 本身份被修正",
        success, len(books), success / max(1, len(books)) * 100, rewritten,
    )
    if aborted:
        log.warning("⚠ 限流早退，剩余书标记为 'skipped'")
    return books


# ============================================================
# 直接测试
# ============================================================

if __name__ == "__main__":
    cases = [
        ("泥潭",            "刘楚昕"),
        ("我是你的遗物",      "张嘉佳"),
        ("万物有光",         "卢骁"),
        ("我大于一切",        "晴山"),
        ("不存在的虚构书名123", "假作者"),
    ]
    for title, author in cases:
        r = verify(title, author)
        print(f"\n《{title}》 - {author}")
        if r:
            print(f"  ✓ 豆瓣: {r['douban_title']}")
            print(f"    出版: {r['douban_pubdate']}  出版社: {r['douban_publisher']}")
            print(f"    评分: {r['douban_rating']} ({r['douban_rating_count']} 评)")
        else:
            print("  ✗ 豆瓣无记录")
