# Attribution and license scope

The root MIT license covers original integration source and documentation.
It does not relicense game-derived artwork, icons, saves or third-party dependencies.

## Integration and dependencies

The inventory implementation is reused from the ZeroHour-Archipelago project.
Networking, persistence and the Win32 memory adapter are adapted from the same
project's design. Source: https://github.com/ItsMrNew/ZeroHour-Archipelago . The
inspected local HEAD was `13bef75243377d0da386e3c1c8a00f678b763800`.

The APWorld uses the Archipelago API. Archipelago is available at
https://github.com/ArchipelagoMW/Archipelago under its repository license.
It is installed separately from this client.

The standalone client bundles Python, Tcl/Tk and websockets using PyInstaller.
Their notices are included in `assets/licenses` in the Windows ZIP/source, and
in `licenses` beside the client extracted from the APWorld:

- Python: `Python-LICENSE.txt`
- Tcl/Tk: `Tcl-Tk-license.terms`
- websockets: `websockets-LICENSE.txt`
- PyInstaller: `PyInstaller-COPYING.txt`, including its bootloader exception

## Renegade

EA's released Renegade source was consulted to locate integration candidates.
No EA source, game executable or SDK is bundled. Source licensing is documented at
https://github.com/electronicarts/CnC_Renegade/blob/main/LICENSE.md .

The launcher uses `Renegade.ico` from the installed Steam game and a PNG derivative
for its Archipelago Launcher entry. These belong to the game's rights holders.
`assets/mission-starts.zip` contains campaign saves created with the game; the
separately installed game is still required to use them. Short mission/objective
names identify tasks in that game; no game string database is distributed.

Command & Conquer and Renegade belong to their respective rights holders.
This is an unofficial community integration and is not endorsed by EA or
Tiberian Technologies. The integration's MIT license does not grant rights to
their game assets or trademarks.
