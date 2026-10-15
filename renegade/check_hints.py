"""Player-facing directions, separate from stable AP location names and IDs.

Landmarks are stock script names or nearby objective markers joined to disc
coordinates in .research/poptracker/disc-survey.json and tracker/objective_targets.json.
Floor names match the tracker. Directions describe search areas, not exact routes.
"""
from .data import DATA_DISCS, disc_id, objective_kind

DISC_HINTS = {
    'M13': {-41: 'At the opening battle, shoot either Black Hand sniper on the ridge above the GDI soldiers. The scripted kill grants Black Hand intel automatically; there is no ground disc to collect. Either sniper awards the same single check, even if the encyclopedia entry was already known.'},
    'M01': {
        116992: 'Beach at the start of the mission. Look for the disc before moving inland.',
        109634: 'First Tiberium cave, near the GDI commander encounter.',
        123646: 'Inside the shed between the turret area and the northern part of the map.',
        123647: 'Second Tiberium cave, east of the clergy rescue area.',
        0: 'Scripted priest reward: rescue the clergy and stay near the surviving priest until his reward conversation finishes. Pick up the disc he drops. The pin marks the encounter area; the drop follows the priest. If he dies before giving it, reload an earlier checkpoint or restart.',
        114764: 'Communications mainframe area in the northwest. This is the Obelisk dossier disc.',
        125064: 'Medical lab in the underground Hand of Nod area, below the surface base.',
    },
    'M02': {
        407528: 'At the guard tower near the early helipad. Search the Secure Guard Tower objective area.',
        407529: 'At the third house in the village. Search the Secure Third House objective area.',
        407530: 'At the ski resort. Search the Secure Ski Resort objective area.',
        300799: 'Hand of Nod / missing scientists area at the far eastern end of the map. D4 and D5 are nearby; collect both.',
        300800: 'Hand of Nod / missing scientists area at the far eastern end of the map. This is the southern of the two nearby discs.',
    },
    'M03': {
        1206589: 'Southern part of the island, near the lower SAM-site area. Search the broken wooden beams and rubble beside an armour pickup.',
        1206590: 'Northwest of the first disc, near the communications center area. Inspect the wooden crate beside the red-edged metal platform.',
    },
    'M05': {
        107976: 'Eastern side of the town map, east of Patch’s objective marker. Look beside the wrecked red buggy, among the supplies.',
        110102: 'At the escapee encounter on the western side of town. Search beside the table with plans and the sofa inside his room.',
    },
    'M06': {113439: 'Mansion interior, near the Mendoza / escort area. Look on the table between the bunk beds in the small sleeping quarters. Use the D1 marker to locate the room.'},
    'M07': {
        123916: 'Beside D2 near the napalm stockpile on the western side of the map. There are two separate pickups.',
        123917: 'Beside D1 near the napalm stockpile on the western side of the map. Collect both discs.',
    },
    'M08': {
        110110: 'On the northern bench in the locker room south of the surface helipad. D2 is on the other bench; both are on the surface floor.',
        110111: 'On the southern bench in the locker room south of the surface helipad. Collect D1 from the other bench before descending.',
    },
    'M09': {1201187: 'Tunnels floor: in the red-lit workshop in the northern tunnels, beside the central workbench and drill press. Select Tunnels in PopTracker to locate this pickup.'},
}


def check_kind(map_name, check):
    key = check['id']
    if key == 'victory':
        return 'victory'
    if str(key).startswith('disc_'):
        return 'disc'
    return objective_kind(map_name, int(key))


def disc_description(map_name, object_id):
    disc_id(map_name, object_id)
    number = DATA_DISCS[map_name].index(object_id) + 1
    return f'PopTracker D{number}: ' + DISC_HINTS[map_name][object_id]


def check_description(map_name, check):
    if check['state'] == 'Missed this attempt':
        return 'Renegade reports this objective failed. Load an earlier checkpoint or restart the mission to try again. This has not sent an AP check.'
    if check_kind(map_name, check) == 'disc':
        return disc_description(map_name, int(str(check['id'])[5:]))
    if check['id'] == 'victory':
        return 'Complete the mission to earn its victory checks. Unfinished side objectives remain available on replay.'
    return check['title']


def filter_missions(rows, query='', hide_completed=False, kinds=None):
    """Filter the display only; mission permissions and check totals are untouched."""
    kinds = {'victory', 'primary', 'secondary', 'disc'} if kinds is None else kinds
    query = query.strip().casefold()
    result = []
    for row in rows:
        mission_matches = query in row['title'].casefold()
        children = [check for check in row.get('objectives', ())
                    if check_kind(row['map'], check) in kinds
                    and not (hide_completed and check['state'] == 'Complete')
                    and (mission_matches or query in (check['title'] + ' ' + check_description(row['map'], check)).casefold())]
        if children:
            result.append(dict(row, objectives=children))
    return result
