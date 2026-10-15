"""Execute TT weapon wrappers with their SSE/register ABIs and relocated DLLs."""
import struct
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
    UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP, UC_X86_REG_XMM0)
from renegade.native import tt_weapon_effects as w, traps as t, services as s, rewards as r
from renegade.native.thunk import CODE_OFFSET, u32
from renegade.native.profile import UnsupportedBuild
from test_tt_inventory import RewardEngine, REWARD, BAG, ARRAY, FRAME
from test_tt_discs import PLAYER,CAMERA,MODE
from test_native import CONTROL,OBJECT,STACK,RETURN,TICK
from test_tt_native import memory

OUTPUT,REF=OBJECT+14500,OBJECT+14600


class WeaponEngine(RewardEngine):
    def __init__(self,band=0x60000000):
        super().__init__(band)
        self.weapon=self.add_weapon();self.put(BAG+28,1)
        self.put(self.weapon+12,REF);self.put(REF+4,PLAYER)
        self.put(self.get(self.weapon)+0x1c,2)
        self.put(REWARD+w.ONE,1)
        self.codes={};self.calls=[];self.reload_count=0
        for i,name in enumerate(('weapon','loaded','ammo','reloads','force')):
            code=0x17001000+i*0x10000;self.uc.mem_map(code-4096,8192);self.codes[name]=code
        for name in ('weapon','loaded','ammo'):
            self.uc.mem_write(self.codes[name],getattr(w,'build_'+name)(self.codes[name],REWARD,CONTROL,self.a))
        self.uc.mem_write(self.codes['force'],w.build_forced_reload(self.codes['force'],self.a))
        self.uc.mem_write(self.codes['reloads'],w.build_reloads(self.codes['reloads'],REWARD,CONTROL,self.a,self.codes['force']))
        self.uc.mem_write(band+w.UPDATE,b'\xc3')
        self.uc.mem_write(band+w.ASSIGN,b'\xc3')
        self.uc.mem_write(band+w.LOOKUP,b'\xc3')
        self.uc.mem_write(band+w.DECREMENT,b'\xc2\x04\x00')
        # Verified native prologue must arrive with ESI=weapon and a balanced
        # EBP-aligned stack; mock continuation finishes the real epilogue ABI.
        self.uc.mem_write(band+0x282331,b'\xc7\x46\x3c'+u32(5)+b'\x5f\x5e\x5b\x8b\xe5\x5d\xc3')
        def observe(uc,address,size,data):
            ecx=uc.reg_read(UC_X86_REG_ECX);esp=uc.reg_read(UC_X86_REG_ESP)
            if address==band+w.UPDATE:
                self.calls.append(('update',struct.unpack('<f',u32(uc.reg_read(UC_X86_REG_XMM0+1)))[0]))
                assert ecx==self.weapon
            elif address==band+w.ASSIGN:
                assert self.get(esp+4)==self.get(self.weapon+0x30)
                self.calls.append(('burst',self.get(ecx)))
                # Win32/TT callees may clobber volatile SIMD registers.
                for i in range(8):uc.reg_write(UC_X86_REG_XMM0+i,0)
            elif address==band+w.LOOKUP:
                self.calls.append(('lookup',self.get(esp+4)));self.put(ecx,REF+12)
            elif address==band+w.DECREMENT:
                self.calls.append(('consume',self.get(esp+4)))
            elif address==band+0x282331:
                assert uc.reg_read(UC_X86_REG_ESI)==self.weapon
                self.reload_count+=1
        self.uc.hook_add(UC_HOOK_CODE,observe)

    def invoke(self,name):
        uc=self.uc;ptr=self.weapon
        preserved={UC_X86_REG_EBX:0x12345678,UC_X86_REG_ESI:ptr,UC_X86_REG_EDI:0x87654321,UC_X86_REG_EBP:0xABCDEF}
        for reg,value in preserved.items():uc.reg_write(reg,value)
        uc.reg_write(UC_X86_REG_ECX,OUTPUT if name=='loaded' else ptr)
        uc.reg_write(UC_X86_REG_ESP,STACK)
        self.put(STACK,RETURN);self.put(STACK+4,9 if name=='loaded' else 1)
        vectors=[(0x0123456789ABCDEF<<64)+(i<<32)+0x3C800000 for i in range(8)]
        for i,value in enumerate(vectors):uc.reg_write(UC_X86_REG_XMM0+i,value)
        uc.emu_start(self.codes[name],RETURN,count=20000)
        assert uc.reg_read(UC_X86_REG_ESP)==STACK+4+(4 if name=='ammo' else 0)
        assert all(uc.reg_read(reg)==value for reg,value in preserved.items())
        for i,value in enumerate(vectors):
            actual=uc.reg_read(UC_X86_REG_XMM0+i)
            if i==1 and name=='weapon':assert actual>>32==value>>32
            else:assert actual==value

    def start(self,kind):self.request(kind);self.frame()


