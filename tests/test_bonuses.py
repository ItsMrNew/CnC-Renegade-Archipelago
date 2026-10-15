import asyncio
import struct
from types import SimpleNamespace
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESI, UC_X86_REG_ESP, UC_X86_REG_EBP
from renegade.native import bonuses as b, traps as t, services as s, rewards as r
from renegade.data import *
from test_traps import TrapEngine, CLOCK
from test_rewards import REWARD, HP, ARMOUR, MAX_HP, MAX_ARMOUR, reward_client
from test_services import CONTROL, OBJECT, BASE, STACK, RETURN

WEAPON, DATA, ALLY = OBJECT+4200, OBJECT+10000, OBJECT+12000
EFFECT, MATERIAL, MATERIAL_DATA, BAG = 0x24000000, 0x24000200, 0x24000300, 0x24000400


class BonusEngine(TrapEngine):
    def __init__(self):
        super().__init__()
        uc=self.machine
        self.codes={}
        for i,name in enumerate(('weapon','ammo','loaded','reload','save','update','damage','holo_damage','holo_name','holo_render')):
            address=0x17001000+i*0x10000
            uc.mem_map(address-4096,8192)
            uc.mem_write(address,getattr(b,'build_'+name)(address,REWARD,CONTROL))
            self.codes[name]=address
        b.initialise(SimpleNamespace(write=uc.mem_write),REWARD,self.codes['holo_render'])
        uc.mem_map(EFFECT,4096)
        self.put(ALLY+0x7b0,BAG)
        self.put(EFFECT+0x54,EFFECT+256);self.put(EFFECT+256+0x14,MATERIAL)
        self.put(MATERIAL+8,MATERIAL_DATA)
        self.put(EFFECT+8,EFFECT+512);self.put(EFFECT+512,BASE+0x380300)
        uc.mem_write(BASE+0x380300,b'\xc3')
        self.weapon_ok=True;self.effect_ok=True;self.effect_added=True
        self.damage_calls=0;self.name_source=None;self.effects_released=0;self.render_calls=0
        uc.mem_write(CONTROL+s.CODE_OFFSET,s.build_services(CONTROL,reward_target=REWARD+r.CODE_OFFSET,
                     clock_target=CLOCK,bonus_target=self.codes['update']))
        uc.ctl_remove_cache(CONTROL+s.CODE_OFFSET,CONTROL+8192)
        self.put(WEAPON+12,OBJECT+11000);self.put(OBJECT+11004,OBJECT+768)
        self.put(ALLY+12,12345)
        self.put(OBJECT+768+t.PLAYER_PHYSICS,OBJECT+7000)
        self.put(ALLY+t.PLAYER_PHYSICS,OBJECT+7500)
        self.teleport_results=[];self.placements=[];self.spawn_position=None
        self.holo=False;self.spawn_ok=True;self.native_calls=[];self.weapon_calls=[];self.saved=[]
        for function in (b.CREATE,b.FIND,b.DESTROY,b.GET_POSITION,b.SET_TEAM,b.SET_MODEL,b.INNATE_ON,b.INNATE_OFF,
                         b.ACTION_GOTO,b.ACTION_RESET,b.HOME,b.AGGRESSION,b.DELETE_PENDING,b.SAVE_OBJECTS,
                         b.SAFE_INT_SET,b.SAFE_INT_GET,b.CLEAR_WEAPONS,b.SELECT_WEAPON,b.SPAWN_EFFECT):
            uc.mem_write(BASE+function,b'\xc3')
        for function,argc in ((b.ADD_WEAPON,12),(b.EFFECT_ADD,8),(b.EFFECT_RENDER,8),
                              (b.DEFENSE_DAMAGE,12),(b.WIDE_ASSIGN,4)):
            uc.mem_write(BASE+function,b'\xc2'+struct.pack('<H',argc))
        uc.mem_write(BASE+b.EMISSIVE,b.ANCHORS[b.EMISSIVE])
        for function in (b.UPDATE_STATE,b.SAFE_SUBTRACT):uc.mem_write(BASE+function,b'\xc2\x04\x00')
        uc.mem_write(BASE+b.IS_ENEMY,b'\xc2\x04\x00')
        uc.mem_write(BASE+b.FIND_TELEPORT,b'\xc2\x0c\x00')
        uc.mem_write(BASE+b.SET_COLLISION_GROUP,b.ANCHORS[b.SET_COLLISION_GROUP])
        uc.mem_write(BASE+b.DEFENSE_HEALTH,b'\xd9\xe8\xc3')  # Return a native float; wrapper must preserve it.
        self.enemy=True;self.health_reads=0
        def observe(machine,address,size,data):
            esp=machine.reg_read(UC_X86_REG_ESP)
            def arg(i=0):return int.from_bytes(machine.mem_read(esp+4+i*4,4),'little')
            def result(value):machine.reg_write(UC_X86_REG_EAX,value)
            if address==BASE+b.CREATE:
                assert bytes(machine.mem_read(arg(),17))==b'GDI_MiniGunner_0\0'
                assert arg(1)==REWARD+b.SPAWN_POSITION
                self.spawn_position=struct.unpack('<3f',machine.mem_read(arg(1),12))
                self.holo=self.spawn_ok;result(ALLY if self.holo else 0)
                self.native_calls.append('create')
            elif address==BASE+b.FIND:
                assert arg()==12345
                result(ALLY if self.holo else 0)
            elif address==BASE+b.DESTROY:
                assert arg()==ALLY;self.native_calls.append('destroy')
            elif address==BASE+b.DELETE_PENDING:self.holo=False;self.native_calls.append('delete')
            elif address==BASE+b.GET_POSITION:
                assert arg(1)==OBJECT+768
                machine.mem_write(arg(),struct.pack('<3f',10,20,30));result(arg())
            elif address==BASE+b.FIND_TELEPORT:
                assert machine.reg_read(UC_X86_REG_ECX)==OBJECT+7000
                assert arg()==REWARD+b.SPAWN_CANDIDATE and arg(1)==0x3f000000
                assert arg(2)==REWARD+b.SPAWN_POSITION
                self.placements.append(struct.unpack('<3f',machine.mem_read(arg(),12)))
                success=self.teleport_results.pop(0) if self.teleport_results else True
                if success:machine.mem_write(arg(2),bytes(machine.mem_read(arg(),12)))
                result(int(success))
            elif address==BASE+b.IS_ENEMY:
                assert machine.reg_read(UC_X86_REG_ECX)==OBJECT+768 and arg()==ALLY
                result(int(self.enemy))
            elif address==BASE+b.DEFENSE_HEALTH:self.health_reads+=1
            elif address==BASE+b.DEFENSE_DAMAGE:self.damage_calls+=1
            elif address==BASE+b.WIDE_ASSIGN:self.name_source=arg()
            elif address==BASE+b.CLEAR_WEAPONS:
                assert arg()==ALLY;self.native_calls.append('clear_weapons')
            elif address==BASE+b.ADD_WEAPON:
                assert machine.reg_read(UC_X86_REG_ECX)==BAG
                assert bytes(machine.mem_read(arg(),len(b'Weapon_VoltAutoRifle_Ai\0')))==b'Weapon_VoltAutoRifle_Ai\0'
                assert arg(1)==0xffffffff and arg(2)==1
                self.native_calls.append('volt');result(WEAPON if self.weapon_ok else 0)
            elif address==BASE+b.SELECT_WEAPON:
                assert arg()==ALLY and arg(1)==REWARD+b.HOLO_WEAPON
                self.native_calls.append('select_volt')
            elif address==BASE+b.SPAWN_EFFECT:
                self.put(EFFECT+12,1);result(EFFECT if self.effect_ok else 0)
                self.native_calls.append('effect')
            elif address==BASE+b.EFFECT_ADD:
                assert machine.reg_read(UC_X86_REG_ECX)==OBJECT+7500+0x54
                assert arg()==EFFECT and arg(1)==1;result(int(self.effect_added))
            elif address==BASE+0x380300:
                assert machine.reg_read(UC_X86_REG_ECX)==EFFECT+8
                self.effects_released+=1
            elif address==BASE+b.EFFECT_RENDER:
                assert machine.reg_read(UC_X86_REG_ECX)==EFFECT
                assert arg()==DATA and arg(1)==OBJECT+7500
                self.render_calls+=1
            elif address in (BASE+b.SET_TEAM,BASE+b.SET_MODEL,BASE+b.INNATE_ON,BASE+b.INNATE_OFF,
                             BASE+b.ACTION_GOTO,BASE+b.ACTION_RESET,BASE+b.HOME,BASE+b.AGGRESSION):
                assert arg()==ALLY
                self.native_calls.append(address-BASE)
                if address==BASE+b.ACTION_GOTO:
                    params=arg(1)
                    assert arg(1)==REWARD+b.ACTION
                    assert int.from_bytes(machine.mem_read(params+44,4),'little')==OBJECT+768
                    assert bytes(machine.mem_read(params+68,4))==b'\0\1\0\1'
                    assert int.from_bytes(machine.mem_read(params,4),'little')==80
                if address==BASE+b.SET_TEAM:assert arg(1)==1
                if address==BASE+b.HOME:
                    assert arg(1)==REWARD+b.POSITION and arg(2)==0x41400000
            elif address==BASE+b.UPDATE_STATE:
                assert machine.reg_read(UC_X86_REG_ECX)==WEAPON
                self.weapon_calls.append(struct.unpack('<f',machine.mem_read(esp+4,4))[0])
            elif address==BASE+b.SAFE_SUBTRACT:self.weapon_calls.append('consume')
            elif address==BASE+b.SAFE_INT_GET:
                self.weapon_calls.append('ordinary_loaded');self.put(arg(1),OBJECT+11100);result(1)
            elif address==BASE+b.SAFE_INT_SET:
                self.weapon_calls.append(('burst',arg(),int.from_bytes(machine.mem_read(arg(1),4),'little')))
            elif address==BASE+b.SAVE_OBJECTS:
                node=int.from_bytes(machine.mem_read(BASE+b.OBJECT_LIST,4),'little')
                while node:
                    self.saved.append(int.from_bytes(machine.mem_read(node+4,4),'little'))
                    node=int.from_bytes(machine.mem_read(node,4),'little')
                result(0)  # Even a failed save must restore the live list.
        uc.hook_add(UC_HOOK_CODE,observe)

    def invoke_bonus(self,name,args=(),this=WEAPON):
        uc=self.machine
        uc.reg_write(UC_X86_REG_ECX,this);uc.reg_write(UC_X86_REG_ESI,WEAPON)
        uc.reg_write(UC_X86_REG_ESP,STACK);self.put(STACK,RETURN)
        for i,arg in enumerate(args):self.put(STACK+4+i*4,arg)
        uc.emu_start(self.codes[name],RETURN,count=20000)
        popped={'weapon':4,'ammo':4,'holo_damage':12,'holo_name':4,'holo_render':8}.get(name,0)
        assert uc.reg_read(UC_X86_REG_ESP)==STACK+4+popped

    def hit(self,damage=1,attacker=OBJECT+768):
        uc=self.machine
        self.put(DATA+0x14,DATA+100);self.put(DATA+104,attacker)
        uc.reg_write(UC_X86_REG_ECX,ALLY+0x72c);uc.reg_write(UC_X86_REG_ESI,ALLY)
        uc.reg_write(UC_X86_REG_EBP,DATA);uc.reg_write(UC_X86_REG_ESP,STACK)
        self.put(STACK,RETURN);uc.mem_write(STACK+0x28,struct.pack('<f',damage))
        # Execute the preserved getter result too, avoiding an accumulating x87 stack.
        uc.mem_write(RETURN,b'\xd9\x1d'+s.u32(OBJECT+12500))
        uc.emu_start(self.codes['damage'],RETURN+6,count=20000)
        assert uc.reg_read(UC_X86_REG_ESP)==STACK+4
        assert uc.reg_read(UC_X86_REG_ESI)==ALLY and uc.reg_read(UC_X86_REG_EBP)==DATA
        assert self.value(OBJECT+12500)==1


