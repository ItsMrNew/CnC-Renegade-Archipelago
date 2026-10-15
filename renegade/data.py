"""Shared world/client contract. IDs are provisional until the first public release."""
from dataclasses import dataclass

GAME = "Command & Conquer: Renegade"
PROTOCOL_VERSION = 1
BASE_ID = 0x524E0000
CLEARANCE = "Progressive Campaign Clearance"
REPORT = "Mission Report"
ITEM_IDS = {CLEARANCE: BASE_ID, REPORT: BASE_ID + 1}
HEALTH_SUPPLY = "Health Supply"
ARMOUR_SUPPLY = "Armour Supply"
AMMO_SUPPLY = "Ammo Supply"
# Existing IDs stay reserved for compatibility with already-generated rooms.
SUPPLIES = (HEALTH_SUPPLY, ARMOUR_SUPPLY, AMMO_SUPPLY)
ITEM_IDS.update({name: BASE_ID + 2 + i for i, name in enumerate(SUPPLIES)})
HEALTH_UPGRADE = "Permanent Health Upgrade"
ARMOUR_UPGRADE = "Permanent Armour Upgrade"
ARMOUR_TRAP = "Armour Loss Trap"
BUTTERFINGERS = "Butterfingers"
CONCRETE_BOOTS = "Concrete Boots"
DISCO_HAVOC = "Disco Havoc"
MOON_BOOTS = "Moon Boots"
NEW_TRAPS = (BUTTERFINGERS, CONCRETE_BOOTS, DISCO_HAVOC, MOON_BOOTS)
TRAPS = (ARMOUR_TRAP, *NEW_TRAPS)
TRAP_DURATIONS = {CONCRETE_BOOTS: 30, DISCO_HAVOC: 60, MOON_BOOTS: 60}
BUTTERFINGERS_RELOADS = 5
BULLET_HOSE = 'Bullet Hose'
VAMPIRIC_ROUNDS = 'Vampiric Rounds'
HOLOGRAM_HAVOC = 'Hologram Havoc'
BUFFS = (BULLET_HOSE, VAMPIRIC_ROUNDS, HOLOGRAM_HAVOC)
BUFF_DURATIONS = {name: 60 for name in BUFFS}
UPGRADES = (HEALTH_UPGRADE, ARMOUR_UPGRADE)
UPGRADE_AMOUNT = 50
UPGRADE_LIMIT = 5
ITEM_IDS.update({HEALTH_UPGRADE: BASE_ID + 5, ARMOUR_UPGRADE: BASE_ID + 6, ARMOUR_TRAP: BASE_ID + 7})
ITEM_IDS.update({name: BASE_ID + 8 + i for i, name in enumerate(NEW_TRAPS)})
ITEM_IDS.update({name: BASE_ID + 12 + i for i, name in enumerate(BUFFS)})
ITEM_FLAGS = {CLEARANCE: 1, REPORT: 0, **{n: 0 for n in SUPPLIES},
              HEALTH_UPGRADE: 2, ARMOUR_UPGRADE: 2, **{n: 4 for n in TRAPS}, **{n: 0 for n in BUFFS}}
# Same palette and light-theme contrast adjustment as the Zero Hour launcher.
ITEM_COLOURS = {1: "#AF99EF", 2: "#6D8BE8", 4: "#FA8072", 0: "#00EEEE"}
LIGHT_ITEM_COLOURS = {1: "#7651B0", 2: "#3055B2", 4: "#B43F32", 0: "#007E83"}


@dataclass(frozen=True)
class Mission:
    number: int
    map_name: str
    title: str

    @property
    def label(self):
        return f"{self.number:02d} - {self.title}"

    @property
    def unlock_item(self):
        return f"Mission Unlock: {self.title}"

    def check_id(self, check=1):
        if type(check) is not int or not 1 <= check <= 5:
            raise ValueError("Check number must be between 1 and 5")
        return BASE_ID + 0x100 + (self.number - 1) * 8 + check

    def check_name(self, check=1):
        return f"{self.label} - Complete" + (f" - Bonus {check - 1}" if check > 1 else "")


