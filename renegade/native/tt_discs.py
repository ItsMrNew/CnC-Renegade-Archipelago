"""Pinned TT b9000 physical disc capture and independent EVA map reveal.

TT's active PowerUp::Think lives in BandTest, not the dormant game2 routine.
Keep its Grant and script observers intact, then latch successful Havoc pickups.
Pickups latch checkpoint events; saving happens later on a controllable frame.
"""
from functools import partial

from ..data import MISSIONS
from . import discs, services as s
from .bonuses import call_bytes
from .memory import MemoryError
from .profile import UnsupportedBuild
from .thunk import CODE_OFFSET, LEASE_MS, u32

COMBAT_OBJECT = 108  # TT scanner's current this; separate from objective data.
STAR, CAMERA, GET_HEALTH, CLOUD_VECTOR = 0x855E48, 0x855EB8, 0x729190, 0x85E2E8
GRANT, GRANT_CALL, THINK, THINK_SLOT = 0x1FC610, 0x1FC809, 0x1FC690, 0x2EB938


def expected_anchors(a):
    b = a.band_base
    return {
        b+GRANT: bytes.fromhex('51538b5c240c56578bf16a018b8ebc0600005653e8b7eaffff'),
        b+0x1FC64C: bytes.fromhex('83beb407000002752b33ff39bef80600007e2190'),
        b+0x1FC660: bytes.fromhex('8b86f0060000538b0cb86a008b016819ca9a3b56ff5020'),
        b+THINK: bytes.fromhex('558bec83e4f883ec40535556578be9e80c2003008bcde8d5fdffff'),
        b+THINK_SLOT: u32(b+THINK),
        b+0x1FC806: bytes.fromhex('578bcd'),
        b+0x1FC80E: bytes.fromhex('5f5e5d5b8be55dc3'),
        # AlwaysAllowGrant -> STATE_GRANTING, including fully revealed discs.
        b+0x1FBB58: bytes.fromhex('80bfd8000000007407c644241301eb088a44241384c0744e8b750c85f674296a028bcee8b0010000'),
        b+0x2316B0: bytes.fromhex('8b0683c4208bceff504485c0740688983c0800005b5e81c400020000c3'),
        0x729550: bytes.fromhex('a1485e850085c074048b4004c333c0c3'),
        0x401084: bytes.fromhex('a1b85e850085c074208b888003000085c97516'),
        GET_HEALTH: bytes.fromhex('8b4c240485c974148b01ff503c85c0740b8d882c070000e99483f6ff'),
        0x729D70: bytes.fromhex('57b90d00000033c0bfe8e28500f3abe87ca4f8ff5fc3'),
    }


