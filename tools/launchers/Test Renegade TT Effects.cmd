@echo off
setlocal
cd /d "%~dp0..\..\"
echo Close the TT AP launcher, then load a safe saved mission and leave it focused.
echo This disposable test drains health/ammo and tests armour recovery.
echo Incoming/outgoing DeathLink modes also kill the current player.
echo No checks are sent. Hologram mode writes a separate temporary save; other modes do not save.
echo Default: incoming DeathLink. For outgoing use: --direction outgoing
echo Movement/timers: --direction movement. Disco/table: --direction presentation
echo Guided pause test: --direction pause --seconds 600
echo Butterfingers/Bullet Hose: --direction weapon. Equip a gun and aim at empty ground.
echo Weapon mode keeps the full two-minute stacked Bullet Hose duration for manual review.
echo Enemy-hit healing: --direction vampire. Requires a live enemy; keeps the full minute.
echo Bodyguard: --direction hologram. Full minute, invincibility and temporary-save exclusion.
echo Hologram mode preserves player health/ammo and backs up all existing saves.
echo Supply keys: --direction hotkeys. Press F2, F3 and F4 when requested.
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0..\..\tools\tt_effect_services_test.py" %*
pause
