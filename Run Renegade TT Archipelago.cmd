@echo off
cd /d "%~dp0"
if exist "dist\Renegade Archipelago.exe" (
    start "" "dist\Renegade Archipelago.exe" --tt-core
) else (
    start "" ".venv\Scripts\pythonw.exe" RenegadeDesktop.py --tt-core
)
