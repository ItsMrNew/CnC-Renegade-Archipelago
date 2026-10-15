@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\unlock_test.py
echo.
echo The unlock test has finished. See the PASS or error above.
echo Results: .research\unlock-status.json and its named run directory.
pause
