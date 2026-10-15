@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\tt_core_test.py --discs
pause
