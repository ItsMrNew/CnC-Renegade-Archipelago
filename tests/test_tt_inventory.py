"""Execute TT inventory programs, including guard and receipt boundaries."""
import asyncio
import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP

from renegade.native import tt_inventory as t, rewards as r, services as s, weapons as w
from renegade.native.thunk import u32, CODE_OFFSET
from renegade.native.profile import UnsupportedBuild
from renegade.data import SUPPLIES, TRAPS, BUFFS, ITEM_IDS
from test_tt_discs import DiscEngine, PLAYER, CAMERA, MODE, DEF, DCTRL
from test_native import CONTROL, OBJECT
from test_tt_native import memory

REWARD, FRAME, WEAPON = 0x13000000, 0x14000000, 0x15000000
HP, MAX_HP, ARMOUR, MAX_ARMOUR = DEF+2048, DEF+2052, DEF+2056, DEF+2060
BAG, ARRAY = OBJECT+11000, OBJECT+11200


class RewardEngine(DiscEngine):
    def __init__(self, band=0x60000000):
        super().__init__(band=band)
        uc=self.uc
        for addr in (REWARD,FRAME,WEAPON): uc.mem_map(addr,8192)
        uc.mem_write(REWARD+CODE_OFFSET,t.build_rewards(REWARD,CONTROL,self.a,testing=True))
        uc.mem_write(REWARD+r.BASES,struct.pack('<4f',200,100,75,10))
        uc.mem_write(REWARD+r.UPGRADE_STEP,struct.pack('<f',50))
        uc.mem_write(WEAPON+CODE_OFFSET,b'\xc3')
        uc.mem_write(FRAME+CODE_OFFSET,t.build_frame(FRAME+CODE_OFFSET,FRAME,CONTROL,self.a,
            WEAPON+CODE_OFFSET,REWARD+CODE_OFFSET,DCTRL+8192))
        self.put(CONTROL+s.ONLINE,1)
        self.put(CONTROL+s.OBSERVER,self.a.objective_status)
        self.put(self.a.commands+0x204,self.a.objective_status)
        self.put(t.DIFFICULTY,1)
        for field,value in ((HP,25),(ARMOUR,30),(MAX_HP,100),(MAX_ARMOUR,100)): self.fput(field,value)
        for getter,field in ((t.GET_HEALTH,HP),(t.GET_MAX_HEALTH,MAX_HP),
                             (t.GET_ARMOUR,ARMOUR),(t.GET_MAX_ARMOUR,MAX_ARMOUR)):
            uc.mem_write(getter,b'\xd9\x05'+u32(field)+b'\xc3')
        for setter,field,offset,ret in ((t.SET_HEALTH,HP,8,b'\xc3'),(t.SET_ARMOUR,ARMOUR,8,b'\xc3'),
            (band+0x3D20,MAX_HP,4,b'\xc2\x04\0'),(band+0x3ED0,MAX_ARMOUR,4,b'\xc2\x04\0')):
            uc.mem_write(setter,b'\x8b\x44\x24'+bytes([offset])+b'\xa3'+u32(field)+ret)
        # SafeData getter stub returns the logical int stored at the fake handle.
        uc.mem_write(band+0x5E80,b'\x8b\x01\xc3')
        for offset in (t.SET_CLIP,t.SET_RESERVE):uc.mem_write(band+offset,b'\xc2\x04\0')
        self.put(PLAYER+0x7B0,BAG); self.put(BAG+8,ARRAY); self.put(BAG+20,1)
        self.next=OBJECT+11500
        self.refilled=[]
        def observe(uc,address,size,data):
            if address not in (band+t.SET_CLIP,band+t.SET_RESERVE): return
            ptr=uc.reg_read(UC_X86_REG_ECX); esp=uc.reg_read(UC_X86_REG_ESP)
            field=0x24 if address==band+t.SET_CLIP else 0x28
            self.put(ptr+field,self.get(esp+4))
            if field==0x24:self.refilled.append(ptr)
        uc.hook_add(UC_HOOK_CODE,observe)
        self.frame()

    def fput(self,addr,value): self.uc.mem_write(addr,struct.pack('<f',value))
    def fget(self,addr): return struct.unpack('<f',self.uc.mem_read(addr,4))[0]
    def get(self,addr): return int.from_bytes(self.uc.mem_read(addr,4),'little')
    def frame(self): self.execute(FRAME+CODE_OFFSET)
    def request(self,kind):
        for off,value in ((r.REQUEST,kind),(r.RESULT,0),(r.PLAYER,PLAYER),(r.MAP,1),
                          (t.REQUEST_GENERATION,self.read(s.GENERATION))): self.put(REWARD+off,value)
    def add_weapon(self,clip=5,reserve=20,clip_max=10,reserve_max=100,owned=True):
        ptr=self.next; self.next+=320; definition=ptr+128
        self.put(ptr,definition); self.put(ptr+0x24,clip); self.put(ptr+0x28,reserve & 0xFFFFFFFF)
        self.put(definition+0x74,clip_max); self.put(definition+0x84,reserve_max & 0xFFFFFFFF)
        self.uc.mem_write(ptr+0x38,bytes([owned]))
        count=self.get(BAG+20); self.put(ARRAY+count*4,ptr); self.put(BAG+20,count+1)
        return ptr


