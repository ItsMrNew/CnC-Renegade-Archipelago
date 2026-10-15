@echo off
cd /d "%~dp0"
if exist "dist\Renegade Archipelago.exe" (
    start "" "dist\Renegade Archipelago.exe" --steam
) else (
    start "" ".venv\Scripts\pythonw.exe" RenegadeDesktop.py --steam
)
