"""Execute the actual x86 disc observer and verify the durable AP contract."""
import asyncio
from types import SimpleNamespace

import pytest
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_ECX, UC_X86_REG_EBP

from renegade.native import discs as d, services as s
from renegade.data import (MISSIONS, DATA_DISCS, disc_id, location_ids, slot_data,
                           validate_slot_data, ALL_LOCATIONS)
from renegade.client import RenegadeClient
from renegade.desktop import snapshot
from test_services import Engine, CONTROL, OBJECT, STACK, RETURN
from test_objective_hook import Memory
from test_core import Socket
from test_primary_objectives import connect

DCTRL, POWERUP, DEF = 0x12000000, 0x21000000, 0x21001000
CASES=[(m,oid) for m in MISSIONS for oid in DATA_DISCS[m.map_name] if oid>=0]


def config(enabled=True):
    return slot_data(5,12,True,False,True,True,True,'progressive',False,True,enabled)


class DiscEngine(Engine):
    def __init__(self,mission=MISSIONS[1],oid=116992):
        super().__init__(mission)
        self.frame()
        uc=self.machine
        uc.mem_map(DCTRL,16384);uc.mem_map(POWERUP,16384)
        self.put(DCTRL+d.ENABLED,1)
        self.put(POWERUP+12,oid or 999999)
        self.put(POWERUP+0x6bc,DEF)
        self.put(DEF,DEF+128);self.put(DEF+128+0x24,DEF+256)
        uc.mem_write(DEF+256,b'\xb8'+d.u32(d.DISC_DEFINITION)+b'\xc3')
        self.put(POWERUP+0x6f0,DEF+512);self.put(POWERUP+0x6f8,1)
        self.put(DEF+512,DEF+640);self.put(DEF+640,DEF+768)
        self.put(DEF+768+4,DEF+896)
        uc.mem_write(DEF+896,b'\xb8'+d.u32(DEF+1024)+b'\xc3')
        uc.mem_write(DEF+1024,d.PRIEST_SCRIPT)
        uc.mem_write(d.BASE+d.GRANT,b'\xc7\x81\xb4\x07\x00\x00'+d.u32(2)+b'\xc2\x04\x00')
        uc.mem_write(DCTRL+4096,d.build_pickup(DCTRL+4096,DCTRL,CONTROL))
        uc.mem_write(DCTRL+8192,d.build_reveal(DCTRL+8192,DCTRL,CONTROL))
        uc.mem_write(d.BASE+d.CLOUD_VECTOR,b'\xff'*52)

    def pickup(self,recipient=OBJECT+768):
        uc=self.machine
        uc.reg_write(UC_X86_REG_ESP,STACK);self.put(STACK,RETURN);self.put(STACK+4,recipient)
        uc.reg_write(UC_X86_REG_ECX,POWERUP);uc.reg_write(UC_X86_REG_EBP,0x12345678)
        uc.emu_start(DCTRL+4096,RETURN,count=20000)
        assert uc.reg_read(UC_X86_REG_ESP)==STACK+8
        assert uc.reg_read(UC_X86_REG_EBP)==0x12345678
        return self.captured()

    def reveal(self):
        self.machine.reg_write(UC_X86_REG_ESP,STACK);self.put(STACK,RETURN)
        self.machine.emu_start(DCTRL+8192,RETURN,count=20000)
        assert self.machine.reg_read(UC_X86_REG_ESP)==STACK+4

    def captured(self):
        return int.from_bytes(self.machine.mem_read(DCTRL+d.CAPTURED+(self.mission.number-1)*4,4),'little')


@pytest.mark.parametrize('mission,oid',CASES)
def test_actual_pickup_after_reveal_and_replay(mission,oid):
    e=DiscEngine(mission,oid)
    e.reveal()
    assert bytes(e.machine.mem_read(d.BASE+d.CLOUD_VECTOR,52))==b'\0'*52
    assert e.captured()==0
    expected=1<<DATA_DISCS[mission.map_name].index(oid)
    assert e.pickup()==expected
    assert e.pickup()==expected
    e.reveal();assert e.captured()==expected


def test_disc_setting_off_still_reveals_and_does_not_capture():
    e=DiscEngine();e.put(DCTRL+d.ENABLED,0);e.reveal()
    assert bytes(e.machine.mem_read(d.BASE+d.CLOUD_VECTOR,52))==b'\0'*52
    assert e.pickup()==0
    assert bytes(e.machine.mem_read(POWERUP+0x7b4,4))==d.u32(2)  # Stock pickup still granted.


