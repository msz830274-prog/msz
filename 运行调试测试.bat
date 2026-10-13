@echo off
title SnipasteOCR 专项调试诊断工具
cd /d "%~dp0"
echo ===================================================
echo 正在启动 SnipasteOCR 专项调试诊断工具...
echo ===================================================
".venv\Scripts\python.exe" debug_test.py
pause
