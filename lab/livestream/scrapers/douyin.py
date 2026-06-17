"""
抖音用户主页抓取
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote


def fetch_user_posts(context, account: dict, options: dict) -> list[dict]:
    """
    抓某个抖音账号最近的作品。

    流程:
      1) 已知 sec_uid → 直接访问 /user/<sec_uid>
      2) 只有抖音号/昵称 → 先搜索定位，再点进主页
      3) 滚动加载列表，拿前 N 条
    """
    page = context.new_page()
    posts: list[dict] = []
    try:
        identifier = account["identifier"]
        n = options.get("posts_per_account", 10)

        # 1) 走搜索（用昵称/抖音号都行）
        url = f"https://www.douyin.com/search/{quote(identifier)}?type=user"
        page.goto(url, timeout=options.get("request_timeout", 30) * 1000)
        page.wait_for_timeout(3000)

        # 2) 找第一个用户卡片，点进去
        # 抖音搜索页结构：用户卡片有 a[href*="/user/"] 链接
        user_link = page.query_selector('a[href*="/user/MS4"]')
        if not user_link:
            return posts
        user_url = user_link.get_attribute("href")
        if not user_url.startswith("http"):
            user_url = "https://www.douyin.com" + user_url
        page.goto(user_url, timeout=options.get("request_timeout", 30) * 1000)
        page.wait_for_timeout(3000)

        # 3) 滚动 + 收集作品卡片
        for _ in range(2):
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(1500)

        # 作品卡片选择器（抖音页面结构会变，多试几个）
        cards = page.query_selector_all('a[href*="/video/"]')[:n]
        for card in cards:
            try:
                href = card.get_attribute("href") or ""
                # 拿标题（抖音卡片标题通常在 ../p 或 .. 兄弟节点）
                title = card.text_content() or ""
                if not title.strip():
                    parent = card.evaluate("(el) => el.closest('li,div')?.innerText || ''")
                    title = parent
                title = re.sub(r"\s+", " ", title).strip()[:200]
                # 时间（卡片角标）—— 抖音 PC 通常显示"2 天前"，没具体时间
                # 点进作品详情页才有完整时间，但为了速度暂不点入
                posts.append({
                    "url": "https://www.douyin.com" + href if not href.startswith("http") else href,
                    "title": title,
                    "content": "",
                    "published_at": None,   # 列表页拿不到精确时间
                })
            except Exception:
                continue
    except Exception as e:
        # 让上层 main.py 处理
        raise
    finally:
        page.close()
    return posts
