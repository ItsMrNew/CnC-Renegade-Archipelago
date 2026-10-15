@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" tools\completion_test.py
echo.
echo The automated completion test has finished. See the PASS or error above.
echo Results: .research\completion-status.json and its named run directory.
pause
