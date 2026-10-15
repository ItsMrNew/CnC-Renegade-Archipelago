"""Execute the production recovery code with lost releases and DirectInput COM.

The fake device keeps its real-time state separate from its truncated buffer,
so stale buffered presses cannot pass merely by zeroing the engine cache.
"""
import struct

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ESP

from renegade.native import checkpoint_input as ci, services as s, tt_campaign as tc
from renegade.native.thunk import CODE_OFFSET, u32
from renegade.native.profile import UnsupportedBuild
from test_services import Engine, CONTROL
from test_tt_campaign import CampaignEngine, CTRL

RECOVERY, DEVICES = 0x16000000, 0x16001000
GET_DATA, GET_STATE = 0x16002000, 0x16002010


def attach(e, a):
    uc = e.machine if hasattr(e,'machine') else e.uc
    uc.mem_map(RECOVERY,16384)
    uc.mem_write(RECOVERY,ci.build_recovery(RECOVERY,a))
    # Run the exact pinned Input::Flush / DirectInput::Flush instructions.
    for site,raw in ci.expected_anchors(a).items():
        uc.mem_write(site,raw)
    uc.mem_write(GET_DATA,b'\xc2\x14\x00')
    uc.mem_write(GET_STATE,b'\xc2\x0c\x00')
    snapshots = {'keys':bytearray(256),'mouse':bytearray(16)}
    queued = {'keys':[(0x11,128)],'mouse':[(12,128)]}
    failures = {}
    calls = []
    for i,(name,slot) in enumerate((('keys',a.keyboard),('mouse',a.mouse))):
        device = DEVICES+i*256
        uc.mem_write(slot,u32(device))
        uc.mem_write(device,u32(device+64))
        uc.mem_write(device+64+0x24,u32(GET_STATE)+u32(GET_DATA))
    for flag in (a.using,a.focus,a.acquired):uc.mem_write(flag,b'\1')
    def observe(uc,address,size,data):
        if address not in (GET_DATA,GET_STATE):return
        esp = uc.reg_read(UC_X86_REG_ESP)
        device = struct.unpack('<I',uc.mem_read(esp+4,4))[0]
        name = {DEVICES:'keys',DEVICES+256:'mouse'}[device]
        op = 'drain' if address==GET_DATA else 'snapshot'
        calls.append((name,op))
        result = failures.get((name,op),0)
        if address==GET_DATA:
            _,item_size,buffer,count,flags = struct.unpack('<5I',uc.mem_read(esp+4,20))
            assert (item_size,buffer,flags)==(20,0,0)
            assert bytes(uc.mem_read(count,4))==u32(0xFFFFFFFF)
            if result<0x80000000:
                uc.mem_write(count,u32(len(queued[name])))
                queued[name].clear()
        else:
            _,length,buffer = struct.unpack('<3I',uc.mem_read(esp+4,12))
            assert length==len(snapshots[name])
            # Failed drivers can leave random output, which must not be read.
            uc.mem_write(buffer,bytes(snapshots[name]) if result<0x80000000 else b'\xff'*length)
        # COM is allowed to clobber EAX; outer save result must survive.
        uc.reg_write(UC_X86_REG_EAX,result)
    uc.hook_add(UC_HOOK_CODE,observe)
    return uc,snapshots,queued,failures,calls


def setup(build):
    if build=='steam':
        e=Engine()
        uc,snapshots,queued,failures,calls=attach(e,ci.STEAM)
        uc.mem_write(CONTROL+CODE_OFFSET,s.build_services(CONTROL,input_target=RECOVERY))
        def checkpoint():e.objective(0);e.frame()
        def idle():e.frame()
        a=ci.STEAM
    else:
        e=CampaignEngine()
        uc,snapshots,queued,failures,calls=attach(e,ci.TT)
        uc.mem_write(CTRL+CODE_OFFSET,tc.build_checkpoint(CTRL,CONTROL,e.a,RECOVERY))
        def checkpoint():e.frame(event=2)
        def idle():e.frame()
        a=ci.TT
    idle()  # Loaded-save baseline never resets gameplay input.
    assert not calls
    return e,a,uc,snapshots,queued,failures,calls,checkpoint,idle


