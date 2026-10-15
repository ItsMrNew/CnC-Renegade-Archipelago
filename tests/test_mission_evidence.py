"""Execute the saved SAM destruction evidence path under both native adapters."""
from types import SimpleNamespace
import pytest
from renegade.native import services as s, mission_evidence as proof, tt
from renegade.native.secondary_capture import SecondaryCapture
from renegade.data import objective_id
from test_services import Engine as SteamEngine, CONTROL as STEAM_CONTROL, OBJECT as STEAM_OBJECT
from test_tt_native import Engine as TTEngine, CONTROL as TT_CONTROL

STATE=0x50000000
OBJECT=0x51001000
SCRIPT=0x51002000
NAME=0x51002100
VTABLE=0x51002200
GET_NAME=0x51002300
VARIABLE=0x51002400
FLAG=0x51002500
OBSERVERS=0x51002600


def fixture(backend, value=0):
    e=SteamEngine() if backend=='steam' else TTEngine()
    uc=e.machine if backend=='steam' else e.uc
    control=STEAM_CONTROL if backend=='steam' else TT_CONTROL
    find=proof.STEAM_FIND if backend=='steam' else proof.TT_FIND
    uc.mem_map(STATE,8192);uc.mem_map(0x51000000,0x4000)
    put=lambda addr,val:uc.mem_write(addr,s.u32(val))
    put(OBJECT+12,proof.CONTROLLER);put(OBJECT+0x6f0,OBSERVERS);put(OBJECT+0x6f8,1)
    put(OBSERVERS,SCRIPT);put(SCRIPT,VTABLE);put(VTABLE+4,GET_NAME)
    put(SCRIPT+0x18,VARIABLE);put(VARIABLE,FLAG);put(VARIABLE+4,1);put(VARIABLE+8,89)
    uc.mem_write(FLAG,bytes([value]));uc.mem_write(NAME,proof.SCRIPT)
    uc.mem_write(GET_NAME,b'\xb8'+s.u32(NAME)+b'\xc3')
    uc.mem_write(find,b'\xb8'+s.u32(OBJECT)+b'\xc3')
    uc.mem_write(STATE+s.CODE_OFFSET,proof.build_evidence(STATE+s.CODE_OFFSET,STATE,control,find))
    if backend=='steam':
        uc.mem_write(control+s.CODE_OFFSET,s.build_services(control,evidence_target=STATE+s.CODE_OFFSET))
        e.put(s.BASE+s.OBJECTIVE_COUNT,0)
        frame=e.frame
    else:
        code=tt.build_scan(control,e.a,evidence_target=STATE+s.CODE_OFFSET)
        frame=lambda:e.run(code)
    return SimpleNamespace(e=e,uc=uc,control=control,put=put,frame=frame,
        value=lambda v:uc.mem_write(FLAG,bytes([v])), read=e.read)


@pytest.mark.parametrize('backend',['steam','tt'])
def test_early_sam_destruction_captures_without_objective_and_saves_once(backend):
    f=fixture(backend);f.frame()
    assert f.read(s.CAPTURED+4)==0
    f.value(1);f.frame()
    assert f.read(s.CAPTURED+4)==proof.SAM_BIT
    assert f.read(s.SCRIPT_CAPTURED+4)==proof.SAM_BIT
    assert f.read(s.CALLBACK_CAPTURED+4)==f.read(s.LIST_CAPTURED+4)==0
    if backend=='steam': assert f.e.calls.count('save')==1
    else:
        assert f.read(s.OBJECTIVE_EVENTS+4)==proof.SAM_BIT
        f.put(f.control+s.OBJECTIVE_EVENTS+4,0)
    f.frame()
    if backend=='steam': assert f.e.calls.count('save')==1
    else: assert f.read(s.OBJECTIVE_EVENTS+4)==0


@pytest.mark.parametrize('backend',['steam','tt'])
def test_loaded_destroyed_sam_recovers_check_without_redundant_checkpoint(backend,caplog):
    f=fixture(backend,1);f.frame()
    assert f.read(s.CAPTURED+4)==proof.SAM_BIT
    assert f.read(s.OBJECTIVE_EVENTS+4)==0
    assert 'save' not in f.e.calls
    read=lambda a:int.from_bytes(f.uc.mem_read(a,4),'little')
    capture=SecondaryCapture()
    with caplog.at_level('INFO'):
        capture.collect(SimpleNamespace(memory=SimpleNamespace(u32=read),control=f.control))
    assert ('M01',108) in capture.pending
    assert 'persistent mission-script completion flag' in caplog.text
    assert 'completion callback' not in caplog.text
    completed=set()
    client=SimpleNamespace(capture_ready=True,config={'primary_objectives':True},progress=SimpleNamespace(completed=completed),
        objective_finished=lambda m,o:completed.add(objective_id(m,o)) or True)
    capture.flush(client)
    assert completed=={objective_id('M01',108)}


@pytest.mark.parametrize('backend',['steam','tt'])
@pytest.mark.parametrize('case',['wrong_controller','wrong_script','missing_script','oversized_observers',
    'missing_flag','cycle','wrong_size','bad_bool','false','disconnected','expired','locked','wrong_map','menu'])
def test_no_inference_from_missing_or_unrelated_state(backend,case):
    f=fixture(backend,1)
    if case=='wrong_controller':f.put(OBJECT+12,100031)
    if case=='wrong_script':f.uc.mem_write(NAME,b'M01_HandOfNod_SAMSite_Script\0')
    if case=='missing_script':f.put(OBJECT+0x6f8,0)
    if case=='oversized_observers':f.put(OBJECT+0x6f8,33)
    if case in ('missing_flag','cycle'):f.put(VARIABLE+8,17)
    if case=='cycle':f.put(VARIABLE+12,VARIABLE)
    if case=='wrong_size':f.put(VARIABLE+4,4)
    if case=='bad_bool':f.value(2)
    if case=='false':f.value(0)
    if case=='disconnected':f.put(f.control+s.ENABLED,0)
    if case=='expired':f.put(f.control+s.HEARTBEAT,1)
    if case=='locked':f.put(f.control+s.ALLOWED,1)
    if case=='menu':f.put(s.BASE+s.STEAM.game_mode if backend=='steam' else f.e.a.game_mode,0)
    if case=='wrong_map':
        addr=s.BASE+s.STEAM.loaded_map if backend=='steam' else f.e.a.loaded_map
        pointer=int.from_bytes(f.uc.mem_read(addr,4),'little');f.uc.mem_write(pointer,b'M02.lsd\0')
    f.frame()
    assert f.read(s.CAPTURED+4)==f.read(s.OBJECTIVE_EVENTS+4)==0


def test_steam_pause_defers_flag_transition_and_checkpoint():
    f=fixture('steam');f.frame();f.put(STEAM_OBJECT+4,1);f.value(1);f.frame()
    assert f.read(s.CAPTURED+4)==0 and 'save' not in f.e.calls
    f.put(STEAM_OBJECT+4,0);f.frame()
    assert f.read(s.CAPTURED+4)==proof.SAM_BIT and f.e.calls.count('save')==1
