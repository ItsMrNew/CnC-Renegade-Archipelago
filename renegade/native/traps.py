"""Temporary traps alter per-frame controls/rendering, never saved physics or fades."""
import colorsys
import struct
from ..data import TRAP_DURATIONS, BUFF_DURATIONS, CONCRETE_BOOTS, DISCO_HAVOC, MOON_BOOTS, BULLET_HOSE, VAMPIRIC_ROUNDS, HOLOGRAM_HAVOC

from .savebuilder import BASE, STAR
from .thunk import Code, CODE_OFFSET, LEASE_MS, u32
from .profile import STEAM, UnsupportedBuild
from .memory import MemoryError
from . import services as s

# Stored in the reward allocation, separate from its request and test mailboxes.
CONCRETE_END, DISCO_END, MOON_END = 160, 164, 168
# Legacy END names now hold remaining playable seconds (float), not wall-clock deadlines.
BULLET_END, VAMPIRE_END, HOLOGRAM_END = 1280, 1284, 1288
TIMERS = {CONCRETE_END: CONCRETE_BOOTS, DISCO_END: DISCO_HAVOC, MOON_END: MOON_BOOTS,
          BULLET_END: BULLET_HOSE, VAMPIRE_END: VAMPIRIC_ROUNDS, HOLOGRAM_END: HOLOGRAM_HAVOC}
DURATIONS = {**TRAP_DURATIONS, **BUFF_DURATIONS}
MAX_EFFECT_SECONDS = 604800.0  # Corruption guard; permits a week of accumulated effects.
FRAME_SECONDS = 0x457B40
OWNER, GENERATION = 172, 176
RELOADS = 180
# Compared only with a freshly resolved held weapon; never dereferenced later.
RELOAD_WEAPON = 184
SLOW_FACTOR, JUMP_FACTOR, RECT, PALETTE = 192, 196, 208, 256
GRAVITY_FACTOR, AIR_STEERING_FACTOR = 200, 204
MOON_GRAVITY_SCALE, MOON_AIR_STEERING_SCALE = 2/3, .8
APPLY_CONTROL, CONTROL_SLOT = 0x149090, 0x3F94C0
BALLISTIC_MOVE, BALLISTIC_SLOT = 0x34B520, 0x40765C
PLAYER_PHYSICS = 0x774
# Complete straight-line prologue, ending with acceleration in x87 ST0.
BALLISTIC_PREFIX = bytes.fromhex('558bec83ec34d90570628a0053568bf16a006a006a01d88e8c000000')
RENDER, RENDER_SLOT = 0x38D0, 0x3EEB18
FORWARD, LEFT, UP, HUMAN_STATE = 0x82C, 0x830, 0x834, 0x994
NEW, RENDER_CTOR, SET_RANGE, ALPHA, ADD_QUAD, DRAW = 0xF1210, 0x238320, 0x2388A0, 0x2387F0, 0x239E40, 0x23AD40
ANCHORS = {
    0x18783D: bytes.fromhex('a1407b85008bcb50e826f6ffff'),
    BALLISTIC_MOVE: BALLISTIC_PREFIX,
    0x149660: bytes.fromhex('8b8e740700008b01ff5074'),
    APPLY_CONTROL: bytes.fromhex('558bec83ec24568bf1e86269faff84c0752a8d8e10080000'),
    0x1496BF: bytes.fromhex('8bcee8aa7a0500d980e4000000d99e34080000'),
    RENDER: bytes.fromhex('8b01ff502084c07468a1687684005385c0743868d0908100'),
    NEW: bytes.fromhex('5357e889051f008b4c240c8bf8a1406d8500408d590483f8'),
    RENDER_CTOR: bytes.fromhex('5355568bf1b80000803f33db8d6e2089460489460857895e'),
    SET_RANGE: bytes.fromhex('8b44240483ec08d94008d820d83d90f17e00d95904d9400c'),
    ALPHA: bytes.fromhex('8a44240484c08b4120741524bf0ca08bd089412080e6bf80'),
    ADD_QUAD: bytes.fromhex('5356578bf96a008b87c400000050e85df3ffff8b87bc0000'),
    DRAW: bytes.fromhex('81ecd001000053568bf133db395e340f84960d0000389eac'),
}


