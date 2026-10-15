"""Stock data-disc pickups, independent of EVA shroud reveal.

Observe the verified PowerUp::Think -> Grant call, after native Grant succeeds.
Never infer collection from fog, absent objects, or mission completion. The
priest reward has a variable ID, so match its exact stock observer name instead.
"""
import logging

from ..data import MISSIONS, DATA_DISCS, BLACK_HAND_INTEL, disc_id
from . import services as s
from .bonuses import call_bytes
from .profile import UnsupportedBuild
from .memory import MemoryError
from .thunk import Code, u32, CODE_OFFSET, LEASE_MS
from .savebuilder import BASE, STAR

DISC_DEFINITION = 0x04E27593
GRANT, GRANT_CALL = 0x177660, 0x17780C
CLOUD_VECTOR = 0x45F76C  # MapMgr::CloudVector: 13 uint32s; zero means visible.
ENABLED, CAPTURED = 0, 64
PRIEST_SCRIPT = b'M01_Priest_Datadisc_JDG\0'
ANCHORS = {
    GRANT: bytes.fromhex('538b5c240856578bf16a015653e8fe9a02008bc8e897eeffff'),
    0x17769C: bytes.fromhex('83beb407000002752e8b86f806000033ff85c07e22'),
    0x177807: bytes.fromhex('8b4c241055e84ffeffff5f5e5d5b83c444c3'),
    0x1A1170: bytes.fromhex('8b81bc060000c3'),
    0x1AAA30: bytes.fromhex('57b90d00000033c0bf6cf78500f3abe8cc8df8ff5fc3'),
}
LOG = logging.getLogger('Renegade.Services')


