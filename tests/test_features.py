import asyncio
import math
import pytest
from renegade.client import RenegadeClient
from renegade.data import (MISSIONS, SECONDARY_OBJECTIVES, location_ids, secondary_id, slot_data, validate_slot_data)
from renegade.deathlink import DeathLinkState
from renegade.desktop import snapshot
from test_core import Socket, received


async def connected(client, socket, secondary=True, death=True):
    config=slot_data(3,12,secondary,death)
    await client.handle(socket,dict(cmd='RoomInfo',seed_name='feature-seed'))
    await client.handle(socket,dict(cmd='Connected',team=0,slot=1,slot_data=config,
        checked_locations=[],missing_locations=sorted(location_ids(3,secondary))))
    await client.handle(socket,dict(cmd='ReceivedItems',index=0,items=received(12)))


def test_v1_contract_is_unchanged_and_v2_locations_are_disjoint():
    old=slot_data()
    assert old['protocol_version']==1 and 'secondary_objectives' not in old
    new=slot_data(3,1,True,True)
    assert new['protocol_version']==2 and len(new['location_ids'])==90
    assert validate_slot_data(old)==old and validate_slot_data(new)==new
    assert location_ids(3) < location_ids(3,True)
    assert sum(map(len,SECONDARY_OBJECTIVES.values()))==54
    for m in MISSIONS:
        for oid in SECONDARY_OBJECTIVES[m.map_name]:
            assert secondary_id(m.map_name,oid) not in location_ids(5)


def test_secondaries_send_only_their_check_and_do_not_gate_victory_goal(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);s=Socket()
        await connected(c,s)
        assert c.objective_finished('M01',104)
        assert not c.objective_finished('M01',104)
        await c.flush_once(s)
        assert s.sent[-1]==dict(cmd='LocationChecks',locations=[secondary_id('M01',104)])
        assert not c.goal_sent
        assert snapshot(c,{})['missions'][1]['checks']=='0/9'
        objective = next(o for o in snapshot(c,{})['missions'][1]['objectives'] if o['id'] == 104)
        assert objective['state']=='Pending sync'
        await c.handle(s,dict(cmd='RoomUpdate',checked_locations=sorted(location_ids(3))))
        await c.flush_once(s)
        assert c.goal_sent and s.sent[-1]==dict(cmd='StatusUpdate',status=30)
    asyncio.run(run())


def test_legacy_room_does_not_send_secondary_locations(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);s=Socket()
        await connected(c,s,False,False)
        assert not c.objective_finished('M01',104)
        assert not c.deathlink.enabled
        assert not c.player_died('M01')
    asyncio.run(run())


def test_deathlink_tags_delivery_and_no_reflection(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);s=Socket()
        await connected(c,s)
        assert dict(cmd='ConnectUpdate',tags=['AP','DeathLink']) in s.sent
        assert c.player_died('M01')
        await c.flush_once(s)
        packet=next(p for p in s.sent if p['cmd']=='Bounce')
        assert packet['data']['source']=='Havoc' and c.deathlink.sent==1
        await c.handle(s,dict(packet,cmd='Bounced'))
        assert not c.deathlink.incoming
        incoming=dict(cmd='Bounced',tags=['DeathLink'],data=dict(time=12.0,source='Sakura',cause='test'))
        await c.handle(s,incoming);await c.handle(s,incoming)
        assert len(c.deathlink.incoming)==1 and not c.deathlink.outgoing
        c.deathlink.clear_pending()
        assert not c.deathlink.incoming
    asyncio.run(run())


@pytest.mark.parametrize('value',[None,{},dict(time=float('nan'),source='Sakura'),dict(time=float('inf'),source='Sakura'),
    dict(time=True,source='Sakura'),dict(time=1,source=3),dict(time=10**400,source='Sakura')])
def test_malformed_deaths_ignored(value):
    state=DeathLinkState();state.enabled=True
    assert not state.receive(dict(tags=['DeathLink'],data=value),'Havoc')


@pytest.mark.parametrize('tags', [None, 1, 'DeathLink', {}])
def test_malformed_death_tags_ignored(tags):
    state = DeathLinkState(); state.enabled = True
    assert not state.receive(dict(tags=tags, data=dict(time=1, source='Sakura')), 'Havoc')


@pytest.mark.parametrize("enabled", [False, True])
def test_deathlink_follows_only_room_setting(tmp_path, enabled):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);c.death_link_override=not enabled;s=Socket()
        await connected(c,s,death=enabled)
        assert c.deathlink.enabled is enabled
        assert any(p['cmd']=='ConnectUpdate' for p in s.sent) is enabled
    asyncio.run(run())
