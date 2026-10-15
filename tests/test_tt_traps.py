"""TT-specific movement, SSE gravity, play-time clock and stacked receipts."""
import struct
from types import SimpleNamespace
import pytest

from renegade.native import tt_traps as tt, traps as t, services as s, rewards as r
from renegade.native.thunk import Code, CODE_OFFSET, u32
from test_tt_inventory import RewardEngine, REWARD, FRAME
from test_tt_discs import PLAYER, CAMERA, MODE, POWERUP
from test_native import CONTROL, OBJECT
from test_tt_native import memory

WRAPPER,CLOCK,CALLER,OUTPUT=0x17000000,0x18000000,0x19000000,0x19000100


def engine():
    e=RewardEngine()
    for p in (WRAPPER,CLOCK,CALLER):e.uc.mem_map(p,8192)
    t.initialise(SimpleNamespace(write=lambda p,v:e.uc.mem_write(p,v)),REWARD)
    e.put(REWARD+t.OWNER,PLAYER);e.put(REWARD+t.GENERATION,e.read(s.GENERATION))
    e.fput(tt.FRAME_SECONDS,.25)
    e.uc.mem_write(CLOCK+CODE_OFFSET,t.build_clock(CLOCK+CODE_OFFSET,REWARD,CONTROL,
        guard_builder=tt.guarded(e.a),frame_seconds=tt.FRAME_SECONDS))
    return e


def invoke(e,entry,ecx,args=()):
    c=Code(CALLER+CODE_OFFSET)
    for v in reversed(args):c.emit(b'\x68'+struct.pack('<f',v))
    c.emit(b'\xb9'+u32(ecx));c.jump(b'\xe8',entry);c.emit(b'\xc3')
    e.uc.mem_write(CALLER+CODE_OFFSET,c.finish())
    e.uc.ctl_remove_cache(CALLER,CALLER+8192)
    e.execute(CALLER+CODE_OFFSET)


@pytest.mark.parametrize('kind,offset,duration',[(8,t.CONCRETE_END,30),(9,t.DISCO_END,60),(10,t.MOON_END,60),
    (11,t.BULLET_END,60),(12,t.VAMPIRE_END,60)])
def test_tt_repeats_extend_time_and_new_player_clears_effects(kind,offset,duration):
    e=engine()
    e.request(kind);e.frame();assert e.fget(REWARD+offset)==duration
    e.execute(CLOCK+CODE_OFFSET);assert e.fget(REWARD+offset)==duration-.25
    e.request(kind);e.frame();assert e.fget(REWARD+offset)==2*duration-.25
    e.put(FRAME,0);e.frame();assert e.fget(REWARD+offset)==0


@pytest.mark.parametrize('reason',['pause','cutscene','control','menu','stale','observer','dead','owner','generation','victory'])
def test_tt_clock_checks_fresh_native_state_and_never_charges_pause(reason):
    e=engine();e.fput(REWARD+t.MOON_END,60)
    field,value={'pause':(MODE+4,3),'cutscene':(CAMERA+s.CAMERA_HOST,OBJECT),
        'control':(PLAYER+s.CONTROL_ENABLED,0),'menu':(e.a.game_mode,0),
        'stale':(CONTROL+s.HEARTBEAT,0),'observer':(e.a.commands+0x204,0),
        'dead':(CONTROL+s.ALIVE,0),'owner':(REWARD+t.OWNER,123),
        'generation':(REWARD+t.GENERATION,999),'victory':(e.a.pending_continue,1)}[reason]
    old=e.get(field);e.put(field,value);e.execute(CLOCK+CODE_OFFSET)
    assert e.fget(REWARD+t.MOON_END)==60
    e.put(field,old);e.execute(CLOCK+CODE_OFFSET)
    assert e.fget(REWARD+t.MOON_END)==59.75


@pytest.mark.parametrize('delta',[0,-.1,2,float('inf'),float('nan')])
def test_tt_clock_rejects_invalid_delta(delta):
    e=engine();e.fput(tt.FRAME_SECONDS,delta);e.fput(REWARD+t.MOON_END,60)
    e.execute(CLOCK+CODE_OFFSET);assert e.fget(REWARD+t.MOON_END)==60


@pytest.mark.parametrize('state',[0,5,9,6])
@pytest.mark.parametrize('owned',[True,False])
def test_tt_movement_only_changes_current_players_upright_or_airborne_controls(state,owned):
    e=engine();e.put(PLAYER+t.HUMAN_STATE,state)
    e.uc.mem_write(e.a.band_base+tt.APPLY_CONTROL,b'\xc3')
    e.uc.mem_write(WRAPPER+CODE_OFFSET,t.build_movement(WRAPPER+CODE_OFFSET,REWARD,CONTROL,
        guard_builder=tt.guarded(e.a),apply_control=e.a.band_base+tt.APPLY_CONTROL,hologram=False))
    for off in (t.FORWARD,t.LEFT,t.UP):e.fput(PLAYER+off,10)
    e.fput(REWARD+t.CONCRETE_END,30);e.fput(REWARD+t.MOON_END,60)
    invoke(e,WRAPPER+CODE_OFFSET,PLAYER if owned else POWERUP)
    eligible=owned and state in (0,5)
    assert e.fget(PLAYER+t.FORWARD)==pytest.approx(10*.45*(.8 if state==5 else 1) if eligible else 10)
    assert e.fget(PLAYER+t.UP)==pytest.approx(0 if eligible else 10)


@pytest.mark.parametrize('active',[False,True])
@pytest.mark.parametrize('own_physics',[False,True])
def test_tt_sse_ballistic_prefix_scales_only_this_calls_gravity(active,own_physics):
    e=engine();e.put(PLAYER+t.PLAYER_PHYSICS,POWERUP if own_physics else OBJECT)
    e.fput(REWARD+t.MOON_END,60 if active else 0)
    e.put(e.a.band_base+0x3221D8,OBJECT+14000);e.fput(OBJECT+14008,-9.81)
    e.fput(POWERUP+0x8C,1.2)
    prefix=tt.ballistic_prefix(e.a)
    suffix=(b'\xf3\x0f\x11\x0d'+u32(OUTPUT)+
        b'\x83\xc4\x04\x5e\x5b\x8b\xe5\x5d\xc2\x04\0')
    e.uc.mem_write(e.a.band_base+tt.BALLISTIC_MOVE,prefix+suffix)
    e.uc.mem_write(WRAPPER+CODE_OFFSET,tt.build_gravity(WRAPPER+CODE_OFFSET,REWARD,CONTROL,e.a))
    invoke(e,WRAPPER+CODE_OFFSET,POWERUP,(.1,))
    assert e.fget(OUTPUT)==pytest.approx(-9.81*1.2*(2/3 if active and own_physics else 1))
    assert e.fget(POWERUP+0x8C)==pytest.approx(1.2)


def test_tt_movement_callbacks_restore_only_our_hooks(memory):
    traps=tt.NativeTraps(memory,0x15000000,CONTROL,memory.a)
    traps.update();traps.update()
    assert all(memory.u32(field)==code for field,original,code in traps.entries)
    traps.close()
    assert all(memory.u32(field)==original for field,original,code in traps.entries)
