# EVA maps and optional data-disc checks

The client reveals the current mission's full EVA map when playable control
returns, including after a save load. This works with `data_discs` enabled or
disabled and does not collect a disc or award a check.

Set `data_discs: true` before generating to add **23 disc/intel checks**. The
recommended presets enable this; the underlying world option defaults to false.
With five victory checks and all objectives, discs raise the total from 167 to
190. They are optional for the goal, which requires all 12 mission victories.

| Campaign mission | Discs |
| --- | ---: |
| 01 - The Scorpion Hunters | 1 scripted intel reward |
| 02 - Rescue and Retribution | 7 |
| 03 - Armored Assault | 5 |
| 04 - The Plot Erupts | 2 |
| 06 - Deadly Reunion | 2 |
| 07 - The Grip of the Black Hand | 1 |
| 08 - Obelisk of Oppression | 2 |
| 09 - Evolution of Evil | 2 |
| 10 - All Brains, No Brawn | 1 |


These are map/encyclopedia discs and one scripted intel reward, not keycards or
equipment. Select a disc/intel entry in the client's mission list for its location
and pickup directions. In mission 02, the priest reward requires keeping him alive
and staying for his reward conversation; the drop follows his current position.

## The Scorpion Hunters' Black Hand intel

Defeat either opening Black Hand sniper to receive **Black Hand Intel - Defeat an
opening Black Hand sniper**. It is a scripted intel reward; there is no ground
disc to pick up. The first accepted reward sends one check and requests a
checkpoint. Defeating the second sniper does not duplicate it.

## Checkpoints and older saves

Keep the client running while collecting. Both supported builds capture successful
pickups and queue a checkpoint at the next safe game update. Captured checks survive
temporary connection loss and are not duplicated on replay. A disc collected before
the client session must be collected again from an earlier save or a mission restart.
A missing disc, revealed map or mission victory alone is not proof of collection.

Checkpoints keep three snapshots per mission and room, selectable with **Continue
Mission**. Loading an earlier save retains AP checks and received/spent items.

Older rooms keep their generated location catalogue: protocol 9 rooms have the
original 22 disc checks; protocol 10 adds the opening Black Hand reward. Existing
location IDs are preserved. Generate a new room for all 23 rewards.