@pytest.mark.parametrize('reason',['failed','weapon','unknown','npc','stale','menu','locked','paused','dead','load','map','suffix','victory','cinematic'])
def test_reject_non_pickups_or_invalid_session(reason):
    e=DiscEngine();recipient=OBJECT+768
    if reason=='failed':e.machine.mem_write(d.BASE+d.GRANT,b'\xc2\x04\x00')
    if reason=='weapon':e.machine.mem_write(DEF+256,b'\x31\xc0\xc3')
    if reason=='unknown':
        e.put(POWERUP+12,123);e.put(POWERUP+0x6f8,0)
    if reason=='npc':recipient=OBJECT+4096
    if reason=='stale':e.set(s.HEARTBEAT,1)
    if reason=='menu':e.put(d.BASE+s.STEAM.game_mode,0)
    if reason=='locked':e.set(s.ALLOWED,0)
    if reason=='paused':e.put(OBJECT+4,1)
    if reason=='dead':e.set(s.ALIVE,0)
    if reason=='load':e.put(OBJECT+516,OBJECT+1024)
    if reason=='map':e.machine.mem_write(OBJECT+128,b'M02.lsd\0')
    if reason=='suffix':e.machine.mem_write(OBJECT+128,b'M01.bad\0')
    if reason=='victory':e.machine.mem_write(d.BASE+s.STEAM.pending_continue,b'\1')
    if reason=='cinematic':e.set(s.PLAYABLE,0)
    assert e.pickup(recipient)==0


@pytest.mark.parametrize('name',[b'M01_Priest_Datadisc_JDG_fake\0',b'M01_Priest\0',b'\0'])
def test_dynamic_disc_requires_exact_script(name):
    e=DiscEngine(oid=0);e.machine.mem_write(DEF+1024,name)
    assert not e.pickup()


@pytest.mark.parametrize('enabled',[False,True])
def test_v10_contract_option_and_unchanged_physical_disc_ids(enabled):
    cfg=config(enabled);assert validate_slot_data(cfg)==cfg
    assert cfg['protocol_version']==10
    assert len(cfg['location_ids'])==167+23*enabled
    assert len(ALL_LOCATIONS)==190==len(set(ALL_LOCATIONS.values()))
    assert len(CASES)==22
    assert len(location_ids(5,True,True))==167


@pytest.mark.parametrize('value',[None,1,0,'true',[],{}])
def test_malformed_disc_setting_rejected(value):
    cfg=config();cfg['data_discs']=value
    with pytest.raises(ValueError):validate_slot_data(cfg)


def test_legacy_cannot_enable_discs():
    cfg=config();cfg['protocol_version']=8
    with pytest.raises(ValueError):validate_slot_data(cfg)


def test_persistence_disconnect_retry_restart_and_ui(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(c,sock,config());c.connected=False
        mem=Memory();mem.write_u32(DCTRL+d.CAPTURED+4,1)
        capture=d.DiscCapture();capture.collect(SimpleNamespace(memory=mem,discs=SimpleNamespace(control=DCTRL)))
        merge=c.progress.merge
        def fail(checks):raise OSError('disk unavailable')
        c.progress.merge=fail;capture.flush(c)
        assert capture.pending=={('M01',116992)} and not c.progress.completed
        c.progress.merge=merge;capture.flush(c)
        assert not capture.pending and c.progress.completed=={disc_id('M01',116992)}
        other=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sck=Socket()
        await connect(other,sck,config());await other.flush_once(sck)
        assert sck.sent[-1]==dict(cmd='LocationChecks',locations=[disc_id('M01',116992)])
        assert not other.disc_collected('M01',116992)
        rows=snapshot(other,{})['missions'][1]['objectives']
        assert sum(str(row['id']).startswith('disc_') for row in rows)==7
        other.connected=False
        assert all(row['state']=='Unknown' for row in snapshot(other,{})['missions'][1]['objectives'])
    asyncio.run(run())


def test_native_reveal_does_not_create_checks_with_off_option(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path)
        await connect(c,Socket(),config(False))
        for m,oid in CASES:assert not c.disc_collected(m.map_name,oid)
        assert not c.progress.completed
    asyncio.run(run())


@pytest.mark.parametrize('mission,oid',CASES)
def test_disc_pickup_saves_on_next_frame_not_inside_grant(mission, oid):
    e = DiscEngine(mission,oid)
    e.reveal(); e.frame()
    assert 'save' not in e.calls
    e.pickup()
    assert e.read(s.DISC_EVENT) == 1 and 'save' not in e.calls
    e.frame(); e.frame()
    assert e.calls.count('save') == 1
    assert e.read(s.DISC_EVENT) == 0


@pytest.mark.parametrize('reason',['paused','cinematic','no_control','dead','load'])
def test_disc_checkpoint_waits_for_control_but_cannot_cross_loads(reason):
    e=DiscEngine();e.pickup()
    if reason=='paused':e.put(OBJECT+4,1)
    if reason=='cinematic':e.put(OBJECT+8192+s.CAMERA_HOST,OBJECT)
    if reason=='no_control':e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\0')
    if reason=='dead':e.health(0)
    if reason=='load':e.set(s.GENERATION,e.read(s.GENERATION)+1)
    e.frame();assert 'save' not in e.calls
    e.put(OBJECT+4,0);e.put(OBJECT+8192+s.CAMERA_HOST,0)
    e.machine.mem_write(OBJECT+768+s.CONTROL_ENABLED,b'\1');e.health(100)
    e.frame();e.frame()
    assert e.calls.count('save') == (0 if reason=='load' else 1)
