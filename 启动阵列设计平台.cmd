@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\run_platform.py %*
if errorlevel 1 pause
