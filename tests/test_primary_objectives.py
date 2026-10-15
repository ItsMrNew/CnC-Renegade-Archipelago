"""Primary check contract, exact native capture and durable client delivery."""
import asyncio
from types import SimpleNamespace

import pytest

from renegade.client import RenegadeClient
from renegade.data import (ALL_LOCATIONS, BASE_ID, MISSIONS, PRIMARY_OBJECTIVES,
    SECONDARY_OBJECTIVES, SECONDARY_DISPLAY_ORDER, OBJECTIVES, OBJECTIVE_DISPLAY_ORDER,
    VICTORY_PRIMARY_OBJECTIVES, primary_id, secondary_id, objective_id, objective_name,
    objective_kind, location_ids, slot_data, validate_slot_data)
from renegade.desktop import snapshot, preview_missions
from renegade.native import services as s
from renegade.native.secondary_capture import SecondaryCapture
from test_core import Socket, received
from test_services import Engine, CONTROL, OBJECT
from test_objective_hook import prepare, call, Memory

PRIMARY_CASES = [(m, oid) for m in MISSIONS for oid in PRIMARY_OBJECTIVES[m.map_name]]


def config(primary=True, secondary=True):
    return slot_data(5, 12, secondary, False, True, True, True, 'progressive', False, primary)


async def connect(client, socket, cfg=None):
    cfg = cfg or config()
    await client.handle(socket, dict(cmd='RoomInfo', seed_name='primary-test'))
    await client.handle(socket, dict(cmd='Connected', team=0, slot=1, slot_data=cfg,
        checked_locations=[], missing_locations=cfg['location_ids']))
    await client.handle(socket, dict(cmd='ReceivedItems', index=0, items=received(12)))


@pytest.mark.parametrize('primary', [False, True])
@pytest.mark.parametrize('secondary', [False, True])
def test_v8_feature_combinations(primary, secondary):
    cfg = config(primary, secondary)
    assert validate_slot_data(cfg) == cfg
    assert cfg['protocol_version'] == 8
    assert len(cfg['location_ids']) == 60 + 53 * primary + 54 * secondary
    assert slot_data()['checks_per_mission'] == 5


@pytest.mark.parametrize('value', [None, 0, 1, 'true', {}, []])
def test_bad_primary_setting_rejected(value):
    cfg = config(); cfg['primary_objectives'] = value
    with pytest.raises(ValueError): validate_slot_data(cfg)


def test_legacy_contract_cannot_enable_new_locations():
    cfg = slot_data(3, 1, True, False, True, True, True, 'progressive', False)
    assert cfg['protocol_version'] == 7 and len(cfg['location_ids']) == 90
    assert validate_slot_data(cfg) == cfg
    cfg['primary_objectives'] = True
    with pytest.raises(ValueError): validate_slot_data(cfg)


def test_catalogue_ids_labels_route_order_and_native_buffer_bounds():
    assert len(PRIMARY_CASES) == 53 and len(ALL_LOCATIONS) == 190
    assert not PRIMARY_OBJECTIVES['M13']  # Stock opening uses HUD help, not objective entries.
    assert 1009 not in PRIMARY_OBJECTIVES['M03']  # Actually completed as tertiary.
    assert 906 not in PRIMARY_OBJECTIVES['M09']  # Commented-out objective.
    for m in MISSIONS:
        key = m.map_name
        assert set(OBJECTIVE_DISPLAY_ORDER[key]) == set(OBJECTIVES[key])
        assert len(OBJECTIVE_DISPLAY_ORDER[key]) == len(set(OBJECTIVES[key])) <= 32
        assert tuple(o for o in OBJECTIVE_DISPLAY_ORDER[key] if o in SECONDARY_OBJECTIVES[key]) == SECONDARY_DISPLAY_ORDER[key]
        for i, oid in enumerate(SECONDARY_OBJECTIVES[key]):
            assert secondary_id(key, oid) == BASE_ID + 0x1000 + (m.number-1)*32 + i
            assert OBJECTIVES[key].index(oid) == i  # Existing capture bits unchanged.
        for oid in OBJECTIVES[key]:
            kind = objective_kind(key, oid).title()
            assert objective_name(m, oid).endswith(f'({kind} Objective)')
            assert ALL_LOCATIONS[objective_name(m, oid)] == objective_id(key, oid)
    assert location_ids(5, True).isdisjoint({primary_id(m.map_name, o) for m,o in PRIMARY_CASES})
    assert s.PRESENT_AT_CALL + 12*4 <= s.RECORDS
    assert s.RECORDS + sum(map(len, OBJECTIVES.values()))*s.RECORD_SIZE <= s.FILES
    assert s.FILES + 12*64 <= s.WARHEAD < 4096 - 32


@pytest.mark.parametrize('mission,oid', PRIMARY_CASES)
def test_every_primary_callback_captured_even_without_list_entry(mission, oid):
    e = prepare(mission)
    e.put(s.BASE+s.OBJECTIVE_COUNT, 0)
    bit = 1 << OBJECTIVES[mission.map_name].index(oid)
    assert call(e, oid) == bit
    assert e.read(s.CALLBACK_CAPTURED+(mission.number-1)*4) == bit
    e.frame()
    assert e.calls.count('save') == 1
    call(e, oid); e.frame()
    assert e.calls.count('save') == 1


