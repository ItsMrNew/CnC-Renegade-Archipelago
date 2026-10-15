import struct
from types import SimpleNamespace

import pytest
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ECX, UC_X86_REG_ESP

from renegade.native import mission_launch as m
from renegade.native.savefiles import INFO, DATA
from test_services import Engine, CONTROL, BASE, OBJECT, STACK, RETURN


@pytest.mark.parametrize('reason',['okay','locked','stale','combat','suspended','invalid_map','reentry'])
def test_menu_loader_calls_native_loader_only_from_authorized_main_menu(reason):
    e=Engine();uc=e.machine
    uc.mem_write(CONTROL+m.CODE_OFFSET,m.build_loader(CONTROL))
    for off,val in ((m.REQUEST,1),(m.RESULT,0),(m.BUSY,0),(m.HEARTBEAT,9900),(m.ALLOWED,2),(m.MAP,1)):
        e.put(CONTROL+off,val)
    e.put(BASE+m.STEAM.game_mode,0)
    for fun in (m.INIT_SP,m.START_GAME,m.MENU_THINK):uc.mem_write(BASE+fun,b'\xc3')
    if reason=='locked':e.put(CONTROL+m.ALLOWED,1)
    if reason=='stale':e.put(CONTROL+m.HEARTBEAT,0)
    if reason=='combat':e.put(BASE+m.STEAM.game_mode,1)
    if reason=='suspended':e.put(OBJECT+4,1)
    if reason=='invalid_map':e.put(CONTROL+m.MAP,12)
    if reason=='reentry':e.put(CONTROL+m.BUSY,1)
    calls=[]
    def observe(machine,address,size,data):
        if address in (BASE+m.INIT_SP,BASE+m.START_GAME,BASE+m.MENU_THINK):calls.append(address-BASE)
        if address==BASE+m.START_GAME:
            esp=uc.reg_read(UC_X86_REG_ESP)
            assert struct.unpack('<3I',uc.mem_read(esp+4,12))==(CONTROL+m.FILENAME,0xffffffff,0)
    uc.hook_add(UC_HOOK_CODE,observe)
    uc.reg_write(UC_X86_REG_ECX,OBJECT);uc.reg_write(UC_X86_REG_ESP,STACK);e.put(STACK,RETURN)
    uc.emu_start(CONTROL+m.CODE_OFFSET,RETURN,count=3000)
    assert uc.reg_read(UC_X86_REG_ESP)==STACK+4 and uc.reg_read(UC_X86_REG_ECX)==OBJECT
    assert calls==([m.INIT_SP,m.START_GAME] if reason=='okay' else [] if reason=='reentry' else [m.MENU_THINK])
    assert e.read(m.RESULT)==(1 if reason=='okay' else 0 if reason=='reentry' else 2)


def saved(map_name='M01',difficulty=1):
    def chunk(kind,payload):return struct.pack('<II',kind,len(payload))+payload
    metadata=b'\1\x08'+(map_name+'.lsd\0').encode()
    world=b'\1\1\0\2\4'+struct.pack('<I',difficulty)
    for kind in (916991715,916991655,0x40000,DATA):world=chunk(kind,world)
    return chunk(INFO,metadata)+world


def test_save_selection_uses_mission_and_difficulty_and_never_another_rooms_checkpoint(tmp_path):
    for difficulty,label in m.DIFFICULTIES.items():
        (tmp_path/f'ap_start_pack_{label}_02_M01.sav').write_bytes(saved(difficulty=difficulty))
    (tmp_path/'ap_checkpoint_other_M01.sav').write_bytes(saved())
    for difficulty,label in m.DIFFICULTIES.items():
        assert label in m.select_save(tmp_path,'room','M01',difficulty).name
    with pytest.raises(ValueError,match='No checkpoint'):m.select_save(tmp_path,'room','M01',1,True)
    path=tmp_path/'ap_checkpoint_room_M01.sav';path.write_bytes(saved(difficulty=2))
    with pytest.raises(ValueError,match='Commando'):m.select_save(tmp_path,'room','M01',1,True)
    assert m.select_save(tmp_path,'room','M01',2,True)==path
    path.write_bytes(saved('M02'))
    with pytest.raises(ValueError,match='different mission'):m.select_save(tmp_path,'room','M01',1,True)


