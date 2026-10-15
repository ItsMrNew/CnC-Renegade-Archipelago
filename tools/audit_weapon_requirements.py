"""Read-only campaign weapon audit against installed data and mission-start saves.

Exports damage types, weapon ammunition, object defenses and attached scripts.
Positive damage is evidence of vulnerability, NOT proof of a playable route:
range, scripts, repair, escorts and encounters spawned later need manual review.
No live process is opened and no installed game/save files are changed.
"""
import argparse
import configparser
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from renegade.data import MISSIONS
from renegade.native.savefiles import fields, inspect_save, saved_difficulty
from renegade.native.weapons import CATALOGUE
from tools.tracker_assets import MixArchive, descendants, unique_fields


def integer(value):
    if len(value) != 4:
        raise ValueError('Expected a 32-bit field')
    return int.from_bytes(value, 'little')


def real(value):
    if len(value) != 4:
        raise ValueError('Expected a float field')
    result = struct.unpack('<f', value)[0]
    if not math.isfinite(result):
        raise ValueError('Non-finite game value')
    return result


def string(value):
    return value.rstrip(b'\0').decode('cp1252')


def definitions(raw):
    """Index DefinitionClass metadata without assuming a particular subclass."""
    result = {}
    for kind, _, payload in descendants(raw):
        if kind != 0x100101:
            continue
        try:
            meta = unique_fields(payload, 0x100)
        except ValueError:
            continue
        if 1 not in meta or 3 not in meta:
            continue
        ident = integer(meta[1])
        if ident in result:
            raise ValueError(f'Duplicate definition {ident}')
        result[ident] = (string(meta[3]), payload)
    return result


def armor_table(raw):
    ini = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(';',))
    ini.optionxform = str
    ini.read_string(raw.decode('cp1252'))
    armor = {int(v): k for k, v in ini['Armor_Save_IDs'].items()}
    warheads = {int(v): k for k, v in ini['Warhead_Save_IDs'].items()}
    # Saved defenses use save IDs; AmmoDefinition stores a warhead table index.
    warhead_indices = {int(k): v for k, v in ini['Warhead_Types'].items()}
    damage = {name: dict(ini['Scale_' + name]) for name in armor.values()}
    armor_indices = {int(k): v for k, v in ini['Armor_Types'].items()}
    return dict(armor=armor, armor_indices=armor_indices, warheads=warheads, warhead_indices=warhead_indices,
                damage={a: {w: float(v) for w, v in row.items()} for a, row in damage.items()})


def weapon_stats(presets, table):
    result = []
    for weapon in CATALOGUE:
        name, raw = presets[weapon.ids[0]]
        if name != weapon.preset:
            raise ValueError('Weapon catalogue does not match installed definitions')
        values = unique_fields(raw, 0x47D43D46)
        ammo_id = integer(values[0x1b])
        ammo_name, ammo_raw = presets[ammo_id]
        ammo = unique_fields(ammo_raw, 0x47E37EA5)
        result.append(dict(name=weapon.name, definition_id=weapon.ids[0], preset=name,
                           clip=integer(values[0x1d]),
                           reserve=int.from_bytes(values[0x23], 'little', signed=True),
                           reload_seconds=real(values[9]), ammo_id=ammo_id, ammo=ammo_name,
                           warhead=table['warhead_indices'][integer(ammo[3])],
                           damage=real(ammo[4]), range=real(ammo[5]),
                           rate_of_fire=real(ammo[0x30])))
    return result


def saved_actors(raw, presets, table, warhead='Shrapnel'):
    """Resolve each object's observer pointers; names live in a separate subsystem.

    Keep repeated observer-pointer microchunks: converting them to a dict loses
    all but the last script (notably M10's repair and damage-modifier scripts).
    Native engine observers are absent from ScriptManager and listed as unknown.
    """
    scripts = {}
    for kind, _, payload in descendants(raw):
        if kind == 0x07CEEB2F:
            values = dict(fields(payload))
            scripts[integer(values[3])] = dict(name=string(values[1]), parameters=string(values[2]))
    result = []
    for kind, _, payload in descendants(raw):
        if not 0x40001 <= kind < 0x50000:
            continue
        try:
            base = unique_fields(payload, 910991407)
        except ValueError:
            continue
        definition = integer(base[2])
        record = dict(object_id=integer(base[3]), definition_id=definition,
                      preset=presets.get(definition, ('UNKNOWN',))[0], scripts=[])
        if definition in presets:
            try:
                building = unique_fields(presets[definition][1], 207011031)
            except ValueError:
                pass
            else:
                # Building MCTSkin is an index, unlike DefenseObject's save ID.
                skin = table['armor_indices'][integer(building[2])]
                record['building_mct'] = dict(skin=skin, pistol_scale=table['damage'][skin][warhead])
        for ident, _, data in descendants(payload):
            if ident == 627001123:
                for key, value in fields(data):
                    if key == 2:
                        record['scripts'].append(scripts.get(integer(value), dict(name='[native observer]')))
            elif ident == 914991020:
                values = dict(fields(data))
                skin, shield = table['armor'][integer(values[5])], table['armor'][integer(values[8])]
                record['defense'] = dict(health=real(values[3]), max_health=real(values[4]),
                                        armor=real(values[6]), max_armor=real(values[7]),
                                        skin=skin, shield=shield,
                                        pistol_skin_scale=table['damage'][skin][warhead],
                                        pistol_shield_scale=table['damage'][shield][warhead])
        if 'defense' in record or record['scripts']:
            result.append(record)
    return result