@pytest.mark.parametrize('build',['steam','tt'])
def test_lost_releases_and_stale_buffered_presses_are_cleared_after_save(build):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    uc.mem_write(a.keys+0x11,b'\1')  # Released W; release event was lost.
    uc.mem_write(a.buttons,b'\1')  # Released fire; release event was lost.
    uc.mem_write(a.functions,b'\xff'*(134*4))
    checkpoint()
    assert bytes(uc.mem_read(a.keys,256))==b'\0'*256
    assert bytes(uc.mem_read(a.buttons,3))==b'\0'*3
    assert bytes(uc.mem_read(a.functions,134*4))==b'\0'*(134*4)
    assert queued=={'keys':[],'mouse':[]}
    assert calls==[('keys','drain'),('keys','snapshot'),('mouse','drain'),('mouse','snapshot')]
    idle();assert len(calls)==4  # Only after a save, never every frame.
    if build=='steam':assert e.read(s.SAVE_RESULTS+4)==1


@pytest.mark.parametrize('build',['steam','tt'])
def test_genuinely_held_buttons_survive_without_fabricated_press_events(build):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    snapshots['keys'][0x11]=128
    snapshots['keys'][0x39]=255
    snapshots['mouse'][12:15]=bytes((128,0,128))
    checkpoint()
    assert bytes(uc.mem_read(a.keys+0x11,1))==b'\1'
    assert bytes(uc.mem_read(a.keys+0x39,1))==b'\1'
    assert bytes(uc.mem_read(a.buttons,3))==b'\1\0\1'
    # HELD=1 only: no HIT=2, RELEASED=4 or DOUBLE=8 generated by checkpoint.
    assert set(uc.mem_read(a.keys,256)) <= {0,1}


@pytest.mark.parametrize('build',['steam','tt'])
@pytest.mark.parametrize('name',['keys','mouse'])
@pytest.mark.parametrize('op',['drain','snapshot'])
def test_device_loss_does_not_reuse_failed_snapshot_or_reacquire(build,name,op):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    failures[(name,op)]=0x8007001E
    uc.mem_write(a.keys,b'\1'*256);uc.mem_write(a.buttons,b'\1'*3)
    checkpoint()
    assert bytes(uc.mem_read(a.keys,256))==b'\0'*256
    assert bytes(uc.mem_read(a.buttons,3))==b'\0'*3
    if op=='drain':assert (name,'snapshot') not in calls


@pytest.mark.parametrize('build',['steam','tt'])
def test_buffer_overflow_hresult_is_success_and_uses_current_state(build):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    failures[('keys','drain')]=1;failures[('mouse','drain')]=1
    checkpoint()
    assert len(calls)==4 and not any(queued.values())


@pytest.mark.parametrize('build',['steam','tt'])
@pytest.mark.parametrize('reason',['unfocused','unacquired','missing_devices','not_directinput'])
def test_no_device_calls_if_focus_lost_or_device_unavailable(build,reason):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    if reason=='missing_devices':
        uc.mem_write(a.keyboard,u32(0));uc.mem_write(a.mouse,u32(0))
    else:uc.mem_write({'unfocused':a.focus,'unacquired':a.acquired,'not_directinput':a.using}[reason],b'\0')
    checkpoint()
    assert not calls


def test_failed_steam_save_still_recovers_input_and_preserves_failure():
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup('steam')
    uc.mem_write(s.BASE+s.SAVE_GAME,b'\x31\xc0\xc3')
    checkpoint()
    assert len(calls)==4 and e.read(s.SAVE_RESULTS+4)==0


@pytest.mark.parametrize('build',['steam','tt'])
def test_pause_defers_save_and_input_recovery(build):
    e,a,uc,snapshots,queued,failures,calls,checkpoint,idle=setup(build)
    if build=='steam':
        from test_services import OBJECT
        pause=OBJECT+4
    else:
        from test_tt_discs import MODE
        pause=MODE+4
    uc.mem_write(pause,u32(1));checkpoint();assert not calls
    uc.mem_write(pause,u32(0));checkpoint();assert len(calls)==4


@pytest.mark.parametrize('binding',[ci.STEAM,ci.TT])
def test_anchor_validation_rejects_modified_input_before_allocation(binding):
    anchors=ci.expected_anchors(binding)
    class Memory:
        def read(self,address,size):return anchors[address]
        def allocate(self):raise AssertionError('No allocation before validation')
    for site in anchors:
        class BadMemory(Memory):
            def read(self,address,size):
                raw=super().read(address,size)
                return bytes([raw[0]^1])+raw[1:] if address==site else raw
        with pytest.raises(UnsupportedBuild,match='Checkpoint input anchor'):
            ci.CheckpointInput(BadMemory(),tt=binding==ci.TT)