@pytest.mark.parametrize('mission,oid', PRIMARY_CASES)
def test_every_primary_loaded_save_captured_without_redundant_checkpoint(mission, oid):
    e = Engine(mission, [oid])
    e.objective(0, kind=1)
    e.frame()
    bit = 1 << OBJECTIVES[mission.map_name].index(oid)
    assert e.read(s.CAPTURED+(mission.number-1)*4) == bit
    assert e.read(s.LIST_CAPTURED+(mission.number-1)*4) == bit
    assert 'save' not in e.calls


@pytest.mark.parametrize('status,kind', [(0,1), (2,1), (3,1), (1,2), (1,3)])
def test_primary_pending_failed_hidden_and_wrong_type_rejected(status, kind):
    e = Engine(MISSIONS[1], [106]); e.frame()
    e.objective(0, status=status, kind=kind); e.frame()
    assert e.read(s.CAPTURED+4) == 0 and 'save' not in e.calls


def test_primary_live_checkpoint_waits_for_control_and_keeps_secondary_capture():
    e = prepare(); e.put(s.BASE+s.OBJECTIVE_COUNT, 0)
    e.put(OBJECT+8192+s.CAMERA_HOST, OBJECT)
    primary_bit = 1 << OBJECTIVES['M01'].index(106)
    assert call(e, 106) == primary_bit
    assert call(e, 109) == primary_bit | 16
    e.frame(); assert 'save' not in e.calls
    e.put(OBJECT+8192+s.CAMERA_HOST, 0); e.frame()
    assert e.calls.count('save') == 1


def test_all_primary_checks_send_once_and_do_not_replace_victory(tmp_path):
    async def run():
        c = RenegadeClient('localhost:1', 'Havoc', state_dir=tmp_path); sock=Socket()
        await connect(c, sock)
        for m, oid in PRIMARY_CASES:
            assert c.objective_finished(m.map_name, oid)
            assert not c.objective_finished(m.map_name, oid)
        await c.flush_once(sock)
        expected = {primary_id(m.map_name, o) for m,o in PRIMARY_CASES}
        assert set(sock.sent[-1]['locations']) == expected and not c.goal_sent
        await c.handle(sock, dict(cmd='RoomUpdate', checked_locations=sorted(expected)))
        rows = snapshot(c, {})['missions']
        assert rows[1]['state'] == 'Available' and rows[1]['checks'] == '4/15'
        row = next(o for o in rows[1]['objectives'] if o['id'] == 106)
        assert row == dict(id=106, title='Locate Detention Center (Primary Objective)', state='Complete', checks='1/1')
        assert any(o['title']=='Rescue Prisoners (Secondary Objective)' for o in rows[1]['objectives'])
        for m in MISSIONS: c.mission_finished(m.map_name, True)
        await c.handle(sock, dict(cmd='RoomUpdate', checked_locations=sorted(c.progress.completed)))
        await c.flush_once(sock)
        assert c.goal_sent  # No secondary objective is required to finish.
    asyncio.run(run())


@pytest.mark.parametrize('primary,secondary', [(False,False), (False,True), (True,False), (True,True)])
def test_client_feature_switches_filter_each_category(tmp_path, primary, secondary):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(c,sock,config(primary,secondary))
        assert c.objective_finished('M01',106) is primary
        assert c.objective_finished('M01',104) is secondary
        ids={o['id'] for o in snapshot(c,{})['missions'][1]['objectives']}
        assert (106 in ids) is primary and (104 in ids) is secondary
    asyncio.run(run())


def test_only_whitelisted_terminal_primaries_complete_on_successful_victory(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(c,sock)
        for m in MISSIONS:
            before=set(c.progress.completed)
            assert not c.mission_finished(m.map_name,False)
            assert c.progress.completed == before
            assert c.mission_finished(m.map_name,True)
            expected={m.check_id(i) for i in range(1,6)} | {
                primary_id(m.map_name,oid) for oid in VICTORY_PRIMARY_OBJECTIVES.get(m.map_name,())}
            assert c.progress.completed-before == expected
            assert not c.mission_finished(m.map_name,True)
    asyncio.run(run())


def test_primary_survives_disconnect_disk_failure_and_client_restart(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);sock=Socket()
        await connect(c,sock); c.connected=False
        mem=Memory(); bit=1<<OBJECTIVES['M01'].index(106)
        mem.write_u32(CONTROL+s.CAPTURED+4,bit)
        captures=SecondaryCapture(); captures.collect(SimpleNamespace(memory=mem,control=CONTROL))
        merge=c.progress.merge
        def fail(checks): raise OSError('disk unavailable')
        c.progress.merge=fail; captures.flush(c)
        assert captures.pending == {('M01',106)} and not c.progress.completed
        c.progress.merge=merge; captures.flush(c)
        assert not captures.pending and c.progress.completed=={primary_id('M01',106)}
        reloaded=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);new_socket=Socket()
        await connect(reloaded,new_socket);await reloaded.flush_once(new_socket)
        assert new_socket.sent[-1] == dict(cmd='LocationChecks',locations=[primary_id('M01',106)])
        reloaded.connected=False
        assert all(o['state']=='Unknown' for o in snapshot(reloaded,{})['missions'][1]['objectives'])
    asyncio.run(run())


def test_preview_has_both_objective_labels_before_connection():
    row=preview_missions()[1]
    assert any(o['title'].endswith('(Primary Objective)') for o in row['objectives'])
    assert any(o['title'].endswith('(Secondary Objective)') for o in row['objectives'])
    assert all(o['state']=='Unknown' for o in row['objectives'])
