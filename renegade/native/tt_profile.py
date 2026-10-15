"""Pinned TT 4.8.4/rev9000 bindings. Every address is process-local and verified.

TT owns the command table and save-manager map string in BandTest.dll. Retain
TT's existing detours when calling game entry points; never restore Steam code.
"""
from dataclasses import dataclass
import struct

from .profile import UnsupportedBuild, require_digest

HASHES = {
    'game2.exe': '1325ed64b91c023caf6a96f35654156e0ef277069f6d1da580c258bb459a809e',
    'bandtest.dll': 'd520443f5618b7d34d48a2e1d4b8348516d53d3ac9a7e201077e259f64c100db',
    'scripts.dll': '65ed3a90adb147d161b229b91915c519d41ad7949ed89bbb9a950f3813461666',
    'scripts2.dll': 'a0439b3bfe6cb497f402291548a312a9f43c0d38bdaf26c83e21dbd3f4d55702',
    'shared.dll': 'b9a15062d6928a5f2a4d315226fcddf6f2b6de9d5ccccb65bfa4ee37ec83dd8d',
}


@dataclass(frozen=True)
class TTAddresses:
    band_base: int
    scripts_base: int
    tick_iat: int = 0x7CD1D4
    game_mode: int = 0x856518
    game_data: int = 0x82F118
    pending_continue: int = 0x81C624
    mission_complete: int = 0x72A110
    objective_status: int = 0x72A740
    objective_array: int = 0x8564C4
    objective_count: int = 0x8564CC
    combat_keyboard: int = 0x401040
    message_dialog: int = 0x492360
    message_count: int = 0x82F170
    end_game: int = 0x404A80
    end_game_menu: int = 0x404E50
    shared_base: int = 0

    @property
    def commands(self):
        return self.band_base + 0x314080

    @property
    def loaded_map(self):
        return self.band_base + 0x5F9E70

    @property
    def combat_think(self):
        return self.band_base + 0x2A16B0

    @property
    def combat_think_slot(self):
        return self.band_base + 0x2F32DC


def verify(memory, *, allowed_callbacks=None, gate=False, allow_gate=None):
    modules = {m.name.lower(): m for m in memory.modules}
    game = memory.modules[0]
    if game.name.lower() != 'game2.exe' or game.base != 0x400000:
        raise UnsupportedBuild('Expected the pinned TT game2.exe at its fixed image base')
    for name, expected in HASHES.items():
        module = modules.get(name)
        if module is None:
            raise UnsupportedBuild(f'TT {name} is not loaded; wait for the main menu')
        if module.path.resolve().parent != game.path.resolve().parent:
            raise UnsupportedBuild(f'TT {name} belongs to another installation')
        require_digest(module.path, expected)
    a = TTAddresses(modules['bandtest.dll'].base, modules['scripts.dll'].base,
                    shared_base=modules['shared.dll'].base)
    pack = lambda value: struct.pack('<I', value)
    anchors = {
        0x477CA0: bytes.fromhex('a118f18200c3'),
        a.mission_complete: bytes.fromhex('8b44240450e86672f4ff59c3'),
        a.objective_status: bytes.fromhex('8b4424088b4c24045051e811a5f4ff83c408c3'),
        0x674C60: bytes.fromhex('8b15cc648500535533c0565785d20f8ead0200008b35c4648500'),
        0x403960: bytes.fromhex('8b0d18658500b8010000003bc875158a4c240484c97408a224c68100c20400'),
        a.scripts_base + 0x17C70: bytes.fromhex('83ec08558b6c24108b4500a3') + pack(a.scripts_base + 0x2E690C),
        a.band_base + 0x22BA3B: b'\xa1' + pack(a.loaded_map),
    }
    if gate:
        anchors.update({
            # TT constructs CombatGameMode with its own vtable; the otherwise
            # intact game2.exe vtable is never called by the playing game.
            a.combat_think: bytes.fromhex('558bec83e4f883ec1c5356578bf98b078b4020ffd084c0'),
            a.band_base + 0x2A7AE0: b'\xb8' + pack(a.band_base + 0x2D0470) + b'\xc3',
            a.band_base + 0x2D0470: b'Combat\0',
            a.band_base + 0x3D370: bytes.fromhex('33c03941040f94c0c3'),
            a.band_base + 0x2F32C8: pack(a.band_base + 0x3D370) + pack(a.band_base + 0x2A7AE0),
            a.combat_keyboard: bytes.fromhex('a0266e850053555684c07520a0256e850084c07517'),
            a.message_dialog: bytes.fromhex('535668380d0000e804081e008bf033db83c4043b'),
            0x4923C8: bytes.fromhex('a170f182008b148d74bd7f00408bcea370f18200'),
            a.end_game_menu: bytes.fromhex('a164c6810083e803741d48740b6a00e87cc20a0083c404c3'),
            # TT's original End_Game detour is part of the supported layout.
            a.end_game: b'\xe9' + struct.pack('<i', a.band_base + 0x2A6E80 - a.end_game - 5),
            a.band_base + 0x2A6E80: b'\xa1' + pack(a.band_base + 0x5FA3E8) + bytes.fromhex('568338017417'),
        })
    for address, expected in anchors.items():
        if memory.read(address, len(expected)) != expected:
            raise UnsupportedBuild(f'TT live anchor differs at {address:#x}; no new hooks installed')
    if memory.u32(a.scripts_base + 0x2E690C) != a.commands:
        raise UnsupportedBuild('TT command-table owner has changed')
    if memory.read(a.commands, 8) != struct.pack('<II', 0x330, 174):
        raise UnsupportedBuild('TT command-table size/version has changed')
    for offset, original in ((0x1AC, a.mission_complete), (0x204, a.objective_status)):
        pointer = memory.u32(a.commands + offset)
        if pointer != original and pointer != (allowed_callbacks or {}).get(offset):
            raise UnsupportedBuild(f'TT command callback {offset:#x} is owned by another hook')
    if gate and memory.u32(a.combat_think_slot) not in (a.combat_think, allow_gate):
        raise UnsupportedBuild('TT combat update is owned by another hook')
    if gate:
        # Confirm the target table belongs to an actual game-mode instance.
        # Merely recognizing dormant vanilla code previously gave a false pass.
        node = memory.u32(0x81E854)
        found = False
        seen = set()
        for _ in range(32):
            if not node:
                break
            if node in seen:
                raise UnsupportedBuild('TT game-mode list is cyclic')
            seen.add(node)
            obj = memory.u32(node + 4)
            if obj and memory.u32(obj) == a.band_base + 0x2F32A8:
                found = True
            node = memory.u32(node)
        if node or not found:
            raise UnsupportedBuild('TT active combat-mode instance could not be verified')
    return a


def read_text(memory, pointer, limit=128):
    if not pointer:
        return None
    raw = bytearray()
    for offset in range(limit):
        value = memory.read(pointer + offset, 1)[0]
        if not value:
            return raw.decode('ascii')
        raw.append(value)
    raise UnsupportedBuild('TT map string is not terminated')


def telemetry(memory, addresses):
    a = addresses
    game = memory.u32(a.game_data)
    return dict(pid=memory.pid, build='tt-4.8.4-b9000', mode=memory.u32(a.game_mode),
                map_name=read_text(memory, memory.u32(a.loaded_map)),
                source_name=read_text(memory, memory.u32(game + 0x28)) if game else None,
                game_data=game, pending_campaign_continue=bool(memory.read(a.pending_continue, 1)[0]))
