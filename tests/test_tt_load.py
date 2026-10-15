"""Execute the full pinned TT constructor to preserve its ABI and member defaults."""
import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import (UC_X86_REG_EAX,UC_X86_REG_EBX,UC_X86_REG_ECX,
    UC_X86_REG_ESP,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP)

from renegade.native import tt_load as l
from renegade.native.profile import UnsupportedBuild
from renegade.native.thunk import CODE_OFFSET,u32
from test_native import CONTROL,OBJECT,STACK,RETURN,TICK
from test_tt_native import Engine,Memory
from tt_fixture import relocated_ctor


def emulate(mode=1,server=True,band=0x60000000,guarded=True):
    e=Engine(band=band);uc=e.uc;a=e.a;obj=OBJECT+256
    e.put(a.game_mode,mode)
    e.put(band+0x5FA364,OBJECT+0x3000)  # Clock initialized flag.
    e.put(band+0x5FA368,OBJECT+0x3100)
    e.put(band+0x5FA634,OBJECT+0x3200)  # NetworkObjectClass::IsServer.
    e.put(OBJECT+0x3000,1); e.put(OBJECT+0x3100,1000)
    e.put(OBJECT+0x3200,int(server));e.put(band+0x2C95A8,TICK)
    uc.mem_write(obj,b'\xaa'*0x700)
    native=relocated_ctor(band)
    uc.mem_write(band+l.CONSTRUCTOR,native)
    uc.mem_write(CONTROL+CODE_OFFSET,l.build_guard(CONTROL,a,native))
    calls=[]
    funcs={band+0x297500:'allocate_id',band+0x297480:'register',
           band+0x2B4ECC:'memset',band+0x2AF520:'dirty_bits'}
    for fn in funcs: uc.mem_write(fn,b'\xc3')
    def observe(uc,address,size,data):
        if address not in funcs:return
        name=funcs[address];calls.append(name)
        sp=uc.reg_read(UC_X86_REG_ESP)
        read=lambda p:int.from_bytes(uc.mem_read(p,4),'little')
        if name=='allocate_id':uc.reg_write(UC_X86_REG_EAX,1500000204)
        elif name=='register':
            assert read(sp+4)==obj and read(obj+4)==1500000204
        elif name=='memset':
            dest,value,count=(read(sp+n) for n in (4,8,12))
            uc.mem_write(dest,bytes([value])*count)
    uc.hook_add(UC_HOOK_CODE,observe)
    for reg,value in e.registers.items():uc.reg_write(reg,value)
    uc.mem_write(STACK,u32(RETURN))
    uc.emu_start(CONTROL+CODE_OFFSET if guarded else band+l.CONSTRUCTOR,RETURN,count=5000)
    assert uc.reg_read(UC_X86_REG_ESP)==STACK+4
    assert uc.reg_read(UC_X86_REG_EAX)==obj
    for reg in (UC_X86_REG_EBX,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP):
        assert uc.reg_read(reg)==e.registers[reg]
    assert int.from_bytes(uc.mem_read(OBJECT+0x3200,4),'little')==int(server)
    return e,bytes(uc.mem_read(obj,0x700)),calls


@pytest.mark.parametrize('band',[0x60000000,0x71000000])
def test_campaign_definitions_do_not_claim_saved_object_ids(band):
    e,body,calls=emulate(band=band)
    assert 'register' not in calls and 'allocate_id' not in calls
    assert int.from_bytes(body[4:8],'little')==0
    assert e.read(l.SINGLE_PLAYER_DEFINITIONS)==1
    # Apart from registry participation, all constructor writes match its native
    # non-server path. In particular the shared server flag stays unchanged.
    _,normal,_=emulate(server=False,band=band,guarded=False)
    assert body==normal


@pytest.mark.parametrize('mode',[0,2,3])
@pytest.mark.parametrize('server',[False,True])
def test_menu_and_multiplayer_use_original_constructor(mode,server):
    e,body,calls=emulate(mode,server)
    _,expected,original_calls=emulate(mode,server,guarded=False)
    assert body==expected and calls==original_calls
    assert ('register' in calls)==server
    assert e.read(l.ORIGINAL_CALLS)==1


def test_regular_campaign_objects_still_register_normally():
    _,body,calls=emulate(guarded=False)
    assert int.from_bytes(body[4:8],'little')==1500000204
    assert 'register' in calls


def test_guard_reuses_both_verified_calls_and_leaves_ctor_unchanged():
    m=Memory();guard=l.NativeLoadGuard(m,m.a);before=guard.original
    assert guard.install()
    again=l.NativeLoadGuard(m,m.a)
    assert not again.install() and again.control==guard.control
    assert m.read(m.a.band_base+l.CONSTRUCTOR,l.CTOR_SIZE)==before
    for rva in l.CALL_SITES:
        site=m.a.band_base+rva
        assert m.read(site,5)==l.call(site,guard.control+CODE_OFFSET)
    assert again.snapshot()==dict(installed=True,campaign_definitions=0,original_calls=0)


@pytest.mark.parametrize('offset',[0,0xB4,0xFC])
def test_changed_constructor_is_rejected(offset):
    m=Memory();m.write(m.a.band_base+l.CONSTRUCTOR+offset,b'\xcc')
    before=dict(m.values)
    with pytest.raises(UnsupportedBuild):l.NativeLoadGuard(m,m.a)
    assert m.values==before


def test_other_or_partially_owned_calls_are_rejected():
    m=Memory();site=m.a.band_base+l.CALL_SITES[0]
    m.write(site,l.call(site,0x12345678))
    with pytest.raises(UnsupportedBuild):l.NativeLoadGuard(m,m.a)


def test_uncertain_completed_install_is_recognized(monkeypatch):
    m=Memory();replace=m.replace_calls
    def fail(values):replace(values);raise OSError('interrupted after installation')
    monkeypatch.setattr(m,'replace_calls',fail)
    with pytest.raises(OSError):l.NativeLoadGuard(m,m.a).install()
    assert not l.NativeLoadGuard(m,m.a).install()
