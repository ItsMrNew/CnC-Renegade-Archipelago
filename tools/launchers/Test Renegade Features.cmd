@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\features_test.py
echo.
echo Results are in .research\features-status.json and its named run directory.
pause
