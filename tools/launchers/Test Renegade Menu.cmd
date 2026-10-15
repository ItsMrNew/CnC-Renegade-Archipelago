@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\gate_test.py --menu
echo.
echo The menu test has stopped. See .research\menu-status.json and the named logs.
pause
