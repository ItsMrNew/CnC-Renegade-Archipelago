@echo off
cd /d "%~dp0..\..\"
echo Creating 36 mission-start saves: Recruit, Soldier and Commando.
echo Close AP clients and leave stock Steam Renegade at its main menu.
".venv\Scripts\python.exe" tools\create_save_pack.py --difficulty all
echo.
echo Save pack generation has stopped. See .research\savepack-status.json and its named logs.
pause
