"""Exercise the real x86 reward observer and old/new AP room contracts."""
import asyncio
from functools import partial
from types import SimpleNamespace
import pytest
from unicorn.x86_const import UC_X86_REG_ESP,UC_X86_REG_EAX,UC_X86_REG_EBP,UC_X86_REG_EBX,UC_X86_REG_ESI,UC_X86_REG_EDI
from renegade.data import MISSIONS,BLACK_HAND_INTEL,disc_id,validate_slot_data
from renegade.native import intel,discs as d,tt_discs as td,services as s
from renegade.native.thunk import u32
from test_discs import DiscEngine as SteamEngine,config,DCTRL,DEF
from test_tt_discs import DiscEngine as TTEngine,MODE,PLAYER,CAMERA
from test_services import CONTROL,STACK,RETURN,OBJECT
from test_core import Socket
from test_primary_objectives import connect
from renegade.client import RenegadeClient
from renegade.desktop import snapshot

CHECK=disc_id('M13',BLACK_HAND_INTEL)


def prepare(tt=False, mission=MISSIONS[0], checkpoint=True):
    e=TTEngine(mission) if tt else SteamEngine(mission)
    e.uc=e.uc if tt else e.machine
    if tt:
        from test_native import STACK as tt_stack, RETURN as tt_return
        e.stack,e.return_address=tt_stack,tt_return
    else:e.stack,e.return_address=STACK,RETURN
    e.original=intel.TT_REVEAL if tt else intel.STEAM_REVEAL
    e.put(DCTRL+intel.ENABLED,1)
    e.code=DCTRL+12288
    guard=partial(td.guard,addresses=e.a) if tt else d.guard
    e.uc.mem_write(e.code,intel.build_intel(e.code,DCTRL,CONTROL,guard_builder=guard,original=e.original,checkpoint=checkpoint))
    return e


def call(e,entry=41,result=1):
    uc=e.uc
    # Native return value and received argument remain observable, even on rejection.
    uc.mem_write(e.original,b'\x8b\x44\x24\x04\xa3'+u32(DEF+3000)+b'\xa1'+u32(DEF+3004)+b'\xc3')
    uc.mem_write(DEF+3004,u32(result))
    regs={UC_X86_REG_EBP:0x12345678,UC_X86_REG_EBX:123,UC_X86_REG_ESI:456,UC_X86_REG_EDI:789}
    for reg,value in regs.items():uc.reg_write(reg,value)
    uc.reg_write(UC_X86_REG_ESP,e.stack)
    uc.mem_write(e.stack,u32(e.return_address)+u32(entry))
    uc.emu_start(e.code,e.return_address,count=20000)
    assert uc.reg_read(UC_X86_REG_ESP)==e.stack+4  # cdecl caller retains the argument.
    assert uc.reg_read(UC_X86_REG_EAX)==result
    assert int.from_bytes(uc.mem_read(DEF+3000,4),'little')==entry
    assert all(uc.reg_read(reg)==value for reg,value in regs.items())
    return int.from_bytes(uc.mem_read(DCTRL+d.CAPTURED,4),'little')


@pytest.mark.parametrize('tt',[False,True])
@pytest.mark.parametrize('known',[False,True])
def test_either_sniper_captures_one_reward_even_when_encyclopedia_already_known(tt,known):
    e=prepare(tt)
    assert call(e,result=0 if known else 1)==1
    assert e.read(s.DISC_EVENT)==1
    e.put(CONTROL+s.DISC_EVENT,0)
    assert call(e,result=0)==1 and e.read(s.DISC_EVENT)==0


