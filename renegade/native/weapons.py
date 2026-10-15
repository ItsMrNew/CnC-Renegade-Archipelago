"""Reconcile only Havoc's weapon bag, on the stock game's combat thread.

Stock objects.ddb definition IDs group faction/multiplayer variants under one
unlock. Add_Weapon grants a missing family; existing weapons (even empty ones)
are never refilled. No vehicle, AI or shared weapon definitions are modified.
"""
from dataclasses import dataclass
import logging

from ..data import WEAPONS
from .thunk import Code, CODE_OFFSET, u32
from .savebuilder import BASE
from .profile import UnsupportedBuild
from .memory import MemoryError
from . import services as s

LOG = logging.getLogger('Renegade.Weapons')
ENABLED, MASK, FRAME_MASK, PRESENT, ERRORS, ADDED, REMOVED = range(0, 28, 4)
TABLE, NAMES = 128, 1024
NAME_SIZE = 64
ADD_WEAPON, REMOVE_WEAPON = 0x179720, 0x179450
SELECT_INDEX, SELECT_NAME = 0x179C20, 0x179A80
ANCHORS = {
    ADD_WEAPON: bytes.fromhex('8b44240c8b542404568bf18b4c240c505152e8c9a8030083c4048bce50e81efeffff5ec20c00'),
    REMOVE_WEAPON: bytes.fromhex('56578b7c240c8bf185ff7c463b7e147d418b4608538b1cb885db74108bcbe88d4c0000'),
    SELECT_INDEX: bytes.fromhex('568bf1578b7c240c3b7e147c0233ff8b461c3bc7743e'),
    SELECT_NAME: bytes.fromhex('538b5c24085556578bf985db0f84bf000000803b00'),
}


@dataclass(frozen=True)
class Weapon:
    name: str
    preset: str
    ids: tuple
    rounds: int


# Full clip + stock reserve on a missing weapon's first grant. -1 is used only
# for the stock unlimited-ammo pistol/repair gun; other weapons remain finite.
CATALOGUE = (
    Weapon('Pistol', 'Weapon_Pistol_Player', (1393,), -1),
    Weapon('Automatic Rifle', 'Weapon_AutoRifle_Player', (409610031,409610107,409610118,409610119), 500),
    Weapon('Shotgun', 'Weapon_Shotgun_Player', (1128,), 40),
    Weapon('Chaingun', 'Weapon_Chaingun_Player', (2263,409610105), 500),
    Weapon('Flamethrower', 'Weapon_Flamethrower_Player', (1361,409610006), 500),
    Weapon('Chemical Sprayer', 'Weapon_ChemSprayer_Player', (3269,409610007), 500),
    Weapon('Grenade Launcher', 'Weapon_GrenadeLauncher_Player', (2264,), 48),
    Weapon('Rocket Launcher', 'Weapon_RocketLauncher_Player', (1131,409610120), 42),
    Weapon('Sniper Rifle', 'Weapon_SniperRifle_Player', (1129,409610108,409610124,409610125), 36),
    Weapon('Laser Rifle', 'Weapon_LaserRifle_Player', (409610030,), 500),
    Weapon('Laser Chaingun', 'Weapon_LaserChaingun_Player', (409610032,), 500),
    Weapon('Personal Ion Cannon', 'Weapon_PersonalIonCannon_Player', (409610033,), 31),
    Weapon('Ramjet Rifle', 'Weapon_RamjetRifle_Player', (409610034,409610123), 41),
    Weapon('Railgun', 'Weapon_Railgun_Player', (409610074,), 31),
    Weapon('Tiberium Automatic Rifle', 'Weapon_TiberiumAutoRifle_Player', (409610035,), 250),
    Weapon('Tiberium Flechette Gun', 'Weapon_TiberiumFlechetteGun_Player', (409610036,), 500),
    Weapon('Volt Auto Rifle', 'Weapon_VoltAutoRifle_Player', (409610037,409610112), 500),
    Weapon('Repair Gun', 'Weapon_RepairGun_Player', (409610052,409610121), -1),
    Weapon('Remote C4', 'Weapon_MineRemote_Player', (2267,409610116,409610117), 9),
    Weapon('Timed C4', 'Weapon_MineTimed_Player', (2268,409610114,409610115), 5),
    Weapon('Proximity C4', 'Weapon_MineProximity_Player', (3078,), 6),
    Weapon('Ion Cannon Beacon', 'Weapon_IonCannonBeacon_Player', (409610005,409610126), 1),
    Weapon('Nuclear Strike Beacon', 'Weapon_NukeBeacon_Player', (409610092,409610127), 1),
)
assert tuple(w.name for w in CATALOGUE[1:]) == WEAPONS
DEFINITIONS = tuple((ident, 1 << i) for i, w in enumerate(CATALOGUE) for ident in w.ids)
ALL_WEAPONS = (1 << len(CATALOGUE)) - 1
assert len({ident for ident, _ in DEFINITIONS}) == len(DEFINITIONS)
assert TABLE + len(DEFINITIONS) * 8 <= NAMES
assert NAMES + len(CATALOGUE) * NAME_SIZE <= CODE_OFFSET


