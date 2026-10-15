@echo off
cd /d "%~dp0..\..\"
echo Close AP clients and leave stock Steam Renegade at its main menu.
echo Testing supplies, upgrades, and Armour Loss in a disposable local room.
".venv\Scripts\python.exe" tools\rewards_test.py
echo.
echo Test stopped. See .research\rewards-status.json and its named log.
pause
