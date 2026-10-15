@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\tt_hud_fix.py
pause
