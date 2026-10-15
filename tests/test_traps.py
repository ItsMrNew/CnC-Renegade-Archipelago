import asyncio
import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP, UC_X86_REG_EAX

from renegade.data import *
from renegade.native import traps as t, rewards as r, services as s
from test_rewards import RewardEngine, REWARD, reward_client
from test_services import BASE, CONTROL, OBJECT, STACK, RETURN, TICK

MOVE, RENDER = 0x13001000, 0x14001000
GRAVITY = 0x15001000
CLOCK = 0x16001000


class TrapEngine(RewardEngine):
    def __init__(self):
        super().__init__()
        uc=self.machine
        uc.mem_write(TICK,b'\xa1'+s.u32(OBJECT+6000)+b'\xc3')
        uc.ctl_remove_cache(TICK,TICK+16)
        self.tick(10000)
        for address in (MOVE-4096,RENDER-4096): uc.mem_map(address,8192)
        t.initialise(SimpleNamespace(write=uc.mem_write),REWARD)
        uc.mem_map(CLOCK-4096,8192)
        uc.mem_write(CLOCK,t.build_clock(CLOCK,REWARD,CONTROL))
        uc.mem_write(CONTROL+s.CODE_OFFSET,s.build_services(CONTROL,reward_target=REWARD+r.CODE_OFFSET,clock_target=CLOCK))
        uc.ctl_remove_cache(CONTROL+s.CODE_OFFSET,CONTROL+8192)
        uc.mem_write(MOVE,t.build_movement(MOVE,REWARD,CONTROL))
        uc.mem_write(RENDER,t.build_disco(RENDER,REWARD,CONTROL))
        uc.mem_write(BASE+t.APPLY_CONTROL,b'\xc3')
        uc.mem_write(BASE+t.RENDER,b'\xc3')
        uc.mem_write(BASE+r.FORCE_RELOAD,b'\xc7\x41\x3c'+s.u32(5)+b'\xc3')
        uc.mem_write(BASE+r.SET_WEAPON_STATE,b'\xc7\x41\x3c'+s.u32(5)+b'\xc2\x04\x00')
        self.put(OBJECT+4000+20,1); self.put(OBJECT+4000+28,0)
        self.put(OBJECT+4100,OBJECT+4200)
        self.put(OBJECT+4200,OBJECT+4600); self.put(OBJECT+4600+0x1c,2)
        uc.mem_write(OBJECT+4200+0x38,b'\x01')
        self.reloads=0
        def observe(machine,address,size,data):
            if address == BASE+r.FORCE_RELOAD:
                assert machine.reg_read(UC_X86_REG_ECX)==OBJECT+4200
                self.reloads+=1
        uc.hook_add(UC_HOOK_CODE,observe)

    def start(self,kind): self.request(kind); self.frame(); assert self.result()==1
    def tick(self,value):
        self.put(OBJECT+6000,value & 0xffffffff)
        self.set(s.HEARTBEAT,(value-100)&0xffffffff)
    def advance(self,seconds):
        while seconds > 0:
            dt=min(.25,seconds)
            self.machine.mem_write(BASE+t.FRAME_SECONDS,struct.pack('<f',dt))
            self.frame()
            seconds-=dt
        self.machine.mem_write(BASE+t.FRAME_SECONDS,struct.pack('<f',0))
    def invoke(self,code,player=OBJECT+768):
        self.machine.reg_write(UC_X86_REG_ECX,player)
        self.machine.reg_write(UC_X86_REG_ESP,STACK)
        self.put(STACK,RETURN)
        self.machine.emu_start(code,RETURN,count=10000)
        assert self.machine.reg_read(UC_X86_REG_ESP)==STACK+4
    def movement(self,player=OBJECT+768):
        self.machine.mem_write(player+t.FORWARD,struct.pack('<3f',1,-.5,2))
        self.invoke(MOVE,player)
        return struct.unpack('<3f',self.machine.mem_read(player+t.FORWARD,12))