def test_bullet_hose_firearm_rate_burst_and_no_ammo():
    e=BonusEngine();e.start(11)
    e.put(WEAPON+0x30,123)
    before=e.machine.mem_read(WEAPON+0x24,8)
    e.invoke_bonus('weapon',(0x3c800000,))
    assert e.weapon_calls==[('burst',123,0xffffffff),.03125]
    e.invoke_bonus('ammo',(OBJECT,),WEAPON+0x24)
    assert e.machine.mem_read(WEAPON+0x24,8)==before and 'consume' not in e.weapon_calls
    e.invoke_bonus('loaded',(123,OBJECT+11104))
    assert int.from_bytes(e.machine.mem_read(OBJECT+11104,4),'little')==REWARD+b.ONE
    e.invoke_bonus('reload');assert e.reloads==0
    # Forced/manual reload still follows the normal engine call; this hook only
    # intercepts Update_State's automatic empty-clip reload.
    e.put(WEAPON+0x3c,5);e.invoke_bonus('weapon',(0x3c800000,))
    assert e.weapon_calls[-1]==.015625
    e.advance(60);e.weapon_calls.clear();e.invoke_bonus('ammo',(OBJECT,),WEAPON+0x24)
    assert e.weapon_calls==['consume']


@pytest.mark.parametrize('bullet_hose',[False,True])
def test_butterfingers_blocks_held_primary_and_secondary_through_final_reload(bullet_hose):
    e=BonusEngine()
    if bullet_hose:e.start(11)
    e.start(7)
    def fire_is_blocked():
        e.machine.mem_write(WEAPON+0x47,b'\1\1')  # Engine input reasserted every frame.
        e.invoke_bonus('weapon',(0x3c800000,))
        assert e.machine.mem_read(WEAPON+0x47,2)==b'\0\0'
        assert e.weapon_calls[-1]==.015625  # Reloads keep their normal speed.
    fire_is_blocked()  # Before the first reload starts.
    for cycle in range(1,6):
        e.frame();assert e.reloads==cycle
        for _ in range(3):fire_is_blocked();e.frame()
        e.put(WEAPON+0x3c,1)  # Ready gap after this reload, before next service frame.
        fire_is_blocked()
        assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOADS,4),'little')==6-cycle
    e.frame()
    assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOADS,4),'little')==0
    e.machine.mem_write(WEAPON+0x47,b'\1\1');e.invoke_bonus('weapon',(0x3c800000,))
    assert e.machine.mem_read(WEAPON+0x47,2)==b'\1\1'


