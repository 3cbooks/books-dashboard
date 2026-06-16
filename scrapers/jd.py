"""
京东图书 POP / 自营 抓取器（基于 Playwright）

核心目标：
  找到「京东 POP 商家在卖、京东自营没卖」的书 → 自营选品的内部缺口

策略：
  1. 用 Playwright 浏览京东搜索页（多个关键词 + 多个分类，提高 POP 覆盖率）
  2. 提取每本书的 SKU、是否自营、书名、作者、出版社、出版年月
  3. 把所有 SKU 分到两组：jd_self / jd_pop
  4. 后续在 main.py 里做差集（POP - 自营），输出 jd_pop_only.json

注意：
  - 京东用了 CSS modules 类名混淆（_xxxx_随机串），不能依赖 class 选择器
  - 用属性选择器（data-sku, alt="自营"）+ 文本特征 来稳定提取
  - GitHub Actions 上跑：headless=True + 单实例 + 谨慎滚动
"""
from __future__ import annotations

import re
import time
from typing import Iterator
from urllib.parse import quote

from .common import get_logger

log = get_logger("jd")

# 关键词 — 选取覆盖广 + POP 出现率高的搜索词
SEARCH_QUERIES = [
    "小说",
    "文学",
    "童书",
    "历史",
    "科普",
    "经管",
    "心理",
]

# 京东每页 60 个商品，我们只抓第 1 页（避免触发反爬）
SEARCH_URL_TMPL = (
    "https://search.jd.com/Search?keyword={kw}&book=y&enc=utf-8"
)


def _extract_cards_from_page(page) -> list[dict]:
    """
    在已打开的搜索页面里，用 JS 提取所有商品卡的关键字段。
    返回的 dict 字段：
      sku, is_self, title, author, publisher, pubdate, price, detail_url
    """
    return page.evaluate(r"""() => {
        const cards = document.querySelectorAll('[data-sku]');
        return Array.from(cards).map(c => {
            const sku = c.getAttribute('data-sku');
            // 是否自营：找 alt="自营" 的 img
            const isSelf = !!c.querySelector('img[alt="自营"]');
            // 标题：[title] 属性是最完整的（不会被截断）
            const titled = c.querySelector('[title]');
            const title = (titled?.getAttribute('title') || '').trim();

            // 提取所有 span 里的元数据
            // 京东把 "作者 著 | 出版社 | YYYY-MM" 拆成多个 span
            const spans = Array.from(c.querySelectorAll('span'));
            let pubdate = '', publisher = '', author = '';
            spans.forEach(s => {
                const t = (s.innerText || '').trim();
                if (/^\d{4}-\d{1,2}$/.test(t)) {
                    if (!pubdate) pubdate = t;
                } else if (t.includes('出版社') && !publisher) {
                    publisher = t.replace(/\|/g, '').trim();
                } else if ((t.endsWith(' 著') || t.endsWith('著')) && !author && t.length < 40) {
                    author = t.replace(/\|/g, '').replace(/\s*著\s*$/, '').trim();
                }
            });

            // 价格：找 [class*="price"] 的内层文本
            const priceEl = c.querySelector('[class*="price"]');
            const price = (priceEl?.innerText || '').replace(/\s/g, '');

            // 详情页 URL
            const linkEl = c.querySelector('a[href*="item.jd.com"]');
            let detailUrl = linkEl?.getAttribute('href') || '';
            if (detailUrl.startsWith('//')) detailUrl = 'https:' + detailUrl;
            if (!detailUrl && sku) detailUrl = 'https://item.jd.com/' + sku + '.html';

            return { sku, is_self: isSelf, title, author, publisher, pubdate, price, detail_url: detailUrl };
        });
    }""")


def _scrape_query(playwright, query: str, max_scrolls: int = 4) -> list[dict]:
    """用 Playwright 抓单个关键词的搜索页（带 stealth 反检测）。"""
    url = SEARCH_URL_TMPL.format(kw=quote(query))
    log.info("抓 [%s] → %s", query, url[:80])

    # 启动浏览器，带反检测参数
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",  # 隐藏 navigator.webdriver
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )
    try:
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                           "image/avif,image/webp,*/*;q=0.8"),
                "Accept-Encoding": "gzip, deflate, br",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Sec-Fetch-Dest": "document",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        # 启用 stealth 反检测脚本（隐藏 webdriver / 修复 chrome.runtime 等指纹）
        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(ctx)
        except ImportError:
            log.warning("playwright-stealth 未安装，跳过 stealth 增强")

        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 滚动触发懒加载，每次滚动后小停
            for _ in range(max_scrolls):
                try:
                    page.evaluate("window.scrollBy(0, 1000)")
                except Exception:
                    break  # 页面可能被反爬重定向
                page.wait_for_timeout(1500)

            # 检查是否被反爬重定向
            if "passport" in page.url or "captcha" in page.url.lower():
                log.warning("[%s] 被重定向到验证页: %s", query, page.url[:80])
                return []

            try:
                cards = _extract_cards_from_page(page)
            except Exception as e:
                log.warning("[%s] 提取失败: %s", query, e)
                cards = []
        finally:
            ctx.close()
    finally:
        browser.close()

    log.info("  └─ 抓到 %d 个商品", len(cards))
    return cards


def fetch() -> dict:
    """
    抓多个关键词，分类到自营 / POP。
    返回 {self: [...], pop: [...]}
    """
    from playwright.sync_api import sync_playwright

    self_books: list[dict] = []
    pop_books: list[dict] = []
    seen_skus: set[str] = set()

    with sync_playwright() as p:
        for query in SEARCH_QUERIES:
            try:
                cards = _scrape_query(p, query)
            except Exception as e:
                log.error("抓 [%s] 失败: %s", query, e)
                continue

            for c in cards:
                sku = c.get("sku")
                if not sku or sku in seen_skus:
                    continue
                seen_skus.add(sku)
                # 标记搜索关键词，便于后续按品类分组
                c["_query"] = query
                if c.get("is_self"):
                    self_books.append(c)
                else:
                    pop_books.append(c)

            # 关键词之间停 3 秒，避免触发反爬
            time.sleep(3)

    log.info("=== 京东抓取完成 ===")
    log.info("  自营 (jd_self): %d 本", len(self_books))
    log.info("  POP  (jd_pop):  %d 本", len(pop_books))

    return {"self": self_books, "pop": pop_books}


# ============================================================
# 直接运行测试
# ============================================================

if __name__ == "__main__":
    import json
    result = fetch()
    print("\n=== POP 商家书（前 5 本）===")
    for b in result["pop"][:5]:
        print(f"  {b['title'][:50]}")
        print(f"    作者: {b.get('author', '')} | 出版: {b.get('pubdate', '')} | {b.get('price', '')}")
    print("\n=== 自营书（前 3 本）===")
    for b in result["self"][:3]:
        print(f"  {b['title'][:50]}")