# Campaign order is not numerical map order: M13 is the opening mission.
MISSIONS = tuple(Mission(n, map_name, title) for n, (map_name, title) in enumerate([
    ("M13", "The Scorpion Hunters"),
    ("M01", "Rescue and Retribution"),
    ("M02", "Armored Assault"),
    ("M03", "The Plot Erupts"),
    ("M04", "Stowaway"),
    ("M05", "Deadly Reunion"),
    ("M06", "The Grip of the Black Hand"),
    ("M07", "Obelisk of Oppression"),
    ("M08", "Evolution of Evil"),
    ("M09", "All Brains, No Brawn"),
    ("M10", "Tomorrow's Technology Today"),
    ("M11", "Stomping on Holy Ground"),
], 1))
BY_MAP = {mission.map_name: mission for mission in MISSIONS}
MISSION_UNLOCK_MODES = ('progressive', 'individual')
# Separate reserved range; existing item and location IDs remain unchanged.
ITEM_IDS.update({m.unlock_item: BASE_ID + 0x20 + m.number - 1 for m in MISSIONS})
ITEM_FLAGS.update({m.unlock_item: 1 for m in MISSIONS})
# One item per player weapon family, independent of faction/preset variants.
# The pistol is deliberately absent: it is always available in weapon mode.
WEAPONS = (
    'Automatic Rifle', 'Shotgun', 'Chaingun', 'Flamethrower', 'Chemical Sprayer',
    'Grenade Launcher', 'Rocket Launcher', 'Sniper Rifle', 'Laser Rifle',
    'Laser Chaingun', 'Personal Ion Cannon', 'Ramjet Rifle', 'Railgun',
    'Tiberium Automatic Rifle', 'Tiberium Flechette Gun', 'Volt Auto Rifle',
    'Repair Gun', 'Remote C4', 'Timed C4', 'Proximity C4',
    'Ion Cannon Beacon', 'Nuclear Strike Beacon',
)
WEAPON_ITEMS = tuple('Weapon Unlock: ' + name for name in WEAPONS)
ITEM_IDS.update({name: BASE_ID + 0x40 + i for i, name in enumerate(WEAPON_ITEMS)})
ITEM_FLAGS.update({name: 2 for name in WEAPON_ITEMS})
ALL_LOCATIONS = {m.check_name(c): m.check_id(c) for m in MISSIONS for c in range(1, 6)}


def mission_requirement(mission, mode='progressive'):
    if mode not in MISSION_UNLOCK_MODES:
        raise ValueError('Invalid mission unlock mode')
    return (mission.unlock_item, 1) if mode == 'individual' else (CLEARANCE, mission.number)

# Stock campaign Add_Objective calls, excluding commented-out objectives and
# M06's internal hidden alarm-state marker (610). Type SECONDARY is 2.
# IDs are game objective IDs, scoped to a map; never awarded on mission victory.
# This order defines AP IDs and native capture bits. NEVER reorder it for display.
SECONDARY_OBJECTIVES = {
    "M13": (), "M01": (100, 102, 103, 104, 109, 114),
    "M02": (202, 204, *range(206, 222)), "M03": (1001, 1002, 1004),
    "M04": (200, 500), "M05": (507, 508, 509, 510, 512), "M06": (609,),
    "M07": (704, 705, 706, 707, 708), "M08": (804, 806, 807), "M09": (),
    "M10": (1008, 1009, 1010, 1011, 1013, 1014, 1015, 1016, 1017, 1018, 1019), "M11": (),
}