@pytest.mark.parametrize('state',[0,1,2,3,4,5,6,7])
def test_butterfingers_blocks_charge_and_switch_gaps_without_changing_weapon_state(state):
    e=BonusEngine();e.start(7);e.put(WEAPON+0x3c,state)
    e.machine.mem_write(WEAPON+0x47,b'\1\1');e.invoke_bonus('weapon',(0x3c800000,))
    assert e.machine.mem_read(WEAPON+0x47,2)==b'\0\0'
    assert int.from_bytes(e.machine.mem_read(WEAPON+0x3c,4),'little')==state


@pytest.mark.parametrize('reason',['npc','stale','disabled','load','cutscene','paused'])
def test_butterfingers_fire_lock_ownership_and_lifetime(reason):
    e=BonusEngine();e.start(7)
    if reason=='npc':e.put(OBJECT+11004,ALLY)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='disabled':e.set(s.ENABLED,0)
    if reason=='load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if reason=='cutscene':e.put(OBJECT+8192+s.CAMERA_HOST,ALLY)
    if reason=='paused':e.set(s.PLAYABLE,0)
    e.machine.mem_write(WEAPON+0x47,b'\1\1');e.invoke_bonus('weapon',(0x3c800000,))
    assert e.machine.mem_read(WEAPON+0x47,2)==b'\1\1'