@pytest.mark.parametrize('band',[0x60000000,0x71000000])
@pytest.mark.parametrize('state',range(8))
def test_tt_bullet_sse_rate_burst_no_ammo_and_reload_speed(band,state):
    e=WeaponEngine(band);e.start(11);e.put(e.weapon+0x3c,state)
    before=bytes(e.uc.mem_read(e.weapon+0x24,8));e.invoke('weapon')
    assert e.calls==[('burst',0xFFFFFFFF),('update',.03125 if state<=4 else .015625)]
    e.invoke('loaded');assert e.get(OUTPUT)==REWARD+w.ONE
    e.invoke('ammo');assert e.calls[-1][0]=='update'
    assert bytes(e.uc.mem_read(e.weapon+0x24,8))==before
    e.fput(REWARD+t.BULLET_END,0);e.invoke('ammo');assert e.calls[-1]==('consume',1)
    e.invoke('loaded');assert e.calls[-1]==('lookup',9)


@pytest.mark.parametrize('bullet',[False,True])
def test_tt_five_reload_cycles_block_all_gaps_and_stack(bullet):
    e=WeaponEngine()
    if bullet:e.start(11)
    e.start(7);assert e.get(REWARD+t.RELOADS)==5
    for cycle in range(5):
        e.uc.mem_write(e.weapon+0x47,b'\1\1');e.invoke('weapon')
        assert bytes(e.uc.mem_read(e.weapon+0x47,2))==b'\0\0'
        assert e.calls[-1]==('update',.015625)
        e.invoke('reloads');assert e.reload_count==cycle+1
        e.invoke('reloads');assert e.reload_count==cycle+1
        e.put(e.weapon+0x3c,1)
    assert e.get(REWARD+t.RELOADS)==1 # Final reload's firing lock is still held.
    e.invoke('reloads');assert e.get(REWARD+t.RELOADS)==0
    e.start(7);e.start(7);assert e.get(REWARD+t.RELOADS)==10
    e.put(FRAME,0);e.frame();assert e.get(REWARD+t.RELOADS)==0


@pytest.mark.parametrize('style',[0,1,6,7,8,9])
def test_tt_equipment_excluded_from_bullet_and_reload_but_cannot_bypass_butter(style):
    e=WeaponEngine();e.start(11);e.put(e.get(e.weapon)+0x1c,style)
    e.invoke('weapon');e.invoke('loaded');e.invoke('ammo')
    assert e.calls==[('update',.015625),('lookup',9),('consume',1)]
    e.start(7);e.uc.mem_write(e.weapon+0x47,b'\1\1');e.invoke('weapon');e.invoke('reloads')
    assert bytes(e.uc.mem_read(e.weapon+0x47,2))==b'\0\0' and e.reload_count==0


@pytest.mark.parametrize('reason',['pause','cutscene','control','menu','stale','observer','dead','owner','generation','victory','npc','unowned'])
def test_tt_weapon_effects_recheck_native_control_identity_and_lease(reason):
    e=WeaponEngine();e.start(11);e.start(7)
    field,value={'pause':(MODE+4,3),'cutscene':(CAMERA+s.CAMERA_HOST,OBJECT),
        'control':(PLAYER+s.CONTROL_ENABLED,0),'menu':(e.a.game_mode,0),
        'stale':(CONTROL+s.HEARTBEAT,0),'observer':(e.a.commands+0x204,0),
        'dead':(CONTROL+s.ALIVE,0),'owner':(REWARD+t.OWNER,123),
        'generation':(REWARD+t.GENERATION,999),'victory':(e.a.pending_continue,1),
        'npc':(REF+4,OBJECT),'unowned':(e.weapon+0x38,0)}[reason]
    e.put(field,value);e.uc.mem_write(e.weapon+0x47,b'\1\1')
    e.invoke('weapon');e.invoke('loaded');e.invoke('ammo')
    assert e.calls==[('update',.015625),('lookup',9),('consume',1)]
    assert bytes(e.uc.mem_read(e.weapon+0x47,2))==b'\1\1'


def test_tt_bullet_receipts_extend_without_strength_stacking():
    e=WeaponEngine();e.start(11);e.fput(REWARD+t.BULLET_END,41);e.start(11)
    assert e.fget(REWARD+t.BULLET_END)==101
    e.invoke('weapon');assert e.calls[-1]==('update',.03125)


def test_tt_reload_weapon_switch_cannot_finish_another_weapons_cycle():
    e=WeaponEngine();e.start(7);e.invoke('reloads')
    previous=e.get(REWARD+t.RELOAD_WEAPON);e.put(REWARD+t.RELOAD_WEAPON,previous+320)
    e.put(e.weapon+0x3c,1);e.invoke('reloads')
    assert e.get(REWARD+t.RELOADS)==5 and e.reload_count==2


def test_tt_weapon_calls_validate_and_restore_without_touching_foreign_hook(memory):
    effects=w.NativeWeaponEffects(memory,REWARD,CONTROL,memory.a)
    effects.update();effects.update()
    assert all(memory.read(p,5)==code for p,old,code in effects.entries)
    p,old,code=effects.entries[-1];memory.write(p,b'\xe8\0\0\0\0')
    with pytest.raises(OSError):effects.update()
    effects.close()
    assert memory.read(p,5)==b'\xe8\0\0\0\0'
    assert all(memory.read(p,5)==old for p,old,code in effects.entries[:-1])


def test_tt_wrong_weapon_build_rejected_before_writes(memory):
    address=next(iter(w.expected_anchors(memory.a)));memory.write(address,b'\0')
    with pytest.raises(UnsupportedBuild):w.NativeWeaponEffects(memory,REWARD,CONTROL,memory.a)
