"""Execute Steam's queued combat/menu chain, including recursive engine updates."""
import struct

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EFLAGS

from renegade.native import mission_launch as l, gate, services as s
from renegade.native.thunk import Code, u32, steam_addresses
from test_services import Engine, BASE, CONTROL, OBJECT, STACK, RETURN, TICK
from test_mission_launch import launcher, saved

CTRL, GATE = 0x13000000, 0x14000000
MENU, PLAYER, CAMERA = OBJECT+14000, OBJECT+768, OBJECT+8192


class LoaderEngine(Engine):
    def __init__(self, state=0):
        super().__init__()
        super().frame()
        self.calls.clear()
        self.uc=self.machine
        self.events=[]
        for region in (CTRL,GATE):self.uc.mem_map(region,8192)
        self.a=a=steam_addresses(BASE)
        a.init_sp=BASE+l.INIT_SP; a.start_game=BASE+l.START_GAME; a.menu_think=BASE+l.MENU_THINK
        self.uc.mem_write(CTRL+4096,l.build_loader(CTRL,combat_object=OBJECT))
        self.uc.mem_write(CTRL+5120,l.build_transition(CTRL+5120,CTRL,a,OBJECT,
            star=BASE+s.STAR,camera=BASE+s.COMBAT_CAMERA,checkpoint_busy=(CONTROL+s.BUSY,),
            checkpoint_serials=tuple((CONTROL+s.SAVES+i*4,CONTROL+s.PUBLISHED+i*4) for i in range(12))))
        self.uc.mem_write(GATE+4096,gate.build_gate(GATE,return_to_menu=True,transition_target=CTRL+5120))
        self.put(GATE,0xFFF);self.put(GATE+4,9900)
        for off,val in ((l.REQUEST,1),(l.ALLOWED,2),(l.MAP,1),(l.HEARTBEAT,9900)):
            self.put(CTRL+off,val)
        self.put(OBJECT+4,state);self.put(MENU+4,0 if state in (1,3) else 1)
        self.put(OBJECT,OBJECT+64);self.put(OBJECT+64+16,TICK+128)
        self.uc.mem_write(TICK,b'\x31\xc9\x31\xd2\xb8'+u32(10000)+b'\xc3')
        self.uc.mem_write(TICK+128,b'\xc7\x41\x04'+u32(3)+b'\xc3')
        code=Code(a.end_game)
        code.emit(b'\xb9'+u32(OBJECT));code.jump(b'\xe8',GATE+4096)
        code.emit(b'\xb9'+u32(MENU));code.jump(b'\xe8',CTRL+4096)
        code.emit(b'\xc7\x05'+u32(OBJECT+4)+u32(1)+b'\xc7\x05'+u32(MENU+4)+u32(0)+b'\xc3')
        self.uc.mem_write(a.end_game,code.finish())
        for address in (a.end_game_menu,a.init_sp,a.menu_think):self.uc.mem_write(address,b'\xc3')
        self.uc.mem_write(a.start_game,
            b'\xc7\x05'+u32(OBJECT+4)+u32(0)+b'\xc7\x05'+u32(MENU+4)+u32(1)+b'\xc3')
        names={TICK+128:'suspend',a.end_game:'end',a.end_game_menu:'menu',
               a.init_sp:'init',a.start_game:'load',a.menu_think:'menu_think'}
        def observe(uc,address,size,data):
            if address not in names:return
            self.events.append(names[address])
            if address!=a.menu_think:assert not uc.reg_read(UC_X86_REG_EFLAGS)&0x400
            if address==TICK+128:assert uc.reg_read(UC_X86_REG_ECX)==OBJECT
            if address==a.start_game:
                esp=uc.reg_read(UC_X86_REG_ESP)
                assert struct.unpack('<3I',uc.mem_read(esp+4,12))==(CTRL+l.FILENAME,0xffffffff,0)
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def value(self,off):return int.from_bytes(self.uc.mem_read(CTRL+off,4),'little')

    def frame(self,menu=False):
        self.registers[UC_X86_REG_ECX]=MENU if menu else OBJECT
        for reg,value in self.registers.items():self.uc.reg_write(reg,value)
        self.put(STACK,RETURN)
        self.uc.emu_start(CTRL+4096 if menu else GATE+4096,RETURN,count=10000)
        assert self.uc.reg_read(UC_X86_REG_ESP)==STACK+4
        for reg,value in self.registers.items():
            if reg!=UC_X86_REG_ESP:assert self.uc.reg_read(reg)==value


@pytest.mark.parametrize('state',[0,3])
def test_active_or_paused_steam_quits_once_and_loads_only_after_teardown(state):
    e=LoaderEngine(state)
    if state==3:
        e.frame(menu=True)
        assert e.events==['menu_think'] and e.value(l.REQUEST)==1
        e.events.clear()
    e.frame()
    assert e.events==['suspend','end','menu'] and not e.calls
    assert e.value(l.REQUEST)==1 and e.value(l.PHASE)==l.RETURNING
    e.frame(menu=True)
    assert e.events==['suspend','end','menu','init','load']
    assert e.value(l.RESULT)==1 and not e.value(l.REQUEST)
    e.frame();assert e.calls==['think']


