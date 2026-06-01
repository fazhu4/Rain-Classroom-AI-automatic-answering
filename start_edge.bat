@echo off
chcp 65001 >nul
title 启动 Edge 调试模式

echo ============================================
echo   正在准备启动 Edge 浏览器（调试模式）
echo ============================================
echo.

echo [1/3] 正在关闭所有 Edge 窗口...
taskkill /f /im msedge.exe 2>nul
echo 已完成

echo [2/3] 等待进程完全退出...
timeout /t 2 /nobreak >nul

echo [3/3] 正在启动 Edge 调试模式...
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
echo.

echo ============================================
echo   Edge 已启动！
echo.
echo   请在新打开的浏览器窗口中：
echo   1. 登录考试网站
echo   2. 进入考试页面
echo   3. 然后双击运行 AutoExamSolver.exe
echo ============================================
echo.
echo 按任意键关闭此窗口...
pause >nul