# Presentation only: Grawl's GameFAQs guide 16086, mission objective lists.
# Preserve the listed relative order and insert omitted checks near their route
# encounters. See docs/objective-order.md for omissions and inferred placements.
SECONDARY_DISPLAY_ORDER = {
    "M13": (),
    "M01": (114, 102, 104, 109, 100, 103),
    "M02": (206, 207, 209, 208, 210, 221, 212, 213, 211, 214, 215, 216,
            217, 202, 218, 219, 220, 204),
    "M03": (1001, 1002, 1004),
    "M04": (200, 500),
    "M05": (507, 508, 509, 512, 510),
    "M06": (609,),
    "M07": (704, 705, 706, 707, 708),
    "M08": (804, 806, 807),
    "M09": (),
    "M10": (1015, 1008, 1009, 1018, 1010, 1011, 1017, 1014, 1019, 1013, 1016),
    "M11": (),
}


# English objective titles from the stock Steam string database, matched through
# each campaign script's Add_Objective title string ID. Keep IDs/order above stable.
SECONDARY_TITLES = {
    "M01": {
        100: "Rescue Clergy",
        102: "Contact GDI Commander",
        103: "Disable Hand of Nod",
        104: "Eliminate Nod Officer",
        109: "Destroy Nod Turrets",
        114: "Rescue Prisoners",
    },
    "M02": {
        202: "Disable Obelisk",
        204: "Disable Hand of Nod",
        206: "Disable Helipad",
        207: "Secure Guard Tower",
        208: "Secure Second House",
        209: "Secure First House",
        210: "Secure Third House",
        211: "Secure Ski Resort",
        212: "Disable First SAM Site",
        213: "Destroy Nod Convoy",
        214: "Disable Second SAM Site",
        215: "Disable Third SAM Site",
        216: "Disable Fourth SAM Site",
        217: "Disable Power Plant",
        218: "Destroy Tiberium Machine Alpha",
        219: "Destroy Tiberium Machine Beta",
        220: "Destroy Tiberium Machine Gamma",
        221: "Acquire Mammoth Tank",
    },
    "M03": {
        1001: "Secure Beachhead",
        1002: "Disable Lower SAM Sites",
        1004: "Disable Upper SAM Sites",
    },
    "M04": {
        200: "Disable Critical Engine Areas",
        500: "Destroy Apache",
    },
    "M05": {
        507: "Secure Town Square",
        508: "Escort Resistance",
        509: "Contact Escapee",
        510: "Rescue Babushka",
        512: "Eliminate Engineers",
    },
    "M06": {
        609: "Disable Alarm",
    },
    "M07": {
        704: "Destroy Radar Installations",
        705: "Rescue Resistance",
        706: "Destroy Napalm Stockpile",
        707: "Destroy Stealth Tank",
        708: "Disable Obelisk",
    },
    "M08": {
        804: "Disable Helipad",
        806: "Disable Station Alpha",
        807: "Disable Station Beta",
    },
    "M10": {
        1008: "Disable Hand of Nod",
        1009: "Disable Tiberium Refinery",
        1010: "Destroy Airstrip",
        1011: "Disable Tiberium Silos",
        1013: "Disable Northern Helipad",
        1014: "Disable Western Helipad",
        1015: "Disable Southern SAM Site",
        1016: "Destroy Northern SAM Site",
        1017: "Disable Western SAM Sites",
        1018: "Disable Eastern SAM Site",
        1019: "Destroy Turrets",
    },
}

# Stock primary objectives. M13 uses help text rather than objective-manager
# entries. M03/1009 completes as a tertiary objective and is excluded here.
# Keep this order stable: it defines AP IDs. Native bits append these to the
# existing secondary bits, whose meaning is unchanged.
PRIMARY_OBJECTIVES = {
    'M13': (),
    'M01': (106, 107, 108, 113),
    'M02': (201, 203, 205),
    'M03': (1000, 1007, 1008, 1010),
    'M04': (100, 110, 300, 400, 600, 700, 800),
    'M05': (501, 502, 503, 504, 506),
    'M06': (601, 603, 604, 611),
    'M07': (701, 702, 703, 709, 710),
    'M08': (801, 802, 803, 805),
    'M09': (900, 901, 902, 903),
    'M10': (1001, 1002, 1003, 1004, 1005, 1006, 1007, 1012),
    'M11': (4, 8, 11, 14, 17),
}