def guard(c, control, service, end, pickup=True, *, addresses, sample=False):
    """Resolve live player/map each call; never reuse a player across loads."""
    a = addresses
    for field, value in ((s.PLAYABLE, 0), (s.PLAYER, 0), (s.MAP_INDEX, 0xFFFFFFFF)):
        c.emit(b'\xc7\x05'+u32(service+field)+u32(value))
    fields = (service+s.ENABLED,)
    if pickup:
        fields += (control+discs.ENABLED,)
    for field in fields:
        c.emit(b'\x83\x3d'+u32(field)+b'\x01'); c.jump(b'\x0f\x85', end)
    c.emit(b'\xff\x15'+u32(a.tick_iat))
    c.emit(b'\x2b\x05'+u32(service+s.HEARTBEAT)+b'\x3d'+u32(LEASE_MS))
    c.jump(b'\x0f\x87', end)
    c.emit(b'\x83\x3d'+u32(a.game_mode)+b'\x01'); c.jump(b'\x0f\x85', end)
    c.emit(b'\x83\x3d'+u32(a.game_data)+b'\x00'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x80\x3d'+u32(a.pending_continue)+b'\x00'); c.jump(b'\x0f\x85', end)
    c.emit(b'\xa1'+u32(service+COMBAT_OBJECT)+b'\x85\xc0'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x83\x78\x04\x00')
    if sample:
        c.jump(b'\x0f\x84', 'disc_mode')
        c.emit(b'\x83\x78\x04\x03')  # Suspended combat still has a valid player.
    c.jump(b'\x0f\x85', end)
    c.label('disc_mode')
    c.emit(b'\x8b\x15'+u32(a.loaded_map)+b'\x85\xd2'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x8b\x02\x0d'+u32(0x20202020))
    for i, mission in enumerate(MISSIONS):
        c.emit(b'\x3d'+(mission.map_name.lower()+'.').encode())
        c.jump(b'\x0f\x85', f'disc_next{i}')
        c.emit(b'\xbf'+u32(i)); c.jump(b'\xe9', 'disc_suffix')
        c.label(f'disc_next{i}')
    c.jump(b'\xe9', end)
    c.label('disc_suffix')
    c.emit(b'\x8b\x42\x04\x0d'+u32(0x00202020)+b'\x3d'+b'mix\0')
    c.jump(b'\x0f\x84', 'disc_map')
    c.emit(b'\x3d'+b'lsd\0'); c.jump(b'\x0f\x85', end)
    c.label('disc_map')
    c.emit(b'\x0f\xa3\x3d'+u32(service+s.ALLOWED)); c.jump(b'\x0f\x83', end)
    c.emit(b'\xa1'+u32(STAR)+b'\x85\xc0'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x8b\x70\x04\x85\xf6'); c.jump(b'\x0f\x84', end)
    if sample:
        c.emit(b'\x89\x35'+u32(service+s.PLAYER)+b'\x89\x3d'+u32(service+s.MAP_INDEX))
        c.emit(b'\x56'); c.jump(b'\xe8', GET_HEALTH)
        c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(service+s.HEALTH))
        # Sampling is read-only; never equate an existing player with control.
        c.emit(b'\xa1'+u32(service+COMBAT_OBJECT)+b'\x83\x78\x04\x00')
        c.jump(b'\x0f\x85', 'disc_sampled')
    reject = 'disc_sampled' if sample else end
    c.emit(b'\x80\xbe'+u32(s.CONTROL_ENABLED)+b'\x00'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\xa1'+u32(CAMERA)+b'\x85\xc0'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\x83\xb8'+u32(s.CAMERA_HOST)+b'\x00'); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x56'); c.jump(b'\xe8', GET_HEALTH)
    c.emit(b'\x83\xc4\x04\xd9\x1d'+u32(service+s.HEALTH))
    c.emit(b'\xa1'+u32(service+s.HEALTH)+b'\x85\xc0'); c.jump(b'\x0f\x8e', reject)
    c.emit(b'\x3d'+u32(0x7F800000)); c.jump(b'\x0f\x83', reject)
    c.emit(b'\x89\x35'+u32(service+s.PLAYER)+b'\x89\x3d'+u32(service+s.MAP_INDEX))
    c.emit(b'\xc7\x05'+u32(service+s.PLAYABLE)+u32(1))
    if sample: c.label('disc_sampled')


class NativeDiscs:
    def __init__(self, memory, service, addresses, *, checkpoint=False):
        self.memory, self.addresses = memory, addresses
        for address, expected in expected_anchors(addresses).items():
            if memory.read(address, len(expected)) != expected:
                raise UnsupportedBuild(f'TT disc anchor differs at {address:#x}')
        self.site = addresses.band_base+GRANT_CALL
        self.original = call_bytes(self.site, addresses.band_base+GRANT)
        if memory.read(self.site, 5) != self.original:
            raise UnsupportedBuild('TT disc pickup call is owned by another hook')
        self.control = memory.allocate()
        memory.write(self.control, b'\0'*4096)
        self.code = self.control+CODE_OFFSET
        self.reveal = memory.allocate()+CODE_OFFSET
        guarded = partial(guard, addresses=addresses)
        programs = (
            (self.code, discs.build_pickup(self.code, self.control, service,
                guard_builder=guarded, grant=addresses.band_base+GRANT, checkpoint=checkpoint)),
            (self.reveal, discs.build_reveal(self.reveal, self.control, service,
                guard_builder=guarded, cloud_vector=CLOUD_VECTOR)),
        )
        for address, program in programs:
            if len(program) > 4096:
                raise ValueError('TT disc program exceeds code page')
            memory.write(address, program)
            if memory.read(address, len(program)) != program:
                raise MemoryError('TT disc program read-back failed')
            memory.make_executable(address)
        self.replacement = call_bytes(self.site, self.code)
        from .intel import IntelHook
        self.intel = IntelHook(memory, self.control, service, addresses=addresses, checkpoint=checkpoint)

    def update(self, enabled, *, intel_enabled=False):
        current = self.memory.read(self.site, 5)
        if current not in (self.original, self.replacement):
            raise MemoryError('Another hook owns the TT disc pickup call')
        if enabled and current == self.original:
            self.memory.replace_calls([(self.site, self.original, self.replacement)])
        self.memory.write_u32(self.control+discs.ENABLED, int(enabled))
        self.intel.update(enabled and intel_enabled)

    def close(self):
        self.memory.write_u32(self.control+discs.ENABLED, 0)
        self.intel.close()
        if self.memory.read(self.site, 5) == self.replacement:
            self.memory.replace_calls([(self.site, self.replacement, self.original)])