def boss_defenses(presets, table):
    result = {}
    for name in ('Mendoza Boss', 'Raveshaw', 'Mutant_3Boss_Petrova'):
        matches = [(ident, raw) for ident, (label, raw) in presets.items() if label == name]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous boss definition: ' + name)
        ident, raw = matches[0]
        values = unique_fields(raw, 7311607)
        skin, shield = table['armor'][integer(values[2])], table['armor'][integer(values[5])]
        result[name] = dict(definition_id=ident, health=real(values[0]), armor=real(values[3]),
                            skin=skin, shield=shield,
                            pistol_skin_scale=table['damage'][skin]['Shrapnel'],
                            pistol_shield_scale=table['damage'][shield]['Shrapnel'])
    return result


def verify_evidence(report):
    """Check the installed-data premises used in docs/weapon-requirements-audit.md.

    These are deliberately narrow assertions, not an automated beatability test.
    A changed definition or save should require another review, not silently pass.
    """
    def require(condition, message):
        if not condition:
            raise ValueError('Weapon audit premise changed: ' + message)

    pistol = next(w for w in report['weapons'] if w['name'] == 'Pistol')
    require(pistol['reserve'] == -1 and pistol['warhead'] == 'Shrapnel'
            and pistol['damage'] == 10 and pistol['range'] == 50, 'pistol')
    for boss in report['boss_defenses'].values():
        require(boss['pistol_skin_scale'] > 0 and boss['pistol_shield_scale'] > 0, 'boss vulnerability')
    expected = {(m.map_name, d) for m in MISSIONS for d in ('Recruit', 'Soldier', 'Commando')}
    require({(m['map'], m['difficulty']) for m in report['missions']} == expected, '36 save coverage')
    for mission in report['missions']:
        actors = {a['object_id']: a for a in mission['actors']}
        if mission['map'] == 'M10':
            yard = actors[1153933]
            require(yard['preset'] == 'SP_Con_Yard_Nod', 'construction yard identity')
            require(yard['building_mct']['pistol_scale'] == 1, 'construction yard terminal')
            names = {s['name'] for s in yard['scripts']}
            require('M10_Con_Yard' in names and 'M10_Con_Yard_Repair' not in names, 'yard does not self-repair')
            repair = {a['object_id'] for a in actors.values()
                      if any(s['name'] == 'M10_Con_Yard_Repair' for s in a['scripts'])}
            require(len(repair) == 22, '22 repaired M10 objects')
        if mission['map'] == 'M04':
            for ident in (100416, 100417, 100418, 100419):
                defense = actors[ident]['defense']
                require(defense['pistol_skin_scale'] == 1 and defense['armor'] == 0,
                        'ship engine console vulnerability')
        if mission['map'] == 'M02':
            target = actors[1111116]
            require(any(s['name'] == 'M02_Dam_MCT' for s in target['scripts']), 'dam terminal binding')
            require(target['defense']['pistol_skin_scale'] > 0
                    and target['defense']['pistol_shield_scale'] > 0, 'dam terminal vulnerability')
    return dict(passed=True, live_game_test=False, route_proof=False,
                saves_checked=len(report['missions']), boss_definitions_checked=3)


def audit(game_data):
    game_data = Path(game_data)
    objects = MixArchive(game_data / 'always.dbs').read('objects.ddb')
    presets = definitions(objects)
    tables = {}
    for filename in ('always.dat', 'Always2.dat'):
        raw = MixArchive(game_data / filename).read('armor.ini')
        tables[filename] = dict(sha256=hashlib.sha256(raw).hexdigest(), **armor_table(raw))
    table = tables['Always2.dat']
    weapons = weapon_stats(presets, table)
    pistol = next(w for w in weapons if w['name'] == 'Pistol')
    report = dict(live_game_test=False, route_proof=False,
                  objects_sha256=hashlib.sha256(objects).hexdigest(),
                  armor_tables=tables, weapons=weapons,
                  boss_defenses=boss_defenses(presets, table), missions=[])
    for mission in MISSIONS:
        for difficulty, label in enumerate(('Recruit', 'Soldier', 'Commando')):
            candidates = sorted((game_data / 'save').glob(f'ap_start_*{label}*_{mission.map_name}.sav'))
            if not candidates:
                raise ValueError(f'Missing {label} start save for {mission.map_name}')
            path = candidates[-1]
            raw = path.read_bytes()
            info = inspect_save(raw)
            if info['map_name'] != mission.map_name or saved_difficulty(raw) != difficulty:
                raise ValueError('Save metadata mismatch: ' + str(path))
            actors = saved_actors(raw, presets, table, pistol['warhead'])
            report['missions'].append(dict(map=mission.map_name, difficulty=label,
                                            save=path.name, sha256=info['sha256'], actors=actors))
    report['evidence_checks'] = verify_evidence(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-data', type=Path, default=Path(
        r'D:\SteamLibrary\steamapps\common\Command & Conquer Renegade\Data'))
    parser.add_argument('--output', type=Path, default=ROOT / '.research/weapon-requirements-audit.json')
    args = parser.parse_args()
    report = audit(args.game_data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(report=str(args.output), saves=len(report['missions']),
                          weapons=len(report['weapons']), live_game_test=False)))


if __name__ == '__main__':
    main()