def test_timings_and_independent_effects_expire_without_persistent_stats():
    e=TrapEngine(); e.start(8);e.start(10)
    assert e.movement()==pytest.approx((.45,-.225,0))
    assert e.movement()==pytest.approx((.45,-.225,0))  # No multiplication across frames.
    e.advance(29.75); assert e.movement()==pytest.approx((.45,-.225,0))
    e.advance(.25); assert e.movement()==pytest.approx((1,-.5,3.2))
    e.advance(29.75); assert e.movement()==pytest.approx((1,-.5,3.2))
    e.advance(.25); assert e.movement()==pytest.approx((1,-.5,2))
    assert TRAP_DURATIONS=={CONCRETE_BOOTS:30,DISCO_HAVOC:60,MOON_BOOTS:60}


@pytest.mark.parametrize('reason',['npc','cinematic','disabled','expired_lease','no_control','new_load','loading','menu','victory','paused','dead','vehicle','ladder'])
def test_movement_guard_does_not_modify_nonplayable_or_other_players(reason):
    e=TrapEngine();e.start(8);e.start(10)
    player=OBJECT+768
    if reason=='npc': player=OBJECT+5000
    if reason=='cinematic': e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='disabled': e.set(s.ENABLED,0)
    if reason=='expired_lease': e.set(s.HEARTBEAT,0)
    if reason=='no_control': e.machine.mem_write(player+s.CONTROL_ENABLED,b'\0')
    if reason=='new_load': e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if reason=='loading': e.put(BASE+s.STEAM.commands+0x204,BASE+s.SET_OBJECTIVE_STATUS)
    if reason=='menu': e.put(BASE+s.STEAM.game_mode,0)
    if reason=='victory': e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\1')
    if reason=='paused': e.set(s.PLAYABLE,0)
    if reason=='dead': e.set(s.ALIVE,0)
    if reason=='vehicle': e.put(player+t.HUMAN_STATE,9)
    if reason=='ladder': e.put(player+t.HUMAN_STATE,8)
    assert e.movement(player)==pytest.approx((1,-.5,2))


def test_deadline_wrap_and_repeated_trap_extends_without_strength_stacking():
    e=TrapEngine();e.tick(0xfffff000);e.start(8)
    e.tick(0x1000);e.advance(8);assert e.movement()[0]==pytest.approx(.45)
    e.start(8);e.advance(51.75);assert e.movement()[0]==pytest.approx(.45)
    e.advance(.25);assert e.movement()[0]==1


@pytest.mark.parametrize('kind,offset',[(9,t.DISCO_END),(10,t.MOON_END)])
def test_minute_traps_refresh_and_expire_across_tick_wrap(kind,offset):
    e=TrapEngine();e.tick(0xfffff000);e.start(kind)
    e.tick(0x1000);e.start(kind)
    assert e.value(REWARD+offset)==120
    if kind==10:
        e.advance(119.75);assert e.movement()[2]==pytest.approx(3.2)
        e.advance(.25);assert e.movement()[2]==2
    else:
        calls,destructor=setup_renderer(e)
        e.advance(119.75);e.invoke(RENDER,OBJECT);assert calls[-1]==destructor
        calls.clear();e.advance(.25);e.invoke(RENDER,OBJECT);assert calls==[BASE+t.RENDER]


def test_moon_softens_air_steering_only_and_combines_with_concrete():
    e=TrapEngine();e.start(10)
    assert e.movement()==pytest.approx((1,-.5,3.2))
    e.put(OBJECT+768+t.HUMAN_STATE,5)
    assert e.movement()==pytest.approx((.8,-.4,3.2))
    e.start(8);assert e.movement()==pytest.approx((.36,-.18,0))