def guard(c, control, service, end, pickup=True):
    addresses = (service+s.ENABLED, service+s.PLAYABLE, service+s.ALIVE)
    if pickup: addresses += (control+ENABLED,)
    for address in addresses:
        c.emit(b'\x83\x3d'+u32(address)+b'\x01'); c.jump(b'\x0f\x85', end)
    c.emit(b'\xff\x15'+u32(BASE+s.STEAM.tick_iat))
    c.emit(b'\x2b\x05'+u32(service+s.HEARTBEAT)+b'\x3d'+u32(LEASE_MS)); c.jump(b'\x0f\x87', end)
    c.emit(b'\x83\x3d'+u32(BASE+s.STEAM.game_mode)+b'\x01'); c.jump(b'\x0f\x85', end)
    c.emit(b'\xa1'+u32(BASE+s.STEAM.game_data)+b'\x85\xc0'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x83\x78\x04\x00'); c.jump(b'\x0f\x85', end)  # Active mode.
    c.emit(b'\x80\x3d'+u32(BASE+s.STEAM.pending_continue)+b'\x00'); c.jump(b'\x0f\x85', end)
    c.emit(b'\x8b\x3d'+u32(service+s.MAP_INDEX)+b'\x83\xff\x0c'); c.jump(b'\x0f\x83', end)
    c.emit(b'\x0f\xa3\x3d'+u32(service+s.ALLOWED)); c.jump(b'\x0f\x83', end)
    # The service snapshot must still refer to the current loaded map and star.
    c.emit(b'\xa1'+u32(BASE+STAR)+b'\x85\xc0'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x8b\x40\x04\x85\xc0'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x3b\x05'+u32(service+s.PLAYER)); c.jump(b'\x0f\x85', end)
    c.emit(b'\x8b\x15'+u32(BASE+s.STEAM.loaded_map)+b'\x85\xd2'); c.jump(b'\x0f\x84', end)
    c.emit(b'\x8b\x02\x0d'+u32(0x20202020))
    for i,m in enumerate(MISSIONS):
        c.emit(b'\x83\xff'+bytes([i])); c.jump(b'\x0f\x85', 'guard_next'+str(i))
        c.emit(b'\x3d'+(m.map_name.lower()+'.').encode()); c.jump(b'\x0f\x85', end)
        c.jump(b'\xe9', 'guard_suffix')
        c.label('guard_next'+str(i))
    c.jump(b'\xe9',end)
    c.label('guard_suffix')
    c.emit(b'\x8b\x42\x04\x0d'+u32(0x00202020)+b'\x3d'+b'mix\0')
    c.jump(b'\x0f\x84', 'guard_ok')
    c.emit(b'\x3d'+b'lsd\0'); c.jump(b'\x0f\x85', end)
    c.label('guard_ok')


def build_reveal(address, control, service, *, guard_builder=guard, cloud_vector=BASE+CLOUD_VECTOR):
    c=Code(address); c.emit(b'\x9c\x60\xfc')
    guard_builder(c, control, service, 'done', pickup=False)
    # Exactly Clear_All_Cloud_Cells, without repeated HUD notifications. No
    # Grant, Custom, encyclopaedia or objective call occurs here.
    c.emit(b'\x31\xc0\xb9'+u32(13)+b'\xbf'+u32(cloud_vector)+b'\xf3\xab')
    c.label('done'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


def build_pickup(address, control, service, *, guard_builder=guard, grant=BASE+GRANT,
                 checkpoint=True):
    c=Code(address)
    # Preserve this while native Grant (thiscall, one argument) and all stock
    # observers run. Objects marked for deletion remain valid until Think ends.
    c.emit(b'\x55\x89\xe5\x83\xec\x04\x89\x4d\xfc\xff\x75\x08')
    c.jump(b'\xe8',grant)
    c.emit(b'\x9c\x60\xfc')
    guard_builder(c,control,service,'done')
    c.emit(b'\x8b\x45\x08\x3b\x05'+u32(service+s.PLAYER)); c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x75\xfc\x81\xbe\xb4\x07\x00\x00'+u32(2)); c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x8e\xbc\x06\x00\x00\x85\xc9'); c.jump(b'\x0f\x84','done')
    c.emit(b'\x8b\x11\xff\x52\x24\x3d'+u32(DISC_DEFINITION)); c.jump(b'\x0f\x85','done')
    c.emit(b'\x8b\x46\x0c')  # Persistent stock object ID.
    for i,m in enumerate(MISSIONS):
        ids=DATA_DISCS[m.map_name]
        if not ids: continue
        c.emit(b'\x83\xff'+bytes([i])); c.jump(b'\x0f\x85',f'next{i}')
        for bit,oid in enumerate(ids):
            if oid <= 0: continue  # Scripted rewards never match a placed object ID.
            c.emit(b'\x3d'+u32(oid)); c.jump(b'\x0f\x84',f'capture{i}_{bit}')
        if m.map_name=='M01':
            c.emit(b'\x8b\x9e\xf0\x06\x00\x00\x8b\xbe\xf8\x06\x00\x00')
            c.emit(b'\x85\xdb'); c.jump(b'\x0f\x84','done')
            c.emit(b'\x83\xff\x20'); c.jump(b'\x0f\x87','done')
            c.label('observer')
            c.emit(b'\x85\xff'); c.jump(b'\x0f\x84','done')
            c.emit(b'\x8b\x0b\x85\xc9'); c.jump(b'\x0f\x84','observer_next')
            c.emit(b'\x8b\x11\xff\x52\x04\x85\xc0'); c.jump(b'\x0f\x84','observer_next')
            # Byte comparisons stop at the first difference, including NUL;
            # never read beyond a shorter observer name's terminating byte.
            for offset,value in enumerate(PRIEST_SCRIPT):
                c.emit(b'\x80\x78'+bytes([offset,value])); c.jump(b'\x0f\x85','observer_next')
            c.jump(b'\xe9',f'capture{i}_{ids.index(0)}')
            c.label('observer_next'); c.emit(b'\x83\xc3\x04\x4f'); c.jump(b'\xe9','observer')
        c.jump(b'\xe9','done')
        for bit,oid in enumerate(ids):
            c.label(f'capture{i}_{bit}')
            c.emit(b'\xf0\x81\x0d'+u32(control+CAPTURED+i*4)+u32(1<<bit))
            # Never save inside PowerUp::Grant. Its observers may still be
            # changing the world; the next controllable frame performs the save.
            if checkpoint:
                c.emit(b'\xa1'+u32(service+s.GENERATION)+b'\xa3'+u32(service+s.DISC_GENERATION))
                c.emit(b'\xc7\x05'+u32(service+s.DISC_EVENT)+u32(1))
            c.jump(b'\xe9','done')
        c.label(f'next{i}')
    c.label('done'); c.emit(b'\x61\x9d\x89\xec\x5d\xc2\x04\x00')
    return c.finish()


class NativeDiscs:
    def __init__(self,memory,service):
        self.memory=memory
        for rva,expected in ANCHORS.items():
            if memory.read(BASE+rva,len(expected))!=expected:
                raise UnsupportedBuild(f'Data disc anchor differs at {rva:#x}')
        self.control=memory.allocate()
        memory.write(self.control,b'\0'*4096)
        self.code=self.control+CODE_OFFSET
        self.reveal=memory.allocate()+CODE_OFFSET
        for address,program in ((self.code,build_pickup(self.code,self.control,service)),
                                (self.reveal,build_reveal(self.reveal,self.control,service))):
            if len(program)>4096: raise ValueError('Disc observer exceeds code page')
            memory.write(address,program)
            if memory.read(address,len(program))!=program: raise MemoryError('Disc observer read-back failed')
            memory.make_executable(address)
        self.original=call_bytes(BASE+GRANT_CALL,BASE+GRANT)
        self.replacement=call_bytes(BASE+GRANT_CALL,self.code)
        from .intel import IntelHook
        self.intel = IntelHook(memory, self.control, service)

    def update(self,client):
        enabled=client.capture_ready and client.config.get('data_discs',False)
        current=self.memory.read(BASE+GRANT_CALL,5)
        if current not in (self.original,self.replacement):
            raise MemoryError('Another hook owns the data-disc pickup callback')
        if enabled and current==self.original:
            self.memory.replace_calls([(BASE+GRANT_CALL,self.original,self.replacement)])
        self.memory.write_u32(self.control+ENABLED,int(enabled))
        self.intel.update(enabled and disc_id('M13', BLACK_HAND_INTEL) in client.locations)

    def close(self):
        self.memory.write_u32(self.control+ENABLED,0)
        self.intel.close()
        if self.memory.read(BASE+GRANT_CALL,5)==self.replacement:
            self.memory.replace_calls([(BASE+GRANT_CALL,self.replacement,self.original)])


class DiscCapture:
    def __init__(self):
        self.pending=set()
        self.errors={}

    def collect(self,runtime):
        for i,m in enumerate(MISSIONS):
            mask=runtime.memory.u32(runtime.discs.control+CAPTURED+i*4)
            ids=DATA_DISCS[m.map_name]
            if mask>>len(ids): raise MemoryError('Corrupt data-disc capture evidence')
            self.pending.update((m.map_name,oid) for bit,oid in enumerate(ids) if mask&(1<<bit))

    def flush(self,client):
        if not client.capture_ready: return
        for key in sorted(self.pending):
            if not client.config.get('data_discs',False) or disc_id(*key) not in client.locations:
                self.pending.discard(key); continue
            try:
                if client.disc_collected(*key) or disc_id(*key) in client.progress.completed:
                    self.pending.discard(key); self.errors.pop(key,None)
            except OSError as error:
                if self.errors.get(key)!=str(error):
                    LOG.error('Could not persist data disc %s; retaining for retry: %s',key,error)
                    self.errors[key]=str(error)
