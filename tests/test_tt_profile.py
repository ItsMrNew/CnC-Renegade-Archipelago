"""Reject altered TT modules, relocated operands and callbacks before attaching."""
from pathlib import Path
from types import SimpleNamespace
import struct

import pytest

from renegade.native import tt_profile as tt
from renegade.native.profile import UnsupportedBuild
from test_native import FakeMemory, pack


@pytest.fixture
def memory(monkeypatch, tmp_path):
    mem = FakeMemory()
    mem.modules = [SimpleNamespace(name=name, path=tmp_path/name, base=base)
        for name, base in zip(tt.HASHES, (0x400000, 0x60000000, 0x61000000, 0x62000000,0x63000000))]
    mem.pid = 123
    mem.hash_calls = []
    monkeypatch.setattr(tt, 'require_digest', lambda path, digest: mem.hash_calls.append((path, digest)))
    blocks = {
        0x477CA0: 'a118f18200c3',
        0x72A110: '8b44240450e86672f4ff59c3',
        0x72A740: '8b4424088b4c24045051e811a5f4ff83c408c3',
        0x674C60: '8b15cc648500535533c0565785d20f8ead0200008b35c4648500',
        0x403960: '8b0d18658500b8010000003bc875158a4c240484c97408a224c68100c20400',
        0x61017C70: '83ec08558b6c24108b4500a30c692e61',
        0x6022BA3B: 'a1709e5f60',
        0x602A16B0: '558bec83e4f883ec1c5356578bf98b078b4020ffd084c0',
        0x602A7AE0: 'b870042d60c3',
        0x602D0470: '436f6d62617400',
        0x6003D370: '33c03941040f94c0c3',
        0x602F32C8: '70d30360e07a2a60',
        0x401040: 'a0266e850053555684c07520a0256e850084c07517',
        0x492360: '535668380d0000e804081e008bf033db83c4043b',
        0x4923C8: 'a170f182008b148d74bd7f00408bcea370f18200',
        0x404E50: 'a164c6810083e803741d48740b6a00e87cc20a0083c404c3',
        0x602A6E80: 'a1e8a35f60568338017417',
    }
    for address, value in blocks.items(): mem.write(address, bytes.fromhex(value))
    mem.write(0x404A80, b'\xe9'+struct.pack('<i', 0x602A6E80-0x404A85))
    mem.write_u32(0x612E690C, 0x60314080)
    mem.write(0x60314080, pack(816)+pack(174))
    mem.write_u32(0x6031422C, 0x72A110)
    mem.write_u32(0x60314284, 0x72A740)
    mem.write_u32(0x602F32DC, 0x602A16B0)
    mem.write_u32(0x81E854, 0x20000000)
    mem.write_u32(0x20000004, 0x20000100)
    mem.write_u32(0x20000100, 0x602F32A8)
    return mem


def test_tt_profile_accepts_only_complete_verified_layout(memory):
    a = tt.verify(memory, gate=True)
    assert a.loaded_map == 0x605F9E70
    assert a.commands == 0x60314080
    assert [p.name for p, _ in memory.hash_calls] == list(tt.HASHES)


@pytest.mark.parametrize('address', [0x477CA0, 0x72A110, 0x72A740, 0x674C60, 0x403960,
    0x61017C70, 0x6022BA3B, 0x602A16B0, 0x602A7AE0, 0x602D0470, 0x6003D370, 0x602F32C8,
    0x401040, 0x492360, 0x4923C8, 0x404E50, 0x404A80, 0x602A6E80, 0x612E690C,
    0x60314080, 0x6031422C, 0x60314284, 0x602F32DC, 0x20000100])
def test_tt_profile_rejects_mutated_live_anchors_or_callback_ownership(memory, address):
    memory.write(address, b'\0')
    with pytest.raises(UnsupportedBuild): tt.verify(memory, gate=True)


@pytest.mark.parametrize('change', ['wrong_game', 'wrong_base', 'missing_module', 'mixed_install', 'hash'])
def test_tt_profile_rejects_unknown_installation(memory, monkeypatch, change):
    if change == 'wrong_game': memory.modules[0].name = 'Game.dat'
    if change == 'wrong_base': memory.modules[0].base = 0x500000
    if change == 'missing_module': memory.modules.pop()
    if change == 'mixed_install': memory.modules[2].path = Path('another/scripts.dll')
    if change == 'hash':
        def reject(*args): raise UnsupportedBuild('wrong digest')
        monkeypatch.setattr(tt, 'require_digest', reject)
    with pytest.raises(UnsupportedBuild): tt.verify(memory, gate=True)


def test_tt_live_map_ignores_save_filename_and_requires_bounded_string(memory):
    a = tt.verify(memory)
    memory.write_u32(a.game_data, 0x20000000)
    memory.write_u32(0x20000028, 0x20000100)
    memory.write(0x20000100, b'save_name.sav\0')
    memory.write_u32(a.loaded_map, 0x20000200)
    memory.write(0x20000200, b'M01.lsd\0')
    assert tt.telemetry(memory, a)['map_name'] == 'M01.lsd'
    memory.write(0x20000200, b'A'*128)
    with pytest.raises(UnsupportedBuild): tt.telemetry(memory, a)
