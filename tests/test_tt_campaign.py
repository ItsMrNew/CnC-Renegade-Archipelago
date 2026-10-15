"""Run the TT save/launch machine code and exercise durable publication."""
import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP

from renegade.data import MISSIONS
from renegade.native import tt_campaign as t, mission_launch as launch, services as s
from renegade.native.profile import UnsupportedBuild
from renegade.native.thunk import Code, u32
from renegade.native.checkpoints import checkpoint_history
from renegade.native.savefiles import saved_difficulty
from test_tt_discs import DiscEngine, MODE, CAMERA, PLAYER
from test_native import CONTROL
from test_mission_launch import saved, launcher
from test_checkpoints import save
from test_tt_native import Memory

CTRL = 0x13000000


class CampaignEngine(DiscEngine):
    def __init__(self, mission=MISSIONS[1], band=0x60000000):
        super().__init__(mission, band=band)
        self.uc.mem_map(CTRL,16384)
        self.put(CTRL+t.ENABLED,1)
        self.put(CONTROL+s.ALIVE,1)
        self.put(CONTROL+s.GENERATION,1)
        self.put(CONTROL+s.OBSERVER,0x123456)
        self.put(self.a.commands+0x204,0x123456)
        self.put(self.a.band_base+t.SAVE_SYSTEM,0x81C648)
        self.uc.mem_write(CTRL+4096,t.build_checkpoint(CTRL,CONTROL,self.a))
        wrapper=Code(CTRL+8192)
        wrapper.emit(b'\x55\x8b\x2d'+u32(CTRL+128))
        wrapper.jump(b'\xe8',CTRL+4096)
        wrapper.emit(b'\x5d\xc3')
        self.uc.mem_write(CTRL+8192,wrapper.finish())
        # Save_Game is void; deliberately return zero to prevent bool assumptions.
        self.uc.mem_write(self.a.band_base+t.SAVE,b'\x31\xc0\xc3')
        self.saves=[]
        def observe(uc,address,size,data):
            if address==self.a.band_base+t.SAVE:
                args=struct.unpack('<3I',uc.mem_read(uc.reg_read(UC_X86_REG_ESP)+4,12))
                assert args==(CONTROL+s.FILES+(mission.number-1)*64,0x81C648,0)
                assert self.value(t.BUSY)==1 and self.value(t.READY)==0
                self.saves.append(args)
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def value(self,offset):
        return int.from_bytes(self.uc.mem_read(CTRL+offset,4),'little')

    def frame(self,mask=0,event=0,disc=False):
        self.put(CONTROL+s.OBJECTIVE_EVENTS+(self.mission.number-1)*4,event)
        if disc:
            self.put(CONTROL+s.DISC_GENERATION,self.read(s.GENERATION))
            self.put(CONTROL+s.DISC_EVENT,1)
        self.put(CTRL+128,mask)
        self.execute(CTRL+8192)


@pytest.mark.parametrize('mission',MISSIONS)
@pytest.mark.parametrize('band',[0x60000000,0x71000000])
def test_tt_loaded_baseline_new_objective_and_void_save_abi(mission,band):
    e=CampaignEngine(mission,band)
    e.frame(mask=1,event=1)  # Older loaded save must not overwrite latest.
    assert not e.saves
    e.frame(mask=1,event=2)  # Hidden objective callback.
    assert len(e.saves)==1 and e.value(t.READY)==1 and e.value(t.SERIAL)==1
    e.put(CTRL+t.READY,0)
    e.frame(mask=3,event=2)  # Late list registration / repeated callback.
    assert len(e.saves)==1
    e.frame(mask=7)
    assert len(e.saves)==2 and e.value(t.SAVED_MAP)==mission.number-1


@pytest.mark.parametrize('reason',['disabled','stale','menu','paused','cutscene','dead',
    'no_control','no_alive','victory','locked','foreign_observer','busy'])
