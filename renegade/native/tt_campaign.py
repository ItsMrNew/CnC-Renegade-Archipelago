"""TT b9000 saves and menu loading, with a separate native/Python handoff.

TT Save_Game is void (and variadic), unlike the stock binding. Success is
established by validating the resulting file, never by interpreting EAX.
"""
import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

from ..data import MISSIONS
from . import services as s
from .checkpoints import checkpoint_name, publish_checkpoint
from .mission_launch import MissionLauncher, build_loader
from . import mission_launch as launch
from .profile import UnsupportedBuild
from .thunk import Code, CODE_OFFSET, u32
from .tt_discs import guard, STAR, CAMERA
from .tt_inventory import install_code

LOG = logging.getLogger('Renegade.TT.Checkpoints')
SAVE, SAVE_SYSTEM, FILE_FACTORY = 0x22B950, 0x5F82A4, 0x5F83AC
MENU_SLOT, MENU_THINK, START_GAME, INIT_SP = 0x2F326C, 0x2ADDE0, 0x2A6C50, 0x4050F0
ENABLED, BUSY, GENERATION, BASELINED, LAST_MASK, SEEN, PENDING, READY, SAVED_MAP, SERIAL = range(0, 40, 4)


def jump(site, target):
    return b'\xe9'+u32((target-site-5) & 0xFFFFFFFF)


def expected_anchors(a):
    b = a.band_base
    return {
        b+SAVE: bytes.fromhex('558bec83e4f881ec1c0c00005356578b7d0885ff7445'),
        b+0x22BE03: bytes.fromhex('8b750c85f674438d7d0c8d49008b068bce8b400c'),
        b+0x22BE6E: bytes.fromhex('5f5e5b8be55dc3'),
        # Native autosave pushes a null terminator, then the save subsystem.
        b+0x2A185B: b'\x6a\x00\xff\x35'+u32(b+SAVE_SYSTEM)+b'\x68'+u32(b+0x2F2C28),
        b+START_GAME: bytes.fromhex('558bec83e4f85153565768')+u32(b+0x2D0470),
        INIT_SP: bytes.fromhex('51a164c681005333db3bc30f848b0000004883f8037728'),
        0x429800: bytes.fromhex('568bf18b460885c0746d8b06ff502084c0743d8b4e088b11ff92c4000000'),
        0x6A35C0: jump(0x6A35C0,b+SAVE),
        0x404690: jump(0x404690,b+START_GAME),
        b+0xC9E0: bytes.fromhex('558bec83e4f883ec3c538bd956807b1c008b750857'),
    }


def verify_campaign(memory, a):
    for address, expected in expected_anchors(a).items():
        if memory.read(address,len(expected)) != expected:
            raise UnsupportedBuild(f'TT campaign anchor differs at {address:#x}')
    b = a.band_base
    # TT resolves this forwarding stub on first use by replacing its first five
    # bytes with a jump. Both complete forms are known; third-party detours fail.
    original = (b'\x51\x52\x68'+u32(b+MENU_THINK)+b'\x6a\x00\x68'+u32(0x429980)
        +b'\x68'+u32(0x429800)+b'\xe8'+u32((b+0x2947B0-(b+MENU_THINK+24)) & 0xFFFFFFFF)
        +b'\x83\xc4\x10\x5a\x59\xff\xe0\xcc')
    resolved = jump(b+MENU_THINK,0x429800)+original[5:]
    if memory.read(b+MENU_THINK,len(original)) not in (original,resolved):
        raise UnsupportedBuild('TT menu forwarding stub differs')
    if memory.u32(b+SAVE_SYSTEM) != 0x81C648:
        raise UnsupportedBuild('TT campaign save subsystem differs')


def save_directory(memory, a):
    """Read the active save factory's std::string, including its short form."""
    factory = memory.u32(memory.u32(a.band_base+FILE_FACTORY))
    if memory.u32(factory) != a.band_base+0x2CDBB4:
        raise UnsupportedBuild('TT save file factory differs')
    length, capacity = memory.u32(factory+20), memory.u32(factory+24)
    if not 0 < length <= 259 or capacity < length:
        raise UnsupportedBuild('TT save directory is invalid')
    string = factory+4 if capacity < 16 else memory.u32(factory+4)
    raw = memory.read(string,length+1)
    if raw[-1:] != b'\0' or b'\0' in raw[:-1]:
        raise UnsupportedBuild('TT save directory is unterminated')
    value = raw[:-1].decode('mbcs')
    if any(c in value for c in ';\r\n'):
        raise UnsupportedBuild('TT save factory has multiple or invalid directories')
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise UnsupportedBuild('TT save directory is unavailable')
    result = path.resolve()/'save'
    result.mkdir(exist_ok=True)
    return result


