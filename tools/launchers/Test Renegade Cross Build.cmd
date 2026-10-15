@echo off
setlocal
cd /d "%~dp0..\..\"
echo Run as administrator if either Renegade process is elevated.
echo Launch stock Renegade using Steam's Play button ^(Game.dat, not stock Game.exe^).
echo Launch the separate TT copy using its game.exe ^(starts game2.exe^).
echo Both may be in single player or at their menus.
echo Close AP launchers. Follow the console's Steam/TT focus instructions.
echo If either game shows its death dialog during a queued load, choose Quit to return to the menu.
echo Uses a disposable room, backs up both save folders and kills both players twice.
echo Two AP checks are synthetic transport probes; no native objectives or victories are forced.
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\tools\cross_build_test.py" %*
echo.
echo Results: .research\cross-build-status.json and its named run folder.
pause