@pytest.mark.parametrize('band',[0x60000000,0x71000000])
@pytest.mark.parametrize('difficulty,base',[(0,200),(1,100),(2,75)])
@pytest.mark.parametrize('count',range(1,6))
def test_tt_upgrades_are_difficulty_floors_without_healing_or_stacking(band,difficulty,base,count):
    e=RewardEngine(band); e.put(t.DIFFICULTY,difficulty)
    for field in (MAX_HP,MAX_ARMOUR): e.fput(field,base)
    for offset in (r.HEALTH_COUNT,r.ARMOUR_COUNT):e.put(REWARD+offset,count)
    e.frame();e.frame()
    assert e.fget(MAX_HP)==e.fget(MAX_ARMOUR)==base+count*50
    assert (e.fget(HP),e.fget(ARMOUR))==(25,30)
    e.fput(MAX_HP,base);e.fput(MAX_ARMOUR,700);e.frame()
    assert e.fget(MAX_HP)==base+count*50 and e.fget(MAX_ARMOUR)==700


@pytest.mark.parametrize('kind,field,maximum',[(1,HP,MAX_HP),(2,ARMOUR,MAX_ARMOUR)])
def test_tt_supply_applies_once_and_full_supply_is_refunded(kind,field,maximum):
    e=RewardEngine(); e.fput(maximum,425);e.request(kind);e.frame()
    assert e.fget(field)==425 and e.get(REWARD+r.RESULT)==1
    e.request(kind);e.frame();assert e.get(REWARD+r.RESULT)==3
    e.fput(field,40);e.frame();assert e.fget(field)==40


@pytest.mark.parametrize('clip,reserve,maximum',[(4,100,100),(10,20,100),(4,-1,-1),(0,0,0)])
def test_tt_ammo_refills_clip_and_reserve_including_unlimited_and_placeables(clip,reserve,maximum):
    e=RewardEngine();ptr=e.add_weapon(clip=clip,reserve=reserve,reserve_max=maximum)
    unowned=e.add_weapon(owned=False)
    e.request(3);e.frame()
    assert e.refilled==[ptr] and e.get(REWARD+r.RESULT)==1
    assert e.get(ptr+0x24)==10 and e.get(ptr+0x28)==maximum & 0xFFFFFFFF
    assert e.get(unowned+0x24)==5
    e.request(3);e.frame();assert e.get(REWARD+r.RESULT)==3