@pytest.mark.parametrize('tt',[False,True])
@pytest.mark.parametrize('reason',['disabled','physical_disabled','stale','menu','locked','wrong_map','wrong_entry','dead','paused','cutscene','load'])
def test_irrelevant_rewards_and_unsafe_states_are_forwarded_without_capture(tt,reason):
    e=prepare(tt, MISSIONS[1] if reason=='wrong_map' else MISSIONS[0])
    entry=42 if reason=='wrong_entry' else 41
    if reason=='disabled':e.put(DCTRL+intel.ENABLED,0)
    if reason=='physical_disabled':e.put(DCTRL+d.ENABLED,0)
    if reason=='stale':e.put(CONTROL+s.HEARTBEAT,1)
    if reason=='menu':e.put(e.a.game_mode if tt else d.BASE+s.STEAM.game_mode,0)
    if reason=='locked':e.put(CONTROL+s.ALLOWED,0)
    if reason=='dead':e.put(DEF+2048,0) if tt else e.put(CONTROL+s.ALIVE,0)
    if reason=='paused':e.put(MODE+4 if tt else OBJECT+4,1)
    if reason=='cutscene':e.put(CAMERA+s.CAMERA_HOST,1) if tt else e.put(CONTROL+s.PLAYABLE,0)
    if reason=='load':e.uc.mem_write(PLAYER+s.CONTROL_ENABLED,b'\0') if tt else e.put(OBJECT+516,OBJECT+1024)
    assert call(e,entry)==0 and e.read(s.DISC_EVENT)==0


def test_steam_checkpoint_runs_on_next_safe_frame():
    e=prepare();call(e)
    assert 'save' not in e.calls
    e.put(OBJECT+4,1);e.frame();assert 'save' not in e.calls
    e.put(OBJECT+4,0);e.frame();assert e.calls.count('save')==1


def test_map_reveal_and_physical_pickups_cannot_award_intel():
    for tt in (False,True):
        e=prepare(tt);e.reveal();e.pickup()
        assert e.captured()==0


def legacy_config():
    cfg=config();cfg['protocol_version']=9;cfg['location_ids'].remove(CHECK)
    return cfg


def test_client_accepts_legacy_disc_room_without_new_check(tmp_path):
    cfg=legacy_config();assert validate_slot_data(cfg)==cfg
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path)
        await connect(c,Socket(),cfg)
        assert CHECK not in c.locations and not c.disc_collected('M13',BLACK_HAND_INTEL)
        row=snapshot(c,{})['missions'][0]
        assert not any(str(o['id']).startswith('disc_') for o in row['objectives'])
        capture=d.DiscCapture();capture.pending.add(('M13',BLACK_HAND_INTEL));capture.flush(c)
        assert not capture.pending and not c.progress.completed
    asyncio.run(run())


def test_new_room_reward_retries_durably_once_and_shows_in_launcher(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(c,sock,config());c.connected=False
        capture=d.DiscCapture();capture.pending.add(('M13',BLACK_HAND_INTEL))
        merge=c.progress.merge
        def fail(checks):raise OSError('test disk failure')
        c.progress.merge=fail;capture.flush(c);assert capture.pending and not c.progress.completed
        c.progress.merge=merge;capture.flush(c);assert not capture.pending and c.progress.completed=={CHECK}
        again=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(again,sock,config());await again.flush_once(sock)
        assert any(m==dict(cmd='LocationChecks',locations=[CHECK]) for m in sock.sent)
        assert not again.disc_collected('M13',BLACK_HAND_INTEL)
        assert any(o['title'].startswith('Black Hand Intel') for o in snapshot(again,{})['missions'][0]['objectives'])
    asyncio.run(run())


@pytest.mark.parametrize('tt',[False,True])
def test_callback_ownership_load_reinstallation_and_cleanup(tt):
    from test_objective_hook import Memory
    from renegade.native.tt_profile import TTAddresses
    mem=Memory();a=TTAddresses(0x60000000,0x61000000) if tt else None
    original=intel.TT_REVEAL if tt else intel.STEAM_REVEAL
    field=(a.commands if tt else d.BASE+s.STEAM.commands)+intel.COMMAND_OFFSET
    mem.write(original,intel.TT_ANCHOR if tt else intel.STEAM_ANCHOR);mem.write_u32(field,original)
    hook=intel.IntelHook(mem,DCTRL,CONTROL,addresses=a)
    hook.update(False);assert mem.u32(field)==original
    hook.update(True);assert mem.u32(field)==hook.code
    mem.write_u32(field,0);hook.update(True);assert mem.u32(DCTRL+intel.ENABLED)==0
    mem.write_u32(field,original);hook.update(True);assert mem.u32(field)==hook.code
    hook.close();assert mem.u32(field)==original
    hook.update(True);mem.write_u32(field,123)
    with pytest.raises(OSError):hook.update(True)
    hook.close();assert mem.u32(field)==123 and mem.u32(DCTRL+intel.ENABLED)==0
