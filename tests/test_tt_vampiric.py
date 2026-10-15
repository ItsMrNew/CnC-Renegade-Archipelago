import struct
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX,UC_X86_REG_EBX,UC_X86_REG_ECX,
    UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP,UC_X86_REG_ESP,UC_X86_REG_XMM0)
from renegade.native import tt_vampiric as v, traps as t, services as s
from renegade.native.bonuses import call_bytes
from renegade.native.thunk import u32,CODE_OFFSET
from renegade.native.profile import UnsupportedBuild
from test_tt_inventory import RewardEngine,REWARD,HP,ARMOUR,MAX_HP,MAX_ARMOUR
from renegade.native.tt_inventory import GET_HEALTH
from test_tt_discs import PLAYER,MODE,CAMERA,DEF
from test_native import CONTROL,OBJECT,STACK,RETURN
from test_tt_native import memory

VICTIM,OFFENSE,REF,OUTPUT=OBJECT+12000,OBJECT+14000,OBJECT+14200,OBJECT+14300
VTABLE=DEF+4096
CODE=0x18001000


class HitEngine(RewardEngine):
    def __init__(self,band=0x60000000):
        super().__init__(band)
        self.put(REWARD+t.OWNER,PLAYER);self.put(REWARD+t.GENERATION,self.read(s.GENERATION))
        self.fput(REWARD+t.VAMPIRE_END,60)
        self.put(PLAYER,VTABLE);self.put(VICTIM,VTABLE)
        self.put(VTABLE+0x68,band+0x118D10)
        self.uc.mem_write(band+0x118D10,v.expected_anchors(self.a)[band+0x118D10])
        self.put(PLAYER+0x764,1);self.put(VICTIM+0x764,0)
        self.put(OFFENSE+0x14,REF);self.put(REF+4,PLAYER)
        self.uc.mem_map(CODE-4096,8192)
        self.uc.mem_write(CODE,v.build_hit(CODE,REWARD,CONTROL,self.a,testing=True))
        self.uc.mem_write(band+v.LOOKUP,b'\xc7\x01'+u32(VICTIM+0x730)+b'\xb8\x01\0\0\0\xc3')
        self.uc.mem_write(GET_HEALTH,b'\x81\x7c\x24\x04'+u32(VICTIM)+b'\x75\x07\xd9\x05'+u32(VICTIM+0x730)+b'\xc3\xd9\x05'+u32(HP)+b'\xc3')
        self.lookups=0
        def observe(uc,address,size,data):
            if address==band+v.LOOKUP:
                assert uc.reg_read(UC_X86_REG_ECX)==OUTPUT
                assert self.get(uc.reg_read(UC_X86_REG_ESP)+4)==123
                self.lookups+=1
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def hit(self,damage=1,victim=VICTIM,offense=OFFENSE):
        uc=self.uc
        registers={UC_X86_REG_EBX:victim,UC_X86_REG_EBP:offense,
            UC_X86_REG_ESI:0x87654321,UC_X86_REG_EDI:0x12345678}
        for reg,value in registers.items():uc.reg_write(reg,value)
        uc.reg_write(UC_X86_REG_ECX,OUTPUT);uc.reg_write(UC_X86_REG_ESP,STACK)
        self.put(STACK,RETURN);self.put(STACK+4,123);self.fput(STACK+0x18,damage)
        vectors=[((i+1)<<96)+0x1234567890ABCDEF for i in range(8)]
        for i,value in enumerate(vectors):uc.reg_write(UC_X86_REG_XMM0+i,value)
        uc.emu_start(CODE,RETURN,count=20000)
        assert uc.reg_read(UC_X86_REG_ESP)==STACK+4 # Native lookup uses caller cleanup.
        assert all(uc.reg_read(reg)==value for reg,value in registers.items())
        assert all(uc.reg_read(UC_X86_REG_XMM0+i)==value for i,value in enumerate(vectors))
        assert self.get(OUTPUT)==VICTIM+0x730 and uc.reg_read(UC_X86_REG_EAX)==1


