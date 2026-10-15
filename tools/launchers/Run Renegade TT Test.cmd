@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0..\..\tools\tt_test_install.ps1" -Action Launch
if errorlevel 1 pause