PRIMARY_TITLES = {
    'M13': {
    },
    'M01': {
        106: 'Locate Detention Center',
        107: 'Access Communications Mainframe',
        108: 'Disable SAM Site',
        113: 'Open Detention Center',
    },
    'M02': {
        201: 'Locate Missing Scientists',
        203: 'Disable Dam Master Control Terminal',
        205: 'Infiltrate Cargo Plane',
    },
    'M03': {
        1000: 'Locate Communications Center',
        1007: 'Acquire Security Card',
        1008: 'Hack Communications Mainframe',
        1010: 'Escape via Submarine',
    },
    'M04': {
        100: 'Rescue Prisoners',
        110: 'Acquire Prison Security Card',
        300: 'Sabotage Missile Racks',
        400: 'Sabotage Torpedo Racks',
        600: 'Acquire Deck Security Card',
        700: 'Acquire Submarine Security Card',
        800: 'Protect Prisoners',
    },
    'M05': {
        501: 'Contact Gunner',
        502: 'Locate Deadeye',
        503: 'Locate Hotwire',
        504: 'Locate Patch',
        506: 'Eliminate Black Hand',
    },
    'M06': {
        601: 'Access War Room Computer',
        603: 'Evacuate Scientists',
        604: 'Eliminate Mendoza',
        611: 'Escort Sydney',
    },
    'M07': {
        701: 'Evacuate Sydney',
        702: 'Escort Hotwire to SAM Sites',
        703: 'Destroy SSM Launchers',
        709: 'Protect Team Members',
        710: 'Escape Nuclear Strike',
    },
    'M08': {
        801: 'Escape Prison',
        802: 'Infiltrate Research Facility',
        803: 'Rescue Scientists',
        805: 'Eliminate Raveshaw',
    },
    'M09': {
        900: 'Protect Mobius',
        901: 'Acquire Power Suit',
        902: 'Escort Mobius to Surface',
        903: 'Escort Mobius to GDI',
    },
    'M10': {
        1001: 'Disable Power Plant',
        1002: 'Disable Construction Yard',
        1003: 'Sabotage Temple of Nod',
        1004: 'Disable Communications Center',
        1005: 'Access Southeastern Gate',
        1006: 'Access Northeastern Gate',
        1007: 'Access Northwestern Gate',
        1012: 'Acquire Base Security Card',
    },
    'M11': {
        4: 'Infiltrate Temple Bunker',
        8: 'Rescue Sydney',
        11: 'Infiltrate Temple Power Core',
        14: 'Escort Sydney',
        17: 'Sabotage Nuclear Missile',
    },
}

# These terminal objectives have no reliable accomplished call in the stock
# mission scripts. Only a successful native victory proves their completion.
VICTORY_PRIMARY_OBJECTIVES = {'M03': (1010,), 'M04': (800,), 'M09': (900, 903)}
OBJECTIVES = {m: SECONDARY_OBJECTIVES[m] + PRIMARY_OBJECTIVES[m] for m in PRIMARY_OBJECTIVES}