def test_tt_checkpoint_waits_for_control_and_ownership(reason):
    e=CampaignEngine(); e.frame()
    changes={
        'disabled':(CTRL+t.ENABLED,0),'stale':(CONTROL+s.HEARTBEAT,0),
        'menu':(e.a.game_mode,0),'paused':(MODE+4,1),
        'cutscene':(CAMERA+s.CAMERA_HOST,1),'dead':(0x21001000+2048,0),
        'no_control':(PLAYER+s.CONTROL_ENABLED,0),'no_alive':(CONTROL+s.ALIVE,0),
        'victory':(e.a.pending_continue,1),'locked':(CONTROL+s.ALLOWED,1),
        'foreign_observer':(e.a.commands+0x204,0x654321),'busy':(CTRL+t.BUSY,1)}
    address,value=changes[reason]
    original=bytes(e.uc.mem_read(address,4))
    e.put(address,value);e.frame(event=2,disc=True)
    assert not e.saves and e.read(s.OBJECTIVE_EVENTS+4)==2
    e.uc.mem_write(address,original);e.frame(event=2)
    assert len(e.saves)==1


def test_tt_pending_events_wait_for_publication_and_disc_generation():
    e=CampaignEngine();e.frame();e.frame(disc=True)
    assert len(e.saves)==1
    e.frame(event=4);e.frame(mask=4)
    assert len(e.saves)==1 and e.value(t.PENDING)
    e.put(CTRL+t.READY,0);e.frame(mask=4)
    assert len(e.saves)==2
    e.put(CTRL+t.READY,0)
    e.put(CONTROL+s.DISC_EVENT,1);e.put(CONTROL+s.DISC_GENERATION,0)
    e.frame(mask=4)
    assert len(e.saves)==2


def test_tt_reload_baselines_old_state_but_new_callbacks_can_save_again():
    e=CampaignEngine();e.frame();e.frame(event=4)
    e.put(CTRL+t.READY,0)
    e.put(CONTROL+s.GENERATION,2);e.frame(mask=1,event=4,disc=True)
    assert len(e.saves)==1 and not e.value(t.PENDING)
    e.frame(event=4)
    assert len(e.saves)==2


def test_tt_replay_callback_relatches_save_event_without_duplicating_ap_evidence():
    from test_tt_native import Engine
    from renegade.native.objective_hook import build_objective_hook
    e=Engine()
    code=build_objective_hook(CONTROL+4096,CONTROL,addresses=e.a,replay_events=True)
    e.run(code,109,1)
    captured=e.read(s.CAPTURED+4)
    assert captured and e.read(s.OBJECTIVE_EVENTS+4)==captured
    e.put(CONTROL+s.OBJECTIVE_EVENTS+4,0)
    e.run(code,109,1)
    assert e.read(s.CAPTURED+4)==captured
    assert e.read(s.OBJECTIVE_EVENTS+4)==captured


def test_tt_real_pickup_code_latches_deferred_save_without_calling_save():
    from functools import partial
    from renegade.native import discs, tt_discs
    from test_tt_discs import DCTRL
    e=DiscEngine()
    address=DCTRL+12288
    e.uc.mem_write(address,discs.build_pickup(address,DCTRL,CONTROL,
        guard_builder=partial(tt_discs.guard,addresses=e.a),
        grant=e.a.band_base+tt_discs.GRANT,checkpoint=True))
    e.put(CONTROL+s.GENERATION,7)
    e.execute(address,(PLAYER,))
    assert e.captured()==1 and e.grants==1
    assert e.read(s.DISC_EVENT)==1 and e.read(s.DISC_GENERATION)==7