@pytest.mark.parametrize('style',[0,6])
def test_butterfingers_cannot_be_bypassed_by_switching_to_placeables(style):
    e=BonusEngine();e.start(7);e.put(OBJECT+4600+0x1c,style)
    e.frame();assert e.reloads==0
    e.machine.mem_write(WEAPON+0x47,b'\1\1');e.invoke_bonus('weapon',(0x3c800000,))
    assert e.machine.mem_read(WEAPON+0x47,2)==b'\0\0'


@pytest.mark.parametrize('style',[0,1,6,7,8,9])
def test_bullet_hose_excludes_c4_beacons_and_other_equipment(style):
    e=BonusEngine();e.start(11);e.put(OBJECT+4600+0x1c,style)
    e.invoke_bonus('weapon',(0x3c800000,));e.invoke_bonus('ammo',(OBJECT,),WEAPON+0x24)
    assert e.weapon_calls==[.015625,'consume']


@pytest.mark.parametrize('reason',['npc','vehicle','paused','cutscene','no_control','stale','load','disabled'])
def test_bullet_hose_guards(reason):
    e=BonusEngine();e.start(11)
    if reason=='npc':e.put(OBJECT+11004,ALLY)
    if reason=='vehicle':e.put(OBJECT+768+t.HUMAN_STATE,9)
    if reason=='paused':e.set(s.PLAYABLE,0)
    if reason=='cutscene':e.put(OBJECT+8192+s.CAMERA_HOST,ALLY)
    if reason=='no_control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\0')
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if reason=='disabled':e.set(s.ENABLED,0)
    e.invoke_bonus('weapon',(0x3c800000,))
    assert e.weapon_calls==[.015625]