class GravityEngine(TrapEngine):
    def __init__(self):
        super().__init__()
        uc=self.machine
        uc.mem_map(GRAVITY-4096,8192)
        uc.mem_write(GRAVITY,t.build_gravity(GRAVITY,REWARD,CONTROL))
        self.physics=OBJECT+7000
        self.put(OBJECT+768+t.PLAYER_PHYSICS,self.physics)
        uc.mem_write(BASE+0x4a6270,struct.pack('<f',-9.8))
        for physics in (self.physics,OBJECT+7500):uc.mem_write(physics+0x8c,struct.pack('<f',1.25))
        # Continuation checks that the copied prologue presents the original
        # engine frame, this pointer and dt, with only ST0 acceleration changed.
        suffix=b'\xd9\x1d'+s.u32(OBJECT+6500)+b'\x83\xc4\x0c\x5e\x5b\x89\xec\x5d\xb8\x01\x00\x00\x00\xc2\x04\x00'
        uc.mem_write(BASE+t.BALLISTIC_MOVE,t.BALLISTIC_PREFIX+suffix)
        def observe(machine,address,size,data):
            if address==BASE+t.BALLISTIC_MOVE+len(t.BALLISTIC_PREFIX):
                from unicorn.x86_const import UC_X86_REG_ESI, UC_X86_REG_EBP
                assert machine.reg_read(UC_X86_REG_ESI)==self.expected_physics
                bp=machine.reg_read(UC_X86_REG_EBP)
                assert machine.mem_read(bp+8,4)==struct.pack('<f',.02)
        uc.hook_add(UC_HOOK_CODE,observe)
    def acceleration(self,physics=None):
        physics=physics or self.physics;self.expected_physics=physics
        self.machine.reg_write(UC_X86_REG_ECX,physics)
        self.machine.reg_write(UC_X86_REG_ESP,STACK)
        self.put(STACK,RETURN);self.machine.mem_write(STACK+4,struct.pack('<f',.02))
        self.machine.emu_start(GRAVITY,RETURN,count=10000)
        assert self.machine.reg_read(UC_X86_REG_ESP)==STACK+8
        assert self.machine.reg_read(UC_X86_REG_EAX)==1
        assert struct.unpack('<f',self.machine.mem_read(physics+0x8c,4))[0]==1.25
        return struct.unpack('<f',self.machine.mem_read(OBJECT+6500,4))[0]


def test_moon_gravity_scales_only_local_acceleration_and_expires():
    e=GravityEngine();assert e.acceleration()==pytest.approx(-9.8*1.25)
    e.start(10)
    assert e.acceleration()==pytest.approx(-9.8*1.25*2/3)
    assert e.acceleration()==pytest.approx(-9.8*1.25*2/3)  # No stacking or saved-field changes.
    e.advance(30);assert e.acceleration()==pytest.approx(-9.8*1.25*2/3)
    e.advance(29.75);assert e.acceleration()==pytest.approx(-9.8*1.25*2/3)
    e.advance(.25);assert e.acceleration()==pytest.approx(-9.8*1.25)


@pytest.mark.parametrize('reason',['npc','cinematic','menu','paused','dead','disconnect','stale','loading','new_load','no_control','vehicle','ladder','victory'])
def test_moon_gravity_forwards_unmodified_outside_eligible_gameplay(reason):
    e=GravityEngine();e.start(10);physics=e.physics
    if reason=='npc':physics=OBJECT+7500
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='menu':e.put(BASE+s.STEAM.game_mode,0)
    if reason=='paused':e.set(s.PLAYABLE,0)
    if reason=='dead':e.set(s.ALIVE,0)
    if reason=='disconnect':e.set(s.ENABLED,0)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='loading':e.put(BASE+s.STEAM.commands+0x204,BASE+s.SET_OBJECTIVE_STATUS)
    if reason=='new_load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if reason=='no_control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\0')
    if reason=='vehicle':e.put(OBJECT+768+t.HUMAN_STATE,9)
    if reason=='ladder':e.put(OBJECT+768+t.HUMAN_STATE,8)
    if reason=='victory':e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\1')
    assert e.acceleration(physics)==pytest.approx(-9.8*1.25)


def test_butterfingers_runs_five_separate_reload_cycles_and_keeps_ammo():
    e=TrapEngine(); e.put(OBJECT+4200+0x24,123);e.put(OBJECT+4200+0x28,456)
    e.start(7)
    for count in range(1,6):
        e.frame();e.frame()
        assert e.reloads==count
        assert e.machine.mem_read(OBJECT+4200+0x24,8)==s.u32(123)+s.u32(456)
        e.put(OBJECT+4200+0x3c,1)  # Engine finishes reload, returns to ready.
    e.frame();assert e.reloads==5


