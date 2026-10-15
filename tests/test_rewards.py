import asyncio
import struct
from types import SimpleNamespace
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX
from renegade.native import rewards as r, services as s
from renegade.data import *
from renegade.client import RenegadeClient
from renegade.state import Progress
from test_core import Socket, received
from test_services import Engine, CONTROL, OBJECT, BASE

REWARD = 0x11000000
HP, ARMOUR, MAX_HP, MAX_ARMOUR = (OBJECT + n for n in (256,260,264,268))


class RewardEngine(Engine):
    def __init__(self, testing=False):
        super().__init__()
        uc=self.machine
        self.set(s.OBSERVER,0x15001000)
        self.put(BASE+s.STEAM.commands+0x204,0x15001000)
        uc.mem_map(REWARD,8192)
        uc.mem_write(REWARD+r.CODE_OFFSET,r.build_rewards(REWARD, testing=testing, service=CONTROL))
        uc.mem_write(REWARD+r.BASES,struct.pack('<4f',200,100,75,10))
        uc.mem_write(REWARD+r.UPGRADE_STEP,struct.pack('<f',UPGRADE_AMOUNT))
        uc.mem_write(CONTROL+s.CODE_OFFSET,s.build_services(CONTROL,reward_target=REWARD+r.CODE_OFFSET))
        for address,value in ((ARMOUR,30),(MAX_HP,100),(MAX_ARMOUR,100)):
            uc.mem_write(address,struct.pack('<f',value))
        self.put(BASE+r.GAME_DIFFICULTY,1)
        for function,field in ((r.GET_MAX_HEALTH,MAX_HP),(r.GET_MAX_ARMOUR,MAX_ARMOUR),(r.GET_ARMOUR,ARMOUR)):
            uc.mem_write(BASE+function,b'\xd9\x05'+r.u32(field)+b'\xc3')
        for function,field,ret in ((r.SET_HEALTH,HP,b'\xc3'),(r.SET_ARMOUR,ARMOUR,b'\xc3'),
                                    (r.SET_MAX_HEALTH,MAX_HP,b'\xc2\x04\x00'),(r.SET_MAX_ARMOUR,MAX_ARMOUR,b'\xc2\x04\x00')):
            arg=8 if function in (r.SET_HEALTH,r.SET_ARMOUR) else 4
            uc.mem_write(BASE+function,b'\x8b\x44\x24'+bytes([arg])+b'\xa3'+r.u32(field)+ret)
        uc.mem_write(BASE+r.SET_TOTAL_ROUNDS,b'\xc2\x04\x00')
        uc.mem_write(BASE+r.AMMO_MAXED,b'\x31\xc0\xc3')
        self.put(OBJECT+768+0x7b0,OBJECT+4000)
        self.put(OBJECT+4000+8,OBJECT+4100)
        self.put(OBJECT+4000+20,0)
        self.refilled=[]
        def observe(machine,address,size,data):
            if address == BASE+r.SET_TOTAL_ROUNDS:
                esp=machine.reg_read(UC_X86_REG_ESP)
                assert int.from_bytes(machine.mem_read(esp+4,4),'little') in (0,1000000)
                self.refilled.append(machine.reg_read(UC_X86_REG_ECX))
        uc.hook_add(UC_HOOK_CODE,observe)
        self.frame()

    def value(self,address): return struct.unpack('<f',self.machine.mem_read(address,4))[0]
    def request(self,kind):
        for off,value in ((r.PLAYER,OBJECT+768),(r.MAP,1),(r.RESULT,0),(r.REQUEST,kind)):
            self.put(REWARD+off,value)
    def result(self): return int.from_bytes(self.machine.mem_read(REWARD+r.RESULT,4),'little')


@pytest.mark.parametrize('kind,field,maximum',[(1,HP,MAX_HP),(2,ARMOUR,MAX_ARMOUR)])
def test_supplies_refill_to_actual_maximum(kind,field,maximum):
    e=RewardEngine(); e.machine.mem_write(maximum,struct.pack('<f',475)); e.request(kind); e.frame()
    assert e.value(field)==475 and e.result()==1
    e.machine.mem_write(field,struct.pack('<f',30));e.frame()
    assert e.value(field)==30  # No repeated refill next frame.


def test_armour_trap_changes_only_current_armour():
    e=RewardEngine(); e.request(6);e.frame()
    assert e.value(ARMOUR)==0 and e.value(HP)==100
    assert e.value(MAX_ARMOUR)==100 and e.value(MAX_HP)==100 and e.result()==1


