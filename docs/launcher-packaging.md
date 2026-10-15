# Standalone Windows launcher

`dist/Renegade Archipelago.exe` is a single Windows x64 GUI executable. It supports
stock Steam and TT 4.8.4 b9000 through a selector; native build validation remains
enabled. Renegade itself must be installed and started separately. The APWorld is
needed by the room generator, not by people joining an existing room.

## Build

Install `requirements-build.txt` in a build environment, then run:

```powershell
python -m tools.build_launcher
```

This also produces `dist/Renegade-Archipelago-Windows.zip` containing the EXE,
APWorld, both recommended YAMLs, setup instructions, release notes and dependency notices.
The EXE can be downloaded separately by players joining an existing room.
The APWorld also embeds that exact EXE and registers **Renegade Client**, with
description **Renegade Archipelago: Steam / TT Build** and the Renegade icon.
Installing the APWorld and restarting Archipelago Launcher makes the entry available.

To package an already-built EXE, use
`python -m tools.build --executable "dist/Renegade Archipelago.exe"`.
`python -m tools.build --world-only` creates a smaller development APWorld without
the launcher entry. Release builds require the EXE and never silently omit it.

## Archipelago Launcher integration

World import only registers the launcher entry; it neither extracts nor runs the
EXE. Clicking Open verifies and extracts the payload to
`%LOCALAPPDATA%/RenegadeArchipelagoLauncher/clients/<payload-sha256>`.
Subsequent launches verify and reuse the cache. Updates get a new directory;
damaged copies are preserved for diagnosis and replaced from the bundle.

The adapter starts an independent Windows GUI process, resets inherited PyInstaller
state and temporarily clears Archipelago's DLL search directory. It preserves
LOCALAPPDATA so AP-launched and standalone clients share profiles and progress.
The bundle contains the EXE and its license notices, without personal state. URI autoconnect is not advertised; enter room details in the Renegade client.

The manifest uses APContainer format 7 for both `version` and `compatible_version`,
as specified by the [APWorld specification](https://github.com/ArchipelagoMW/Archipelago/blob/main/docs/apworld%20specification.md).
These fields are separate from the Renegade client protocol and world version.

The builder embeds `assets/Renegade.ico` and `assets/mission-starts.zip`.
The save archive contains 36 existing, validated campaign-start saves, with SHA-256
hashes and mission/difficulty metadata. To replace that archive deliberately:

```powershell
python -m tools.package_start_saves path/to/validated-mission-start-pack
```

The source entry scripts remain available for development. The existing Steam/TT
CMD shortcuts prefer the EXE when present, otherwise launch source via pythonw.
After source changes, rebuild the EXE before using those shortcuts.

## Player data

Frozen builds keep profiles in `%LOCALAPPDATA%/RenegadeArchipelago/steam` and `/tt`,
logs in `/logs`, and the selected game version in `launcher.json`. Replacing or moving
the EXE does not remove progress. On first use, an EXE beside the source project
or inside its `dist` folder copies missing JSON state from `.state` / `.state-tt`.
It never overwrites existing destination state. Source runs retain their original
state folders. Close the old launcher before moving to the EXE.

Start Mission installs a bundled save only if that mission/difficulty has no
matching AP starting save already installed. Bytes, hash, map and difficulty are
checked before writing. Publication is atomic and never replaces an existing file.
Checkpoints are never installed from the bundle.

## Release validation

The hidden `--self-test path/to/report.json` switch creates a withdrawn Tk window,
loads both adapters, checks a localhost WebSocket exchange and installs/validates
all 36 starting saves in a temporary directory. It never attaches to a game or room.
Run it on the built EXE copied by itself to a clean directory, with Python absent
from PATH. A successful report is an executable packaging check, not a live gameplay
test. Test one real Steam and TT connection before publishing a 1.0 release.

The launcher has no console subsystem and uses file logging plus UI error messages.
It is not code-signed by this build script. No GitHub upload occurs during a build.

## Export for GitHub

After building, run:

```powershell
python -m tools.prepare_release --launcher-dir dist --output dist/release-1.0
```

Use a fresh output path. `main` contains the release downloads and `SHA256SUMS.txt`;
`repositories/renegade-archipelago` is the clean repository source. The source ZIP
contains that same repository. Personal state, research, logs, old exports and
historical validation drafts are excluded. The default exports only this mod.

The exporter rejects mismatched EXE/APWorld/Windows ZIP contents, stale world
source and stale player documentation. Run the test suite and packaged-client
checks first; content consistency alone cannot prove an EXE's behavior. Nothing
is uploaded by these commands.
