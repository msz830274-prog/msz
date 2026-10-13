@echo off
title 启动 SnipasteOCR
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py
