"""Observe script completion intent, including objectives absent from the list.

Some scripts can complete an objective before its delayed announcement registers
the objective. The engine setter then has no list entry to update. Capture the
allowlisted accomplished call and always forward the original arguments intact.
"""
import logging

from ..data import MISSIONS
from .profile import STEAM, UnsupportedBuild
from .memory import MemoryError
from .thunk import Code, u32, CODE_OFFSET, LEASE_MS, steam_addresses
from .savebuilder import BASE
from .services import (ALLOWED, ENABLED, HEARTBEAT, CAPTURED, OBJECTIVE_EVENTS,
    DESCRIPTORS, SET_OBJECTIVE_STATUS, OBJECTIVE_COUNT, OBJECTIVE_ARRAY,
    CALLBACK_CAPTURED, MISSING_AT_CALL, PRESENT_AT_CALL)

COMMAND_OFFSET = 0x204
ANCHOR = bytes.fromhex('8b4424088b4c24045051e8117ff4ff83c408c3')
LOG = logging.getLogger('Renegade.Services')


def build_objective_hook(code_address, control, *, addresses=None, replay_events=False):
    if addresses is None:
        addresses = steam_addresses(BASE)
        addresses.objective_status = BASE + SET_OBJECTIVE_STATUS
        addresses.objective_count = BASE + OBJECTIVE_COUNT
        addresses.objective_array = BASE + OBJECTIVE_ARRAY
    c = Code(code_address)
    c.emit(b'\x9c\x60')
    c.emit(b'\x83\x7c\x24\x2c\x01')  # original cdecl status argument
    c.jump(b'\x0f\x85', 'exit')
    c.emit(b'\x83\x3d'+u32(control+ENABLED)+b'\x01')
    c.jump(b'\x0f\x85', 'exit')
    c.emit(b'\xff\x15'+u32(addresses.tick_iat))
    c.emit(b'\x2b\x05'+u32(control+HEARTBEAT)+b'\x3d'+u32(LEASE_MS))
    c.jump(b'\x0f\x87', 'exit')
    c.emit(b'\x83\x3d'+u32(addresses.game_mode)+b'\x01')
    c.jump(b'\x0f\x85', 'exit')
    c.emit(b'\x83\x3d'+u32(addresses.game_data)+b'\x00')
    c.jump(b'\x0f\x84', 'exit')
    c.emit(b'\x8b\x15'+u32(addresses.loaded_map)+b'\x85\xd2')
    c.jump(b'\x0f\x84', 'exit')
    c.emit(b'\x8b\x02\x0d'+u32(0x20202020))
    for i, mission in enumerate(MISSIONS):
        c.emit(b'\x3d'+(mission.map_name.lower()+'.').encode())
        c.jump(b'\x0f\x84', f'map{i}')
    c.jump(b'\xe9', 'exit')
    for i in range(12):
        c.label(f'map{i}')
        c.emit(b'\xbf'+u32(i))
        c.jump(b'\xe9', 'map')
    c.label('map')
    c.emit(b'\x8b\x42\x04\x0d'+u32(0x00202020)+b'\x3d'+b'mix\0')
    c.jump(b'\x0f\x84', 'suffix')
    c.emit(b'\x3d'+b'lsd\0'); c.jump(b'\x0f\x85', 'exit')
    c.label('suffix')
    c.emit(b'\x0f\xa3\x3d'+u32(control+ALLOWED))
    c.jump(b'\x0f\x83', 'exit')
    c.emit(b'\x8b\x44\x24\x28')  # objective ID, original argument untouched
    c.emit(b'\x8b\x34\xfd'+u32(control+DESCRIPTORS))
    c.emit(b'\x8b\x0c\xfd'+u32(control+DESCRIPTORS+4))
    c.label('record')
    c.emit(b'\x85\xc9'); c.jump(b'\x0f\x84', 'exit')
    c.emit(b'\x3b\x06'); c.jump(b'\x0f\x84', 'capture')
    c.emit(b'\x83\xc6\x0c\x49'); c.jump(b'\xe9', 'record')
    c.label('capture')
    c.emit(b'\x8b\x56\x04')  # edx = allowlisted objective bit; eax still holds its ID.
    c.emit(b'\xf0\x09\x14\xbd'+u32(control+CALLBACK_CAPTURED))
    # Diagnose presence before forwarding the setter. Absence is only asserted
    # after a complete bounded scan; an invalid list is classified as unknown.
    c.emit(b'\x8b\x0d'+u32(addresses.objective_count)+b'\x81\xf9'+u32(128))
    c.jump(b'\x0f\x87', 'diagnosed')
    c.emit(b'\x85\xc9'); c.jump(b'\x0f\x84', 'missing')
    c.emit(b'\x8b\x1d'+u32(addresses.objective_array)+b'\x85\xdb')
    c.jump(b'\x0f\x84', 'diagnosed')
    c.label('find_objective')
    c.emit(b'\x8b\x33\x85\xf6'); c.jump(b'\x0f\x84', 'find_next')
    c.emit(b'\x3b\x06'); c.jump(b'\x0f\x84', 'present')
    c.label('find_next')
    c.emit(b'\x83\xc3\x04\x49'); c.jump(b'\x0f\x85', 'find_objective')
    c.label('missing')
    c.emit(b'\xf0\x09\x14\xbd'+u32(control+MISSING_AT_CALL))
    c.jump(b'\xe9', 'diagnosed')
    c.label('present')
    c.emit(b'\xf0\x09\x14\xbd'+u32(control+PRESENT_AT_CALL))
    c.label('diagnosed')
    if replay_events:
        # TT checkpoints deduplicate per attempt, independently of AP checks.
        c.emit(b'\xf0\x09\x14\xbd'+u32(control+OBJECTIVE_EVENTS))
    c.emit(b'\x85\x14\xbd'+u32(control+CAPTURED))
    c.jump(b'\x0f\x85', 'exit')  # Repeated accomplished calls do not spam saves.
    c.emit(b'\xf0\x09\x14\xbd'+u32(control+CAPTURED))
    c.emit(b'\xf0\x09\x14\xbd'+u32(control+OBJECTIVE_EVENTS))
    c.label('exit')
    c.emit(b'\x61\x9d'); c.jump(b'\xe9', addresses.objective_status)
    result = c.finish()
    if len(result) > 4096:
        raise ValueError('Objective observer exceeds code page')
    return result