def test_vampiric_enemy_hits_heal_without_a_kill_and_clamp():
    e=BonusEngine();e.machine.mem_write(HP,struct.pack('<f',70));e.start(12)
    assert e.value(HP)==70 and e.value(ARMOUR)==30
    e.hit();assert e.value(HP)==80 and e.value(ARMOUR)==40
    e.frame();assert e.value(HP)==80  # A hit is not applied again by the frame poll.
    for _ in range(8):e.hit()
    assert e.value(HP)==100 and e.value(ARMOUR)==100 and e.health_reads==9


@pytest.mark.parametrize('reason',['ally','npc','no_attacker','repair','blocked','nan','infinity',
                                  'expired','not_active','paused','cutscene','no_control','load','stale','dead','disabled'])
def test_vampiric_rejects_non_enemy_damage_and_inactive_gameplay(reason):
    e=BonusEngine();e.start(12);damage=1;attacker=OBJECT+768
    if reason=='ally':e.enemy=False
    if reason=='npc':attacker=ALLY
    if reason=='no_attacker':attacker=0
    if reason=='repair':damage=-10
    if reason=='blocked':damage=0
    if reason=='nan':damage=float('nan')
    if reason=='infinity':damage=float('inf')
    if reason=='expired':e.advance(60)
    if reason=='not_active':e.put(REWARD+t.VAMPIRE_END,0)
    if reason=='paused':e.set(s.PLAYABLE,0)
    if reason=='cutscene':e.put(OBJECT+8192+s.CAMERA_HOST,ALLY)
    if reason=='no_control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\0')
    if reason=='load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if reason=='stale':e.set(s.HEARTBEAT,0)
    if reason=='dead':e.health(0)  # ALIVE cache is still true; no revival.
    if reason=='disabled':e.set(s.ENABLED,0)
    before=(e.value(HP),e.value(ARMOUR))
    e.hit(damage,attacker)
    assert (e.value(HP),e.value(ARMOUR))==before and e.health_reads==1


def test_vampiric_never_replays_hits_before_receipt_or_refresh():
    e=BonusEngine();e.hit();e.start(12)
    assert e.value(ARMOUR)==30
    e.hit();assert e.value(ARMOUR)==40
    e.start(12);e.frame();assert e.value(ARMOUR)==40


def test_hologram_follows_pauses_refreshes_and_cleans_up_at_expiry():
    e=BonusEngine();e.start(13)
    assert e.holo and e.native_calls.count('create')==1 and b.ACTION_GOTO in e.native_calls
    e.advance(20);e.put(OBJECT+4,1);e.advance(120)
    assert e.holo and e.value(REWARD+t.HOLOGRAM_END)==40
    assert e.native_calls.count(b.INNATE_OFF)==1
    e.put(OBJECT+4,0);e.start(13)
    assert e.native_calls.count('create')==1 and b.INNATE_ON in e.native_calls
    e.advance(99.75);assert e.holo
    e.advance(.25);assert not e.holo and e.native_calls[-2:]==['destroy','delete']


