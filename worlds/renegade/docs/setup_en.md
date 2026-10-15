# Renegade Archipelago 1.0 â€” installation and setup

Renegade Archipelago connects the single-player campaign to an Archipelago room.
One Windows client supports both Steam and Tiberian Technologies (TT). The game
must be installed separately. You do not need Python, a command window, or files
copied into the game's installation folder.

## Requirements and supported versions

| Requirement | Supported / tested version |
| --- | --- |
| Operating system | 64-bit Windows |
| Stock game | Steam edition, launched with Steam â†’ Play |
| Patched game alternative | Tiberian Technologies **4.8.4, revision 9000 (b9000)**, launched with its `game.exe` |
| Archipelago, if generating a room or using its launcher | Tested with **0.6.8** |

Use one game instance and one Renegade client at a time. Other game patches,
custom binaries and multiplayer matches are not supported. Build checks prevent
attaching to an unrecognized executable. The client does not patch game executables
on disk. TT's campaign compatibility fixes are applied in memory while it runs.

TT is optional. Its supported installer is **4.8 Update 4 / revision 9000** on the
[official TT downloads page](https://www.tiberiantechnologies.org/downloads).
Follow that installer's instructions if choosing TT. Launch TT through `game.exe`;
do not start `game2.exe` directly. Stock Steam players should use Steam's Play
button rather than directly opening the installation's `game.exe`.

## Install the client

**Recommended:** download **Renegade-Archipelago-Windows.zip** from the GitHub
Releases page, right-click it, choose **Extract All**, and keep the extracted folder
somewhere convenient. Open **Renegade Archipelago.exe** from that folder.

The ZIP contains the client, `renegade.apworld`, two recommended YAML presets,
these instructions, release notes and license notices. The client includes its
own runtime and all 36 mission-start saves. No separate save download is needed.

If somebody else has already generated your room, the standalone
**Renegade Archipelago.exe** is sufficient. You do not need to install Archipelago
or the APWorld just to join that room.

### Optional: use Archipelago Launcher

1. Install [Archipelago for Windows](https://github.com/ArchipelagoMW/Archipelago/releases/tag/0.6.8)
   if you do not already have it. Version 0.6.8 is the tested release version.
2. Close Archipelago Launcher. Copy **renegade.apworld** to its **custom_worlds**
   folder, commonly `C:\ProgramData\Archipelago\custom_worlds`. Use the folder
   belonging to your installation if you installed it elsewhere.
3. Replace an older `renegade.apworld` rather than keeping duplicate copies under
   different names. Do not unzip the APWorld.
4. Restart Archipelago Launcher and open **Renegade Client**, described as
   **Renegade Archipelago: Steam / TT Build**.

This opens the same bundled client as the standalone EXE. You only need one of
these launch methods. First launch extracts the included client automatically;
future launches verify and reuse it. Both methods share your settings and progress.

## Connect and start playing

1. Start Renegade with the launch method for your game build above and leave it
   at the main menu.
2. Open the Renegade client. Set **Game version** to **Steam** or
   **Tiberian Technologies 4.8.4** to match the game you started.
3. Enter the room's **Server** address, for example `archipelago.gg:12345`.
   Use the address and port from the room page, without the `/connect` prefix;
   the room page's browser URL is not the server address.
4. Enter your **Slot name** exactly as generated in your YAML. Both recommended
   presets use **Renegade**. This is the in-room player name, not the YAML filename
   or your Archipelago website username. Enter a password only if the room uses one.
5. Click **Connect**. Wait for **Connected** and the observer/services to attach.
6. Select an **Available** mission in the table. Choose **Recruit**, **Soldier**
   or **Commando**, then click **Start Mission**. Return focus to the game to play.
7. Keep the client running and connected. Mission access, weapons and rewards
   are applied automatically. Expand a mission to see its individual checks.

If the game runs as administrator, run the client as administrator too. When
using Archipelago Launcher, start that launcher as administrator in this case.
Disconnect before changing the Game version selector.

## Generate and host a room

Skip this section if a host has already given you a room address and slot name.
The person generating the room needs the APWorld installed, including for a solo room.

1. Install the APWorld as described above.
2. Open the extracted **recommended-yamls** folder and choose **one** file:
   - **Recommended YAML Renegade.yaml** â€” DeathLink disabled.
   - **Recommended YAML Renegade - Death Link.yaml** â€” same settings with DeathLink enabled.
3. Copy your choice into Archipelago's **Players** folder. Keep only the YAMLs
   intended for this multiworld there; copying both presets creates duplicate
   player slots. Add the other players' YAMLs for a multiworld.
4. Edit `name: Renegade` if you want a different slot name. Each player needs a
   unique name. Configure the options below before generating. You can also use
   Archipelago's **Options Creator** and select **Command & Conquer: Renegade**.
5. Run **Generate** from Archipelago Launcher (or `ArchipelagoGenerate.exe`).
   Read any generation error; an item pool that exceeds the chosen check count
   needs more checks or fewer unlocks/upgrades.
6. Find the generated ZIP in Archipelago's **output** folder. Upload that ZIP to
   [Archipelago's Host Game page](https://archipelago.gg/uploads), then use the
   resulting page to create/open the room. Generate locally with the custom
   APWorld; upload the generated game ZIP for hosting, not the mod download ZIP.
7. Give each player their slot name and the server address/port displayed on the
   room page. Connect the Renegade client using those details.

### Configure your YAML

Options are under `Command & Conquer: Renegade:`. Preserve indentation and edit
values, not option names. Options Creator groups them into Gameplay Checks,
Death Link, Permanent Upgrades, Traps and Filler Items.

| Setting | Recommended preset | What it changes |
| --- | --- | --- |
| `checks_per_mission` | `5` | 1â€“5 checks awarded together for each mission victory. |
| `starting_missions` | `1` | First 1â€“12 missions available at the start. |
| `mission_unlocks` | `progressive` | `progressive` opens missions in story order; `individual` gives each remaining mission its own unlock item. |
| `weapon_unlocks` | `true` | Adds 22 weapon/equipment unlocks. The pistol is always available and is not an item. |
| `primary_objectives` | `true` | Adds 53 primary-objective checks. |
| `secondary_objectives` | `true` | Adds 54 secondary-objective checks. |
| `data_discs` | `true` | Adds 23 disc/intel checks, including the opening Black Hand encounter. EVA maps are revealed with this option on or off. |
| `death_link` | `false` / `true` | Shares deaths with other DeathLink participants. Only the Death Link preset enables it. |
| `health_upgrades` / `armour_upgrades` | `5` each | 0â€“5 permanent upgrades per stat; each adds 50 maximum capacity. |
| `trap_percentage` | `50` | Percentage of remaining item slots filled with traps after mission/weapon unlocks and upgrades. |
| `buff_percentage` | `50` | Percentage of the non-trap remainder filled with temporary bonuses; the rest are supplies. |
| Individual `*_weight` settings | `50` each | Relative chances within the trap or bonus category; `0` disables that item. |

With these presets there are **190 checks**: 60 victory rewards, 53 primary,
54 secondary and 23 disc/intel rewards. Individual weights of 50 give equal
chances within their category; they are not separate 50% shares of the whole pool.
With both category percentages at 50, the remaining pool is approximately **50%
traps, 25% temporary bonuses and 25% supplies**, subject to rounding. Turning traps
off gives all remaining slots to bonuses/supplies. An all-zero weight category
becomes supplies. Permanent upgrades and weapon unlocks are helpful items;
mission access logic is based on map unlocks and does not guarantee a particular
weapon before a boss or encounter.

**YAML changes require a newly generated room.** Editing a YAML after generation
does not alter that room. DeathLink and mission locks cannot be toggled in the
client; mission locks are always enabled. Difficulty is selected in the client.

## Saves, supplies and progress

**Start Mission** uses a bundled starting save at the selected difficulty. Missing
starting saves are installed when needed and reported in the session log. Existing
saves are preserved. The bundle has 12 missions Ã— 3 difficulties; it contains no
AP checks or items for your room.

**Continue Mission** loads a checkpoint for your room and mission. Enabled primary,
secondary and disc/intel checks request a checkpoint when gameplay is safe to save.
The list keeps the latest three snapshots. Choosing a difficulty different from
the checkpoint warns that continuing will restart the mission at that difficulty;
cancel to keep your current attempt. Start/Continue can queue a load while in a
mission or paused; cutscenes and transitions delay it until safe. Refocus the game
if a queued load is waiting for its update loop.

Earlier saves do not undo AP checks or restore spent supplies. Keep the client
open during play; captured checks survive temporary connection drops and sync
on reconnect. Disc pickups made before the client was running need to be repeated
from an earlier save or mission restart. Temporary effects end on a detected
mission/player replacement or explicit disconnect.

| Key | Banked supply |
| --- | --- |
| **F2** | Health: refill to maximum. |
| **F3** | Armour: refill to maximum. |
| **F4** | Ammo: refill all weapons currently held, including empty weapons. |

The supply buttons also work. A supply is kept if it cannot improve the relevant
resource. Traps and bonuses activate during playable control, with an in-game
effects table. Pauses/cutscenes suspend timers; repeated timed effects extend them.

A mission's **Complete mission** row records its victory separately from its side
checks. The parent mission turns green when all enabled checks are confirmed.
**Total Checks** counts all checks; **Checks in Logic** counts checks on unlocked
maps. **Goal completion requires victories in all 12 missions**, not every optional
check. Automatic release of remaining items depends on the room's server settings.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Cannot connect / slot rejected | Use the room's address and current port, exact slot name and password. Ensure the hosted room is running. |
| Connected but waiting for game | Start the correct installation, select its game version, and use the same administrator privilege level as the game. |
| Unsupported build | Use stock Steam via Steam â†’ Play, or TT 4.8.4 b9000 via `game.exe`. Other patches/custom scripts are not supported. |
| Renegade Client missing in Archipelago Launcher | Put one `renegade.apworld` in that installation's `custom_worlds` folder and restart the launcher. |
| Start/Continue unavailable | Connect and synchronize, select an unlocked mission, and wait for an earlier load to finish. Continue also needs a checkpoint for this room/mission. |
| Load queued but nothing changes | Return focus to Renegade; let a cutscene or loading screen finish. Read the load status below the mission table. |
| Mission won but still orange | Expand it: the victory row may be complete while objective/disc checks remain. |
| Newly edited YAML appears ignored | Generate a new room; existing rooms keep their original settings. |
| A supply does nothing | Check its stock count and whether the resource is already full. It waits while the player is not controllable. |
| Unsure whether an interrupted item applied | Read the client's recovery prompt. Retry/refund only if the item did not apply; otherwise mark it used. |

Logs are at `%LOCALAPPDATA%\RenegadeArchipelago\logs\desktop.log`. Steam and TT
progress/settings are stored in separate `steam` and `tt` folders under
`%LOCALAPPDATA%\RenegadeArchipelago`. For a GitHub issue, include your game version,
what you clicked/did, the relevant log excerpt, and whether it repeats after a
restart. Remove room passwords and other private details before sharing logs.

## Update or remove the client

Close the client and Archipelago Launcher before replacing their release files.
Replace the standalone EXE, or replace `renegade.apworld` and restart Archipelago
Launcher. Your progress/settings are retained in the Windows user-data folder.
Older rooms keep their original check catalogue; generate a new room for new
YAML options/checks. Use files from the same release.

To stop using the mod, disconnect and close its client. Restart the game to clear
any process-lifetime compatibility guards. You can remove the downloaded client
and APWorld without removing the separately installed game or its saves.
