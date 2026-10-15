@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\gate_test.py
echo.
echo The gate test has stopped. See .research\gate-status.json and the named logs.
pause