def test_hologram_spawns_beside_player_and_cannot_block_after_collision_reset():
    from test_traps import MOVE
    e=BonusEngine();e.start(13)
    assert e.spawn_position==(12.5,20,30)
    physics=OBJECT+7500
    assert int.from_bytes(e.machine.mem_read(physics+0x38,4),'little')==b.HOLO_COLLISION_GROUP|b.PHYS_DONT_SAVE
    # Real collision setter executes, preserving all non-group physics flags.
    for old_group in (6,7):
        e.put(physics+0x38,0xabc0|old_group);e.invoke(MOVE,ALLY)
        assert int.from_bytes(e.machine.mem_read(physics+0x38,4),'little')==0xabc4
    e.put(ALLY+12,98765);e.put(physics+0x38,6);e.invoke(MOVE,ALLY)
    assert int.from_bytes(e.machine.mem_read(physics+0x38,4),'little')==6


def test_hologram_tries_other_sides_when_spawn_position_is_obstructed():
    e=BonusEngine();e.teleport_results=[False,False,True];e.start(13)
    assert e.placements==[(12.5,20,30),(7.5,20,30),(10,22.5,30)]
    assert e.spawn_position==(10,22.5,30)


def test_hologram_refunds_if_no_standing_room_without_spawning_inside_player():
    e=BonusEngine();e.teleport_results=[False]*4;e.request(13);e.frame()
    assert len(e.placements)==4 and not e.holo and e.result()==2
    assert 'create' not in e.native_calls


@pytest.mark.parametrize('reason',['disconnect','death','victory','stale'])
def test_hologram_cleanup_without_consuming_a_full_minute(reason):
    e=BonusEngine();e.start(13)
    if reason=='disconnect':e.set(s.ENABLED,0)
    if reason=='death':e.health(0)
    if reason=='victory':e.machine.mem_write(BASE+s.STEAM.pending_continue,b'\1')
    if reason=='stale':e.set(s.HEARTBEAT,0)
    e.frame();assert not e.holo


def test_hologram_does_not_delete_reused_pointer_on_load():
    e=BonusEngine();e.start(13);e.set(s.GENERATION,e.read(s.GENERATION)+1);e.frame()
    assert 'destroy' not in e.native_calls
    assert int.from_bytes(e.machine.mem_read(REWARD+b.HOLO_PTR,4),'little')==0


def test_failed_hologram_spawn_refunds_request():
    e=BonusEngine();e.spawn_ok=False;e.request(13);e.frame()
    assert e.result()==2 and e.value(REWARD+t.HOLOGRAM_END)==0 and not e.holo


@pytest.mark.parametrize('failure',['weapon','effect','attach'])
def test_incomplete_hologram_setup_destroys_ally_and_refunds(failure):
    e=BonusEngine()
    if failure=='weapon':e.weapon_ok=False
    if failure=='effect':e.effect_ok=False
    if failure=='attach':e.effect_added=False
    e.request(13);e.frame()
    assert e.result()==2 and not e.holo and e.value(REWARD+t.HOLOGRAM_END)==0
    assert e.native_calls[-2:]==['destroy','delete']
    assert e.effects_released==(1 if failure=='attach' else 0)