def initialise(memory, control):
    memory.write(control+SLOW_FACTOR, struct.pack('<2f', .45, 1.6))
    memory.write(control+GRAVITY_FACTOR, struct.pack('<2f', MOON_GRAVITY_SCALE, MOON_AIR_STEERING_SCALE))
    memory.write(control+RECT, struct.pack('<4f', 0, 0, 1, 1))
    # Low opacity, smooth 16-second hue cycle. No flashes or native fade changes.
    palette = []
    for i in range(256):
        r, g, b = (round(v*255) for v in colorsys.hsv_to_rgb(i/256, .8, 1))
        palette.append(0x28000000 | r << 16 | g << 8 | b)
    memory.write(control+PALETTE, struct.pack('<256I', *palette))


def guard(c, control, service, reject, *, addresses=None, star=BASE+STAR,
          camera=BASE+s.COMBAT_CAMERA, combat_object=None):
    """All callbacks recheck native gameplay state; returns player ESI and tick EAX."""
    for offset in (s.ENABLED, s.PLAYABLE, s.ALIVE):
        c.emit(b'\x83\x3d'+u32(service+offset)+b'\x01'); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x8b\x15'+u32(service+s.OBSERVER)+b'\x85\xd2'); c.jump(b'\x0f\x84',reject)
    commands=addresses.commands if addresses else BASE+STEAM.commands
    tick=addresses.tick_iat if addresses else BASE+STEAM.tick_iat
    mode=addresses.game_mode if addresses else BASE+STEAM.game_mode
    pending=addresses.pending_continue if addresses else BASE+STEAM.pending_continue
    if combat_object is not None:
        c.emit(b'\x8b\x15'+u32(service+combat_object)+b'\x85\xd2');c.jump(b'\x0f\x84',reject)
        c.emit(b'\x83\x7a\x04\x00');c.jump(b'\x0f\x85',reject)
        c.emit(b'\x8b\x15'+u32(service+s.OBSERVER))
    c.emit(b'\x3b\x15'+u32(commands+0x204)); c.jump(b'\x0f\x85',reject)
    c.emit(b'\xff\x15'+u32(tick))
    c.emit(b'\x89\xc2\x2b\x15'+u32(service+s.HEARTBEAT)+b'\x81\xfa'+u32(LEASE_MS))
    c.jump(b'\x0f\x87', reject)
    c.emit(b'\x83\x3d'+u32(mode)+b'\x01'); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x80\x3d'+u32(pending)+b'\x00'); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x8b\x15'+u32(camera)+b'\x85\xd2'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\x83\xba'+u32(s.CAMERA_HOST)+b'\x00'); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x8b\x35'+u32(star)+b'\x85\xf6'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\x8b\x76\x04\x85\xf6'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\x3b\x35'+u32(control+OWNER)); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x3b\x35'+u32(service+s.PLAYER)); c.jump(b'\x0f\x85', reject)
    c.emit(b'\x80\xbe'+u32(s.CONTROL_ENABLED)+b'\x00'); c.jump(b'\x0f\x84', reject)
    c.emit(b'\x8b\x15'+u32(service+s.GENERATION)+b'\x3b\x15'+u32(control+GENERATION))
    c.jump(b'\x0f\x85', reject)


def deadline(c, control, offset, expired):
    c.emit(b'\x8b\x15'+u32(control+offset)+b'\x85\xd2'); c.jump(b'\x0f\x8e', expired)
    c.emit(b'\x81\xfa'+struct.pack('<f', MAX_EFFECT_SECONDS)); c.jump(b'\x0f\x86',f'valid_{offset}')
    c.label(f'expired_{offset}')
    c.emit(b'\xc7\x05'+u32(control+offset)+u32(0)); c.jump(b'\xe9',expired)
    c.label(f'valid_{offset}')


