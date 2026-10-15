import asyncio
import pytest

from renegade.client import RenegadeClient
from renegade.data import (MISSIONS, ITEM_IDS, ITEM_FLAGS, CLEARANCE, BUFFS,
                           slot_data, validate_slot_data, mission_requirement, secondary_id)
from renegade.desktop import snapshot
from test_core import Socket


def config(mode='individual', starting=1, checks=3, secondary=True):
    return slot_data(checks, starting, secondary, False, True, True, True, mode)


def items(*names):
    return [dict(item=ITEM_IDS[name], location=-2, player=1, flags=ITEM_FLAGS[name]) for name in names]


async def connected(tmp_path, cfg):
    client=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path);socket=Socket()
    await client.handle(socket,dict(cmd='RoomInfo',seed_name='mission-mode'))
    await client.handle(socket,dict(cmd='Connected',team=0,slot=1,slot_data=cfg,
                                  checked_locations=[],missing_locations=cfg['location_ids']))
    return client,socket


@pytest.mark.parametrize('mode',['progressive','individual'])
@pytest.mark.parametrize('starting',[1,3,12])
@pytest.mark.parametrize('checks',[1,5])
def test_mission_unlock_contract(mode,starting,checks):
    cfg=config(mode,starting,checks)
    assert cfg['protocol_version']==6 and validate_slot_data(cfg)==cfg
    assert len(set(ITEM_IDS.values()))==len(ITEM_IDS)
    assert all(ITEM_FLAGS[m.unlock_item]==1 for m in MISSIONS)
    assert all(ITEM_FLAGS[name]==0 for name in BUFFS)


@pytest.mark.parametrize('mode',[None,'random',1,True])
def test_invalid_mode_is_rejected(mode):
    cfg=config();cfg['mission_unlocks']=mode
    with pytest.raises(ValueError):validate_slot_data(cfg)


def test_legacy_contract_is_unchanged_and_cannot_claim_individual_unlocks():
    for cfg in (slot_data(),slot_data(3,1,True),slot_data(3,1,True,False,True),
                slot_data(3,1,True,False,True,True),slot_data(3,1,True,False,True,True,True)):
        assert validate_slot_data(cfg)==cfg and 'mission_unlocks' not in cfg
        cfg['mission_unlocks']='individual'
        with pytest.raises(ValueError):validate_slot_data(cfg)


def test_individual_receipts_unlock_only_their_maps_and_allow_out_of_order_checks(tmp_path):
    async def run():
        c,sock=await connected(tmp_path,config())
        receipt=items(MISSIONS[0].unlock_item,MISSIONS[7].unlock_item,MISSIONS[-1].unlock_item,CLEARANCE)
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=receipt))
        assert c.unlocked_maps==('M13','M07','M11')
        assert not c.mission_finished('M01',True)
        assert c.mission_finished('M11',True)
        assert c.objective_finished('M07',704)
        await c.flush_once(sock)
        checks={MISSIONS[-1].check_id(i) for i in range(1,4)}|{secondary_id('M07',704)}
        assert c.progress.completed==checks and not c.goal_sent
        await c.handle(sock,dict(cmd='RoomUpdate',checked_locations=list(checks)))
        data=snapshot(c,{})
        assert (data['confirmed'],data['total'])==(4,90)
        assert (data['logic_confirmed'],data['logic_total'],data['unlocked'])==(4,14,3)
        # Replayed inventory and duplicate individual items never open another map.
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=receipt))
        await c.handle(sock,dict(cmd='ReceivedItems',index=4,items=items(MISSIONS[-1].unlock_item)))
        assert c.unlocked_maps==('M13','M07','M11')
        c.connected=False
        assert not c.unlocked_maps and snapshot(c,{})['logic_total'] is None
    asyncio.run(run())


def test_progressive_mode_ignores_individual_items_and_matches_world_requirements(tmp_path):
    async def run():
        c,sock=await connected(tmp_path,config('progressive'))
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items(CLEARANCE,CLEARANCE,MISSIONS[-1].unlock_item)))
        assert c.unlocked_maps==('M13','M01')
        assert mission_requirement(MISSIONS[-1])==(CLEARANCE,12)
        assert mission_requirement(MISSIONS[-1],'individual')==(MISSIONS[-1].unlock_item,1)
    asyncio.run(run())


def test_logic_counters_keep_unfinished_secondaries_after_victory(tmp_path):
    async def run():
        c,sock=await connected(tmp_path,config('progressive',2))
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=items(CLEARANCE,CLEARANCE)))
        c.mission_finished('M01',True)
        # Pending checks are not yet server-confirmed in either counter.
        data=snapshot(c,{})
        assert (data['confirmed'],data['logic_confirmed'],data['logic_total'])==(0,0,12)
        await c.handle(sock,dict(cmd='RoomUpdate',checked_locations=list(c.progress.completed)))
        data=snapshot(c,{})
        assert (data['confirmed'],data['logic_confirmed'],data['logic_total'])==(3,3,12)
        assert data['missions'][1]['state']=='Available'  # Secondary checks remain.
        assert data['missions'][1]['objectives'][0]['state']=='Complete'  # Victory itself is confirmed.
        await c.handle(sock,dict(cmd='ReceivedItems',index=2,items=items(CLEARANCE)))
        assert snapshot(c,{})['logic_total']==33
    asyncio.run(run())
