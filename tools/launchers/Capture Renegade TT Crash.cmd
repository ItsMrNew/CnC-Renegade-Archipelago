@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\tt_crash_capture.py
pause
