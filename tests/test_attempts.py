import asyncio
import struct
from types import SimpleNamespace

import pytest

from renegade.native import attempts as a, services as s
from renegade.data import MISSIONS, OBJECTIVES, objective_kind, objective_id
from renegade.client import RenegadeClient
from renegade.desktop import snapshot
from test_services import Engine, CONTROL, OBJECT
from test_primary_objectives import connect
from test_core import Socket

ATTEMPT = 0x18000000


def observer(engine):
    uc = engine.machine
    uc.mem_map(ATTEMPT, 8192)
    uc.mem_write(ATTEMPT + s.CODE_OFFSET, a.build_attempts(ATTEMPT, CONTROL))
    uc.mem_write(CONTROL + s.CODE_OFFSET, s.build_services(CONTROL, attempt_target=ATTEMPT + s.CODE_OFFSET))
    reader = a.NativeAttempts.__new__(a.NativeAttempts)
    reader.commands = s.BASE+s.STEAM.commands
    reader.game_mode = s.BASE+s.STEAM.game_mode
    reader.control, reader.service = ATTEMPT, CONTROL
    reader.memory = SimpleNamespace(read=lambda p, n: bytes(uc.mem_read(p, n)),
                                    u32=lambda p: int.from_bytes(uc.mem_read(p, 4), 'little'))
    return reader


@pytest.mark.parametrize('mission,oid', [(m, o) for m in MISSIONS for o in OBJECTIVES[m.map_name]])
def test_only_failed_objectives_report_missed_without_sending_checks_or_saving(mission, oid):
    e = Engine(mission, [oid]); reader = observer(e)
    kind = 1 if objective_kind(mission.map_name, oid) == 'primary' else 2
    for status in (0, 2, 3, 0):
        e.objective(0, status, kind); e.frame()
        assert reader.snapshot() == dict(map=mission.map_name, failed=[oid] if status == 2 else [])
        assert e.read(s.CAPTURED + (mission.number-1)*4) == 0
    assert 'save' not in e.calls


@pytest.mark.parametrize('reason', ['menu', 'load', 'wrong_type', 'bad_count', 'no_list', 'disabled', 'torn', 'new_generation'])
def test_invalid_or_stale_failure_evidence_is_not_displayed(reason):
    e = Engine(); reader = observer(e); e.objective(0, 2); e.frame()
    assert reader.snapshot()['failed']
    if reason == 'menu': e.put(s.BASE+s.STEAM.game_mode, 0)
    if reason == 'load': e.put(s.BASE+s.STEAM.commands+0x204, 0)
    if reason == 'wrong_type': e.objective(0, 2, 1)
    if reason == 'bad_count': e.put(s.BASE+s.OBJECTIVE_COUNT, 129)
    if reason == 'no_list': e.put(s.BASE+s.OBJECTIVE_ARRAY, 0)
    if reason == 'disabled': e.set(s.ENABLED, 0)
    if reason == 'torn': e.put(ATTEMPT+a.SEQUENCE, 3)
    elif reason == 'new_generation': e.set(s.GENERATION, e.read(s.GENERATION)+1)
    else: e.frame()
    result = reader.snapshot()
    assert result is None or not result['failed']


def test_pause_keeps_failure_visible_and_reload_of_pending_objective_clears_it():
    e = Engine(); reader = observer(e); e.objective(0, 2); e.frame()
    e.put(OBJECT+4, 1); e.frame()
    assert reader.snapshot()['failed']
    e.objective(0, 0); e.set(s.GENERATION, e.read(s.GENERATION)+1); e.frame()
    assert reader.snapshot()['failed'] == []


def test_failure_ui_never_changes_ap_completion_and_global_completion_wins(tmp_path):
    async def run():
        client = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path)
        socket = Socket(); await connect(client, socket)
        client.attempt = dict(map='M01', failed=[100])
        def state():
            return next(o['state'] for o in snapshot(client,{})['missions'][1]['objectives'] if o['id']==100)
        assert state() == 'Missed this attempt'
        assert not client.progress.completed
        client.connected = False; assert state() == 'Unknown'
        client.connected = True
        client.objective_finished('M01', 100)
        assert state() == 'Pending sync'
        client.acknowledged.add(objective_id('M01',100))
        assert state() == 'Complete'
        client.attempt = dict(map='M02', failed=[100])
        assert state() == 'Complete'
    asyncio.run(run())


@pytest.mark.parametrize('tt',[False,True])
def test_tracker_position_uses_game_thread_abi_and_rejects_stale_actor(tt):
    from unicorn import UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_ESP,UC_X86_REG_XMM0
    e=Engine();reader=observer(e);uc=e.machine
    address=0x728090 if tt else 0x5A8D50
    if tt:
        addresses=SimpleNamespace(commands=reader.commands,objective_count=s.BASE+s.OBJECTIVE_COUNT,
                                  objective_array=s.BASE+s.OBJECTIVE_ARRAY)
        uc.mem_write(ATTEMPT+s.CODE_OFFSET,a.build_attempts(ATTEMPT,CONTROL,addresses=addresses))
    e.put(reader.commands+0x40,address);uc.mem_write(address,b'\xc3')
    calls=[]
    def position(machine,pc,size,_):
        if pc!=address:return
        stack=machine.reg_read(UC_X86_REG_ESP)
        output,actor=struct.unpack('<2I',machine.mem_read(stack+4,8))
        assert output==ATTEMPT+a.POSITION and actor==e.read(s.PLAYER)
        machine.mem_write(output,struct.pack('<3f',10,20,-150))
        machine.reg_write(UC_X86_REG_XMM0,456);calls.append(actor)
    uc.hook_add(UC_HOOK_CODE,position)
    uc.reg_write(UC_X86_REG_XMM0,123);e.frame()
    sample=reader.tracker_snapshot()
    assert [sample[k] for k in ('x','y','z')]==[10,20,-150] and calls
    assert uc.reg_read(UC_X86_REG_XMM0)==123
    e.set(s.PLAYER,e.read(s.PLAYER)+4);assert reader.tracker_snapshot()['z'] is None
    e.put(reader.commands+0x40,address+4);e.frame()
    assert reader.tracker_snapshot()['z'] is None and len(calls)==1
    e.put(ATTEMPT+a.SEQUENCE,3);assert reader.tracker_snapshot() is None


def test_tracker_polling_a_stopped_game_frame_does_not_refresh_timestamp(monkeypatch):
    e=Engine();reader=observer(e);e.frame()
    monkeypatch.setattr(a.time,'monotonic',lambda:10)
    assert reader.tracker_snapshot()['sampled_at']==10
    monkeypatch.setattr(a.time,'monotonic',lambda:20)
    assert reader.tracker_snapshot()['sampled_at']==10
    e.frame()
    assert reader.tracker_snapshot()['sampled_at']==20