def test_hologram_has_selected_volt_rifle_and_private_persistent_cyan_effect():
    e=BonusEngine();e.start(13)
    assert e.native_calls.index('clear_weapons')<e.native_calls.index('volt')<e.native_calls.index('select_volt')
    assert int.from_bytes(e.machine.mem_read(EFFECT+12,4),'little')==1  # Sole physics-list reference.
    assert int.from_bytes(e.machine.mem_read(EFFECT,4),'little')==REWARD+b.EFFECT_VTABLE
    assert struct.unpack('<4I',e.machine.mem_read(REWARD+b.EFFECT_VTABLE,16))==(
        BASE+b.NATIVE_EFFECT_VTABLE[0],BASE+b.NATIVE_EFFECT_VTABLE[1],
        e.codes['holo_render'],BASE+b.NATIVE_EFFECT_VTABLE[3])
    assert e.machine.mem_read(EFFECT+0x10,2)==b'\0\1'  # Never autoremove; suppress shadows.
    assert e.machine.mem_read(EFFECT+0x40,3)==b'\0\0\1'  # No timed removal or opaque base skin.
    assert e.value(EFFECT+0x14)==pytest.approx(.65) and e.value(EFFECT+0x18)==pytest.approx(.65)
    assert e.value(EFFECT+0x1c)==0
    e.invoke_bonus('holo_render',(DATA,OBJECT+7500),EFFECT)
    assert e.render_calls==1
    assert struct.unpack('<3f',e.machine.mem_read(MATERIAL_DATA+0x30,12))==pytest.approx((.084,.56,.7))
    e.start(13)
    assert e.native_calls.count('volt')==1 and e.native_calls.count('effect')==1


@pytest.mark.parametrize('case',['owned','paused','cleanup','other_actor','reused_id','load','observer','removed'])
def test_hologram_name_and_invulnerability_are_scoped_to_live_summon(case):
    e=BonusEngine();e.start(13);actor=ALLY
    if case=='paused':e.set(s.PLAYABLE,0)
    if case=='cleanup':e.put(REWARD+b.CLEANUP,1);e.set(s.ENABLED,0)
    if case=='other_actor':actor=OBJECT+768
    if case=='reused_id':e.put(ALLY+12,54321)
    if case=='load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    if case=='observer':e.put(BASE+s.STEAM.commands+0x204,123)
    if case=='removed':e.put(REWARD+b.HOLO_PTR,0)
    owned=case in ('owned','paused','cleanup')
    e.machine.reg_write(UC_X86_REG_EBP,DATA);e.put(DATA-0x6c,actor)
    e.invoke_bonus('holo_name',(DATA+100,),DATA+200)
    assert e.name_source==(REWARD+b.HOLO_NAME if owned else DATA+100)
    assert bytes(e.machine.mem_read(REWARD+b.HOLO_NAME,30)).decode('utf-16le').rstrip('\0')=='Hologram Havoc'
    e.invoke_bonus('holo_damage',(DATA,0x3f800000,0xffffffff),actor+0x72c)
    assert e.damage_calls==int(not owned) and e.health_reads==int(owned)


@pytest.mark.parametrize('position',[0,1,2])
def test_bodyguard_excluded_from_save_and_list_restored(position):
    e=BonusEngine();e.start(13)
    objects=[OBJECT+100,OBJECT+200];objects.insert(position,ALLY)
    nodes=[OBJECT+11200+i*16 for i in range(3)]
    for i,node in enumerate(nodes):e.put(node,nodes[i+1] if i<2 else 0);e.put(node+4,objects[i])
    e.put(BASE+b.OBJECT_LIST,nodes[0])
    before=e.machine.mem_read(nodes[0],48)
    e.invoke_bonus('save',(OBJECT,))
    assert e.saved==[OBJECT+100,OBJECT+200]
    assert e.machine.mem_read(nodes[0],48)==before
    assert int.from_bytes(e.machine.mem_read(BASE+b.OBJECT_LIST,4),'little')==nodes[0]


def test_hologram_marks_only_its_physics_transient_before_acknowledgement():
    e=BonusEngine();physics=OBJECT+7500;player_physics=OBJECT+7000
    e.put(physics+0x38,0x109003);e.put(player_physics+0x38,0x109003)
    e.start(13)
    assert e.result()==1
    assert int.from_bytes(e.machine.mem_read(physics+0x38,4),'little')==0x10D004
    assert int.from_bytes(e.machine.mem_read(player_physics+0x38,4),'little')==0x109003
    e.put(OBJECT+4,1);e.advance(120)
    assert int.from_bytes(e.machine.mem_read(physics+0x38,4),'little')&b.PHYS_DONT_SAVE


@pytest.mark.parametrize('kind,offset',[(8,t.CONCRETE_END),(9,t.DISCO_END),(10,t.MOON_END),
                                      (11,t.BULLET_END),(12,t.VAMPIRE_END),(13,t.HOLOGRAM_END)])
