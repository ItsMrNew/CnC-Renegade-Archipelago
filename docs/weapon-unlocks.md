# Optional weapon unlocks

Set `weapon_unlocks: true` in the player YAML before generating a room. The
recommended presets enable this; the underlying option defaults to false.
With the option off, normal campaign weapon ownership is retained.

There are **22 Helpful / blue** unlock items, one per family below. They replace
other item slots without increasing the check count. The pistol is always
available with its normal unlimited reserve, and is never an AP item. Unlocked
weapons are added when entering/reloading a mission; locked weapons are removed.

Mission/check logic is based on map unlocks. It does not guarantee a particular
weapon before an encounter, so early fights can be harder. AP's `start_inventory`
can grant a preferred starting weapon. A generation error is shown if the chosen
check count is too small for unlocks and upgrades; enable more objective checks
or reduce those item counts.

| Guns | Equipment |
| --- | --- |
| Automatic Rifle | Remote C4 |
| Shotgun | Timed C4 |
| Chaingun | Proximity C4 |
| Flamethrower | Ion Cannon Beacon |
| Chemical Sprayer | Nuclear Strike Beacon |
| Grenade Launcher | |
| Rocket Launcher | |
| Sniper Rifle | |
| Laser Rifle | |
| Laser Chaingun | |
| Personal Ion Cannon | |
| Ramjet Rifle | |
| Railgun | |
| Tiberium Automatic Rifle | |
| Tiberium Flechette Gun | |
| Volt Auto Rifle | |
| Repair Gun | |

## Inventory behavior

- Each playable game update checks Havoc's inventory against the room's received
  unlocks. This covers mission entry, manual/checkpoint reloads, new AP items,
  scripted grants and ordinary pickups. Unlocked faction variants count as the
  same weapon family; they do not require separate items.
- A missing unlocked weapon is granted with its stock full magazine/reserve.
  Beacons receive one charge, Remote C4 nine, Timed C4 five, and Proximity C4 six.
  The pistol and repair gun retain their stock unlimited reserves.
- Existing owned weapons retain their ammunition. Empty guns, C4 and beacons
  stay empty, including when reloading a save that owns an empty weapon.
  Repeated AP copies do not refill ammo. Use pickups or Ammo Supply for refills.
- Locked weapons are removed, including ammo-only entries. Removing the selected
  weapon safely deselects it and selects the pistol. Other authorized selections
  and ammunition are preserved.
- Changes wait for a living player in a playable, unlocked campaign mission.
  Pauses, cutscenes, menus, death and victory transitions do not mutate the bag.
  AI allies (including Hologram Havoc) and vehicle inventories are untouched.
- Temporary network loss retains the last authenticated unlock set. A new client
  session waits for AP synchronization; a disk cache alone grants no access.

