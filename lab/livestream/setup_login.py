"""
首次登录设置 — 你要跑一次

它做什么:
  1) 启动一个 Chrome 浏览器（保留你的登录状态到本地目录）
  2) 自动跳转抖音 → 你扫码登录
  3) 自动跳转小红书 → 你扫码登录
  4) 你点回车关闭，登录信息就保存了
  5) 之后 main.py 跑就用这份登录状态，不用每天再登

只跑一次，除非:
  - 你换了电脑
  - 抖音/小红书把你的 cookie 过期了（一般几个月一次）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
USER_DATA_DIR = ROOT / ".chrome_userdata"


def main() -> int:
    print("═══ 直播预告监控 — 首次登录设置 ═══\n")
    print(f"登录数据目录: {USER_DATA_DIR}")
    if USER_DATA_DIR.exists():
        print("  → 已存在，将复用（如要重新登录请删除该目录）")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright 未安装。请运行:")
        print("   pip install playwright pyyaml")
        print("   playwright install chromium")
        return 1

    USER_DATA_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(USER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        # 抖音
        print("【步骤 1/2】打开抖音首页...")
        page1 = context.new_page()
        page1.goto("https://www.douyin.com/", timeout=60000)
        print("  → 请扫码登录抖音（用手机抖音 App 扫页面右上角的二维码）")
        input("  → 登录完成后按回车继续...\n")

        # 小红书
        print("【步骤 2/2】打开小红书首页...")
        page2 = context.new_page()
        page2.goto("https://www.xiaohongshu.com/explore", timeout=60000)
        print("  → 请扫码登录小红书")
        input("  → 登录完成后按回车继续...\n")

        # 验证
        print("正在验证登录状态...")
        page1.goto("https://www.douyin.com/user/self", timeout=30000)
        page1.wait_for_timeout(3000)
        if "登录" in page1.content() and "退出" not in page1.content():
            print("  ⚠ 抖音登录可能未成功，请稍后再试")
        else:
            print("  ✓ 抖音登录成功")

        page2.goto("https://www.xiaohongshu.com/user/profile/me", timeout=30000)
        page2.wait_for_timeout(3000)
        if "登录" in page2.content()[:5000] and "退出" not in page2.content()[:5000]:
            print("  ⚠ 小红书登录可能未成功")
        else:
            print("  ✓ 小红书登录成功")

        context.close()

    print("\n═══ 设置完成！═══")
    print("现在可以运行: python -m lab.livestream.main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