@pytest.mark.parametrize('difficulty,base',[(0,200),(1,100),(2,75)])
def test_permanent_capacity_survives_reload_without_stacking(difficulty,base):
    e=RewardEngine(); e.put(BASE+r.GAME_DIFFICULTY,difficulty)
    for field in (MAX_HP,MAX_ARMOUR):e.machine.mem_write(field,struct.pack('<f',base))
    e.put(REWARD+r.HEALTH_COUNT,4);e.put(REWARD+r.ARMOUR_COUNT,2);e.frame();e.frame()
    assert e.value(MAX_HP)==base+200 and e.value(MAX_ARMOUR)==base+100
    # Fresh save gets the floor again; a save with a higher campaign cap keeps it.
    e.machine.mem_write(MAX_HP,struct.pack('<f',base)); e.machine.mem_write(MAX_ARMOUR,struct.pack('<f',600));e.frame()
    assert e.value(MAX_HP)==base+200 and e.value(MAX_ARMOUR)==600
    assert e.value(HP)==100 and e.value(ARMOUR)==30


@pytest.mark.parametrize('difficulty,base',[(0,200),(1,100),(2,75)])
@pytest.mark.parametrize('count',range(1,6))
def test_each_capacity_upgrade_adds_fifty_without_healing(difficulty,base,count):
    e=RewardEngine();e.put(BASE+r.GAME_DIFFICULTY,difficulty)
    for field in (MAX_HP,MAX_ARMOUR):e.machine.mem_write(field,struct.pack('<f',base))
    for off in (r.HEALTH_COUNT,r.ARMOUR_COUNT):e.put(REWARD+off,count)
    e.frame();e.frame()
    assert e.value(MAX_HP)==e.value(MAX_ARMOUR)==base+count*50
    assert e.value(HP)==100 and e.value(ARMOUR)==30
    assert e.value(REWARD+r.TEN)==10  # Vampiric Rounds remains independent.


def test_legacy_rooms_and_duplicate_upgrade_receipts_cannot_exceed_five(tmp_path):
    async def run():
        c,sock,items=await reward_client(tmp_path)
        extra=[dict(item=ITEM_IDS[name],location=-1,player=1,flags=2) for name in UPGRADES for _ in range(19)]
        await c.handle(sock,dict(cmd='ReceivedItems',index=len(items),items=extra))
        data={};runtime=r.NativeRewards.__new__(r.NativeRewards)
        runtime.control=0;runtime.pending=None
        runtime.memory=SimpleNamespace(write_u32=lambda a,v:data.__setitem__(a,v))
        runtime.update(c,123,1,False)
        assert data[r.HEALTH_COUNT]==data[r.ARMOUR_COUNT]==5
    asyncio.run(run())


def test_ammo_only_visits_owned_weapons_and_keeps_ownership():
    e=RewardEngine();bag=OBJECT+4000;array=OBJECT+4100
    e.put(OBJECT+768+0x7b0,bag);e.put(bag+8,array);e.put(bag+20,4)
    weapons=[0,OBJECT+4200,OBJECT+4300,OBJECT+4400]
    for i,w in enumerate(weapons):
        e.put(array+4*i,w)
        if w:e.machine.mem_write(w+0x38,bytes([i!=2]))
    e.request(3);e.frame()
    assert e.refilled==[weapons[1],weapons[3]] and e.result()==1
    assert e.machine.mem_read(weapons[2]+0x38,1)==b'\0'


@pytest.mark.parametrize('reason',['paused','dead','menu','locked','stale','victory','cinematic','no_camera','no_control'])
def test_effects_wait_outside_active_unlocked_gameplay(reason):
    e=RewardEngine();e.request(6)
    if reason=='paused':e.put(OBJECT+4,1)
    if reason=='dead':e.health(0)
    if reason=='menu':e.put(BASE+s.STEAM.game_mode,0)
    if reason=='locked':e.set(s.ALLOWED,0)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='victory':e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\x01')
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='no_camera':e.put(BASE+s.COMBAT_CAMERA,0)
    if reason=='no_control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\x00')
    e.frame();assert e.value(ARMOUR)==30 and e.result()==0