def test_corrupt_start_save_is_not_loaded(tmp_path):
    (tmp_path/'ap_start_pack_Soldier_02_M01.sav').write_bytes(b'truncated save')
    with pytest.raises(ValueError):m.select_save(tmp_path,'room','M01',1)


def launcher(tmp_path):
    data={BASE+m.MENU_SLOT:CONTROL+m.CODE_OFFSET,BASE+m.STEAM.game_mode:0,OBJECT+4:1}
    writes={}
    runtime=m.MissionLauncher.__new__(m.MissionLauncher)
    runtime.control=CONTROL;runtime.save_dir=tmp_path;runtime.token='room';runtime.combat=OBJECT
    runtime.pending=runtime.confirmation=None;runtime.submitted=0
    runtime.memory=SimpleNamespace(u32=lambda a:data.get(a,0),write_u32=lambda a,v:data.__setitem__(a,v),
                                   tick_count=lambda:10000,write=lambda a,v:writes.__setitem__(a,v))
    client=SimpleNamespace(ready=True,unlocked_maps=('M01',),mission_request=None,mission_confirmation=None)
    checkpoint=tmp_path/'ap_checkpoint_room_M01.sav';checkpoint.write_bytes(saved(difficulty=1))
    for difficulty,label in m.DIFFICULTIES.items():
        (tmp_path/f'ap_start_pack_{label}_02_M01.sav').write_bytes(saved(difficulty=difficulty))
    return runtime,client,data,writes,checkpoint


def test_matching_difficulty_continues_without_a_restart_offer(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    client.mission_request=('M01',1,True);runtime.update(client)
    assert client.mission_confirmation is None and data[CONTROL+m.REQUEST]==1
    assert writes[CONTROL+m.FILENAME]==b'save\\ap_load_room.sav\0'
    assert (tmp_path/'ap_load_room.sav').read_bytes()==checkpoint.read_bytes()


@pytest.mark.parametrize('difficulty',[0,2])
def test_different_difficulty_requires_confirmation_then_loads_start_save(tmp_path,difficulty):
    runtime,client,data,writes,checkpoint=launcher(tmp_path);original=checkpoint.read_bytes()
    client.mission_request=('M01',difficulty,True);runtime.update(client)
    offer=client.mission_confirmation
    assert offer['difficulty']==difficulty and offer['saved_difficulty']==1 and offer['map']=='M01'
    assert client.mission_launch['busy'] and not data.get(CONTROL+m.REQUEST) and not writes
    client.mission_request=('M01',difficulty,True,offer['id']);runtime.update(client)
    assert data[CONTROL+m.REQUEST]==1 and client.mission_confirmation is None
    assert writes[CONTROL+m.FILENAME]==b'save\\ap_load_room.sav\0'
    assert m.saved_difficulty((tmp_path/'ap_load_room.sav').read_bytes())==difficulty
    assert checkpoint.read_bytes()==original  # Never rewrite the existing checkpoint.


@pytest.mark.parametrize('reason',['cancel','disconnect','locked','wrong_id'])
def test_stale_or_cancelled_restart_confirmation_never_loads(tmp_path,reason):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    client.mission_request=('M01',2,True);runtime.update(client);token=client.mission_confirmation['id']
    if reason=='cancel':client.mission_confirmation=None
    elif reason=='disconnect':client.ready=False
    elif reason=='locked':client.unlocked_maps=()
    else:token='stale-token'
    client.mission_request=('M01',2,True,token);runtime.update(client)
    assert not writes and not data.get(CONTROL+m.REQUEST)
    assert client.mission_confirmation is None


def test_checkpoint_replaced_while_prompt_open_requires_new_confirmation(tmp_path):
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    client.mission_request=('M01',2,True);runtime.update(client);token=client.mission_confirmation['id']
    checkpoint.write_bytes(saved(difficulty=0))
    client.mission_request=('M01',2,True,token);runtime.update(client)
    assert not writes and not data.get(CONTROL+m.REQUEST)
    assert client.mission_confirmation['id']!=token and client.mission_confirmation['saved_difficulty']==0


def test_missing_start_save_does_not_offer_an_impossible_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('renegade.start_saves.resource_root', lambda: tmp_path / 'no-bundled-assets')
    runtime,client,data,writes,checkpoint=launcher(tmp_path)
    (tmp_path/'ap_start_pack_Commando_02_M01.sav').unlink()
    client.mission_request=('M01',2,True);runtime.update(client)
    assert client.mission_confirmation is None and not writes and not data.get(CONTROL+m.REQUEST)