@pytest.mark.parametrize('reason',['cinematic','firing','switching','c4','beacon','vehicle'])
def test_butterfingers_waits_until_a_gun_is_ready(reason):
    e=TrapEngine();e.start(7)
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='firing':e.put(OBJECT+4200+0x3c,3)
    if reason=='switching':e.put(OBJECT+4200+0x3c,6)
    if reason=='c4':e.put(OBJECT+4600+0x1c,0)
    if reason=='beacon':e.put(OBJECT+4600+0x1c,6)
    if reason=='vehicle':e.put(OBJECT+768+t.HUMAN_STATE,9)
    e.frame();assert e.reloads==0
    assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOADS,4),'little')==5


def test_load_cancels_reload_sequence_and_old_timers_before_new_trap():
    e=TrapEngine();e.start(7);e.start(8)
    e.set(s.GENERATION,e.read(s.GENERATION)+1);e.start(9)
    assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOADS,4),'little')==0
    assert e.movement()[0]==1


def setup_renderer(e,allocation=True):
    uc=e.machine;renderer=OBJECT+10000;vtable=OBJECT+9800;destructor=BASE+0x380000
    e.put(renderer,vtable);e.put(vtable,destructor)
    uc.mem_write(BASE+t.NEW,b'\xb8'+s.u32(renderer if allocation else 0)+b'\xc3')
    uc.mem_write(BASE+t.RENDER_CTOR,b'\x89\xc8\xc2\x04\x00')
    for function,pop in ((t.SET_RANGE,4),(t.ALPHA,4),(t.ADD_QUAD,8),(t.DRAW,0)):
        uc.mem_write(BASE+function,b'\xc2'+struct.pack('<H',pop) if pop else b'\xc3')
    uc.mem_write(destructor,b'\xc2\x04\x00')
    calls=[]
    def observe(machine,address,size,data):
        esp=machine.reg_read(UC_X86_REG_ESP)
        if address in (BASE+t.RENDER,BASE+t.NEW,BASE+t.DRAW,destructor):calls.append(address)
        if address==BASE+t.ADD_QUAD:
            assert machine.reg_read(UC_X86_REG_ECX)==renderer
            rect,colour=struct.unpack('<2I',machine.mem_read(esp+4,8))
            assert rect==REWARD+t.RECT and colour >> 24==0x28
        if address==destructor:
            assert machine.reg_read(UC_X86_REG_ECX)==renderer
            assert machine.mem_read(esp+4,4)==s.u32(1)
    uc.hook_add(UC_HOOK_CODE,observe)
    return calls,destructor


def test_disco_uses_private_renderer_and_releases_it_each_frame():
    e=TrapEngine();e.start(9);calls,destructor=setup_renderer(e)
    original_fades=bytes(range(80));e.machine.mem_write(BASE+0x45faa0,original_fades)
    e.invoke(RENDER,OBJECT)
    assert calls==[BASE+t.RENDER,BASE+t.NEW,BASE+t.DRAW,destructor]
    assert e.machine.mem_read(BASE+0x45faa0,80)==original_fades
    e.advance(59.75);calls.clear();e.invoke(RENDER,OBJECT)
    assert calls==[BASE+t.RENDER,BASE+t.NEW,BASE+t.DRAW,destructor]
    e.advance(.25);calls.clear();e.invoke(RENDER,OBJECT)
    assert calls==[BASE+t.RENDER]


@pytest.mark.parametrize('reason',['cinematic','menu','paused','dead','expired_lease','disabled','new_load'])
def test_disco_does_not_draw_outside_playable_state(reason):
    e=TrapEngine();e.start(9);calls,_=setup_renderer(e)
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='menu':e.put(BASE+s.STEAM.game_mode,0)
    if reason=='paused':e.put(OBJECT+4,1)
    if reason=='dead':e.set(s.ALIVE,0)
    if reason=='expired_lease':e.set(s.HEARTBEAT,0)
    if reason=='disabled':e.set(s.ENABLED,0)
    if reason=='new_load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    e.invoke(RENDER,OBJECT);assert calls==[BASE+t.RENDER]


def test_disco_allocation_failure_skips_overlay_safely():
    e=TrapEngine();e.start(9);calls,_=setup_renderer(e,False)
    e.invoke(RENDER,OBJECT);assert calls==[BASE+t.RENDER,BASE+t.NEW]


