# Item rewards

These items work in both supported Steam and TT builds. Mission access comes
from required items; weapons and permanent upgrades are helpful items; supplies
and temporary bonuses are filler. Traps apply negative effects.

| Category | Item | Effect |
| --- | --- | --- |
| Required / purple | Progressive Campaign Clearance | Unlocks the next campaign mission. |
| Required / purple | Mission Unlock: [mission title] | In individual mode, unlocks only the named mission, independently of earlier missions. |
| Helpful / blue | Permanent Health Upgrade | +50 guaranteed maximum health per copy, up to five copies (+250). |
| Helpful / blue | Permanent Armour Upgrade | +50 guaranteed maximum armour per copy, up to five copies (+250). |
| Helpful / blue | Weapon Unlock: [weapon name] | Optional permanent access to one of 22 weapon/equipment families. Pistol is always available. See [weapon unlocks](weapon-unlocks.md). |
| Filler / cyan | Bullet Hose | Doubles firing/charge speed, bypasses burst limits for held-trigger full auto and prevents ammo consumption for 60 playable seconds. Guns only; C4, beacons and other equipment are excluded. |
| Filler / cyan | Vampiric Rounds | Each damaging enemy hit credited to Havoc restores 10 health and 10 armour, capped at current maxima, for 60 playable seconds. |
| Filler / cyan | Hologram Havoc | An invincible, cyan shimmering Havoc follows and fights with a Volt auto rifle for 60 playable seconds. One ally at a time; repeat copies add 60 seconds. |
| Filler / cyan | Health Supply | Refills current health to its actual maximum. |
| Filler / cyan | Armour Supply | Refills current armour to its actual maximum. |
| Filler / cyan | Ammo Supply | Fills magazine and reserve ammunition for every owned weapon; does not grant weapons or enable an infinite-ammo cheat. |
| Trap / red | Armour Loss Trap | Sets current armour to zero. Leaves health and maximum armour unchanged. |
| Trap / red | Butterfingers | Blocks firing through five successive native reload cycles, including the gaps and final reload. Does not delete ammunition. |
| Trap / red | Concrete Boots | Reduces on-foot movement input to 45% and prevents jumping for 30 seconds. |
| Trap / red | Disco Havoc | Adds a gentle, low-opacity colour cycle for 60 seconds. No flashing. |
| Trap / red | Moon Boots | 1.6Ã— jump impulse with two-thirds gravity and 80% airborne steering for 60 seconds. Approximately 50% higher/longer jumps than the original Moon Boots. |


## Using items

Supplies are banked until you press **F2** (health), **F3** (armour), **F4** (ammo),
or their client buttons. A supply is kept if the resource is already full. Ammo
Supply refills all held weapons, including weapons with empty magazine and reserve;
it does not grant locked weapons. Hold a key to request once, then release before
using another supply.

Traps and temporary bonuses activate automatically while Havoc is alive and
controllable in an unlocked mission. Menus, pauses, cutscenes, death and mission
transitions delay activation. Timers count playable game time only, and the
in-game effects table shows remaining time/reloads. Repeats extend the same effect
without multiplying its strength; each Butterfingers adds five reloads.

Temporary effects end on detected level/player replacement or explicit disconnect.
A temporary network outage preserves active effects, while new received items
wait for synchronization. Spent items are not replayed on reconnect or restored by
loading an older save. An interrupted activation with an uncertain outcome is
shown in the client's recovery prompt so you can mark it used or retry/refund it.

Upgrades increase maximum capacity, not current health/armour. Higher scripted
campaign maxima are preserved. Hologram Havoc follows on foot, does not enter
vehicles, and is omitted from checkpoint saves. It expires or is removed with
the current attempt. If no safe spawn space exists, the item is refunded.

Item names in transfer/receipt logs use the **server's classification**, including
items from other games: progression purple, helpful blue, filler cyan, traps
salmon/red. A console grant whose type is not supplied by the server is neutral.
Warnings/errors are red. The rest of an item-transfer message stays neutral.

## Pool settings

The recommended YAML includes five health and five armour upgrades, each +50.
`trap_percentage` controls the trap share of the pool left after mission unlocks,
weapon unlocks and permanent upgrades. `buff_percentage` controls the temporary
bonus share of the non-trap remainder. Both recommended percentages are 50:
approximately half of the remainder becomes traps, a quarter bonuses and a quarter
supplies, subject to rounding.

Each trap/bonus has a weight from 0 to 100, initially 50. These are relative weights
within a category; 0 disables that item. Setting every weight in a category to 0
turns its slots into supplies. All settings take effect when generating a new room.

## DeathLink

Set `death_link: true` in the YAML before generating, or use the Death Link preset.
Deaths are shared with other enabled participants in the room. Incoming deaths
wait until Havoc is alive in playable control; menus and cutscenes are not targets.
There is no client toggle. Consumables and Vampiric Rounds cannot revive Havoc.
