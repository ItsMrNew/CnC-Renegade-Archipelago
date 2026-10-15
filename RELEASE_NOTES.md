# Renegade Archipelago 1.0

Play all 12 Renegade campaign missions in an Archipelago multiworld, with mission
unlocks, optional weapon unlocks, objectives, supplies, bonuses, traps and DeathLink.

## Download and install

**Recommended: download `Renegade-Archipelago-Windows.zip`, extract it, and open
`Renegade Archipelago.exe`.** The ZIP includes a complete installation/configuration
guide, the APWorld, recommended YAMLs and notices. No separate Python installation
or command window is needed. Renegade itself is required separately.

- **Joining an existing room:** the standalone `Renegade Archipelago.exe` also works.
- **Generating a room or using Archipelago Launcher:** put `renegade.apworld` in
  Archipelago's `custom_worlds` folder, restart its launcher, and open **Renegade Client**.
  The APWorld includes the same client EXE.
- **Creating your first room:** choose one recommended YAML, use slot **Renegade**,
  and follow the included guide. The two presets differ only in DeathLink.

Start the game, select its version in the client, enter the room server and slot,
and connect. Select an available mission and use **Start Mission** or **Continue
Mission**. Keep the client open while playing. `INSTALL.md` is also available as a
separate release download. `SHA256SUMS.txt` covers the downloadable files.

## Supported versions

- **Stock Steam Renegade**, started through **Steam → Play**.
- **Tiberian Technologies 4.8.4, revision b9000**, started through that install's **game.exe**.
- One **64-bit Windows** client handles both builds. Single-player campaign only.
- Archipelago generation and launcher integration tested with **0.6.8**.

Other patches/custom game binaries are not supported. The client validates the
build before attaching; no game executable is modified on disk.

## Features

- Up to **190 checks**: 60 victory rewards, 53 primary objectives, 54 secondary
  objectives and 23 optional data-disc/intel checks.
- Progressive or individual mission unlocks; optional unlocks for 22 weapon and
  equipment families. The pistol is always available.
- **F2 / F3 / F4** health, armour and ammo supplies; five +50 health and five +50
  armour upgrades; Bullet Hose, Vampiric Rounds and Hologram Havoc bonuses.
- Armour Loss, Butterfingers, Concrete Boots, Disco Havoc and Moon Boots traps.
  Individual trap/bonus weights and category percentages are configurable in YAML.
- YAML-controlled DeathLink, in-game item notifications and active-effect timers.
  Timers stop when the player is not in control; repeats extend timed effects.
- All **36 mission-start saves** bundled, automatic objective/disc checkpoints,
  three-save history, and a warning before a difficulty change restarts a mission.
- Full EVA maps, mission locks, check search/filters, item-transfer logs and light/dark themes.

The goal is to **win all 12 missions**. Optional checks are not required for the
goal; automatic release of remaining items follows the room's server settings.

## Fixes included in 1.0

- Mission 02's SAM objective recognizes early destruction and saves where the
  game omitted its completion callback.
- Checkpoint saving refreshes input state to prevent stuck firing or movement.
- Start/Continue can queue mission loads during gameplay or pause, waiting for a
  safe transition. TT campaign save-load compatibility guards are included.
- Captured checks survive connection loss; uncertain item activations can be resolved.
- Concrete Boots blocks jumping; Butterfingers blocks firing during all five reloads.
- Item-name colours follow the server's classification, including other games' items.
- Launcher actions safely handle disconnect races, concurrent display updates are
  bounded, and inventory/status updates avoid repeated item-list scans.

## Updating and limitations

Close the client before replacing its EXE or APWorld. Keep your
`%LOCALAPPDATA%\RenegadeArchipelago` folder to retain profiles and progress.
Existing rooms retain their settings/check catalogue; generate a new room for
changed YAML options. Weapon logic is based on unlocked maps and does not promise
a particular weapon before an encounter. Pick up discs with the client running;
pickups from before the client session must be repeated from an earlier save.

Original integration code is MIT licensed. Game assets and dependencies retain
their separate notices, included with the download.
