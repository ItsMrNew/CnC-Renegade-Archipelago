@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\live_test.py
echo.
echo The test has stopped. See .research\live-status.json and the live test logs.
pause