def build_checkpoint(control, service, a, input_target=None):
    """EDI=map, EBP=current accomplished mask, called after the TT scanner."""
    c = Code(control+CODE_OFFSET)
    def store(off, value): c.emit(b'\xc7\x05'+u32(control+off)+u32(value))
    c.emit(b'\x9c\x60')
    for off, value in ((ENABLED,1),(BUSY,0)):
        c.emit(b'\x83\x3d'+u32(control+off)+bytes([value])); c.jump(b'\x0f\x85','done')
    guard(c,control,service,'done',pickup=False,addresses=a)
    c.emit(b'\x83\x3d'+u32(service+s.ALIVE)+b'\x01'); c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(service+s.OBSERVER)+b'\x85\xc0'); c.jump(b'\x0f\x84','done')
    c.emit(b'\x3b\x05'+u32(a.commands+0x204)); c.jump(b'\x0f\x85','done')
    c.emit(b'\xa1'+u32(service+s.GENERATION))
    c.emit(b'\x83\x3d'+u32(control+BASELINED)+b'\x01'); c.jump(b'\x0f\x85','baseline')
    c.emit(b'\x3b\x05'+u32(control+GENERATION)); c.jump(b'\x0f\x84','events')
    c.label('baseline')
    c.emit(b'\xa3'+u32(control+GENERATION)+b'\x89\x2d'+u32(control+LAST_MASK))
    c.emit(b'\x89\x2d'+u32(control+SEEN))
    store(BASELINED,1); store(PENDING,0)
    # Loaded-save objectives still reach AP, but cannot replace a newer save.
    for i in range(12):
        c.emit(b'\xc7\x05'+u32(service+s.OBJECTIVE_EVENTS+i*4)+u32(0))
    c.emit(b'\xc7\x05'+u32(service+s.DISC_EVENT)+u32(0))
    c.jump(b'\xe9','done')
    c.label('events')
    c.emit(b'\x31\xd2\x87\x14\xbd'+u32(service+s.OBJECTIVE_EVENTS))
    c.emit(b'\x8b\x0d'+u32(control+SEEN)+b'\xf7\xd1\x21\xca')  # new callback bits
    c.emit(b'\x09\x15'+u32(control+SEEN))
    c.emit(b'\xa1'+u32(control+SEEN)+b'\xf7\xd0\x21\xe8\x09\xc2')
    c.emit(b'\x09\x2d'+u32(control+SEEN))
    c.emit(b'\x89\x2d'+u32(control+LAST_MASK))
    c.emit(b'\x31\xc0\x87\x05'+u32(service+s.DISC_EVENT))
    c.emit(b'\x8b\x0d'+u32(service+s.GENERATION)+b'\x3b\x0d'+u32(service+s.DISC_GENERATION))
    c.jump(b'\x0f\x85','disc_done'); c.emit(b'\x09\xc2')
    c.label('disc_done')
    c.emit(b'\x09\x15'+u32(control+PENDING))
    c.emit(b'\x83\x3d'+u32(control+PENDING)+b'\x00'); c.jump(b'\x0f\x84','done')
    # Do not overwrite the stage while Python validates/publishes it. New
    # completions remain pending, including while a publication is retried.
    c.emit(b'\x83\x3d'+u32(control+READY)+b'\x00'); c.jump(b'\x0f\x85','done')
    store(BUSY,1); store(PENDING,0)
    c.emit(b'\x89\x3d'+u32(control+SAVED_MAP))
    c.emit(b'\x8b\xc7\xc1\xe0\x06\x05'+u32(service+s.FILES))
    c.emit(b'\xfc\x6a\x00\xff\x35'+u32(a.band_base+SAVE_SYSTEM)+b'\x50')
    c.jump(b'\xe8',a.band_base+SAVE); c.emit(b'\x83\xc4\x0c')
    if input_target is not None:
        c.jump(b'\xe8',input_target)
    c.emit(b'\xff\x05'+u32(control+SERIAL))
    store(READY,1); store(BUSY,0)
    c.label('done'); c.emit(b'\x61\x9d\xc3')
    return c.finish()


def combat_instance(memory, a):
    node, seen = memory.u32(0x81E854), set()
    for _ in range(32):
        if not node or node in seen:
            break
        seen.add(node)
        obj = memory.u32(node+4)
        if obj and memory.u32(obj) == a.band_base+0x2F32A8:
            return obj
        node = memory.u32(node)
    raise UnsupportedBuild('TT mission loader cannot find the combat mode')