def test_tt_ammo_preserves_existing_unlimited_reserve():
    e=RewardEngine();ptr=e.add_weapon(clip=2,reserve=-1,reserve_max=100)
    e.request(3);e.frame()
    assert e.get(ptr+0x24)==10 and e.get(ptr+0x28)==0xFFFFFFFF
    assert e.get(REWARD+r.RESULT)==1


@pytest.mark.parametrize('selected_empty',[False,True])
def test_tt_ammo_refills_completely_empty_laser_chaingun_alongside_other_owned_weapons(selected_empty):
    e=RewardEngine()
    pistol=e.add_weapon(clip=12,reserve=-1,clip_max=12,reserve_max=-1)
    laser=e.add_weapon(clip=0,reserve=0,clip_max=100,reserve_max=400)
    beacon=e.add_weapon(clip=0,reserve=0,clip_max=1,reserve_max=0)
    shotgun=e.add_weapon(clip=8,reserve=21,clip_max=8,reserve_max=32)
    unowned=e.add_weapon(clip=0,reserve=0,owned=False)
    e.put(BAG+28,2 if selected_empty else 1)
    e.request(3);e.frame()
    assert (e.get(laser+0x24),e.get(laser+0x28))==(100,400)
    assert (e.get(beacon+0x24),e.get(beacon+0x28))==(1,0)
    assert (e.get(shotgun+0x24),e.get(shotgun+0x28))==(8,32)
    assert (e.get(pistol+0x24),e.get(pistol+0x28))==(12,0xFFFFFFFF)
    assert (e.get(unowned+0x24),e.get(unowned+0x28))==(0,0)
    assert e.get(REWARD+r.RESULT)==1
    e.request(3);e.frame()
    assert e.get(REWARD+r.RESULT)==3  # One supply refills the whole bag; another is kept.


def test_tt_test_only_depletion_exercises_both_native_ammo_setters():
    e=RewardEngine();ptr=e.add_weapon(clip=10,reserve=100)
    pistol=e.add_weapon(clip=10,reserve=-1,reserve_max=-1)
    e.put(REWARD+r.TEST_ACTION,2);e.frame()
    assert (e.get(ptr+0x24),e.get(ptr+0x28))==(0,0)
    assert (e.get(pistol+0x24),e.get(pistol+0x28))==(0,0xFFFFFFFF)
    assert e.get(REWARD+r.NOT_FULL)==2 and e.get(REWARD+r.TEST_ACTION)==0


@pytest.mark.parametrize('reason',['paused','menu','multiplayer','cinematic','dead','control',
    'locked','stale','victory','offline','observer'])
def test_tt_supplies_wait_outside_authenticated_playable_state(reason):
    e=RewardEngine();e.request(1)
    changes={'paused':(MODE+4,1),'menu':(e.a.game_mode,0),'multiplayer':(e.a.game_mode,2),
        'cinematic':(CAMERA+s.CAMERA_HOST,OBJECT),'dead':(HP,0),'control':(PLAYER+s.CONTROL_ENABLED,0),
        'locked':(CONTROL+s.ALLOWED,0),'stale':(CONTROL+s.HEARTBEAT,0),
        'victory':(e.a.pending_continue,1),'offline':(CONTROL+s.ONLINE,0),
        'observer':(e.a.commands+0x204,0)}
    e.put(*changes[reason]);e.frame()
    assert e.get(REWARD+r.RESULT)==0 and e.get(REWARD+r.REQUEST)==1


@pytest.mark.parametrize('field,value',[(r.PLAYER,123),(r.MAP,7),(t.REQUEST_GENERATION,999)])
def test_tt_stale_player_map_or_load_request_refunds(field,value):
    e=RewardEngine();e.request(1);e.put(REWARD+field,value);e.frame()
    assert e.get(REWARD+r.RESULT)==2 and e.fget(HP)==25


