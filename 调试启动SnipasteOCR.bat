@echo off
title 调试启动 SnipasteOCR (带控制台日志)
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py --show
pause