def build_transition(address, control, a, combat, checkpoint=None):
    return launch.build_transition(address, control, a, combat, star=STAR, camera=CAMERA,
        checkpoint_busy=(checkpoint+BUSY, checkpoint+READY) if checkpoint is not None else ())


class TTLauncher(MissionLauncher):
    can_queue = True

    def __init__(self, memory, save_dir, token, a, checkpoint=None):
        self.memory, self.save_dir, self.token = memory, save_dir, token
        self.menu_slot, self.menu_think = a.band_base+MENU_SLOT, a.band_base+MENU_THINK
        self.game_mode = a.game_mode
        self.combat = combat_instance(memory,a)
        if memory.u32(self.menu_slot) != self.menu_think:
            raise UnsupportedBuild('Another client owns the TT menu callback')
        self.control = memory.allocate()
        memory.write(self.control,b'\0'*4096)
        addresses = SimpleNamespace(tick_iat=a.tick_iat,game_mode=a.game_mode,
            init_sp=INIT_SP,start_game=a.band_base+START_GAME,menu_think=self.menu_think,message_count=a.message_count)
        program = build_loader(self.control,addresses=addresses,combat_object=self.combat)
        if len(program) > 1024:
            raise ValueError('TT menu loader overlaps transition code')
        self.transition = self.control+CODE_OFFSET+1024
        program = program.ljust(1024,b'\xcc') + build_transition(self.transition,self.control,a,self.combat,checkpoint)
        # Protect the single aligned code page. Protecting 4096 bytes starting
        # at the second entry point would run beyond this 8192-byte allocation.
        install_code(memory,self.control+CODE_OFFSET,program)
        self.pending = self.confirmation = None
        self.submitted = 0

    def at_menu(self):
        # GameInitMgr's mode describes the session, not the visible dialog.
        return self.memory.u32(self.combat+4) == 1



class NativeCampaign:
    def __init__(self, memory, service, a, identity):
        verify_campaign(memory,a)
        self.memory, self.identity = memory, identity
        self.save_dir = save_directory(memory,a)
        self.token = hashlib.sha256(repr(identity).encode()).hexdigest()[:12]
        self.control = memory.allocate()
        memory.write(self.control,b'\0'*4096)
        self.code = self.control+CODE_OFFSET
        self.error = None
        self.published = []
        for index, mission in enumerate(MISSIONS):
            name = ('save\\'+checkpoint_name(self.token,mission.map_name)+'.apstage\0').encode('ascii')
            if len(name)>64:
                raise ValueError('TT checkpoint filename is too long')
            memory.write(service+s.FILES+index*64,name)
        from .checkpoint_input import CheckpointInput
        self.checkpoint_input = CheckpointInput(memory,tt=True)
        install_code(memory,self.code,build_checkpoint(self.control,service,a,self.checkpoint_input.code))
        self.launcher = TTLauncher(memory,self.save_dir,self.token,a,self.control)

    def publish(self):
        mem, ctrl = self.memory, self.control
        if mem.u32(ctrl+BUSY) or not mem.u32(ctrl+READY):
            return
        try:
            serial, index = mem.u32(ctrl+SERIAL), mem.u32(ctrl+SAVED_MAP)
            if index >= len(MISSIONS):
                raise ValueError('Invalid TT checkpoint mission')
            name = MISSIONS[index].map_name
            source = self.save_dir/(checkpoint_name(self.token,name)+'.apstage')
            raw = source.read_bytes()
            if mem.u32(ctrl+BUSY) or mem.u32(ctrl+SERIAL) != serial:
                return
            path = publish_checkpoint(self.save_dir,self.token,name,raw)
            mem.write_u32(ctrl+READY,0)
            self.error = None
            self.published.append(path.name)
            self.published[:] = self.published[-12:]
            LOG.info('TT checkpoint updated: %s',path.name)
        except (OSError,ValueError) as error:
            if str(error) != self.error:
                LOG.error('TT checkpoint: %s; keeping previous checkpoint and retrying publication',error)
                self.error = str(error)

    def update(self, client):
        if client.identity != self.identity:
            raise ValueError('Refusing to transfer TT saves to another AP room')
        self.publish()
        self.launcher.update(client)
        self.memory.write_u32(self.control+ENABLED,int(client.capture_ready))

    def close(self):
        self.memory.write_u32(self.control+ENABLED,0)
        self.launcher.close()
        self.publish()
