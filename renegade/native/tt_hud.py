"""Process-lifetime workaround for TT b9000's null HUD sentence renderer.

Captured crashes: HUDClass::Render -> sentence Render -> null this+0x130.
The eight cached sentence renderers are cleared by TT's HUD teardown. Its draw
path can index a null entry after a subsequent save load. Use the existing
hide-text branch for that render call only; preserve the original preference.

This compatibility fix is independent of AP connection/permissions. It remains
in the process until game exit, including after the AP client disconnects. No
original executable instructions or files are changed. Reattachments reuse the same
retained wrapper; an unknown render hook is rejected.
"""
from .memory import MemoryError
from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32

MAGIC = b'RENETTHUD01\0'
GUARDED_FRAMES, TOTAL_FRAMES = 16, 20


def bindings(addresses):
    b = addresses.band_base
    return dict(render=b+0x2A1950, slot=b+0x2F32D8, hidden=b+0x31389A,
                index=b+0x314024, renderers=b+0x314028)


def build_guard(control, addresses):
    a = bindings(addresses)
    c = Code(control+CODE_OFFSET)
    # CombatGameMode::Render is a void thiscall with no stack arguments.
    c.emit(b'\x9c\x60')
    c.emit(b'\x0f\xb6\x05'+u32(a['hidden'])+b'\x50')
    c.emit(b'\xff\x05'+u32(control+TOTAL_FRAMES))
    c.emit(b'\x85\xc0'); c.jump(b'\x0f\x85', 'render')
    c.emit(b'\xa1'+u32(a['index'])+b'\x83\xf8\x08')
    c.jump(b'\x0f\x83', 'missing')  # Includes negative/out-of-range indices.
    c.emit(b'\x83\x3c\x85'+u32(a['renderers'])+b'\x00')
    c.jump(b'\x0f\x85', 'render')
    c.label('missing')
    c.emit(b'\xc6\x05'+u32(a['hidden'])+b'\x01')
    c.emit(b'\xff\x05'+u32(control+GUARDED_FRAMES))
    c.label('render')
    c.emit(b'\xfc\x8b\x4c\x24\x1c')  # Original this, below saved hide byte.
    c.jump(b'\xe8', a['render'])
    c.emit(b'\x58\xa2'+u32(a['hidden']))
    c.emit(b'\x61\x9d\xc3')
    return c.finish()


def expected_anchors(addresses):
    a = bindings(addresses)
    return {
        a['render']: bytes.fromhex('558bec83e4f88b018b4020ffd084c00f84a1000000'),
        # Exact branch which bypasses just this sentence draw when hidden.
        addresses.band_base+0x71FB5: b'\x80\x3d'+u32(a['hidden'])+bytes.fromhex('0075128b0d')
            +u32(a['index'])+bytes.fromhex('8b0c8d')+u32(a['renderers'])+bytes.fromhex('e8a0300400'),
        # Eight entries are cleared during HUD shutdown, not permanent objects.
        addresses.band_base+0xAFC3B: bytes.fromhex('c7060000000083c60481fe')
            +u32(a['renderers']+8*4)+bytes.fromhex('7cdc'),
    }


def verify_live(memory, addresses):
    for address, expected in expected_anchors(addresses).items():
        if memory.read(address, len(expected)) != expected:
            raise UnsupportedBuild(f'TT HUD guard anchor differs at {address:#x}')
    return bindings(addresses)


class NativeHUDGuard:
    def __init__(self, memory, addresses):
        self.memory, self.addresses = memory, addresses
        self.a = verify_live(memory, addresses)
        self.control = None
        current = memory.u32(self.a['slot'])
        if current != self.a['render']:
            if current < CODE_OFFSET:
                raise UnsupportedBuild('TT combat renderer is owned by an unknown hook')
            control = current-CODE_OFFSET
            try:
                program = build_guard(control, addresses)
                valid = (memory.read(control, len(MAGIC)) == MAGIC
                         and memory.read(current, len(program)) == program)
            except OSError:
                valid = False
            if not valid:
                raise UnsupportedBuild('TT combat renderer is owned by an unknown hook')
            self.control = control

    def install(self):
        if self.control is not None:
            return False
        mem = self.memory
        control = mem.allocate()
        mem.write(control, b'\0'*4096)
        mem.write(control, MAGIC)
        code = control+CODE_OFFSET
        program = build_guard(control, self.addresses)
        mem.write(code, program)
        if mem.read(code, len(program)) != program:
            raise MemoryError('TT HUD guard read-back failed')
        mem.make_executable(code)
        mem.replace_readonly_u32(self.a['slot'], self.a['render'], code)
        if mem.u32(self.a['slot']) != code:
            raise MemoryError('TT HUD guard installation could not be verified')
        self.control = control
        return True

    def snapshot(self):
        return dict(installed=self.control is not None,
                    guarded_frames=self.memory.u32(self.control+GUARDED_FRAMES) if self.control else 0,
                    render_frames=self.memory.u32(self.control+TOTAL_FRAMES) if self.control else 0)
