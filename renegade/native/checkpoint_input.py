"""Repair buffered input after a blocking AP save, on the game thread.

Renegade's keyboard/mouse readers retain HELD until a buffered release arrives.
A stalled/overflowed buffer can lose that release. Drain the old events, then
rebuild HELD from DirectInput's immediate state; never synthesize HIT/DOUBLE.
Do not acquire devices, send Windows input, or change bindings/focus.
"""
from dataclasses import dataclass

from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32


@dataclass(frozen=True)
class InputBinding:
    using: int
    focus: int
    acquired: int
    flush: int
    direct_flush: int
    functions: int
    keyboard: int
    mouse: int
    keys: int
    buttons: int
    update: int
    read_keys: int
    read_mouse: int
    init_keys: int
    init_mouse: int
    key_format: int
    mouse_format: int
    read: int


STEAM = InputBinding(0x822369,0x86E3AC,0x85F57D,0x4F4010,0x5728B0,
    0x857270,0x85F570,0x85F574,0x85F468,0x85F568,0x4F42E4,
    0x572A80,0x572BA0,0x57261F,0x57268E,0x7FE614,0x7FE5FC,0x572DB0)
TT = InputBinding(0x8100B1,0x83DBC0,0x85E0FD,0x675950,0x6ECD90,
    0x8569C0,0x85E0F0,0x85E0F4,0x85DFE8,0x85E0E8,0x675C24,
    0x6ECF60,0x6ED080,0x6ECAFF,0x6ECB6E,0x7EE378,0x7EE360,0x6ED290)


def expected_anchors(a):
    # Exact pinned executable instructions and DIDATAFORMAT sizes. The two
    # releases share this implementation but not its addresses. No DLL RVAs.
    relative = (a.direct_flush-(a.flush+14)) & 0xFFFFFFFF
    return {
        a.flush: b'\xa0'+u32(a.using)+b'\x84\xc0\x74\x05\xe9'+u32(relative)+b'\xc3',
        a.direct_flush: (b'\x57\xb9\x40\0\0\0\x33\xc0\xbf'+u32(a.keys)+
            b'\xf3\xab\x33\xc9\x33\xd2\x89\x0d'+u32(a.keys-96)+
            b'\x66\xa3'+u32(a.buttons)+b'\x89\x0d'+u32(a.keys-92)+
            b'\x66\x89\x15'+u32(a.keys-100)+b'\xa2'+u32(a.buttons+2)+
            b'\x89\x0d'+u32(a.keys-88)+b'\x88\x15'+u32(a.keys-98)+b'\x5f\xc3'),
        a.update: b'\xa0'+u32(a.focus)+b'\x84\xc0\x75\x0d',
        a.update+32: b'\xb9\x86\0\0\0\x33\xc0\xbf'+u32(a.functions)+b'\xf3\xab',
        a.read: b'\x51\xa0'+u32(a.acquired)+b'\x84\xc0\x74\x61',
        a.read_keys: b'\xa1'+u32(a.keyboard)+bytes.fromhex('83ec1885c00f84fe00000033c0b9010000008a90')+u32(a.keys),
        a.read_mouse: b'\xa1'+u32(a.mouse)+bytes.fromhex('83ec1c85c00f842f01000033c08a88')+u32(a.buttons),
        a.init_keys: b'\x8b\x10\x68'+u32(a.key_format)+b'\x50\xff\x52\x2c',
        a.init_mouse: b'\x8b\x08\x68'+u32(a.mouse_format)+b'\x50\xff\x51\x2c',
        a.key_format: b''.join(u32(n) for n in (24,16,2,256,256)),
        a.mouse_format: b''.join(u32(n) for n in (24,16,2,16,7)),
    }


def build_recovery(address, a=STEAM):
    c = Code(address)
    c.emit(b'\x9c\x60\xfc')
    c.emit(b'\x80\x3d'+u32(a.using)+b'\x00'); c.jump(b'\x0f\x84','done')
    c.jump(b'\xe8',a.flush)
    # Do not carry this frame's already-derived firing/movement into combat.
    c.emit(b'\x31\xc0\xbf'+u32(a.functions)+b'\xb9'+u32(134)+b'\xf3\xab')
    for flag in (a.focus,a.acquired):
        c.emit(b'\x80\x3d'+u32(flag)+b'\x00'); c.jump(b'\x0f\x84','done')
    # Stack-local snapshot and event count. Never read a failed device snapshot.
    c.emit(b'\x81\xec'+u32(260))
    for name,device,size,target,start,count in (
            ('keys',a.keyboard,256,a.keys,0,256),
            ('mouse',a.mouse,16,a.buttons,12,3)):
        c.emit(b'\x8b\x1d'+u32(device)+b'\x85\xdb'); c.jump(b'\x0f\x84',name+'_done')
        # GetDeviceData(sizeof(DIDEVICEOBJECTDATA), NULL, &INFINITE, 0).
        c.emit(b'\xc7\x84\x24'+u32(256)+u32(0xFFFFFFFF))
        c.emit(b'\x8d\x84\x24'+u32(256)+b'\x6a\x00\x50\x6a\x00\x6a\x14\x53')
        c.emit(b'\x8b\x03\xff\x50\x28\x85\xc0'); c.jump(b'\x0f\x88',name+'_done')
        # GetDeviceState is an immediate snapshot, not another event-buffer read.
        c.emit(b'\x89\xe0\x50\x68'+u32(size)+b'\x53\x8b\x03\xff\x50\x24\x85\xc0')
        c.jump(b'\x0f\x88',name+'_done')
        c.emit(b'\x8d\x74\x24'+bytes([start])+b'\xbf'+u32(target)+b'\xb9'+u32(count))
        c.label(name+'_copy')
        c.emit(b'\xac\xc0\xe8\x07\xaa\x49'); c.jump(b'\x0f\x85',name+'_copy')
        c.label(name+'_done')
    c.emit(b'\x81\xc4'+u32(260))
    c.label('done'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


class CheckpointInput:
    def __init__(self, memory, *, tt=False):
        a = TT if tt else STEAM
        for address, expected in expected_anchors(a).items():
            if memory.read(address,len(expected)) != expected:
                raise UnsupportedBuild(f'Checkpoint input anchor differs at {address:#x}')
        self.control = memory.allocate()
        self.code = self.control+CODE_OFFSET
        program = build_recovery(self.code,a)
        memory.write(self.code,program)
        if memory.read(self.code,len(program)) != program:
            raise OSError('Checkpoint input code read-back failed')
        memory.make_executable(self.code)
