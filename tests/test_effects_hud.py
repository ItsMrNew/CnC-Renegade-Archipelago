import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EAX

from renegade.native import effects_hud as h, traps as t, services as s
from test_bonuses import BonusEngine
from test_rewards import REWARD
from test_services import BASE, CONTROL, OBJECT

HUD, PANEL, TEXT, FONT = 0x27000000, 0x28000000, 0x28001000, 0x28002000


def setup(e, addresses=None, reward=REWARD, table_top=60):
    uc=e.machine
    binding={};options={};font_instance=BASE+h.TEXT_INSTANCE
    if addresses:
        from renegade.native.tt_effects_hud import bindings,FONT_INSTANCE
        from renegade.native.tt_discs import STAR,CAMERA
        binding=bindings(addresses);font_instance=FONT_INSTANCE
        options=dict(bindings=binding,addresses=addresses,star=STAR,camera=CAMERA,font_instance=font_instance)
    def native(rva):return binding.get(rva,BASE+rva)
    uc.mem_map(HUD,8192);uc.mem_map(PANEL,0x3000)
    uc.mem_write(HUD+h.CODE_OFFSET,h.build_hud(HUD+h.CODE_OFFSET,HUD,reward,CONTROL,**options))
    uc.mem_write(HUD+h.RANGE,struct.pack('<5f',0,0,640,480,14))
    e.put(font_instance,OBJECT+9000);e.put(OBJECT+9012,FONT)
    # Tests record native ABI arguments and renderer lifetime, not a substitute
    # for visual verification of the game's actual D3D draw path.
    for fun,argc in ((t.NEW,0),(t.RENDER_CTOR,4),(t.SET_RANGE,4),(t.ALPHA,4),
                     (t.ADD_QUAD,8),(t.DRAW,0),(h.TEXT_CTOR,4),(h.TEXT_DRAW,8),(h.SET_LOCATION,4)):
        uc.mem_write(native(fun),b'\xc2'+struct.pack('<H',argc) if argc else b'\xc3')
    destructor=BASE+0x380800
    uc.mem_write(destructor,b'\xc2\4\0')
    for ptr in (PANEL,TEXT):e.put(ptr,ptr+0x700);e.put(ptr+0x700,destructor)
    allocations=[];draws=[];deleted=[];lines=[];positions=[]
    def call(machine,address,size,data):
        esp=machine.reg_read(UC_X86_REG_ESP)
        def arg(i=0):return int.from_bytes(machine.mem_read(esp+4+4*i,4),'little')
        if address==native(t.NEW):
            allocations.append(arg());machine.reg_write(UC_X86_REG_EAX,PANEL if arg()==0x5b4 else TEXT)
        elif address in (native(t.RENDER_CTOR),native(h.TEXT_CTOR)):
            if address==native(h.TEXT_CTOR):assert arg()==FONT
            machine.reg_write(UC_X86_REG_EAX,machine.reg_read(UC_X86_REG_ECX))
        elif address==native(h.TEXT_DRAW):
            raw=bytes(machine.mem_read(arg(),92));text=raw.decode('utf-16-le').split('\0')[0]
            lines.append((text,arg(1)))
        elif address==native(h.SET_LOCATION):
            positions.append(struct.unpack('<2f',machine.mem_read(arg(),8)))
        elif address==native(t.ADD_QUAD):
            assert arg()==HUD+h.ACTIVE+4 and arg(1)==0xC018222C
        elif address==native(t.DRAW):draws.append(machine.reg_read(UC_X86_REG_ECX))
        elif address==destructor:
            assert arg()==1;deleted.append(machine.reg_read(UC_X86_REG_ECX))
    uc.hook_add(UC_HOOK_CODE,call)
    memory=SimpleNamespace(u32=lambda a:int.from_bytes(uc.mem_read(a,4),'little'),read=lambda a,n:bytes(uc.mem_read(a,n)))
    def publish():
        uc.mem_write(HUD+h.STAGING,h.table_payload(memory,reward,CONTROL,top=table_top));e.put(HUD+h.REQUEST,1)
    return publish,allocations,draws,deleted,lines,positions


def test_hud_draws_native_table_colours_and_native_remaining_time_without_leaking():
    e=BonusEngine();e.start(10);e.start(11);e.advance(7)
    publish,allocs,draws,deleted,lines,positions=setup(e)
    publish();e.invoke(HUD+h.CODE_OFFSET)
    assert allocs==[0x5b4,0x610] and draws==deleted==[PANEL,TEXT]
    assert lines==[('AP EFFECTS           REMAINING',0xfff3d884),
                   ('Moon Boots                0:53',0xfffa8072),('Bullet Hose               0:53',0xff00eeee)]
    assert positions==[(380,66),(380,80),(380,94)]
    assert int.from_bytes(e.machine.mem_read(HUD+h.REQUEST,4),'little')==0
    # Redraw without publishing must not walk the panel down the screen.
    e.invoke(HUD+h.CODE_OFFSET);assert positions[-3:]==positions[:3]


@pytest.mark.parametrize('reason',['menu','cinematic','dead','detached','load','stale','victory'])
def test_hud_hidden_when_it_does_not_describe_current_play(reason):
    e=BonusEngine();e.start(10);publish,allocs,*_=setup(e);publish()
    if reason=='menu':e.put(BASE+s.STEAM.game_mode,0)
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='dead':e.set(s.ALIVE,0)
    if reason=='detached':e.set(s.ENABLED,0)
    if reason=='load':e.set(s.GENERATION,99)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='victory':e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\1')
    e.invoke(HUD+h.CODE_OFFSET);assert not allocs


def test_hud_paused_label_and_empty_table():
    e=BonusEngine();e.start(10);e.put(OBJECT+4,1);e.frame()
    publish,allocs,_,_,lines,_=setup(e);publish();e.invoke(HUD+h.CODE_OFFSET)
    assert lines[0][0].startswith('AP EFFECTS (PAUSED)')
    e.machine.mem_write(REWARD+t.MOON_END,b'\0'*4);publish();allocs.clear()
    e.invoke(HUD+h.CODE_OFFSET);assert not allocs


def test_hud_staging_is_not_overwritten_until_render_acknowledges():
    data={h.REQUEST:1}
    writes=[]
    runtime=h.NativeEffectsHUD.__new__(h.NativeEffectsHUD)
    runtime.control=0;runtime.reward=REWARD;runtime.service=CONTROL
    runtime.memory=SimpleNamespace(u32=lambda a:data.get(a,0),write=lambda *a:writes.append(a))
    runtime.update();assert writes==[]