def test_tt_hologram_receipt_waits_for_actual_spawn_acknowledgement():
    from renegade.native.bonuses import HOLO_ACTIVATION
    from renegade.native.traps import HOLOGRAM_END
    e=RewardEngine();e.request(13);e.frame()
    assert e.get(REWARD+r.RESULT)==0 and e.get(REWARD+HOLO_ACTIVATION)==1
    assert e.fget(REWARD+HOLOGRAM_END)==60 and e.fget(ARMOUR)==30


def test_tt_profile_rejects_changed_inventory_function_before_allocation(memory):
    memory.write(0x6F3C00,b'\xcc')
    before=memory.next_allocation
    with pytest.raises(UnsupportedBuild):t.NativeInventory(memory,CONTROL,memory.a,123)
    assert memory.next_allocation==before


def test_tt_controller_banks_unsupported_items_and_preserves_receipt_recovery(tmp_path):
    from test_rewards import reward_client
    from tools.tt_inventory_test import MaskOverride
    async def run():
        client,sock,items=await reward_client(tmp_path)
        proxy=MaskOverride(client)
        unsupported=[name for name in (*TRAPS,*BUFFS) if name not in t.NativeRewards.automatic_items]
        extra=[dict(item=ITEM_IDS[name],location=-1,player=1,flags=4) for name in unsupported]
        await client.handle(sock,dict(cmd='ReceivedItems',index=len(items),items=extra))
        data={}
        runtime=t.NativeRewards.__new__(t.NativeRewards)
        runtime.control=0;runtime.service=10000;runtime.pending=None;runtime.cookie='ab'*16
        runtime.memory=SimpleNamespace(pid=123,write_u32=lambda a,v:data.__setitem__(a,v),u32=lambda a:data.get(a,0))
        runtime.update(proxy,123,1,True)
        index,name=runtime.pending
        assert name==TRAPS[0] and data[r.REQUEST]==6
        data[r.RESULT]=1;runtime.update(proxy,123,1,True)
        assert index in client.progress.supply_spent
        assert runtime.pending is None and not client.progress.activations
        assert client.request_supply(SUPPLIES[0])
        runtime.update(proxy,123,1,True)
        index,name=runtime.pending
        assert name==SUPPLIES[0] and data[r.REQUEST]==1
        data[r.RESULT]=1;runtime.update(proxy,123,1,True)
        assert index in client.progress.supply_spent and str(index) not in client.progress.activations
        assert client.supply_request is None  # The live runner waits on the real client.
        assert not client.effect_indexes(TRAPS[0])
        assert all(client.effect_indexes(name) for name in unsupported)
    asyncio.run(run())


def weapon_engine(mask):
    from test_weapons import WeaponEngine, WEAPON_CONTROL
    from test_services import CONTROL as SERVICE
    from renegade.native.tt_profile import TTAddresses
    e=WeaponEngine(mask);b=0x60000000;a=TTAddresses(b,b+0x1000000)
    e.machine.mem_map(b,0x600000)
    bindings=t.weapon_bindings(a)
    # Route grant/name/selection to the existing ABI-checked fixture, while
    # executing TT's real removal body (including its internal index repair).
    for source in (w.ADD_WEAPON,w.SELECT_INDEX,w.SELECT_NAME):
        addr=bindings[source]
        e.machine.mem_write(addr,b'\xe9'+u32((0x400000+source-addr-5)&0xFFFFFFFF))
    removal=bytearray.fromhex(
        '5355568b742410578bf985f60f84bc0000008b57143bf20f8db10000008b4f1c33db3bf17d058d59ffeb17751e8d42ff3bf0750c83fa027e09bb01000000eb028bd96a008bcfe8550a00008b47088b2cb08b47143bf07d19488947143bf07d118b47088d0cb0468b410489013b77147cef85db7408538bcfe8230a000085ed74118bcde8e82a000055ff158494e96b83c4048b0f85c9740d8b410883c1086a016a03ff5038a134a41c6c8b400885c074058b4804eb0233c90fb605cf37ee6b390fba010000000f44c2a2cf37ee6b5f5e5d5bc20400')
    for old,rva in ((0x6BE99484,0x2C9484),(0x6C1CA434,0x5FA434),(0x6BEE37CF,0x3137CF)):
        removal=removal.replace(u32(old),u32(b+rva))
    e.machine.mem_write(bindings[w.REMOVE_WEAPON],bytes(removal))
    e.machine.mem_write(b+0x282070,b'\xc3')  # Destructor and allocator boundary.
    e.put(b+0x2C9484,b+0x1000);e.machine.mem_write(b+0x1000,b'\xc3')
    e.put(b+0x5FA434,b+0x1100)
    e.machine.mem_write(WEAPON_CONTROL+CODE_OFFSET,w.build_weapons(WEAPON_CONTROL,SERVICE,
        bindings=bindings,native_repairs_selection=True))
    return e


