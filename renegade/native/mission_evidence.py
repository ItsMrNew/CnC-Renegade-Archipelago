"""Read exact persistent mission-script evidence on the game thread.

M01 suppresses objective 108's status call if its Communications Center SAM dies
before the objective announcement. Its mission controller saves the destruction
flag as auto-variable 89. Missing objects/objectives are never evidence.
"""
from . import services as s
from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32
from ..data import OBJECTIVES

FIND_COMMAND = 0x60
STEAM_FIND, TT_FIND = 0x5A9010, 0x728350
STEAM_ANCHOR = bytes.fromhex('8b44240450e8765af5ff83c404c3')
TT_ANCHOR = bytes.fromhex('8b44240450e8e67ef5ff83c404c3')
CONTROLLER = 100376
SCRIPT = b'M01_Mission_Controller_JDG\0'
SAM_FLAG = 89
SAM_BIT = 1 << OBJECTIVES['M01'].index(108)


def build_evidence(address, state, service, find_object):
    """Called after the parent scanner's session, map and access guards (EDI=index)."""
    c = Code(address)
    c.emit(b'\x9c\x60\xfc')
    c.emit(b'\x83\xff\x01'); c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x68'+u32(CONTROLLER)); c.jump(b'\xe8', find_object)
    c.emit(b'\x83\xc4\x04\x85\xc0'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x81\x78\x0c'+u32(CONTROLLER)); c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x89\xc7')  # Keep the verified controller pointer for attempt detection.
    c.emit(b'\x8b\xb0\xf0\x06\x00\x00\x8b\xa8\xf8\x06\x00\x00')
    c.emit(b'\x85\xf6'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x83\xfd\x20'); c.jump(b'\x0f\x87', 'done')
    c.label('observer')
    c.emit(b'\x85\xed'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x8b\x1e\x85\xdb'); c.jump(b'\x0f\x84', 'next_observer')
    c.emit(b'\x89\xd9\x8b\x11\xff\x52\x04\x85\xc0')
    c.jump(b'\x0f\x84', 'next_observer')
    for offset, value in enumerate(SCRIPT):
        c.emit(b'\x80\x78'+bytes([offset,value])); c.jump(b'\x0f\x85', 'next_observer')
    # ScriptImpClass::AutoVariableList; both pinned vanilla script libraries use
    # data pointer / size / ID / next at +0 / +4 / +8 / +12.
    c.emit(b'\x8b\x53\x18\xb9'+u32(256))
    c.label('variable')
    c.emit(b'\x85\xd2'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x83\x7a\x08'+bytes([SAM_FLAG])); c.jump(b'\x0f\x84', 'flag')
    c.emit(b'\x8b\x52\x0c\x49'); c.jump(b'\x0f\x85', 'variable')
    c.jump(b'\xe9', 'done')
    c.label('flag')
    c.emit(b'\x83\x7a\x04\x01'); c.jump(b'\x0f\x85', 'done')
    c.emit(b'\x8b\x12\x85\xd2'); c.jump(b'\x0f\x84', 'done')
    c.emit(b'\x0f\xb6\x02\x83\xf8\x01'); c.jump(b'\x0f\x87', 'done')
    c.emit(b'\x8b\x15'+u32(service+s.GENERATION))
    # A loaded completed flag recovers the AP check without creating a redundant
    # checkpoint. A false->true change in this attempt requests a safe-frame save.
    c.emit(b'\x83\x3d'+u32(state+4)+b'\x01'); c.jump(b'\x0f\x85', 'baseline')
    c.emit(b'\x3b\x15'+u32(state)); c.jump(b'\x0f\x85', 'baseline')
    c.emit(b'\x3b\x3d'+u32(state+8)); c.jump(b'\x0f\x85', 'baseline')
    c.emit(b'\x85\xc0'); c.jump(b'\x0f\x84', 'baseline')
    c.emit(b'\x83\x3d'+u32(state+12)+b'\x00'); c.jump(b'\x0f\x85', 'baseline')
    c.emit(b'\xf0\x81\x0d'+u32(service+s.OBJECTIVE_EVENTS+4)+u32(SAM_BIT))
    c.label('baseline')
    c.emit(b'\x89\x15'+u32(state)+b'\x89\x3d'+u32(state+8)+b'\xa3'+u32(state+12))
    c.emit(b'\xc7\x05'+u32(state+4)+u32(1))
    c.emit(b'\x85\xc0'); c.jump(b'\x0f\x84', 'done')
    for offset in (s.CAPTURED, s.SCRIPT_CAPTURED):
        c.emit(b'\xf0\x81\x0d'+u32(service+offset+4)+u32(SAM_BIT))
    c.jump(b'\xe9', 'done')
    c.label('next_observer'); c.emit(b'\x83\xc6\x04\x4d'); c.jump(b'\xe9', 'observer')
    c.label('done'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


class MissionEvidence:
    def __init__(self, memory, service, *, tt=False):
        find = TT_FIND if tt else STEAM_FIND
        anchor = TT_ANCHOR if tt else STEAM_ANCHOR
        if memory.read(find,len(anchor)) != anchor:
            raise UnsupportedBuild('Mission evidence Find_Object binding differs')
        self.state = memory.allocate()
        memory.write(self.state,b'\0'*4096)
        self.code = self.state + CODE_OFFSET
        program = build_evidence(self.code,self.state,service,find)
        if len(program)>4096: raise ValueError('Mission evidence scanner exceeds code page')
        memory.write(self.code,program)
        if memory.read(self.code,len(program))!=program: raise OSError('Mission evidence read-back failed')
        memory.make_executable(self.code)