def test_protocol4_requires_trap_support_and_preserves_legacy_contract():
    cfg=slot_data(3,1,True,True,True,True)
    assert validate_slot_data(cfg)==cfg and cfg['protocol_version']==4
    assert all(ITEM_FLAGS[n]==4 for n in TRAPS)
    assert len(set(ITEM_IDS.values()))==len(ITEM_IDS)
    cfg.pop('extra_traps')
    with pytest.raises(ValueError):validate_slot_data(cfg)
    assert validate_slot_data(slot_data(3,1,True,True,True))['protocol_version']==3


def test_new_trap_receipts_are_durable_and_deduplicated(tmp_path):
    async def run():
        c,sock,items=await reward_client(tmp_path)
        packet=[dict(item=ITEM_IDS[n],location=-1,player=1,flags=4) for n in NEW_TRAPS]
        await c.handle(sock,dict(cmd='ReceivedItems',index=len(items),items=packet))
        indexes=[c.effect_indexes(n)[0] for n in NEW_TRAPS]
        for index in indexes:c.progress.mark_supply(index)
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items+packet))
        assert all(not c.effect_indexes(n) for n in NEW_TRAPS)
    asyncio.run(run())


def test_trap_vtable_ownership_restore_and_partial_install_failure():
    from test_objective_hook import Memory
    class HookMemory(Memory):
        def __init__(self):
            super().__init__(); self.next=0x16000000
            for rva,data in t.ANCHORS.items():self.blocks[BASE+rva]=data
            from renegade.native.effects_hud import ANCHORS
            for rva,data in ANCHORS.items():self.blocks[BASE+rva]=data
            for offset in t.TIMERS:self.blocks[REWARD+offset]=b'\0'*4
            for slot,original in ((t.CONTROL_SLOT,t.APPLY_CONTROL),(t.RENDER_SLOT,t.RENDER),
                                  (t.BALLISTIC_SLOT,t.BALLISTIC_MOVE)):
                self.values[BASE+slot]=BASE+original
        def allocate(self):
            self.next+=8192;return self.next
        def replace_readonly_u32(self,address,expected,replacement):
            assert self.u32(address)==expected
            self.write_u32(address,replacement)
    mem=HookMemory();runtime=t.NativeTraps(mem,REWARD,CONTROL)
    runtime.update();runtime.update()
    assert all(mem.u32(field)==code for field,original,code in runtime.entries)
    runtime.close()
    assert all(mem.u32(field)==original for field,original,code in runtime.entries)
    assert mem.u32(CONTROL+s.ENABLED)==0
    mem.write_u32(BASE+t.RENDER_SLOT,0x12345678)
    with pytest.raises(t.MemoryError):runtime.update()
    runtime.close()
    assert mem.u32(BASE+t.CONTROL_SLOT)==BASE+t.APPLY_CONTROL
    assert mem.u32(BASE+t.RENDER_SLOT)==0x12345678


def test_butterfingers_copy_dispatches_and_temporary_outage_preserves_effects(tmp_path):
    async def run():
        c,sock,items=await reward_client(tmp_path)
        c.progress.mark_supply(c.effect_indexes(ARMOUR_TRAP)[0])
        await c.handle(sock,dict(cmd='ReceivedItems',index=len(items),items=[
            dict(item=ITEM_IDS[BUTTERFINGERS],location=-1,player=1,flags=4)]*2))
        values={t.RELOADS:2,t.CONCRETE_END:12345}
        runtime=r.NativeRewards.__new__(r.NativeRewards)
        runtime.control=0;runtime.pending=None;runtime.cookie='a'*32
        runtime.memory=SimpleNamespace(pid=123,u32=lambda a:values.get(a,0),write_u32=lambda a,v:values.__setitem__(a,v))
        runtime.update(c,123,1,True)
        assert len(c.effect_indexes(BUTTERFINGERS))==1 and values[r.REQUEST]==7
        c.connected=False;runtime.update(c,123,1,False)
        assert values[t.RELOADS]==2 and values[t.CONCRETE_END]==12345
    asyncio.run(run())
