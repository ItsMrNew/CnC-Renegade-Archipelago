"""Execute TT control-state, DeathLink and message-window ABIs."""
from collections import deque
from types import SimpleNamespace
import struct

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX

from renegade.native import tt_inventory as t, services as s, rewards as r, attempts as a
from renegade.native import tt_notifications as n
from renegade.native.tt_deathlink import NativeDeathLink
from renegade.native.thunk import CODE_OFFSET, u32
from test_tt_inventory import RewardEngine, FRAME, REWARD, WEAPON, HP, ARMOUR
from test_tt_discs import DCTRL, PLAYER, CAMERA, MODE
from test_native import CONTROL, OBJECT


def death_engine():
    e=RewardEngine()
    e.uc.mem_write(CONTROL+s.WARHEAD,b'BlamoKiller\0')
    e.uc.mem_write(t.APPLY_DAMAGE,b'\xc7\x05'+u32(HP)+u32(0)+b'\xc3')
    e.damage_calls=[]
    def observe(uc,address,size,data):
        if address!=t.APPLY_DAMAGE:return
        sp=uc.reg_read(UC_X86_REG_ESP)
        args=struct.unpack('<4I',uc.mem_read(sp+4,16))
        assert args==(PLAYER,struct.unpack('<I',struct.pack('<f',100000))[0],CONTROL+s.WARHEAD,0)
        assert bytes(uc.mem_read(args[2],12))==b'BlamoKiller\0'
        e.damage_calls.append(args)
    e.uc.hook_add(UC_HOOK_CODE,observe)
    e.put(CONTROL+s.DEATH_ENABLED,1)
    return e


def queue_death(e):
    for off,value in ((s.REQUEST_PLAYER,PLAYER),(s.REQUEST_MAP,1),
                      (t.DEATH_GENERATION,e.read(s.GENERATION)),(s.DEATH_REQUEST,1)):
        e.put(CONTROL+off,value)


def test_tt_incoming_death_uses_native_damage_once_without_echo_or_supply():
    e=death_engine();queue_death(e);e.request(1);e.frame();e.frame()
    assert len(e.damage_calls)==1
    assert e.read(s.DEATH_APPLIED)==1 and e.read(s.DEATH_COUNT)==0
    assert e.read(s.ALIVE)==0 and e.get(REWARD+r.REQUEST)==1


@pytest.mark.parametrize('state',['pause','cutscene','control','menu','offline','victory','locked','stale','observer'])
def test_tt_death_and_armour_loss_cannot_apply_without_current_control(state):
    e=death_engine();queue_death(e);e.request(6)
    field,value={
        'pause':(MODE+4,3),'cutscene':(CAMERA+s.CAMERA_HOST,OBJECT),
        'control':(PLAYER+s.CONTROL_ENABLED,0),'menu':(e.a.game_mode,0),
        'offline':(CONTROL+s.ONLINE,0),'victory':(e.a.pending_continue,1),
        'locked':(CONTROL+s.ALLOWED,0),'stale':(CONTROL+s.HEARTBEAT,0),
        'observer':(e.a.commands+0x204,0)}[state]
    original=e.get(field);e.put(field,value);generation=e.read(s.GENERATION)
    e.frame();e.frame()
    assert not e.damage_calls and e.fget(ARMOUR)==30
    assert e.get(REWARD+r.REQUEST)==6
    if state in ('pause','cutscene','control'):
        assert e.read(s.PLAYER)==PLAYER and e.read(s.ALIVE)==1
        assert e.read(s.GENERATION)==generation
        e.put(field,original);e.frame()
        assert len(e.damage_calls)==1
    else:
        assert e.read(s.DEATH_APPLIED)==0


def test_tt_armour_loss_applies_once_without_health_or_ammo_changes():
    e=RewardEngine();ptr=e.add_weapon();e.request(6);e.frame()
    assert e.fget(ARMOUR)==0 and e.fget(HP)==25 and e.get(ptr+0x24)==5
    assert e.get(REWARD+r.RESULT)==1
    e.fput(ARMOUR,50);e.frame();assert e.fget(ARMOUR)==50


@pytest.mark.parametrize('stale',[(s.REQUEST_PLAYER,123),(s.REQUEST_MAP,4),(t.DEATH_GENERATION,999)])
def test_tt_death_does_not_cross_player_map_or_load_generation(stale):
    e=death_engine();queue_death(e);e.put(CONTROL+stale[0],stale[1]);e.frame()
    assert not e.damage_calls and e.read(s.DEATH_REQUEST)==0


def test_tt_local_death_latches_map_once_even_after_control_is_removed():
    e=death_engine();e.fput(HP,0);e.put(PLAYER+s.CONTROL_ENABLED,0)
    e.frame();e.frame()
    assert e.read(s.DEATH_COUNT)==1 and e.read(t.DEATH_MAP)==1
    e.put(e.a.game_mode,0);e.frame()
    assert e.read(s.DEATH_COUNT)==1 and e.read(t.DEATH_MAP)==1


@pytest.mark.parametrize('hp',[float('nan'),float('inf'),-float('inf'),-float('nan')])
def test_tt_invalid_health_does_not_invent_death(hp):
    e=death_engine();e.fput(HP,hp);e.frame()
    assert e.read(s.DEATH_COUNT)==0 and not e.damage_calls


def test_tt_loading_an_already_dead_save_does_not_emit_death():
    e=death_engine();e.put(FRAME,0);e.fput(HP,0);e.frame()
    assert e.read(s.DEATH_COUNT)==0


def test_tt_failed_incoming_damage_does_not_echo_or_retry():
    e=death_engine();e.uc.mem_write(t.APPLY_DAMAGE,b'\xc3');queue_death(e);e.frame();e.frame()
    assert len(e.damage_calls)==1 and e.read(s.DEATH_FAILED)==1 and e.read(s.DEATH_COUNT)==0