def build_weapons(control, service, *, bindings=None, native_repairs_selection=False):
    c = Code(control + CODE_OFFSET)
    def value(offset, number):
        c.emit(b'\xc7\x05' + u32(control + offset) + u32(number))
    def native(rva):
        c.jump(b'\xe8', bindings[rva] if bindings is not None else BASE + rva)
    c.emit(b'\x9c\x60')
    for base, offset in ((control, ENABLED), (service, s.PLAYABLE), (service, s.ALIVE)):
        c.emit(b'\x83\x3d' + u32(base + offset) + b'\x01')
        c.jump(b'\x0f\x85', 'done')
    # Called exclusively behind the lease/map/cinematic guard in build_services.
    c.emit(b'\x8b\x35' + u32(service + s.PLAYER) + b'\x85\xf6')
    c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x8b\xae' + u32(0x7B0) + b'\x85\xed')  # EBP = Havoc's bag.
    c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x8b\x7d\x14\x83\xff\x01')
    c.jump(b'\x0f\x8c', 'error')
    c.emit(b'\x81\xff' + u32(128))
    c.jump(b'\x0f\x87', 'error')
    c.emit(b'\x83\x7d\x08\x00')
    c.jump(b'\x0f\x84', 'error')
    c.emit(b'\x39\x7d\x1c')  # Validate selected index before native Deselect.
    c.jump(b'\x0f\x83', 'error')
    c.emit(b'\xa1' + u32(control + MASK) + b'\x83\xc8\x01\xa3' + u32(control + FRAME_MASK))
    value(PRESENT, 0)
    c.label('next')
    c.emit(b'\x4f')  # Reverse traversal: Remove_Weapon compacts the vector.
    c.jump(b'\x0f\x84', 'grant')
    c.emit(b'\x8b\x45\x08\x8b\x1c\xb8\x85\xdb')
    c.jump(b'\x0f\x84', 'next')
    c.emit(b'\x8b\x0b\x85\xc9')
    c.jump(b'\x0f\x84', 'error')
    c.emit(b'\x8b\x11\xff\x52\x24')  # DefinitionClass::Get_ID, thiscall.
    c.emit(b'\xb9' + u32(control + TABLE) + b'\xba' + u32(len(DEFINITIONS)))
    c.label('lookup')
    c.emit(b'\x3b\x01')
    c.jump(b'\x0f\x84', 'found')
    c.emit(b'\x83\xc1\x08\x4a')
    c.jump(b'\x0f\x85', 'lookup')
    c.jump(b'\xe9', 'remove')  # Unknown weapons in Havoc's bag are not unlocked.
    c.label('found')
    c.emit(b'\x8b\x41\x04\x85\x05' + u32(control + FRAME_MASK))
    c.jump(b'\x0f\x84', 'remove')
    c.emit(b'\x80\x7b\x38\x00')
    c.jump(b'\x0f\x84', 'next')  # Ammo-only entries do not grant ownership.
    c.emit(b'\x09\x05' + u32(control + PRESENT))
    c.jump(b'\xe9', 'next')
    c.label('remove')
    c.emit(b'\x39\x7d\x1c')
    c.jump(b'\x0f\x85', 'remove_entry')
    c.emit(b'\x6a\x00\x8b\xcd')
    native(SELECT_INDEX)  # Deselect BEFORE destroying the selected weapon.
    c.label('remove_entry')
    c.emit(b'\x57\x8b\xcd')
    native(REMOVE_WEAPON)
    if not native_repairs_selection:
        c.emit(b'\x39\x7d\x1c')
        c.jump(b'\x0f\x86', 'index_done')
        c.emit(b'\xff\x4d\x1c')  # Steam leaves the shifted selection unrepaired.
        c.label('index_done')
    c.emit(b'\xc6\x45\x20\x01\xc6\x45\x21\x01')  # Bag/HUD dirty.
    c.emit(b'\xff\x05' + u32(control + REMOVED))
    c.jump(b'\xe9', 'next')
    c.label('grant')
    for i, weapon in enumerate(CATALOGUE):
        next_label = f'grant_{i}'
        c.emit(b'\xf7\x05' + u32(control + FRAME_MASK) + u32(1 << i))
        c.jump(b'\x0f\x84', next_label)
        c.emit(b'\xf7\x05' + u32(control + PRESENT) + u32(1 << i))
        c.jump(b'\x0f\x85', next_label)
        c.emit(b'\x6a\x01\x68' + u32(weapon.rounds & 0xFFFFFFFF) + b'\x68' + u32(control + NAMES + i * NAME_SIZE) + b'\x8b\xcd')
        native(ADD_WEAPON)  # thiscall, callee pops name/rounds/give_weapon.
        c.emit(b'\x85\xc0')
        c.jump(b'\x0f\x84', 'error')
        c.emit(b'\xff\x05' + u32(control + ADDED))
        c.label(next_label)
    c.emit(b'\x83\x7d\x1c\x00')
    c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x68' + u32(control + NAMES) + b'\x8b\xcd')
    native(SELECT_NAME)  # Always leave a usable fallback after removing a gun.
    c.jump(b'\xe9', 'done')
    c.label('error')
    c.emit(b'\xff\x05' + u32(control + ERRORS))
    c.label('done')
    c.emit(b'\x61\x9d\xc3')
    program = c.finish()
    if len(program) > 4096:
        raise ValueError('Weapon reconciliation exceeds its code page')
    return program