@pytest.mark.parametrize('band',[0x60000000,0x71000000])
def test_tt_enemy_hit_heals_without_kill_and_caps_both_stats(band):
    e=HitEngine(band);e.fput(VICTIM+0x730,100);e.hit(.1)
    assert (e.fget(HP),e.fget(ARMOUR))==(35,40)
    assert e.fget(VICTIM+0x730)==100 and e.get(REWARD+v.HITS)==1
    assert e.get(REWARD+v.NONLETHAL)==1
    assert struct.unpack('<4f',e.uc.mem_read(REWARD+v.HP_BEFORE,16))==(25,35,30,40)
    e.fput(HP,98);e.fput(ARMOUR,97);e.hit(1)
    assert (e.fget(HP),e.fget(ARMOUR))==(100,100) and e.lookups==2


@pytest.mark.parametrize('own,enemy,heal',[(1,0,True),(0,1,True),(1,1,False),(0,0,False),
    (1,-2,False),(-2,1,False),(1,-4,False),(-4,0,False),(1,-1,True),(-1,1,True),
    (-1,-1,True),(-2,-1,False),(-1,-4,False)])
def test_tt_native_enemy_team_semantics(own,enemy,heal):
    e=HitEngine();e.put(PLAYER+0x764,own&0xFFFFFFFF);e.put(VICTIM+0x764,enemy&0xFFFFFFFF)
    e.hit();assert e.fget(HP)==(35 if heal else 25)
    assert e.lookups==1 and e.get(REWARD+v.HITS)==int(heal)


@pytest.mark.parametrize('damage',[0,-1,float('nan'),float('inf'),-float('inf')])
def test_tt_repairs_zero_damage_and_invalid_damage_cannot_heal(damage):
    e=HitEngine();e.hit(damage)
    assert (e.fget(HP),e.fget(ARMOUR))==(25,30) and e.lookups==1


@pytest.mark.parametrize('reason',['pause','cutscene','control','menu','stale','observer','dead','owner','generation','victory',
    'expired','npc_attacker','no_attacker','null_offense','self','dead_health'])
def test_tt_vampire_requires_current_controllable_living_attacker(reason):
    e=HitEngine()
    fields={'pause':(MODE+4,3),'cutscene':(CAMERA+s.CAMERA_HOST,OBJECT),
        'control':(PLAYER+s.CONTROL_ENABLED,0),'menu':(e.a.game_mode,0),
        'stale':(CONTROL+s.HEARTBEAT,0),'observer':(e.a.commands+0x204,0),
        'dead':(CONTROL+s.ALIVE,0),'owner':(REWARD+t.OWNER,123),
        'generation':(REWARD+t.GENERATION,999),'victory':(e.a.pending_continue,1),
        'expired':(REWARD+t.VAMPIRE_END,0),'npc_attacker':(REF+4,VICTIM),
        'no_attacker':(OFFENSE+0x14,0),'dead_health':(HP,0)}
    if reason in fields:e.put(*fields[reason])
    before=(e.fget(HP),e.fget(ARMOUR))
    e.hit(victim=PLAYER if reason=='self' else VICTIM,offense=0 if reason=='null_offense' else OFFENSE)
    assert (e.fget(HP),e.fget(ARMOUR))==before and e.lookups==1


def test_tt_vampire_never_replays_hits_before_activation():
    e=HitEngine();e.fput(REWARD+t.VAMPIRE_END,0);e.hit()
    e.fput(REWARD+t.VAMPIRE_END,60);assert e.fget(HP)==25
    e.hit();e.fput(REWARD+t.VAMPIRE_END,120);assert e.fget(HP)==35


def test_tt_damage_lookup_install_restore_and_conflict(memory):
    hook=v.NativeVampiric(memory,REWARD,CONTROL,memory.a)
    hook.update();hook.update();assert memory.read(hook.site,5)==hook.replacement
    hook.close();assert memory.read(hook.site,5)==hook.original
    memory.write(hook.site,b'\xe8\0\0\0\0')
    with pytest.raises(OSError):hook.update()
    hook.close();assert memory.read(hook.site,5)==b'\xe8\0\0\0\0'