def build_clock(address, control, service, *, guard_builder=guard, frame_seconds=BASE+FRAME_SECONDS):
    """Called once per CombatGameMode Think; engine delta excludes suspended time."""
    c = Code(address)
    c.emit(b'\x9c\x60\xfc')
    guard_builder(c, control, service, 'done')
    # Reject nonsensical frame deltas; never charge a long OS stall to an effect.
    c.emit(b'\xa1'+u32(frame_seconds)+b'\x85\xc0'); c.jump(b'\x0f\x8e','done')
    c.emit(b'\x3d'+struct.pack('<f',1.0)); c.jump(b'\x0f\x87','done')
    for offset in TIMERS:
        deadline(c, control, offset, f'next_{offset}')
        c.emit(b'\xd9\x05'+u32(control+offset)+b'\xd8\x25'+u32(frame_seconds)+b'\xd9\x1d'+u32(control+offset))
        c.emit(b'\x83\x3d'+u32(control+offset)+b'\x00'); c.jump(b'\x0f\x8f', f'next_{offset}')
        c.emit(b'\xc7\x05'+u32(control+offset)+u32(0))
        c.label(f'next_{offset}')
    c.label('done'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


def build_movement(address, control, service, *, guard_builder=guard,
                   apply_control=BASE+APPLY_CONTROL, collision_group=None, hologram=True):
    from .bonuses import HOLO_PTR, HOLO_ID, HOLO_GENERATION, SET_COLLISION_GROUP, HOLO_COLLISION_GROUP
    c = Code(address)
    c.emit(b'\x51')  # Preserve this across the original void thiscall.
    c.jump(b'\xe8', apply_control)
    c.emit(b'\x9c\x60\xfc')
    guard_builder(c, control, service, 'done')
    c.emit(b'\x3b\x74\x24\x24'); c.jump(b'\x0f\x84', 'player')
    if not hologram: c.jump(b'\xe9','done')
    # Soldier coordination zones can reset collision groups during Think. Keep
    # our identified temporary ally intangible before physics movement resumes.
    c.emit(b'\x8b\x4c\x24\x24\x3b\x0d'+u32(control+HOLO_PTR));c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(control+HOLO_GENERATION));c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x41\x0c\x3b\x05'+u32(control+HOLO_ID));c.jump(b'\x0f\x85','done')
    c.emit(b'\x6a'+bytes([HOLO_COLLISION_GROUP]));c.jump(b'\xe8',collision_group or BASE+SET_COLLISION_GROUP)
    c.jump(b'\xe9','done')
    c.label('player')
    # Upright or airborne only: exclude vehicles, ladders, scripted movement/death.
    c.emit(b'\x8b\x96'+u32(HUMAN_STATE)+b'\x85\xd2'); c.jump(b'\x0f\x84', 'walking')
    c.emit(b'\x83\xfa\x05'); c.jump(b'\x0f\x85', 'done')
    c.label('walking')
    deadline(c, control, CONCRETE_END, 'moon')
    for field in (FORWARD, LEFT):
        c.emit(b'\xd9\x86'+u32(field)+b'\xd8\x0d'+u32(control+SLOW_FACTOR)+b'\xd9\x9e'+u32(field))
    # Suppress this frame's upward control, not velocity or saved jump stats.
    # Moon Boots can still affect an existing fall but cannot bypass this lock.
    c.emit(b'\x83\xbe'+u32(UP)+b'\x00'); c.jump(b'\x0f\x8e', 'moon')
    c.emit(b'\xc7\x86'+u32(UP)+u32(0))
    c.label('moon')
    deadline(c, control, MOON_END, 'done')
    c.emit(b'\x83\xbe'+u32(HUMAN_STATE)+b'\x05'); c.jump(b'\x0f\x85','jump')
    for field in (FORWARD, LEFT):
        c.emit(b'\xd9\x86'+u32(field)+b'\xd8\x0d'+u32(control+AIR_STEERING_FACTOR)+b'\xd9\x9e'+u32(field))
    c.label('jump')
    c.emit(b'\x8b\x96'+u32(UP)+b'\x85\xd2'); c.jump(b'\x0f\x8e', 'done')
    c.emit(b'\x81\xfa'+u32(0x7F800000)); c.jump(b'\x0f\x83', 'done')
    c.emit(b'\xd9\x86'+u32(UP)+b'\xd8\x0d'+u32(control+JUMP_FACTOR)+b'\xd9\x9e'+u32(UP))
    c.label('done'); c.emit(b'\x61\x9d\x8d\x64\x24\x04\xc3')
    return c.finish()


def build_gravity(address, control, service):
    """Scale the local ballistic acceleration, never the object's saved gravity."""
    c = Code(address)
    c.emit(b'\x9c\x60\xfc')
    guard(c, control, service, 'ordinary')
    c.emit(b'\x8b\x54\x24\x18\x3b\x96'+u32(PLAYER_PHYSICS))
    c.jump(b'\x0f\x85','ordinary')  # ECX must be Havoc's current physics object.
    c.emit(b'\x8b\x96'+u32(HUMAN_STATE)+b'\x85\xd2'); c.jump(b'\x0f\x84','eligible')
    c.emit(b'\x83\xfa\x05'); c.jump(b'\x0f\x85','ordinary')
    c.label('eligible')
    deadline(c, control, MOON_END, 'ordinary')
    c.emit(b'\x61\x9d')
    c.emit(BALLISTIC_PREFIX)
    c.emit(b'\xd8\x0d'+u32(control+GRAVITY_FACTOR))
    c.jump(b'\xe9',BASE+BALLISTIC_MOVE+len(BALLISTIC_PREFIX))
    c.label('ordinary'); c.emit(b'\x61\x9d'); c.jump(b'\xe9',BASE+BALLISTIC_MOVE)
    return c.finish()