def initialize(memory, control):
    memory.write(control, b'\0' * CODE_OFFSET)
    memory.write(control + TABLE, b''.join(u32(ident) + u32(bit) for ident, bit in DEFINITIONS))
    for i, weapon in enumerate(CATALOGUE):
        memory.write(control + NAMES + i * NAME_SIZE, weapon.preset.encode('ascii') + b'\0')


class NativeWeapons:
    def __init__(self, memory, service):
        self.memory, self.last_errors = memory, 0
        for rva, expected in ANCHORS.items():
            if memory.read(BASE + rva, len(expected)) != expected:
                raise UnsupportedBuild(f'Weapon inventory anchor differs at {rva:#x}')
        self.control = memory.allocate()
        initialize(memory, self.control)
        self.code = self.control + CODE_OFFSET
        program = build_weapons(self.control, service)
        memory.write(self.code, program)
        memory.make_executable(self.code)
        if memory.read(self.code, len(program)) != program:
            raise MemoryError('Weapon inventory code read-back failed')

    def update(self, client):
        mask = client.weapon_mask
        if not mask:
            self.memory.write_u32(self.control + ENABLED, 0)
        self.memory.write_u32(self.control + MASK, mask)
        self.memory.write_u32(self.control + ENABLED, int(bool(mask)))
        errors = self.memory.u32(self.control + ERRORS)
        if errors and not self.last_errors:
            LOG.error('Weapon inventory reconciliation failed in the game; check the stock game installation')
        self.last_errors = errors

    def close(self):
        self.memory.write_u32(self.control + ENABLED, 0)
