@echo off
REM 图书直播预告监控 — 每日运行脚本
REM 由 Windows 任务计划程序定时调用，或者你手动双击运行

REM 切到脚本所在目录
cd /d "%~dp0"

REM 跑主入口
python main.py

REM 任务计划自动跑时不弹窗；手动双击时如果失败保留窗口
if errorlevel 1 (
    echo.
    echo [错误] 脚本运行失败，按任意键关闭...
    pause >nul
)
