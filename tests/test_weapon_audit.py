"""Audit parsing must not confuse saved IDs or lose repeated attached scripts."""
import struct

import pytest

from tools.audit_weapon_requirements import armor_table, definitions, real, saved_actors


def chunk(kind, payload, container=False):
    return struct.pack('<II', kind, len(payload) | (0x80000000 if container else 0)) + payload


def micro(*pairs):
    return b''.join(bytes((key, len(value))) + value for key, value in pairs)


def u32(value):
    return struct.pack('<I', value)


def test_armor_save_ids_are_distinct_from_definition_indices():
    table = armor_table(b'''[Armor_Types]
0=SkinMCT
[Armor_Save_IDs]
SkinMCT=12
[Warhead_Types]
0=Shrapnel
[Warhead_Save_IDs]
Shrapnel=3
[Scale_SkinMCT]
Shrapnel=1.0 ; test comment
''')
    assert table['armor'] == {12: 'SkinMCT'}
    assert table['armor_indices'] == {0: 'SkinMCT'}
    assert table['warhead_indices'] == {0: 'Shrapnel'}
    assert table['damage']['SkinMCT']['Shrapnel'] == 1


def test_saved_actor_retains_all_observers_and_uses_correct_skin_namespace():
    scripts = b''.join(chunk(0x07CEEB2F, micro(
        (1, name + b'\0'), (2, b'1.00\0'), (3, u32(ptr))))
        for ptr, name in ((100, b'Repair'), (200, b'Objective')))
    base = chunk(910991407, micro((2, u32(77)), (3, u32(999))))
    observers = chunk(627001123, micro((2, u32(100)), (2, u32(200)), (2, u32(300))))
    defense = chunk(914991020, micro(
        (3, struct.pack('<f', 20)), (4, struct.pack('<f', 20)), (5, u32(12)),
        (6, struct.pack('<f', 0)), (7, struct.pack('<f', 0)), (8, u32(99))))
    raw = scripts + chunk(0x40101, base + observers + defense, True)
    presets = {77: ('Building', chunk(207011031, micro((2, u32(1)))))}
    table = dict(armor={12: 'SkinMCT', 99: 'Blamo'}, armor_indices={1: 'SkinMCT'},
                 damage={'SkinMCT': {'Shrapnel': 1}, 'Blamo': {'Shrapnel': 0}})
    actor, = saved_actors(raw, presets, table)
    assert actor['object_id'] == 999
    assert [s['name'] for s in actor['scripts']] == ['Repair', 'Objective', '[native observer]']
    assert actor['building_mct'] == {'skin': 'SkinMCT', 'pistol_scale': 1}
    assert actor['defense']['pistol_skin_scale'] == 1
    assert actor['defense']['pistol_shield_scale'] == 0
    assert actor['defense']['armor'] == 0  # Zero armor must not imply an immune target.


def test_duplicate_definition_ids_fail_instead_of_overwriting_evidence():
    one = chunk(0x100101, chunk(0x100, micro((1, u32(77)), (3, b'Pistol\0'))), True)
    with pytest.raises(ValueError, match='Duplicate definition'):
        definitions(one + one)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_damage_values_are_rejected(value):
    with pytest.raises(ValueError, match='Non-finite'):
        real(struct.pack('<f', value))