@pytest.mark.parametrize('reason',['okay','combat','paused','stale','locked','busy'])
def test_tt_menu_loader_uses_tt_bindings_and_preserves_abi(reason):
    e=CampaignEngine();a=e.a
    binding=SimpleNamespace(tick_iat=a.tick_iat,game_mode=a.game_mode,init_sp=t.INIT_SP,
        start_game=a.band_base+t.START_GAME,menu_think=a.band_base+t.MENU_THINK)
    e.uc.mem_write(CTRL+4096,launch.build_loader(CTRL,addresses=binding))
    for off,val in ((launch.REQUEST,1),(launch.RESULT,0),(launch.BUSY,0),
        (launch.HEARTBEAT,9900),(launch.ALLOWED,2),(launch.MAP,1)):
        e.put(CTRL+off,val)
    e.put(a.game_mode,0)
    # DiscEngine.execute supplies this=POWERUP to the loader.
    from test_tt_discs import POWERUP
    if reason=='combat':e.put(a.game_mode,1)
    if reason=='paused':e.put(POWERUP+4,1)
    if reason=='stale':e.put(CTRL+launch.HEARTBEAT,0)
    if reason=='locked':e.put(CTRL+launch.ALLOWED,1)
    if reason=='busy':e.put(CTRL+launch.BUSY,1)
    calls=[]
    for fun in (binding.init_sp,binding.start_game,binding.menu_think):
        e.uc.mem_write(fun,b'\xc3')
    def observe(uc,address,size,data):
        if address in (binding.init_sp,binding.start_game,binding.menu_think):calls.append(address)
        if address==binding.start_game:
            args=struct.unpack('<3I',uc.mem_read(uc.reg_read(UC_X86_REG_ESP)+4,12))
            assert args==(CTRL+launch.FILENAME,0xffffffff,0)
    e.uc.hook_add(UC_HOOK_CODE,observe);e.execute(CTRL+4096)
    assert calls==([binding.init_sp,binding.start_game] if reason=='okay' else
                   [] if reason=='busy' else [binding.menu_think])


def test_tt_launcher_shares_checkpoint_difficulty_confirmation(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    runtime.__class__=t.TTLauncher
    runtime.combat=MODE
    runtime.menu_slot,runtime.menu_think,runtime.game_mode=0x602F326C,0x602ADDE0,0x856518
    data[runtime.menu_slot]=runtime.control+4096;data[runtime.game_mode]=0
    client.mission_request=('M01',2,True);runtime.update(client)
    assert client.mission_confirmation['saved_difficulty']==1 and not writes
    client.mission_request=('M01',2,True,client.mission_confirmation['id']);runtime.update(client)
    assert saved_difficulty((tmp_path/'ap_load_room.sav').read_bytes())==2


def publisher(tmp_path):
    runtime=t.NativeCampaign.__new__(t.NativeCampaign)
    runtime.memory=Memory();runtime.control=CTRL;runtime.save_dir=tmp_path
    runtime.token='testroom';runtime.error=None;runtime.published=[]
    runtime.memory.write_u32(CTRL+t.READY,1)
    runtime.memory.write_u32(CTRL+t.SAVED_MAP,1)
    source=tmp_path/'ap_checkpoint_testroom_M01.apstage'
    return runtime,source


def test_tt_publication_preserves_previous_until_valid_then_archives(tmp_path):
    runtime,source=publisher(tmp_path)
    source.write_bytes(save(difficulty=1));runtime.publish()
    destination=tmp_path/'ap_checkpoint_testroom_M01.sav'
    before=destination.read_bytes()
    runtime.memory.write_u32(CTRL+t.READY,1)
    source.write_bytes(b'incomplete');runtime.publish()
    assert runtime.error and runtime.memory.u32(CTRL+t.READY)==1
    assert destination.read_bytes()==before
    source.write_bytes(save(difficulty=2));runtime.publish()
    assert runtime.error is None and runtime.memory.u32(CTRL+t.READY)==0
    assert [c['difficulty'] for c in checkpoint_history(tmp_path,'testroom','M01')]==[2,1]


def test_tt_publication_never_reads_an_in_progress_save(tmp_path):
    runtime,source=publisher(tmp_path)
    source.write_bytes(saved())
    runtime.memory.write_u32(CTRL+t.BUSY,1);runtime.publish()
    assert not list(tmp_path.glob('*.sav'))


def test_tt_save_path_comes_from_native_factory_not_steam_data(tmp_path):
    mem=Memory();b=mem.a.band_base
    obj,ptr=0x20000000,0x21000000
    mem.write_u32(b+t.FILE_FACTORY,0x809E7C);mem.write_u32(0x809E7C,obj)
    mem.write_u32(obj,b+0x2CDBB4)
    raw=str(tmp_path).encode('mbcs')
    mem.write_u32(obj+4,ptr);mem.write(ptr,raw+b'\0')
    mem.write_u32(obj+20,len(raw));mem.write_u32(obj+24,len(raw))
    assert t.save_directory(mem,mem.a)==tmp_path/'save'
    mem.write_u32(obj,0xBAD)
    with pytest.raises(UnsupportedBuild):t.save_directory(mem,mem.a)
