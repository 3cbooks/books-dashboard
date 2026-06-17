"""
小红书用户主页抓取
"""
from __future__ import annotations

import re
from urllib.parse import quote


def fetch_user_posts(context, account: dict, options: dict) -> list[dict]:
    """
    抓某个小红书账号最近的笔记。
    """
    page = context.new_page()
    posts: list[dict] = []
    try:
        identifier = account["identifier"]
        n = options.get("posts_per_account", 10)

        # 1) 看 identifier 是不是 24 位 hex 用户 ID（直接拼链接）
        if re.match(r"^[a-f0-9]{20,28}$", identifier):
            url = f"https://www.xiaohongshu.com/user/profile/{identifier}"
        else:
            # 否则当昵称/数字号搜
            url = f"https://www.xiaohongshu.com/search_result?keyword={quote(identifier)}&source=web_explore_feed"

        page.goto(url, timeout=options.get("request_timeout", 30) * 1000)
        page.wait_for_timeout(3000)

        # 如果是搜索页 → 切到"用户"tab → 点第一个用户卡片
        if "search_result" in page.url:
            # 点击"用户"筛选
            try:
                page.get_by_text("用户", exact=True).first.click()
                page.wait_for_timeout(2000)
            except Exception:
                pass
            # 第一个用户卡片
            user_link = page.query_selector('a[href*="/user/profile/"]')
            if not user_link:
                return posts
            href = user_link.get_attribute("href")
            if not href.startswith("http"):
                href = "https://www.xiaohongshu.com" + href
            page.goto(href, timeout=options.get("request_timeout", 30) * 1000)
            page.wait_for_timeout(3000)

        # 2) 滚动 + 收集笔记卡片
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1500)

        # 笔记卡片
        cards = page.query_selector_all('a[href*="/explore/"], a[href*="/discovery/item/"]')
        seen_urls = set()
        for card in cards:
            if len(posts) >= n:
                break
            try:
                href = card.get_attribute("href") or ""
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                title = card.text_content() or ""
                title = re.sub(r"\s+", " ", title).strip()[:200]
                if not title:
                    continue
                posts.append({
                    "url": "https://www.xiaohongshu.com" + href if not href.startswith("http") else href,
                    "title": title,
                    "content": "",
                    "published_at": None,
                })
            except Exception:
                continue
    finally:
        page.close()
    return posts