# Route positions; secondary guide order remains unchanged. Overall protection
# and escape goals appear near their completion rather than their announcement.
OBJECTIVE_DISPLAY_ORDER = {
    'M13': (),
    'M01': (114, 102, 104, 109, 100, 103, 106, 113, 107, 108),
    'M02': (206, 207, 209, 208, 210, 221, 212, 213, 211, 214, 215, 216,
            217, 202, 201, 203, 218, 219, 220, 204, 205),
    'M03': (1001, 1002, 1004, 1000, 1007, 1008, 1010),
    'M04': (200, 300, 110, 100, 600, 500, 400, 700, 800),
    'M05': (507, 503, 501, 508, 509, 502, 512, 510, 504, 506),
    'M06': (609, 601, 603, 611, 604),
    'M07': (710, 704, 705, 702, 706, 707, 708, 701, 709, 703),
    'M08': (801, 804, 806, 807, 802, 803, 805),
    'M09': (901, 902, 900, 903),
    'M10': (1015, 1008, 1009, 1012, 1005, 1018, 1007, 1010, 1011, 1017,
            1002, 1014, 1019, 1004, 1006, 1013, 1016, 1001, 1003),
    'M11': (4, 8, 11, 17, 14),
}


def primary_id(map_name, objective_id):
    mission = mission_for_map(map_name)
    ids = PRIMARY_OBJECTIVES[mission.map_name]
    if type(objective_id) is not int or objective_id not in ids:
        raise ValueError('Unknown primary objective for ' + mission.map_name)
    return BASE_ID + 0x2000 + (mission.number - 1) * 32 + ids.index(objective_id)


def objective_kind(map_name, objective_id):
    key = mission_for_map(map_name).map_name
    if type(objective_id) is int:
        if objective_id in PRIMARY_OBJECTIVES[key]: return 'primary'
        if objective_id in SECONDARY_OBJECTIVES[key]: return 'secondary'
    raise ValueError('Unknown objective for ' + key)


def objective_id(map_name, oid):
    return (primary_id if objective_kind(map_name, oid) == 'primary' else secondary_id)(map_name, oid)


def objective_title(map_name, oid):
    key = mission_for_map(map_name).map_name
    return (PRIMARY_TITLES if objective_kind(key, oid) == 'primary' else SECONDARY_TITLES)[key][oid]


def objective_name(mission, oid):
    kind = objective_kind(mission.map_name, oid).title()
    return f'{mission.label} - {objective_title(mission.map_name, oid)} ({kind} Objective)'


def enabled_objectives(map_name, secondary=False, primary=False):
    key = mission_for_map(map_name).map_name
    return tuple(oid for oid in OBJECTIVE_DISPLAY_ORDER[key]
                 if (primary if oid in PRIMARY_OBJECTIVES[key] else secondary))


def secondary_title(map_name, objective_id):
    return SECONDARY_TITLES[mission_for_map(map_name).map_name][objective_id]


def secondary_id(map_name, objective_id):
    mission = mission_for_map(map_name)
    ids = SECONDARY_OBJECTIVES[mission.map_name]
    if type(objective_id) is not int or objective_id not in ids:
        raise ValueError("Unknown secondary objective for " + mission.map_name)
    return BASE_ID + 0x1000 + (mission.number - 1) * 32 + ids.index(objective_id)


def secondary_name(mission, objective_id):
    return objective_name(mission, objective_id)


