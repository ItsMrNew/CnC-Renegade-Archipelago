"""Private native HUD table. Its timers come from the game's playable-time clock."""
import math
import struct

from ..data import BUTTERFINGERS, ITEM_FLAGS, ITEM_COLOURS
from . import traps as t, services as s
from .savebuilder import BASE, STAR
from .profile import STEAM, UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32, LEASE_MS

REQUEST, RANGE = 0, 32
ACTIVE, STAGING = 128, 1152
ROW_SIZE, ROWS, PAYLOAD_SIZE = 96, 8, 800
TEXT_INSTANCE, TEXT_CTOR, TEXT_DRAW, SET_LOCATION = 0x44481C, 0x23BB00, 0x23C4A0, 0x2D850
ANCHORS = {
    TEXT_CTOR: bytes.fromhex('83ec085333db568bf153e811c8ffff'),
    TEXT_DRAW: bytes.fromhex('83ec1053568bf1d986b80500008b86bc050000'),
    SET_LOCATION: bytes.fromhex('8b4424048b108991b80500008b5004'),
    0x2D059: bytes.fromhex('6810060000e8ad410c0083c4043bc3740d8b560c8bc852e88bea2000'),
}


def table_payload(memory, reward, service, *, top=60):
    entries = []
    reloads = memory.u32(reward+t.RELOADS)
    if 0 < reloads <= 1000005:
        entries.append((BUTTERFINGERS, f'{reloads} reloads'))
    for offset, name in t.TIMERS.items():
        seconds = struct.unpack('<f', memory.read(reward+offset,4))[0]
        if math.isfinite(seconds) and 0 < seconds <= t.MAX_EFFECT_SECONDS:
            remaining = math.ceil(seconds)
            entries.append((name, f'{remaining//60}:{remaining%60:02d}'))
    raw = bytearray(PAYLOAD_SIZE)
    if not entries:
        return bytes(raw)
    paused = not memory.u32(service+s.PLAYABLE)
    title = 'AP EFFECTS (PAUSED)' if paused else 'AP EFFECTS'
    rows = [(f'{title:<20} REMAINING', 0xFFF3D884)]
    for name, value in entries:
        colour = int(ITEM_COLOURS[ITEM_FLAGS[name]][1:],16) | 0xFF000000
        rows.append((f'{name:<20} {value:>9}', colour))
    struct.pack_into('<I7f',raw,0,len(rows),372,top,630,top+8+len(rows)*14,380,top+6,top+6)
    for i,(text,colour) in enumerate(rows):
        offset = 32+i*ROW_SIZE
        struct.pack_into('<I',raw,offset,colour)
        encoded = (text+'\0').encode('utf-16-le')
        raw[offset+4:offset+4+len(encoded)] = encoded
    return bytes(raw)