@pytest.mark.parametrize('testing',[False,True])
def test_local_death_diagnostic_exists_only_in_test_build(testing):
    e=death_engine()
    e.uc.mem_write(FRAME+CODE_OFFSET,t.build_frame(FRAME+CODE_OFFSET,FRAME,CONTROL,e.a,
        WEAPON+CODE_OFFSET,REWARD+CODE_OFFSET,DCTRL+8192,testing=testing))
    e.uc.ctl_remove_cache(FRAME,FRAME+8192)
    e.put(CONTROL+s.TEST_ACTION,1);e.frame();e.frame()
    assert len(e.damage_calls)==int(testing)
    assert e.read(s.DEATH_COUNT)==int(testing) and e.read(s.TEST_RESULT)==int(testing)
    assert e.read(s.DEATH_APPLIED)==0


def test_tt_death_bridge_holds_until_playable_and_clears_on_disconnect():
    e=death_engine()
    mem=SimpleNamespace(u32=e.get,write_u32=e.put)
    bridge=NativeDeathLink(mem,CONTROL);out=[]
    client=SimpleNamespace(ready=True,deathlink=SimpleNamespace(enabled=True,incoming=deque([{}])),player_died=out.append)
    e.put(MODE+4,3);e.frame();bridge.update(client)
    assert client.deathlink.incoming and e.read(s.DEATH_REQUEST)==0
    e.put(MODE+4,0);e.frame();bridge.update(client);e.frame();bridge.update(client)
    assert not client.deathlink.incoming and e.read(s.DEATH_APPLIED)==1 and not out
    e.put(CONTROL+s.DEATH_COUNT,1);e.put(CONTROL+t.DEATH_MAP,1)
    bridge.update(client);bridge.update(client);assert out==['M01']
    client.ready=False;client.deathlink.incoming.append({});e.put(CONTROL+s.DEATH_REQUEST,1)
    bridge.update(client)
    assert not client.deathlink.incoming and e.read(s.DEATH_REQUEST)==0 and e.read(s.DEATH_ENABLED)==0


def test_tt_message_window_copies_wide_text_rgb_and_waits_for_window():
    e=RewardEngine();ctrl=0x18000000;e.uc.mem_map(ctrl,8192)
    window=OBJECT+15000;e.put(n.MESSAGE_WINDOW,0)
    e.uc.mem_write(ctrl+CODE_OFFSET,n.build_notifications(ctrl))
    e.uc.mem_write(n.ADD_MESSAGE,b'\xc2\x10\0')
    obj=n.NativeNotifications.__new__(n.NativeNotifications);obj.control=ctrl
    obj.memory=SimpleNamespace(write=lambda p,b:e.uc.mem_write(p,b))
    e.put(ctrl+n.WIDE_STRING,ctrl+n.TEXT)
    obj.write_message('[AP] Health Supply\n\0',bytes.fromhex('00ffff'))
    e.put(ctrl+n.REQUEST,1);e.execute(ctrl+CODE_OFFSET)
    assert e.get(ctrl+n.REQUEST)==1
    calls=[]
    def observe(uc,address,size,data):
        if address!=n.ADD_MESSAGE:return
        assert uc.reg_read(UC_X86_REG_ECX)==window
        args=struct.unpack('<4I',uc.mem_read(uc.reg_read(UC_X86_REG_ESP)+4,16))
        assert args[:3]==(ctrl+n.WIDE_STRING,ctrl+n.COLOUR,0)
        assert struct.unpack('<f',u32(args[3]))==(6.0,)
        length=e.get(ctrl+n.TEXT-4)
        calls.append(bytes(uc.mem_read(e.get(args[0]),length*2)).decode('utf-16le'))
        assert struct.unpack('<3f',uc.mem_read(args[1],12))==(0,1,1)
    e.uc.hook_add(UC_HOOK_CODE,observe)
    e.put(n.MESSAGE_WINDOW,window);e.put(window+4,OBJECT);e.execute(ctrl+CODE_OFFSET);e.execute(ctrl+CODE_OFFSET)
    assert calls==['[AP] Health Supply\n'] and e.get(ctrl+n.REQUEST)==0


def test_tt_failed_objective_snapshot_is_read_only_and_retained_while_paused():
    e=RewardEngine();ctrl=0x18000000;e.uc.mem_map(ctrl,8192)
    e.uc.mem_write(ctrl+CODE_OFFSET,a.build_attempts(ctrl,CONTROL,addresses=e.a))
    e.uc.mem_write(FRAME+CODE_OFFSET,t.build_frame(FRAME+CODE_OFFSET,FRAME,CONTROL,e.a,
        WEAPON+CODE_OFFSET,REWARD+CODE_OFFSET,DCTRL+8192,attempt_target=ctrl+CODE_OFFSET))
    e.uc.ctl_remove_cache(FRAME,FRAME+8192)
    e.objective(104,kind=2,state=2)
    reader=a.NativeAttempts.__new__(a.NativeAttempts);reader.control=ctrl;reader.service=CONTROL
    reader.commands=e.a.commands;reader.game_mode=e.a.game_mode
    reader.memory=SimpleNamespace(u32=e.get,read=lambda p,n:bytes(e.uc.mem_read(p,n)))
    e.frame();assert reader.snapshot()==dict(map='M01',failed=[104])
    e.put(MODE+4,3);e.frame();assert reader.snapshot()['failed']==[104]
    assert e.read(s.CAPTURED+4)==0 and e.read(s.DISC_EVENT)==0
    e.put(OBJECT+2056,0);e.frame();assert reader.snapshot()['failed']==[]
