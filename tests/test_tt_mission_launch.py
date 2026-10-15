"""Run the queued TT exit/menu-load chain, including recursive native Think."""
from types import SimpleNamespace
import struct

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EFLAGS

from renegade.native import tt_campaign as t, mission_launch as l, gate, services as s
from renegade.native.thunk import Code, u32
from renegade.native.savefiles import saved_difficulty
from test_tt_discs import DiscEngine, MODE, CAMERA, PLAYER, POWERUP
from test_native import STACK, RETURN, TICK
from test_mission_launch import launcher, saved

CTRL, GATE, CHECKPOINT = 0x13000000, 0x14000000, 0x15000000


class LoaderEngine(DiscEngine):
    def __init__(self, band=0x60000000, state=0):
        super().__init__(band=band)
        self.events=[]
        for region in (CTRL,GATE,CHECKPOINT):self.uc.mem_map(region,8192)
        a=self.a
        self.binding=SimpleNamespace(tick_iat=a.tick_iat,game_mode=a.game_mode,
            init_sp=t.INIT_SP,start_game=a.band_base+t.START_GAME,
            menu_think=a.band_base+t.MENU_THINK,message_count=a.message_count)
        self.uc.mem_write(CTRL+4096,l.build_loader(CTRL,addresses=self.binding,combat_object=MODE))
        self.uc.mem_write(CTRL+5120,t.build_transition(CTRL+5120,CTRL,a,MODE,CHECKPOINT))
        self.uc.mem_write(GATE+4096,gate.build_gate(GATE,return_to_menu=True,addresses=a,
                                                  transition_target=CTRL+5120))
        self.put(GATE,0xFFF);self.put(GATE+4,9900)
        for off,val in ((l.REQUEST,1),(l.ALLOWED,2),(l.MAP,1),(l.HEARTBEAT,9900)):
            self.put(CTRL+off,val)
        self.put(MODE+4,state)
        self.put(POWERUP+4,0 if state in (1,3) else 1)
        self.put(MODE,MODE+64);self.put(MODE+64+16,TICK+128)
        # TickCount may clobber volatile registers. The wrapper must restore this.
        self.uc.mem_write(TICK,b'\x31\xc9\x31\xd2\xb8'+u32(10000)+b'\xc3')
        self.uc.mem_write(TICK+128,b'\xc7\x41\x04'+u32(3)+b'\xc3')
        # Real End_Game recursively invokes BOTH mode callbacks during teardown.
        code=Code(a.end_game)
        code.emit(b'\xb9'+u32(MODE));code.jump(b'\xe8',GATE+4096)
        code.emit(b'\xb9'+u32(POWERUP));code.jump(b'\xe8',CTRL+4096)
        code.emit(b'\xc7\x05'+u32(MODE+4)+u32(1)+b'\xc7\x05'+u32(POWERUP+4)+u32(0)+b'\xc3')
        self.uc.mem_write(a.end_game,code.finish())
        for address in (a.end_game_menu,self.binding.init_sp,self.binding.menu_think):
            self.uc.mem_write(address,b'\xc3')
        self.uc.mem_write(self.binding.start_game,
            b'\xc7\x05'+u32(MODE+4)+u32(0)+b'\xc7\x05'+u32(POWERUP+4)+u32(1)+b'\xc3')
        self.names={TICK+128:'suspend',a.end_game:'end',a.end_game_menu:'menu',
            self.binding.init_sp:'init',self.binding.start_game:'load',self.binding.menu_think:'menu_think'}
        def observe(uc,address,size,data):
            if address in self.names:
                self.events.append(self.names[address])
                if address!=self.binding.menu_think:
                    assert not uc.reg_read(UC_X86_REG_EFLAGS)&0x400
                if address==TICK+128:assert uc.reg_read(UC_X86_REG_ECX)==MODE
                if address==self.binding.start_game:
                    esp=uc.reg_read(UC_X86_REG_ESP)
                    assert struct.unpack('<3I',uc.mem_read(esp+4,12))==(CTRL+l.FILENAME,0xffffffff,0)
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def value(self,off):return int.from_bytes(self.uc.mem_read(CTRL+off,4),'little')

    def frame(self,menu=False):
        self.registers[UC_X86_REG_ECX]=POWERUP if menu else MODE
        for reg,value in self.registers.items():self.uc.reg_write(reg,value)
        self.put(STACK,RETURN)
        self.uc.emu_start(CTRL+4096 if menu else GATE+4096,RETURN,count=10000)
        assert self.uc.reg_read(UC_X86_REG_ESP)==STACK+4
        for reg,value in self.registers.items():
            if reg!=UC_X86_REG_ESP:assert self.uc.reg_read(reg)==value


@pytest.mark.parametrize('state',[0,3])
@pytest.mark.parametrize('band',[0x60000000,0x71000000])
def test_active_and_paused_combat_exit_once_then_menu_loads_without_mode_flag_reset(state,band):
    e=LoaderEngine(band,state)
    if state==3:
        e.frame(menu=True)  # Pause menu is not permission to load over live combat.
        assert e.events==['menu_think'] and e.value(l.REQUEST)==1
        e.events.clear()
    e.frame()
    assert e.events==['suspend','end','menu'] and not e.calls
    assert e.value(l.REQUEST)==1 and e.value(l.PHASE)==l.RETURNING and e.value(l.BUSY)==0
    e.frame(menu=True)
    assert e.events==['suspend','end','menu','init','load']
    assert e.value(l.RESULT)==1 and e.value(l.REQUEST)==0 and not e.calls
    e.frame()
    assert e.calls==['think']  # Ordinary combat resumes after the request.


