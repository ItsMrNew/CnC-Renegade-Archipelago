@echo off
setlocal
cd /d "%~dp0..\..\"
echo Close AP launchers and leave TT focused at its main menu.
echo Tests queued effects, disconnect cleanup, cutscenes and deferred DeathLink.
echo Existing saves are backed up. The final DeathLink kills the test player.
echo No AP objective, disc or victory checks are forced.
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\tools\tt_lifecycle_test.py" %*
pause
