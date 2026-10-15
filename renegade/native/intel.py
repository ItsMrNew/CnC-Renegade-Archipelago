"""Observe M13's Black Hand intel command; never read saved encyclopedia flags.

MX0_SniperAction calls Reveal_Encyclopedia_Character(41) only when STAR
damages an opening sniper. Capture the command even if native reveal returns
false because a previous playthrough already discovered that entry.
"""
from functools import partial

from . import services as s
from .profile import STEAM, UnsupportedBuild
from .savebuilder import BASE
from .memory import MemoryError
from .thunk import Code, CODE_OFFSET, u32

COMMAND_OFFSET = 0x2E8
STEAM_REVEAL = 0x5AC5F0
TT_REVEAL = 0x72B930
STEAM_ANCHOR = bytes.fromhex('8b442404506a00e80492f9ff83c408c3')
TT_ANCHOR = bytes.fromhex('8b442404506a00e8d4a6f9ff83c408c3')
ENABLED = 4  # Separate from physical-disc enable in the disc control page.


def build_intel(address, control, service, *, guard_builder, original, checkpoint=True):
    from .discs import CAPTURED
    c = Code(address)
    c.emit(b'\x9c\x60\xfc')
    c.emit(b'\x83\x7c\x24\x28\x29'); c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x83\x3d'+u32(control+ENABLED)+b'\x01'); c.jump(b'\x0f\x85', 'done')
    guard_builder(c, control, service, 'done')
    c.emit(b'\x85\xff'); c.jump(b'\x0f\x85', 'done')  # M13 is campaign index zero.
    # One reward shared by both snipers, with a deferred save only on first capture.
    c.emit(b'\xf0\x0f\xba\x2d'+u32(control+CAPTURED)+b'\x00')
    c.jump(b'\x0f\x82', 'done')
    if checkpoint:
        c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(service+s.DISC_GENERATION))
        c.emit(b'\xc7\x05'+u32(service+s.DISC_EVENT)+u32(1))
    c.label('done'); c.emit(b'\x61\x9d'); c.jump(b'\xe9', original)
    return c.finish()


class IntelHook:
    def __init__(self, memory, control, service, *, addresses=None, checkpoint=True):
        from .discs import guard
        self.memory, self.control = memory, control
        self.field = (addresses.commands if addresses else BASE+STEAM.commands)+COMMAND_OFFSET
        self.original = TT_REVEAL if addresses else STEAM_REVEAL
        anchor = TT_ANCHOR if addresses else STEAM_ANCHOR
        if memory.read(self.original, len(anchor)) != anchor:
            raise UnsupportedBuild('Black Hand intel command differs from the supported build')
        if memory.u32(self.field) not in (0, self.original):
            raise UnsupportedBuild('Another observer owns the Black Hand intel command')
        if addresses:
            from .tt_discs import guard as tt_guard
            guard = partial(tt_guard, addresses=addresses)
        self.code = memory.allocate()+CODE_OFFSET
        program = build_intel(self.code, control, service, guard_builder=guard,
                              original=self.original, checkpoint=checkpoint)
        if len(program) > 4096:
            raise ValueError('Intel observer exceeds code page')
        memory.write(self.code, program)
        if memory.read(self.code, len(program)) != program:
            raise MemoryError('Intel observer read-back failed')
        memory.make_executable(self.code)

    def update(self, enabled):
        # Stop capture before attempting ownership changes, including map loads.
        self.memory.write_u32(self.control+ENABLED, 0)
        current = self.memory.u32(self.field)
        if current == 0:
            return  # ScriptCommands is being rebuilt by the game.
        if current not in (self.original, self.code):
            raise MemoryError('Another observer owns the intel callback; leaving it untouched')
        if enabled and current == self.original:
            try:
                self.memory.write_u32(self.field, self.code)
                if self.memory.u32(self.field) != self.code:
                    raise MemoryError('Intel callback installation could not be verified')
            except BaseException:
                self.close()
                raise
        self.memory.write_u32(self.control+ENABLED, int(enabled))

    def close(self):
        self.memory.write_u32(self.control+ENABLED, 0)
        if self.memory.u32(self.field) == self.code:
            self.memory.write_u32(self.field, self.original)
            if self.memory.u32(self.field) != self.original:
                raise MemoryError('Intel callback restoration failed; restart Renegade')
        # Retain executable memory until game exit: a callback may be in flight.