def build_hud(address, control, reward, service, *, bindings=None, addresses=None,
              star=BASE+STAR, camera=BASE+s.COMBAT_CAMERA, font_instance=BASE+TEXT_INSTANCE):
    c = Code(address)
    def native(rva): c.jump(b'\xe8',(bindings or {}).get(rva,BASE+rva))
    tick=addresses.tick_iat if addresses else BASE+STEAM.tick_iat
    mode=addresses.game_mode if addresses else BASE+STEAM.game_mode
    pending=addresses.pending_continue if addresses else BASE+STEAM.pending_continue
    commands=addresses.commands if addresses else BASE+STEAM.commands
    c.emit(b'\x9c\x60\xfc')
    for off in (s.ENABLED, s.ALIVE):
        c.emit(b'\x83\x3d'+u32(service+off)+b'\x01'); c.jump(b'\x0f\x85','done')
    c.emit(b'\xff\x15'+u32(tick)+b'\x2b\x05'+u32(service+s.HEARTBEAT)+b'\x3d'+u32(LEASE_MS))
    c.jump(b'\x0f\x87','done')
    c.emit(b'\x83\x3d'+u32(mode)+b'\x01');c.jump(b'\x0f\x85','done')
    c.emit(b'\x80\x3d'+u32(pending)+b'\x00');c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x3b\x05'+u32(commands+0x204));c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\x3b\x05'+u32(reward+t.GENERATION));c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(star)+b'\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x40\x04\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x3b\x05'+u32(reward+t.OWNER));c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x15'+u32(camera)+b'\x85\xd2');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\xba'+u32(s.CAMERA_HOST)+b'\x00');c.jump(b'\x0f\x85','done')
    c.emit(b'\x83\x3d'+u32(control+REQUEST)+b'\x01');c.jump(b'\x0f\x85','current')
    # Worker writes only STAGING while REQUEST=0; only the render thread copies
    # it to ACTIVE. Drawing never races a changing text or colour buffer.
    c.emit(b'\xbe'+u32(control+STAGING)+b'\xbf'+u32(control+ACTIVE)+b'\xb9'+u32(PAYLOAD_SIZE//4)+b'\xf3\xa5')
    c.emit(b'\xc7\x05'+u32(control+REQUEST)+u32(0))
    c.label('current')
    c.emit(b'\x8b\x2d'+u32(control+ACTIVE)+b'\x85\xed');c.jump(b'\x0f\x84','done')
    c.emit(b'\x83\xfd'+bytes([ROWS]));c.jump(b'\x0f\x87','done')
    c.emit(b'\xa1'+u32(font_instance)+b'\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x58\x0c\x85\xdb');c.jump(b'\x0f\x84','done')  # Stock monospace font.
    # Private untextured translucent panel; destroy both renderers this frame.
    c.emit(b'\x68'+u32(0x5B4));native(t.NEW);c.emit(b'\x83\xc4\x04\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x6a\x00\x89\xc1');native(t.RENDER_CTOR);c.emit(b'\x89\xc7')
    c.emit(b'\x89\xf9\x68'+u32(control+RANGE));native(t.SET_RANGE)
    c.emit(b'\x89\xf9\x6a\x01');native(t.ALPHA)
    c.emit(b'\x89\xf9\x68'+u32(0xC018222C)+b'\x68'+u32(control+ACTIVE+4));native(t.ADD_QUAD)
    c.emit(b'\x89\xf9');native(t.DRAW)
    c.emit(b'\x89\xf9\x8b\x07\x6a\x01\xff\x10')
    c.emit(b'\x68'+u32(0x610));native(t.NEW);c.emit(b'\x83\xc4\x04\x85\xc0');c.jump(b'\x0f\x84','done')
    c.emit(b'\x53\x89\xc1');native(TEXT_CTOR);c.emit(b'\x89\xc7')
    c.emit(b'\x89\xf9\x68'+u32(control+RANGE));native(t.SET_RANGE)
    c.emit(b'\xbe'+u32(control+ACTIVE+32))
    c.label('row')
    c.emit(b'\x89\xf9\x68'+u32(control+ACTIVE+20));native(SET_LOCATION)
    c.emit(b'\xff\x36\x8d\x46\x04\x50\x89\xf9');native(TEXT_DRAW)
    c.emit(b'\xd9\x05'+u32(control+ACTIVE+24)+b'\xd8\x05'+u32(control+RANGE+16)+b'\xd9\x1d'+u32(control+ACTIVE+24))
    c.emit(b'\x83\xc6'+bytes([ROW_SIZE])+b'\x4d');c.jump(b'\x0f\x85','row')
    c.emit(b'\xa1'+u32(control+ACTIVE+28)+b'\xa3'+u32(control+ACTIVE+24))
    c.emit(b'\x89\xf9');native(t.DRAW)
    c.emit(b'\x89\xf9\x8b\x07\x6a\x01\xff\x10')
    c.label('done');c.emit(b'\x61\x9d\xc3')
    return c.finish()


class NativeEffectsHUD:
    def __init__(self, memory, reward, service):
        self.memory, self.reward, self.service = memory, reward, service
        for rva,expected in ANCHORS.items():
            if memory.read(BASE+rva,len(expected)) != expected:
                raise UnsupportedBuild(f'Effects HUD anchor differs at {rva:#x}')
        self.control = memory.allocate()
        memory.write(self.control,b'\0'*4096)
        memory.write(self.control+RANGE,struct.pack('<5f',0,0,640,480,14))
        self.code = self.control+CODE_OFFSET
        code = build_hud(self.code,self.control,reward,service)
        memory.write(self.code,code)
        if memory.read(self.code,len(code)) != code:
            raise OSError('Effects HUD read-back failed')
        memory.make_executable(self.code)

    def update(self):
        if not self.memory.u32(self.control+REQUEST):
            self.memory.write(self.control+STAGING,table_payload(self.memory,self.reward,self.service,
                top=getattr(self,'table_top',60)))
            self.memory.write_u32(self.control+REQUEST,1)
