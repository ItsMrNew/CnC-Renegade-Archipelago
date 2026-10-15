@echo off
cd /d "%~dp0..\..\"
".venv\Scripts\python.exe" -m renegade.native --output ".research\native-live.json"
echo.
echo Diagnostic saved to .research\native-live.json. No game memory was changed.
pause