def test_trap_waits_for_cinematic_to_end_then_applies_once():
    e=RewardEngine(); e.request(6)
    e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT); e.frame()
    assert e.value(ARMOUR)==30 and e.result()==0
    e.put(OBJECT+8192+s.CAMERA_HOST,0); e.frame()
    assert e.value(ARMOUR)==0 and e.result()==1
    e.machine.mem_write(ARMOUR,struct.pack('<f',30)); e.frame()
    assert e.value(ARMOUR)==30


def test_changed_player_rejects_effect():
    e=RewardEngine();e.request(6);e.put(REWARD+r.PLAYER,123);e.frame()
    assert e.result()==2 and e.value(ARMOUR)==30


async def reward_client(tmp_path):
    c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
    cfg=slot_data(3,1,True,False,True)
    await c.handle(sock,dict(cmd='RoomInfo',seed_name='rewards-test'))
    await c.handle(sock,dict(cmd='Connected',team=0,slot=1,slot_data=cfg,checked_locations=[],missing_locations=cfg['location_ids']))
    items=received(1)+[dict(item=ITEM_IDS[n],location=i,player=1,flags=ITEM_FLAGS[n]) for i,n in enumerate((*SUPPLIES,ARMOUR_TRAP,*UPGRADES))]
    await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items))
    return c,sock,items


def test_banked_supplies_and_spent_traps_survive_reconnect(tmp_path):
    async def run():
        c,sock,items=await reward_client(tmp_path)
        assert c.supply_indexes(HEALTH_SUPPLY)==[1]
        assert c.request_supply(HEALTH_SUPPLY) and not c.request_supply(AMMO_SUPPLY)
        c.progress.mark_supply(1); c.progress.mark_supply(4)
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items))
        other,_,_=await reward_client(tmp_path)
        assert not other.supply_indexes(HEALTH_SUPPLY) and not other.effect_indexes(ARMOUR_TRAP)
        assert other.supply_indexes(AMMO_SUPPLY)==[3]
        with pytest.raises(ValueError):other.progress.record_inventory(())
        other.progress.mark_supply(1,False)
        assert other.supply_indexes(HEALTH_SUPPLY)==[1]
    asyncio.run(run())


def test_protocol3_and_stable_ids():
    cfg=slot_data(3,1,False,False,True)
    assert validate_slot_data(cfg)==cfg and cfg['protocol_version']==3
    assert ITEM_IDS[CLEARANCE]==BASE_ID and ITEM_IDS[REPORT]==BASE_ID+1
    assert ITEM_FLAGS[ARMOUR_TRAP]==4 and all(ITEM_FLAGS[n]==2 for n in UPGRADES)
    cfg['supply_items']=False
    with pytest.raises(ValueError):validate_slot_data(cfg)


def test_dedicated_live_test_weakens_and_samples_on_game_thread():
    e=RewardEngine(testing=True)
    e.put(REWARD+r.TEST_ACTION,1);e.frame()
    assert e.value(HP)==25 and e.value(ARMOUR)==25
    assert int.from_bytes(e.machine.mem_read(REWARD+r.TEST_RESULT,4),'little')==1
    assert struct.unpack('<4f',e.machine.mem_read(REWARD+r.STATS,16))==(25,25,100,100)


def test_runtime_reserves_once_and_reject_refunds_without_dropping_queued_supply(tmp_path):
    async def run():
        c,_,_=await reward_client(tmp_path)
        data={}
        mem=SimpleNamespace(pid=123,u32=lambda a:data.get(a,0),write_u32=lambda a,v:data.__setitem__(a,v))
        runtime=r.NativeRewards.__new__(r.NativeRewards)
        runtime.memory=mem;runtime.control=0;runtime.pending=None;runtime.cookie='a'*32
        assert c.request_supply(HEALTH_SUPPLY)
        runtime.update(c,123,1,False)
        assert not c.progress.supply_spent
        runtime.update(c,123,1,True)
        assert c.progress.supply_spent=={4} and data[r.REQUEST]==6  # Automatic trap first.
        runtime.update(c,123,1,True)
        assert c.progress.supply_spent=={4}
        data[r.RESULT]=1;runtime.update(c,123,1,True)
        assert c.supply_request==HEALTH_SUPPLY
        runtime.update(c,123,1,True)
        assert c.progress.supply_spent=={1,4} and data[r.REQUEST]==1
        data[r.RESULT]=2;runtime.update(c,123,1,True)
        assert c.progress.supply_spent=={4} and c.supply_request is None
    asyncio.run(run())
