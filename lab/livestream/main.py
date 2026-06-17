"""
直播预告监控 — 主入口

每天定时跑一次:
  1) 用持久化 Chrome（带你登录的账号 cookie）
  2) 逐个访问 9 个达人主页
  3) 抓最近 10 条作品/笔记
  4) 用正则识别"今晚 8 点 / 6/19 直播"等预告时间
  5) 识别书名《XXX》
  6) 渲染 HTML 报告到 output/

环境要求:
  - Python 3.10+
  - playwright 1.40+
  - 首次跑需运行 setup_login.py 登录一次
"""
from __future__ import annotations

import json
import sys
import yaml
import time
import random
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 项目根目录
ROOT = Path(__file__).parent
USER_DATA_DIR = ROOT / ".chrome_userdata"   # 持久化登录目录（首次手动登录后保留）
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

# 让 scrapers 能直接导入（不论是 python main.py 还是 python -m main）
sys.path.insert(0, str(ROOT))

# Beijing tz
TZ = timezone(timedelta(hours=8))


def log(msg: str):
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_accounts() -> dict:
    """读取账号配置"""
    cfg_path = ROOT / "accounts.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    log("═══ 直播预告监控开始 ═══")
    cfg = load_accounts()
    accounts = cfg["accounts"]
    options = cfg["options"]
    log(f"账号数: {len(accounts)}（抖音 {sum(1 for a in accounts if a['platform']=='douyin')} / "
        f"小红书 {sum(1 for a in accounts if a['platform']=='xiaohongshu')}）")

    # 检查持久化登录目录
    if not USER_DATA_DIR.exists():
        log("❌ 没有找到登录数据目录")
        log(f"   请先运行: python setup_login.py")
        log(f"   它会打开浏览器让你登录抖音和小红书各一次")
        return 1

    # 启动 Playwright
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("❌ Playwright 未安装。请运行:")
        log("   pip install playwright pyyaml")
        log("   playwright install chromium")
        return 1

    from scrapers import douyin, xiaohongshu, extractor, render

    all_posts = []
    failed_accounts = []

    with sync_playwright() as p:
        # 持久化 context — 保留登录状态
        context = p.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=False,         # 首跑用 False 方便看，稳定后改 True
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        for i, acc in enumerate(accounts, 1):
            log(f"[{i}/{len(accounts)}] 抓取 {acc['platform']} · {acc['name']}")
            try:
                if acc["platform"] == "douyin":
                    posts = douyin.fetch_user_posts(context, acc, options)
                elif acc["platform"] == "xiaohongshu":
                    posts = xiaohongshu.fetch_user_posts(context, acc, options)
                else:
                    log(f"   ⚠ 未知平台: {acc['platform']}")
                    continue

                # 解析每条作品里的预告
                for post in posts:
                    parsed = extractor.parse_livestream(
                        post.get("title", "") + "\n" + post.get("content", ""),
                        published_at=post.get("published_at"),
                    )
                    post["livestream"] = parsed
                    post["account_name"] = acc["name"]
                    post["account_platform"] = acc["platform"]

                all_posts.extend(posts)
                log(f"   ✓ {len(posts)} 条作品 / {sum(1 for p in posts if p['livestream'].get('has_preview'))} 条预告")
            except Exception as e:
                log(f"   ❌ 失败: {e}")
                failed_accounts.append(f"{acc['platform']} · {acc['name']}")

            # 账号间隔（避免被限流）
            delay = random.uniform(*options["delay_between"])
            time.sleep(delay)

        context.close()

    # 保存原始数据 + 渲染 HTML
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    snapshot_path = DATA_DIR / f"snapshot_{datetime.now(TZ).strftime('%Y%m%d')}.json"
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "posts": all_posts,
            "failed_accounts": failed_accounts,
        }, f, ensure_ascii=False, indent=2)
    log(f"✓ 原始数据写入 {snapshot_path}")

    html_path = OUTPUT_DIR / "index.html"
    render.render_report(all_posts, failed_accounts, html_path)
    log(f"✓ 报告写入 {html_path}")

    log("═══ 完成 ═══")
    return 0


if __name__ == "__main__":
    sys.exit(main())