def mission_for_map(value):
    """Accept a stock map basename with an optional .mix or .lsd extension."""
    if not isinstance(value, str):
        raise ValueError("Map must be a string")
    name = value.upper()
    for suffix in (".MIX", ".LSD"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    if name not in BY_MAP:
        raise ValueError(f"Not a supported campaign map: {value}")
    return BY_MAP[name]


# Stock POW_Data_Disc objects, in approximate route order. Zero is the priest's
# dynamically spawned reward, identified by its script rather than a runtime ID.
# Includes encyclopedia discs as well as map discs; no keycards or weapon pickups.
BLACK_HAND_INTEL = -41  # Scripted M13 encyclopedia reward, not a powerup ID.
DATA_DISCS = {m.map_name: () for m in MISSIONS}
DATA_DISCS.update({
    'M13': (BLACK_HAND_INTEL,),
    'M01': (116992, 109634, 123646, 123647, 0, 114764, 125064),
    'M02': (407528, 407529, 407530, 300799, 300800),
    'M03': (1206589, 1206590), 'M05': (107976, 110102),
    'M06': (113439,), 'M07': (123916, 123917),
    'M08': (110110, 110111), 'M09': (1201187,),
})
DISC_TITLES = {
    116992: 'Beach', 109634: 'First Tiberium cave', 123646: 'Shed',
    123647: 'Second Tiberium cave', 0: 'Priest reward',
    114764: 'Obelisk dossier', 125064: 'Medical lab',
}


def disc_id(map_name, object_id):
    mission = mission_for_map(map_name)
    if type(object_id) is not int or object_id not in DATA_DISCS[mission.map_name]:
        raise ValueError('Unknown data disc for ' + mission.map_name)
    return BASE_ID + 0x3000 + (mission.number - 1) * 32 + DATA_DISCS[mission.map_name].index(object_id)


def disc_title(map_name, object_id):
    disc_id(map_name, object_id)  # Validate before formatting.
    if map_name == 'M13' and object_id == BLACK_HAND_INTEL:
        return 'Black Hand Intel - Defeat an opening Black Hand sniper'
    number = DATA_DISCS[map_name].index(object_id) + 1
    return f'Data Disc {number}' + (': ' + DISC_TITLES[object_id] if map_name == 'M01' else '')


def disc_name(mission, object_id):
    return mission.label + ' - ' + disc_title(mission.map_name, object_id)


def location_ids(checks_per_mission, secondary_objectives=False, primary_objectives=False, data_discs=False):
    if type(checks_per_mission) is not int or not 1 <= checks_per_mission <= 5:
        raise ValueError("checks_per_mission must be between 1 and 5")
    victories = {m.check_id(c) for m in MISSIONS for c in range(1, checks_per_mission + 1)}
    if secondary_objectives:
        victories.update(secondary_id(m.map_name, oid) for m in MISSIONS for oid in SECONDARY_OBJECTIVES[m.map_name])
    if primary_objectives:
        victories.update(primary_id(m.map_name, oid) for m in MISSIONS for oid in PRIMARY_OBJECTIVES[m.map_name])
    if data_discs:
        victories.update(disc_id(m.map_name, oid) for m in MISSIONS for oid in DATA_DISCS[m.map_name])
    return frozenset(victories)


def slot_data(checks_per_mission=5, starting_missions=1, secondary_objectives=False, death_link=False, supply_items=False, extra_traps=False, temporary_bonuses=False, mission_unlocks=None, weapon_unlocks=None, primary_objectives=None, data_discs=None):
    if type(starting_missions) is not int or not 1 <= starting_missions <= 12:
        raise ValueError("starting_missions must be between 1 and 12")
    result = {
        "game": GAME, "protocol_version": PROTOCOL_VERSION,
        "goal": "all_missions", "checks_per_mission": checks_per_mission,
        "starting_missions": starting_missions,
        "location_ids": sorted(location_ids(checks_per_mission, secondary_objectives, bool(primary_objectives), bool(data_discs))),
        "mission_order": [m.map_name for m in MISSIONS],
    }
    if secondary_objectives or death_link:
        result.update(protocol_version=2, secondary_objectives=bool(secondary_objectives), death_link=bool(death_link))
    if supply_items:
        result.update(protocol_version=3, secondary_objectives=bool(secondary_objectives),
                      death_link=bool(death_link), supply_items=True)
    if extra_traps:
        if not supply_items:
            raise ValueError("Extra traps require supply items")
        result.update(protocol_version=4, extra_traps=True)
    if temporary_bonuses:
        if not extra_traps:
            raise ValueError('Temporary bonuses require the extended item contract')
        result.update(protocol_version=5, temporary_bonuses=True)
    if mission_unlocks is not None:
        if mission_unlocks not in MISSION_UNLOCK_MODES or not temporary_bonuses:
            raise ValueError('Mission unlock modes require a valid mode and the extended item contract')
        result.update(protocol_version=6, mission_unlocks=mission_unlocks)
    if weapon_unlocks is not None:
        if type(weapon_unlocks) is not bool or mission_unlocks is None:
            raise ValueError('Weapon unlocks require a boolean and the mission unlock contract')
        result.update(protocol_version=7, weapon_unlocks=weapon_unlocks)
    if primary_objectives is not None:
        if type(primary_objectives) is not bool or weapon_unlocks is None:
            raise ValueError('Primary objectives require a boolean and the weapon unlock contract')
        result.update(protocol_version=8, primary_objectives=primary_objectives)
    if data_discs is not None:
        if type(data_discs) is not bool or primary_objectives is None:
            raise ValueError('Data discs require a boolean and the primary objective contract')
        result.update(protocol_version=10, data_discs=data_discs)
    return result


def validate_slot_data(data):
    if not isinstance(data, dict):
        raise ValueError("Missing Renegade slot data")
    version = data.get("protocol_version")
    if version not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10) or type(version) is not int:
        raise ValueError("Unsupported Renegade slot protocol")
    if version >= 2 and any(type(data.get(k)) is not bool for k in ("secondary_objectives", "death_link")):
        raise ValueError("Invalid Renegade feature settings")
    if version >= 3 and data.get("supply_items") is not True:
        raise ValueError("Invalid supply item contract")
    if version >= 6 and data.get('mission_unlocks') not in MISSION_UNLOCK_MODES:
        raise ValueError('Invalid mission unlock mode')
    if version < 6 and 'mission_unlocks' in data:
        raise ValueError('Mission unlock modes require slot protocol 6')
    if version >= 7 and type(data.get('weapon_unlocks')) is not bool:
        raise ValueError('Invalid weapon unlock setting')
    if version < 7 and 'weapon_unlocks' in data:
        raise ValueError('Weapon unlocks require slot protocol 7')
    if version >= 8 and type(data.get('primary_objectives')) is not bool:
        raise ValueError('Invalid primary objective setting')
    if version < 8 and 'primary_objectives' in data:
        raise ValueError('Primary objectives require slot protocol 8')
    if version >= 9 and type(data.get('data_discs')) is not bool:
        raise ValueError('Invalid data disc setting')
    if version < 9 and 'data_discs' in data:
        raise ValueError('Data discs require slot protocol 9')
    expected = slot_data(data.get("checks_per_mission"), data.get("starting_missions"),
                         data.get("secondary_objectives", False) if version >= 2 else False,
                         data.get("death_link", False) if version >= 2 else False, version >= 3, version >= 4, version >= 5,
                         data.get('mission_unlocks') if version >= 6 else None,
                         data.get('weapon_unlocks') if version >= 7 else None,
                         data.get('primary_objectives') if version >= 8 else None,
                         data.get('data_discs') if version >= 9 else None)
    if version == 2:
        expected.update(protocol_version=2, secondary_objectives=data["secondary_objectives"], death_link=data["death_link"])
    if version == 9:
        expected['protocol_version'] = 9
        expected['location_ids'] = [i for i in expected['location_ids'] if i != disc_id('M13', BLACK_HAND_INTEL)]
    if any(data.get(key) != value or type(data.get(key)) is not type(value)
           for key, value in expected.items()):
        raise ValueError("Incompatible Renegade world/client contract")
    ids = data["location_ids"]
    if any(type(value) is not int for value in ids):
        raise ValueError("Location IDs must be integers")
    return expected


ALL_LOCATIONS.update({secondary_name(m, oid): secondary_id(m.map_name, oid)
                      for m in MISSIONS for oid in SECONDARY_OBJECTIVES[m.map_name]})

ALL_LOCATIONS.update({objective_name(m, oid): primary_id(m.map_name, oid)
                      for m in MISSIONS for oid in PRIMARY_OBJECTIVES[m.map_name]})
ALL_LOCATIONS.update({disc_name(m, oid): disc_id(m.map_name, oid)
                      for m in MISSIONS for oid in DATA_DISCS[m.map_name]})