@pytest.mark.parametrize('pause',['menu','cutscene','control'])
def test_all_effect_timers_freeze_without_player_control(kind,offset,pause):
    e=BonusEngine();e.start(kind);e.advance(5)
    before=e.value(REWARD+offset)
    if pause=='menu':e.put(OBJECT+4,1)
    if pause=='cutscene':e.put(OBJECT+8192+s.CAMERA_HOST,ALLY)
    if pause=='control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\0')
    e.advance(120);e.tick(130000)
    assert e.value(REWARD+offset)==before
    e.put(OBJECT+4,0);e.put(OBJECT+8192+s.CAMERA_HOST,0)
    e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\1')
    e.advance(before-.25);assert e.value(REWARD+offset)==.25
    e.advance(.25);assert e.value(REWARD+offset)==0


def test_protocol5_and_helpful_receipts(tmp_path):
    cfg=slot_data(3,1,True,True,True,True,True)
    assert validate_slot_data(cfg)==cfg and cfg['protocol_version']==5
    cfg.pop('temporary_bonuses')
    with pytest.raises(ValueError):validate_slot_data(cfg)
    assert [ITEM_IDS[n] for n in BUFFS]==list(range(BASE_ID+12,BASE_ID+15))
    assert all(ITEM_FLAGS[n]==0 for n in BUFFS)
    async def run():
        c,sock,items=await reward_client(tmp_path)
        packet=[dict(item=ITEM_IDS[n],location=-1,player=1,flags=2) for n in BUFFS]
        await c.handle(sock,dict(cmd='ReceivedItems',index=len(items),items=packet))
        for name in BUFFS:c.progress.mark_supply(c.effect_indexes(name)[0])
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items+packet))
        assert all(not c.effect_indexes(n) for n in BUFFS)
    asyncio.run(run())


def test_helpful_hook_install_restore_and_ownership():
    class Memory:
        def __init__(self):
            self.data={};self.next=0x22000000;self.batches=0
            for rva,data in b.ANCHORS.items():self.write(BASE+rva,data)
            for rva,target,_ in b.CALLS:self.write(BASE+rva,b.call_bytes(BASE+rva,BASE+target))
        def write(self,address,data):self.data.update({address+i:v for i,v in enumerate(data)})
        def read(self,address,size):return bytes(self.data.get(address+i,0) for i in range(size))
        def u32(self,address):return int.from_bytes(self.read(address,4),'little')
        def write_u32(self,address,value):self.write(address,s.u32(value))
        def allocate(self):self.next+=8192;return self.next
        def make_executable(self,address):pass
        def replace_calls(self,changes):
            self.batches+=1
            assert all(self.read(a,5)==old for a,old,new in changes)
            for a,old,new in changes:self.write(a,new)
    memory=Memory();runtime=b.NativeBonuses(memory,REWARD,CONTROL)
    runtime.update();runtime.update();assert memory.batches==1
    assert all(memory.read(a,5)==new for a,old,new in runtime.entries)
    runtime.close();assert all(memory.read(a,5)==old for a,old,new in runtime.entries)
    runtime.update()
    foreign=b.call_bytes(BASE+b.CALLS[0][0],0x22222222)
    memory.write(BASE+b.CALLS[0][0],foreign)
    with pytest.raises(b.MemoryError):runtime.update()
    runtime.close();assert memory.read(BASE+b.CALLS[0][0],5)==foreign


def test_bodyguard_acknowledgement_waits_for_spawn():
    e=BonusEngine();e.request(13)
    # Execute only the reward callback before the bonus helper gets its frame.
    uc=e.machine
    from unicorn.x86_const import UC_X86_REG_EDI
    uc.reg_write(UC_X86_REG_ESI,OBJECT+768);uc.reg_write(UC_X86_REG_EDI,1)
    uc.reg_write(UC_X86_REG_ESP,STACK);e.put(STACK,RETURN)
    uc.emu_start(REWARD+r.CODE_OFFSET,RETURN,count=20000)
    assert e.result()==0 and not e.holo
    e.frame();assert e.result()==1 and e.holo