@pytest.mark.parametrize('reason,phase',[
    ('cutscene',l.WAIT_CUTSCENE),('no_control',l.WAIT_CUTSCENE),('dialog',l.WAIT_DIALOG),
    ('saving',l.WAIT_SAVE),('unpublished',l.WAIT_SAVE),('victory',l.WAIT_TRANSITION),
    ('transition',l.WAIT_TRANSITION),('no_player',l.WAIT_TRANSITION),('no_camera',l.WAIT_TRANSITION)])
def test_steam_exit_waits_for_safe_state_and_checkpoint_publication(reason,phase):
    e=LoaderEngine()
    changes={'cutscene':(CAMERA+s.CAMERA_HOST,1),'no_control':(PLAYER+s.CONTROL_ENABLED,0),
        'dialog':(e.a.message_count,1),'saving':(CONTROL+s.BUSY,1),
        'unpublished':(CONTROL+s.SAVES+4,1),'victory':(e.a.pending_continue,1),
        'transition':(OBJECT+4,2),'no_player':(BASE+s.STAR,0),'no_camera':(BASE+s.COMBAT_CAMERA,0)}
    field,value=changes[reason];original=bytes(e.uc.mem_read(field,4))
    e.put(field,value);e.frame()
    assert not e.events and e.value(l.REQUEST)==1 and e.value(l.PHASE)==phase
    if reason=='unpublished':e.put(CONTROL+s.PUBLISHED+4,1)
    else:e.uc.mem_write(field,original)
    e.frame();e.frame(menu=True)
    assert e.events==['suspend','end','menu','init','load']


@pytest.mark.parametrize('reason',['stale','locked','invalid_map','cancelled','busy','multiplayer'])
def test_steam_request_guards_do_not_exit_or_load(reason):
    e=LoaderEngine()
    changes={'stale':(CTRL+l.HEARTBEAT,0),'locked':(CTRL+l.ALLOWED,1),
        'invalid_map':(CTRL+l.MAP,12),'cancelled':(CTRL+l.REQUEST,0),
        'busy':(CTRL+l.BUSY,1),'multiplayer':(e.a.game_mode,2)}
    e.put(*changes[reason]);e.frame()
    assert not e.events
    assert e.value(l.RESULT)==(2 if reason in ('stale','locked','invalid_map') else 0)


def test_steam_main_menu_waits_for_dialog_and_rechecks_unlock():
    e=LoaderEngine(1);e.put(e.a.message_count,1);e.frame(menu=True)
    assert e.value(l.REQUEST)==1 and e.value(l.PHASE)==l.WAIT_DIALOG
    e.put(e.a.message_count,0);e.put(CTRL+l.ALLOWED,1);e.frame(menu=True)
    assert 'load' not in e.events and e.value(l.RESULT)==2


def test_steam_continue_pins_save_during_rotation_and_cancels_on_drop(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    data[runtime.combat+4]=3
    client.mission_request=('M01',1,True);runtime.update(client)
    pinned=tmp_path/'ap_load_room.sav';original=checkpoint.read_bytes()
    assert pinned.read_bytes()==original and client.mission_launch['can_queue']
    checkpoint.write_bytes(saved(difficulty=2));runtime.update(client)
    assert pinned.read_bytes()==original and runtime.pending
    client.ready=False;runtime.update(client)
    assert runtime.pending is None and not data[runtime.control+l.REQUEST]
    client.ready=True;runtime.update(client)
    assert not data[runtime.control+l.REQUEST]


def test_steam_paused_restart_requires_confirmation_before_queuing(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    data[runtime.combat+4]=3
    client.mission_request=('M01',2,True);runtime.update(client)
    assert client.mission_confirmation and not data.get(runtime.control+l.REQUEST)
    assert not (tmp_path/'ap_load_room.sav').exists()
    client.mission_request=('M01',2,True,client.mission_confirmation['id']);runtime.update(client)
    assert data[runtime.control+l.REQUEST]==1
    assert l.saved_difficulty((tmp_path/'ap_load_room.sav').read_bytes())==2


@pytest.mark.parametrize('fault',[None,'anchor','missing','cycle','duplicate','hook'])
def test_steam_loader_verifies_binding_and_installs_one_code_page(tmp_path,fault):
    from test_gate import GateMemory
    mem=GateMemory()
    for rva,expected in l.ANCHORS.items():mem.write(BASE+rva,expected)
    mem.write_u32(BASE+l.MENU_SLOT,BASE+l.MENU_THINK)
    mem.write_u32(BASE+l.MODE_LIST,OBJECT+100)
    mem.write_u32(OBJECT+104,OBJECT);mem.write_u32(OBJECT,BASE+l.COMBAT_VTABLE)
    if fault=='anchor':mem.write(BASE+l.START_GAME,b'\0')
    elif fault=='missing':mem.write_u32(OBJECT,0)
    elif fault=='cycle':mem.write_u32(OBJECT+100,OBJECT+100)
    elif fault=='duplicate':
        mem.write_u32(OBJECT+100,OBJECT+200);mem.write_u32(OBJECT+204,OBJECT)
    elif fault=='hook':mem.write_u32(BASE+l.MENU_SLOT,123)
    if fault:
        with pytest.raises(l.UnsupportedBuild):l.MissionLauncher(mem,tmp_path,'test',CONTROL)
    else:
        runtime=l.MissionLauncher(mem,tmp_path,'test',CONTROL)
        assert runtime.control+4096<runtime.transition<runtime.control+8192
        assert mem.read(runtime.transition,2)==b'\x9c\x60'
        assert runtime.can_queue