@pytest.mark.parametrize('reason,phase',[
    ('cutscene',l.WAIT_CUTSCENE),('no_control',l.WAIT_CUTSCENE),
    ('dialog',l.WAIT_DIALOG),('save_busy',l.WAIT_SAVE),('save_ready',l.WAIT_SAVE),
    ('transition',l.WAIT_TRANSITION),('victory',l.WAIT_TRANSITION),
    ('no_player',l.WAIT_TRANSITION),('no_camera',l.WAIT_TRANSITION)])
def test_queued_load_defers_then_runs_when_native_guard_clears(reason,phase):
    e=LoaderEngine()
    changes={'cutscene':(CAMERA+s.CAMERA_HOST,1),'no_control':(PLAYER+s.CONTROL_ENABLED,0),
        'dialog':(e.a.message_count,1),'save_busy':(CHECKPOINT+t.BUSY,1),
        'save_ready':(CHECKPOINT+t.READY,1),'transition':(MODE+4,2),
        'victory':(e.a.pending_continue,1),'no_player':(t.STAR,0),'no_camera':(t.CAMERA,0)}
    addr,val=changes[reason];before=bytes(e.uc.mem_read(addr,4))
    e.put(addr,val);e.frame()
    assert not e.events and e.value(l.REQUEST)==1 and e.value(l.PHASE)==phase
    e.uc.mem_write(addr,before);e.frame();e.frame(menu=True)
    assert e.events==['suspend','end','menu','init','load'] and e.value(l.RESULT)==1


@pytest.mark.parametrize('reason',['stale','locked','invalid_map','cancelled','busy','multiplayer'])
def test_request_guards_prevent_native_quit(reason):
    e=LoaderEngine()
    changes={'stale':(CTRL+l.HEARTBEAT,0),'locked':(CTRL+l.ALLOWED,1),
        'invalid_map':(CTRL+l.MAP,12),'cancelled':(CTRL+l.REQUEST,0),
        'busy':(CTRL+l.BUSY,1),'multiplayer':(e.a.game_mode,2)}
    e.put(*changes[reason]);e.frame()
    assert not e.events
    assert e.value(l.RESULT)==(2 if reason in ('stale','locked','invalid_map') else 0)
    assert e.calls==([] if reason=='busy' else ['think'])


def test_main_menu_load_waits_for_message_and_still_checks_unlock():
    e=LoaderEngine(state=1)
    e.put(e.a.message_count,1);e.frame(menu=True)
    assert e.value(l.REQUEST)==1 and e.value(l.PHASE)==l.WAIT_DIALOG
    e.put(e.a.message_count,0);e.put(CTRL+l.ALLOWED,1);e.frame(menu=True)
    assert 'load' not in e.events and e.value(l.RESULT)==2


def queued_launcher(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    runtime.__class__=t.TTLauncher;runtime.combat=MODE
    data[MODE+4]=3  # Paused in a mission, not at the main menu.
    return runtime,client,data,writes,checkpoint


def test_continue_pins_checkpoint_and_room_drop_cancels_queued_load(tmp_path):
    runtime,client,data,writes,checkpoint=queued_launcher(tmp_path)
    original=checkpoint.read_bytes()
    client.mission_request=('M01',1,True);runtime.update(client)
    pinned=tmp_path/'ap_load_room.sav'
    assert pinned.read_bytes()==original and client.mission_launch['can_queue']
    checkpoint.write_bytes(saved(difficulty=2))
    runtime.update(client)
    assert pinned.read_bytes()==original and runtime.pending
    client.ready=False;runtime.update(client)
    assert runtime.pending is None and not data[runtime.control+l.REQUEST]
    client.ready=True;runtime.update(client)
    assert not data[runtime.control+l.REQUEST]  # Reconnect must not revive it.


def test_paused_difficulty_change_waits_for_confirmation_before_any_quit(tmp_path):
    runtime,client,data,writes,checkpoint=queued_launcher(tmp_path)
    client.mission_request=('M01',2,True);runtime.update(client)
    assert client.mission_confirmation and not data.get(runtime.control+l.REQUEST)
    assert not (tmp_path/'ap_load_room.sav').exists()
    client.mission_request=('M01',2,True,client.mission_confirmation['id']);runtime.update(client)
    assert data[runtime.control+l.REQUEST]==1
    assert saved_difficulty((tmp_path/'ap_load_room.sav').read_bytes())==2


def test_invalid_or_changed_checkpoint_does_not_queue_or_leave_current_mission(tmp_path):
    runtime,client,data,writes,checkpoint=queued_launcher(tmp_path)
    client.mission_request=('M01',1,True,None,dict(name=checkpoint.name,fingerprint='changed'))
    runtime.update(client)
    assert not data.get(runtime.control+l.REQUEST) and runtime.pending is None
    assert not (tmp_path/'ap_load_room.sav').exists()


def test_launcher_installs_both_entry_points_inside_one_protected_page(tmp_path):
    from test_tt_native import Memory
    mem=Memory();a=mem.a
    mem.write_u32(0x81E854,0x22000000);mem.write_u32(0x22000004,MODE)
    mem.write_u32(MODE,a.band_base+0x2F32A8)
    mem.write_u32(a.band_base+t.MENU_SLOT,a.band_base+t.MENU_THINK)
    protected=[]
    def protect(address):
        assert address%4096==0
        protected.append(address)
    mem.make_executable=protect
    runtime=t.TTLauncher(mem,tmp_path,'testroom',a)
    assert protected==[runtime.control+4096]
    assert runtime.control+4096<runtime.transition<runtime.control+8192
    assert mem.read(runtime.transition,2)==b'\x9c\x60'