class ObjectiveHook:
    def __init__(self, memory, control, *, tt=False, replay_events=False):
        self.memory, self.control = memory, control
        addresses = None
        if tt:
            from .tt_profile import verify as verify_tt
            addresses = verify_tt(memory)
            self.field = addresses.commands + COMMAND_OFFSET
            self.original = addresses.objective_status
        else:
            self.field = BASE+STEAM.commands+COMMAND_OFFSET
            self.original = BASE+SET_OBJECTIVE_STATUS
        self.installed = False
        if not tt and memory.read(self.original, len(ANCHOR)) != ANCHOR:
            raise UnsupportedBuild('Objective status callback differs')
        self.code = memory.allocate()+CODE_OFFSET
        program = build_objective_hook(self.code, control, addresses=addresses, replay_events=replay_events)
        memory.write(self.code, program)
        if memory.read(self.code, len(program)) != program:
            raise MemoryError('Objective observer read-back failed')
        memory.make_executable(self.code)

    def update(self):
        value = self.memory.u32(self.field)
        if value == self.code:
            return
        if value == 0:
            return  # ScriptCommands is rebuilt during level loading.
        if value != self.original:
            raise MemoryError('Another observer owns the objective callback; leaving it untouched')
        if self.installed:
            from .services import GENERATION
            self.memory.write_u32(self.control+GENERATION,
                                  (self.memory.u32(self.control+GENERATION)+1) & 0xFFFFFFFF)
        self.installed = True
        try:
            self.memory.write_u32(self.field, self.code)
            if self.memory.u32(self.field) != self.code:
                raise MemoryError('Objective callback installation could not be verified')
        except BaseException:
            self.close()
            raise
        LOG.info('Native objective completion-call observer attached')

    def close(self):
        self.memory.write_u32(self.control+ENABLED, 0)
        if self.installed and self.memory.u32(self.field) == self.code:
            self.memory.write_u32(self.field, self.original)
            if self.memory.u32(self.field) != self.original:
                raise MemoryError('Objective callback restoration failed; restart Renegade')
        self.installed = False
        # Keep the code allocation until game exit, like the victory observer:
        # an in-flight callback may already have fetched its address.
