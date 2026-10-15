@echo off
setlocal
cd /d "%~dp0..\..\"
echo Close other Archipelago clients. Load a safe mission-start save in TT.
echo Leave the game unpaused and focused. This test changes the current loadout.
echo No game saves are written. Reload your save after the test.
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\tools\tt_inventory_test.py" %*
pause