@pytest.mark.parametrize('index,weapon',list(enumerate(w.CATALOGUE)))
def test_tt_each_weapon_binding_grants_family_without_refilling(index,weapon):
    e=weapon_engine(1|(1<<index));e.frame()
    assert set(e.ids())=={1393,weapon.ids[0]}
    ptr=next(p for p in e.entries() if p and e.ident(p)==weapon.ids[0])
    e.put(ptr+0x50,0);e.frame()
    assert e.get(ptr+0x50)==0


def test_tt_actual_remove_repairs_index_exactly_once():
    from test_weapons import BAG
    e=weapon_engine(3)
    pistol=e.add(1393);rocket=e.add(1131);rifle=e.add(409610031)
    e.put(BAG+28,3);e.frame()
    assert e.ids()==[1393,409610031] and e.selected()==rifle
    assert e.selections==[0,2]
    e.vector([0,pistol,rifle,rocket]);e.put(BAG+28,3);e.frame()
    assert e.ids()==[1393,409610031] and e.selected()==pistol


def test_tt_hotkeys_use_live_bindings_and_respect_remapping(memory):
    from renegade.native import tt_hotkeys as h
    for i,key in enumerate((60,61,62),51):memory.write_u32(h.PRIMARY+4*i,key)
    down=set();requested=[]
    keys=h.NativeHotkeys(memory,sample=lambda:(True,set(down),False))
    client=SimpleNamespace(ready=True,supply_indexes=lambda name:[1],
        request_supply=lambda name:requested.append(name) or True)
    keys.update(client,True)
    down.add(0x71);keys.update(client,True)
    assert requested==[SUPPLIES[0]]
    down.clear();keys.update(client,True)
    memory.write_u32(h.SECONDARY,60)  # Rebind F2 to a gameplay action while attached.
    down.add(0x71);keys.update(client,True)
    assert requested==[SUPPLIES[0]] and keys.disabled=={0x71}
    down.clear();keys.update(client,True)
    down.add(0x72);keys.update(client,False)  # A paused press cannot activate later.
    keys.update(client,True)
    assert requested==[SUPPLIES[0]]


def test_tt_hotkeys_reject_unknown_binding_reader(memory):
    from renegade.native import tt_hotkeys as h
    memory.write(0x6763C0,b'\xcc')
    with pytest.raises(UnsupportedBuild):h.NativeHotkeys(memory,sample=lambda:(False,set(),False))


def test_live_test_status_reader_cannot_abort_native_test(tmp_path,monkeypatch):
    from pathlib import Path
    from tools.tt_inventory_test import publish_status
    path=tmp_path/'status.json'
    assert publish_status(path,{'status':'before'})
    original=Path.replace
    def busy(*args,**kwargs):raise PermissionError('Windows sharing violation')
    monkeypatch.setattr(Path,'replace',busy)
    assert not publish_status(path,{'status':'during'})
    assert 'before' in path.read_text()
    monkeypatch.setattr(Path,'replace',original)
    assert publish_status(path,{'status':'after'})
    assert 'after' in path.read_text()