def build_disco(address, control, service, hud_target=None, *, guard_builder=guard,
                render=BASE+RENDER, bindings=None):
    c = Code(address)
    def native(rva): c.jump(b'\xe8',(bindings or {}).get(rva,BASE+rva))
    c.emit(b'\x51'); c.jump(b'\xe8', render)
    c.emit(b'\x9c\x60\xfc')
    guard_builder(c, control, service, 'done')
    c.emit(b'\x8b\x54\x24\x24\x83\x7a\x04\x00'); c.jump(b'\x0f\x85', 'done')
    deadline(c, control, DISCO_END, 'done')
    c.emit(b'\xc1\xe8\x06\x25'+u32(255)+b'\x8b\x1c\x85'+u32(control+PALETTE))
    # A private one-frame renderer leaves cinematic fades and save data untouched.
    # Construct and destroy on the render thread; no resource survives detach.
    c.emit(b'\x68'+u32(0x5B4)); native(NEW)
    c.emit(b'\x83\xc4\x04\x85\xc0'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x6a\x00\x89\xc1'); native(RENDER_CTOR)
    c.emit(b'\x89\xc7\x89\xf9\x68'+u32(control+RECT)); native(SET_RANGE)
    c.emit(b'\x89\xf9\x6a\x01'); native(ALPHA)
    c.emit(b'\x89\xf9\x53\x68'+u32(control+RECT)); native(ADD_QUAD)
    c.emit(b'\x89\xf9'); native(DRAW)
    c.emit(b'\x89\xf9\x8b\x07\x6a\x01\xff\x10')  # deleting destructor(this, 1).
    c.label('done')
    if hud_target:
        c.jump(b'\xe8', hud_target)
    c.emit(b'\x61\x9d\x8d\x64\x24\x04\xc3')
    return c.finish()


class NativeTraps:
    def __init__(self, memory, control, service):
        self.memory, self.service, self.entries = memory, service, []
        for rva, expected in ANCHORS.items():
            if memory.read(BASE+rva, len(expected)) != expected:
                raise UnsupportedBuild(f'Trap anchor differs at {rva:#x}')
        initialise(memory, control)
        from .effects_hud import NativeEffectsHUD
        self.hud = NativeEffectsHUD(memory, control, service)
        self.clock = memory.allocate()+CODE_OFFSET
        program = build_clock(self.clock, control, service)
        memory.write(self.clock, program)
        if memory.read(self.clock,len(program)) != program: raise MemoryError('Effect clock read-back failed')
        memory.make_executable(self.clock)
        for slot, original, builder in ((CONTROL_SLOT, APPLY_CONTROL, build_movement),
                                       (RENDER_SLOT, RENDER, build_disco),
                                       (BALLISTIC_SLOT, BALLISTIC_MOVE, build_gravity)):
            code = memory.allocate()+CODE_OFFSET
            program = builder(code, control, service, self.hud.code) if builder is build_disco else builder(code, control, service)
            if len(program) > 4096: raise ValueError('Trap wrapper exceeds code page')
            memory.write(code, program)
            if memory.read(code, len(program)) != program: raise MemoryError('Trap wrapper read-back failed')
            memory.make_executable(code)
            self.entries.append((BASE+slot, BASE+original, code))

    def update(self):
        self.hud.update()
        for field, original, code in self.entries:
            current = self.memory.u32(field)
            if current == code: continue
            if current != original: raise MemoryError('Another hook owns a trap callback; leaving it untouched')
            self.memory.replace_readonly_u32(field, original, code)

    def close(self):
        self.memory.write_u32(self.service+s.ENABLED, 0)
        errors = []
        for field, original, code in self.entries:
            try:
                if self.memory.u32(field) == code:
                    self.memory.replace_readonly_u32(field, code, original)
            except OSError as error:
                errors.append(str(error))
        if errors: raise MemoryError('; '.join(errors))
        # Retain code allocations for callbacks already in flight.
