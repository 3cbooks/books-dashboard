@echo off
REM ============================================================
REM  本地手动跑京东抓取 + 推送
REM  双击此文件即可运行（需要提前 git/gh 已经登录）
REM ============================================================
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo === 1. 跑京东抓取（约 2 分钟）===
python -m scrapers.main
if %errorlevel% neq 0 (
    echo.
    echo [失败] Python 脚本异常，请截图发给我
    pause
    exit /b 1
)

echo.
echo === 2. 推送数据到 GitHub ===
git add data/
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo (数据无变化，无需提交)
) else (
    git commit -m "data: 本地手动跑（含京东 POP 数据）"
    git push origin main
)

echo.
echo === 完成 ===
echo 网站会在 1 分钟内自动重建：
echo   https://jiang12481.github.io/books-dashboard/
echo.
pause
