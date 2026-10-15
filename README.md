# Command & Conquer: Renegade â€” Archipelago

Play Renegade's single-player campaign in an Archipelago multiworld. Complete
objectives to send items to the room, and receive mission unlocks, weapons,
supplies, bonuses and traps from other players.

**New player? [Download the Windows ZIP](https://github.com/ItsMrNew/CnC-Renegade-Archipelago/releases/latest/download/Renegade-Archipelago-Windows.zip),
extract it, and follow the [installation guide](docs/windows-quickstart.md).** The game is
required separately. Python is bundled; no command window or separate Python
installation is needed.

## Supported versions

| Game installation | How to start it | Launcher selection |
| --- | --- | --- |
| Stock Steam edition | Steam â†’ Play | Steam |
| Tiberian Technologies **4.8.4, revision b9000** | That installation's `game.exe` | Tiberian Technologies 4.8.4 |

The launcher is for **64-bit Windows** and supports both builds in one EXE.
Single-player campaign only. Other patches, custom game binaries and multiplayer
matches are not supported. The client checks the game build before attaching.
Archipelago generation and its launcher integration have been tested with **0.6.8**.

## Choose your download

| File | Use it for |
| --- | --- |
| **Renegade-Archipelago-Windows.zip** | Recommended: EXE, APWorld, two YAML presets, instructions and notices together. |
| **Renegade Archipelago.exe** | Join an existing room with the standalone client. |
| **renegade.apworld** | Add Renegade to Archipelago generation and add **Renegade Client** to Archipelago Launcher. Includes the same EXE. |
| **Renegade-Archipelago-Source.zip** | Source code for development; players do not need this. |
| **SHA256SUMS.txt** | SHA-256 hashes for checking the downloaded files. |

Use the release downloads above; GitHub's **Code â†’ Download ZIP** contains source.

## Join a room

1. Start the supported game and leave it at the main menu.
2. Open **Renegade Archipelago.exe** and select your game version.
3. Enter the room's **server address and port**, your **slot name**, and its password
   if required. Click **Connect**. The recommended presets use slot **Renegade**.
4. Select an available mission, choose **Recruit**, **Soldier** or **Commando**,
   then click **Start Mission**. Use **Continue Mission** for a saved checkpoint.
5. Keep the client connected while playing. Use **F2 / F3 / F4** for banked health,
   armour and ammo supplies.

If the game runs as administrator, run the client as administrator too.
The [full guide](docs/windows-quickstart.md) explains the Archipelago Launcher
alternative, room generation, YAML configuration, checkpoints and troubleshooting.

## Create and configure a room

Install `renegade.apworld` in Archipelago's `custom_worlds` folder and restart
Archipelago Launcher. Copy **one** preset into its `Players` folder:

- [Recommended YAML Renegade](recommended-yamls/Recommended%20YAML%20Renegade.yaml): DeathLink off.
- [Recommended YAML Renegade - Death Link](recommended-yamls/Recommended%20YAML%20Renegade%20-%20Death%20Link.yaml): DeathLink on.

Both use **Renegade** as the slot name, all 190 checks, one starting mission,
progressive mission unlocks, weapon unlocks, and five health/five armour upgrades.
Edit settings before generating the room. Existing rooms keep their generated
settings. See the [configuration and hosting steps](docs/windows-quickstart.md#generate-and-host-a-room).

## What is included?

- Twelve campaign missions, unlocked progressively or by individual mission items.
- Up to **190 checks**: 60 victory rewards, 53 primary objectives, 54 secondary
  objectives and 23 optional data-disc/intel rewards.
- Optional unlocks for 22 weapons/equipment families; the pistol is always available.
- Banked supplies, +50 permanent health/armour upgrades, three temporary bonuses,
  five traps and optional YAML-controlled DeathLink.
- In-game item notifications and effect timers. Pauses and cutscenes suspend timers;
  repeated timed items extend their duration.
- Start/Continue buttons, all 36 mission-start saves, and automatic objective/disc
  checkpoints with three-save history. Existing saves are preserved.
- Complete EVA maps, mission locks, check filters, item-transfer logs and light/dark themes.

**Goal:** win all 12 missions. Optional objectives and discs are not required for
the goal. Whether remaining items are automatically released depends on the room's
server settings.

More detail: [items](docs/items.md) Â· [weapon unlocks](docs/weapon-unlocks.md) Â·
[data discs](docs/data-discs.md) Â· [1.0 release notes](RELEASE_NOTES.md).

## Updates and help

Close the client before replacing its EXE or APWorld. Settings and progress remain
in `%LOCALAPPDATA%/RenegadeArchipelago`; do not delete this folder when updating.
For a problem, check the red warnings in the session log and the
[troubleshooting guide](docs/windows-quickstart.md#troubleshooting), then open a
GitHub issue with your game build, steps and relevant log excerpt.

## Development

Use Python 3.12 on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-build.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m tools.build_launcher
```

Integration: `renegade/`. APWorld: `worlds/renegade/`. Packaging:
[developer guide](docs/launcher-packaging.md). Release evidence:
[checklist](docs/release-checklist.md). Builds and exports do not publish anything.

## License

Original integration code is [MIT licensed](LICENSE). Game assets and dependencies
retain their separate ownership and licenses; see [notices](THIRD_PARTY_NOTICES.md).
This unofficial integration builds on
[ZeroHour-Archipelago](https://github.com/ItsMrNew/ZeroHour-Archipelago).
Command & Conquer and Renegade belong to their respective rights holders.
