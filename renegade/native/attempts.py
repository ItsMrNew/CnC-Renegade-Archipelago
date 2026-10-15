"""Read-only, game-thread evidence of failed objectives in the current attempt.

Only the native STATUS_FAILED (2) is evidence. A hidden, missing or unfinished
objective is not a failure. This snapshot never enters the AP completion outbox.
"""
import struct
import math
import time

from ..data import MISSIONS, OBJECTIVES
from . import services as s
from .thunk import Code, CODE_OFFSET, u32
from .memory import MemoryError

SEQUENCE, MAP, GENERATION, FAILED = 0, 4, 8, 12
POSITION, POSITION_VALID, POSITION_PLAYER, FLOAT_STATE = 16, 28, 32, 64


def build_attempts(control, service, *, addresses=None):
    commands = addresses.commands if addresses else s.BASE+s.STEAM.commands
    count = addresses.objective_count if addresses else s.BASE+s.OBJECTIVE_COUNT
    array = addresses.objective_array if addresses else s.BASE+s.OBJECTIVE_ARRAY
    c = Code(control + CODE_OFFSET)
    def store(offset, value): c.emit(b'\xc7\x05'+u32(control+offset)+u32(value))
    c.emit(b'\x9c\x60\xff\x05'+u32(control+SEQUENCE))
    store(MAP, 0xFFFFFFFF); store(FAILED, 0); store(POSITION_VALID, 0)
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(control+GENERATION))
    for off in (s.ENABLED, s.PLAYER):
        c.emit(b'\x83\x3d'+u32(service+off)+b'\x00'); c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x3d'+u32(service+s.MAP_INDEX)+b'\x83\xff\x0c'); c.jump(b'\x0f\x83','done')
    c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x3b\x05'+u32(commands+0x204))
    c.jump(b'\x0f\x85','done')
    # Same verified ScriptCommands Get_Position ABI used by Hologram Havoc.
    # Sample on the game thread, never dereference a cached actor externally.
    position = 0x728090 if addresses else 0x5A8D50
    c.emit(b'\x81\x3d'+u32(commands+0x40)+u32(position))
    c.jump(b'\x0f\x85','no_position')
    c.emit(b'\xfc\x0f\xae\x05'+u32(control+FLOAT_STATE))  # cld; fxsave, aligned page + 64
    c.emit(b'\xa1'+u32(service+s.PLAYER)+b'\xa3'+u32(control+POSITION_PLAYER))
    c.emit(b'\x50\x68'+u32(control+POSITION))
    c.jump(b'\xe8',position); c.emit(b'\x83\xc4\x08')
    c.emit(b'\x0f\xae\x0d'+u32(control+FLOAT_STATE))
    store(POSITION_VALID, 1)
    c.label('no_position')
    c.emit(b'\x8b\x0d'+u32(count)+b'\x81\xf9'+u32(128))
    c.jump(b'\x0f\x87','done')
    c.emit(b'\x8b\x15'+u32(array)+b'\x31\xed')
    c.emit(b'\x85\xc9'); c.jump(b'\x0f\x84','publish')
    c.emit(b'\x85\xd2'); c.jump(b'\x0f\x84','done')
    c.label('objective')
    c.emit(b'\x8b\x02\x85\xc0'); c.jump(b'\x0f\x84','next')
    c.emit(b'\x83\x78\x08\x02'); c.jump(b'\x0f\x85','next')
    c.emit(b'\x8b\x18\x51\x52\xff\x70\x04')
    c.emit(b'\x8b\x34\xfd'+u32(service+s.DESCRIPTORS)+b'\x8b\x04\xfd'+u32(service+s.DESCRIPTORS+4))
    c.label('record')
    c.emit(b'\x85\xc0'); c.jump(b'\x0f\x84','record_done')
    c.emit(b'\x3b\x1e'); c.jump(b'\x0f\x84','match')
    c.emit(b'\x83\xc6\x0c\x48'); c.jump(b'\xe9','record')
    c.label('match')
    c.emit(b'\x8b\x0c\x24\x3b\x4e\x08'); c.jump(b'\x0f\x85','record_done')
    c.emit(b'\x0b\x6e\x04')
    c.label('record_done'); c.emit(b'\x83\xc4\x04\x5a\x59')
    c.label('next'); c.emit(b'\x83\xc2\x04\x49'); c.jump(b'\x0f\x85','objective')
    c.label('publish')
    c.emit(b'\x89\x3d'+u32(control+MAP)+b'\x89\x2d'+u32(control+FAILED))
    c.label('done'); c.emit(b'\xff\x05'+u32(control+SEQUENCE)+b'\x61\x9d\xc3')
    return c.finish()


class NativeAttempts:
    def __init__(self, memory, service, *, addresses=None):
        self.memory, self.service = memory, service
        self.commands = addresses.commands if addresses else s.BASE+s.STEAM.commands
        self.game_mode = addresses.game_mode if addresses else s.BASE+s.STEAM.game_mode
        self.control = memory.allocate()
        memory.write(self.control, b'\0'*4096)
        memory.write_u32(self.control+MAP, 0xFFFFFFFF)
        self.code = self.control+CODE_OFFSET
        code = build_attempts(self.control, service, addresses=addresses)
        if len(code) > 4096:
            raise ValueError('Attempt observer exceeds code page')
        memory.write(self.code, code)
        if memory.read(self.code, len(code)) != code:
            raise MemoryError('Attempt observer read-back failed')
        memory.make_executable(self.code)

    def snapshot(self):
        mem, ctrl, service = self.memory, self.control, self.service
        sequence, index, generation, failed = struct.unpack('<4I', mem.read(ctrl, 16))
        if (sequence & 1 or index >= len(MISSIONS) or mem.u32(ctrl+SEQUENCE) != sequence
                or not mem.u32(service+s.ENABLED) or not mem.u32(service+s.PLAYER)
                or generation != mem.u32(service+s.GENERATION) or index != mem.u32(service+s.MAP_INDEX)
                or mem.u32(self.game_mode) != 1
                or mem.u32(service+s.OBSERVER) != mem.u32(self.commands+0x204)):
            return None
        name = MISSIONS[index].map_name
        return dict(map=name, failed=[oid for bit, oid in enumerate(OBJECTIVES[name]) if failed & (1 << bit)])

    def tracker_snapshot(self):
        """Read-only volatile sample; identity and sequence reject reload races."""
        mem,ctrl=self.memory,self.control
        before=mem.u32(ctrl+SEQUENCE)
        attempt=self.snapshot()
        if attempt is None:return None
        xyz=struct.unpack('<3f',mem.read(ctrl+POSITION,12))
        position_valid=(mem.u32(ctrl+POSITION_VALID)==1 and
                        mem.u32(ctrl+POSITION_PLAYER)==mem.u32(self.service+s.PLAYER) and
                        all(math.isfinite(v) and abs(v)<100000 for v in xyz))
        if before & 1 or mem.u32(ctrl+SEQUENCE)!=before:return None
        # Polling an unchanged game-thread snapshot must not make a paused or
        # stopped frame look fresh to the tracker.
        if getattr(self, '_tracker_sequence', None) != before:
            self._tracker_sequence = before
            self._tracker_sampled_at = time.monotonic()
        return dict(**attempt, x=xyz[0] if position_valid else None,
                    y=xyz[1] if position_valid else None, z=xyz[2] if position_valid else None,
                    sampled_at=self._tracker_sampled_at)
