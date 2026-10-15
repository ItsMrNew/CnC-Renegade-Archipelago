@echo off
setlocal
cd /d "%~dp0..\..\"
echo Close other Archipelago clients. Load a safe mission-start save in TT.
echo Leave the game unpaused and focused. Existing saves will be backed up.
echo Mission loads will be requested directly from gameplay and transition automatically.
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\tools\tt_campaign_test.py" %*
pause
