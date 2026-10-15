import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from renegade.client import RenegadeClient, IncompatibleRoom
from renegade.data import *
from renegade.state import Progress
from renegade.native.recovery import recover_activations
from renegade.native import rewards as r, services as s, traps as t
from test_core import Socket, received
from test_rewards import reward_client, RewardEngine, REWARD, HP, ARMOUR, MAX_HP, MAX_ARMOUR
from test_bonuses import BonusEngine
from test_services import OBJECT


def test_outage_checks_persist_and_reconnect_only_to_same_room(tmp_path):
    async def run():
        c=RenegadeClient('localhost:1','Havoc',state_dir=tmp_path); sock=Socket()
        cfg=slot_data(3,2,True)
        packet=dict(cmd='Connected',team=0,slot=1,slot_data=cfg,checked_locations=[],missing_locations=cfg['location_ids'])
        await c.handle(sock,dict(cmd='RoomInfo',seed_name='outage'))
        await c.handle(sock,packet)
        assert not c.capture_ready
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=received(2)))
        c.connected=False;c.inventory.synchronized=False
        assert c.capture_ready and c.capture_maps==('M13','M01') and not c.unlocked_maps
        assert c.objective_finished('M01',109) and c.mission_finished('M01',True)
        assert not c.mission_finished('M02',True) and not c.player_died('M01')
        saved=Progress(tmp_path,*c.identity,c.locations,c.config)
        assert len(saved.completed)==4
        before=len(sock.sent);await c.flush_once(sock);assert len(sock.sent)==before
        await c.handle(sock,packet)
        await c.handle(sock,dict(cmd='ReceivedItems',index=0,items=received(2)))
        await c.flush_once(sock)
        assert sock.sent[-1]==dict(cmd='LocationChecks',locations=sorted(saved.completed))
        await c.handle(sock,dict(cmd='RoomUpdate',checked_locations=list(saved.completed)))
        assert not c.mission_finished('M01',True)
        with pytest.raises(IncompatibleRoom):
            await c.handle(sock,dict(cmd='RoomInfo',seed_name='another seed'))
        c.stop.set();assert not c.capture_ready and not c.capture_maps
    asyncio.run(run())


def test_actual_websocket_drop_reconnects_and_flushes_checks_captured_offline(tmp_path):
    import json
    from websockets.asyncio.server import serve
    async def run():
        cfg=slot_data(3,2,True)
        authenticated=asyncio.Event();drop=asyncio.Event();reported=asyncio.Event()
        connections=0;checks=[]
        async def server(socket):
            nonlocal connections
            connections+=1;first=connections==1
            await socket.send(json.dumps([dict(cmd='RoomInfo',seed_name='socket-drop')]))
            await socket.recv()
            await socket.send(json.dumps([
                dict(cmd='Connected',team=0,slot=1,slot_data=cfg,checked_locations=[],missing_locations=cfg['location_ids']),
                dict(cmd='ReceivedItems',index=0,items=received(2))]))
            if first:
                authenticated.set();await asyncio.wait_for(drop.wait(),10);await socket.close();return
            async for message in socket:
                for packet in json.loads(message):
                    if packet['cmd']=='LocationChecks':
                        checks.extend(packet['locations']);reported.set()
        async with serve(server,'127.0.0.1',0) as listener:
            port=listener.sockets[0].getsockname()[1]
            c=RenegadeClient(f'127.0.0.1:{port}','Havoc',state_dir=tmp_path)
            task=asyncio.create_task(c.network())
            try:
                async def until(predicate):
                    while not predicate():
                        if task.done():task.result()
                        await asyncio.sleep(.01)
                await asyncio.wait_for(authenticated.wait(),5)
                await asyncio.wait_for(until(lambda:c.ready),2)
                drop.set();await asyncio.wait_for(until(lambda:not c.connected),2)
                assert c.capture_ready and c.objective_finished('M01',109)
                assert c.mission_finished('M01',True)
                await asyncio.wait_for(reported.wait(),6)
                assert connections==2 and set(checks)==c.progress.completed and len(checks)==4
            finally:
                drop.set()
                c.stop.set();await asyncio.wait_for(task,3)
    asyncio.run(run())


@pytest.mark.parametrize('result,expected',[(1,'applied'),(2,'refunded'),(3,'refunded'),(0,'uncertain'),(77,'uncertain')])
def test_crashed_activation_recovers_exact_receipt_without_replaying(tmp_path,result,expected):
    async def run():
        c,_,_=await reward_client(tmp_path)
        c.progress.reserve_activation(1,dict(pid=7,control=REWARD,cookie='ab'*16))
        c.progress=Progress(tmp_path,*c.identity,c.locations,c.config)
        values={REWARD+r.RESULT:result,REWARD+r.RECEIPT_INDEX:1}
        memory=SimpleNamespace(read=lambda a,n:bytes.fromhex('ab'*16),u32=lambda a:values.get(a,0),close=lambda:None)
        recover_activations(c,lambda pid:memory)
        assert (1 in c.progress.supply_spent)==(expected!='refunded')
        assert bool(c.progress.activations)==(expected=='uncertain')
        if expected=='uncertain':
            assert c.progress.activations['1']['status']=='uncertain'
            c.resolve_uncertain_items(True)
            assert not c.progress.activations and c.supply_indexes(HEALTH_SUPPLY)==[1]
    asyncio.run(run())


@pytest.mark.parametrize('failure',['wrong_cookie','wrong_index','game_exited'])
def test_recovery_never_trusts_reused_process_or_mailbox(tmp_path,failure):
    async def run():
        c,_,_=await reward_client(tmp_path)
        c.progress.reserve_activation(1,dict(pid=7,control=REWARD,cookie='ab'*16))
        def opener(pid):
            if failure=='game_exited':raise OSError('Process exited')
            return SimpleNamespace(read=lambda a,n:bytes.fromhex(('cd' if failure=='wrong_cookie' else 'ab')*16),
                u32=lambda a:2 if failure=='wrong_index' and a==REWARD+r.RECEIPT_INDEX else 1,close=lambda:None)
        recover_activations(c,opener)
        assert c.progress.activations['1']['status']=='uncertain'
        assert not c.supply_indexes(HEALTH_SUPPLY)
        c.resolve_uncertain_items(False)
        assert not c.progress.activations and not c.supply_indexes(HEALTH_SUPPLY)
    asyncio.run(run())


def test_reservation_disk_failure_does_not_spend_item(tmp_path):
    async def run():
        c,_,_=await reward_client(tmp_path)
        with patch('renegade.state.os.replace',side_effect=OSError('Disk full')):
            with pytest.raises(OSError):c.progress.reserve_activation(1,dict(pid=7,control=REWARD,cookie='ab'*16))
        assert not c.progress.supply_spent and not c.progress.activations
    asyncio.run(run())


def test_transient_windows_replace_denial_retries_same_flushed_outbox(tmp_path):
    import os
    cfg=slot_data()
    progress=Progress(tmp_path,'busy-file',0,1,location_ids(3),cfg)
    original=os.replace;attempts=[]
    def replace(source,target):
        attempts.append(source)
        if len(attempts)<3:raise PermissionError('Sharing violation')
        original(source,target)
    with patch('renegade.state.os.replace',side_effect=replace),patch('renegade.state.time.sleep'):
        progress.merge({MISSIONS[0].check_id()})
    assert len(attempts)==3 and len(set(attempts))==1
    assert Progress(tmp_path,'busy-file',0,1,location_ids(3),cfg).completed==progress.completed
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('kind,field,max_field',[(1,HP,MAX_HP),(2,ARMOUR,MAX_ARMOUR)])
def test_full_supply_keeps_inventory(kind,field,max_field):
    import struct
    e=RewardEngine();e.machine.mem_write(field,struct.pack('<f',e.value(max_field)))
    e.request(kind);e.frame();assert e.result()==3
    assert e.value(field)==e.value(max_field)


def test_ammo_full_and_empty_bags_do_not_consume_supply():
    e=RewardEngine();e.request(3);e.frame();assert e.result()==3 and not e.refilled
    e.put(OBJECT+4000+20,1);e.put(OBJECT+4100,OBJECT+4200)
    e.machine.mem_write(OBJECT+4200+0x38,b'\1')
    e.machine.mem_write(r.BASE+r.AMMO_MAXED,b'\xb8\1\0\0\0\xc3')
    e.request(3);e.frame();assert e.result()==3 and not e.refilled


def test_queued_reward_never_dispatches_during_network_loss():
    e=RewardEngine();e.request(6);e.set(s.ONLINE,0);e.frame()
    assert e.result()==0 and e.value(ARMOUR)==30
    e.set(s.ONLINE,1);e.frame();assert e.result()==1 and e.value(ARMOUR)==0


def test_offline_native_capture_keeps_permissions_but_cancels_queued_deathlink():
    data={s.DEATH_REQUEST:1};permissions=[]
    runtime=s.NativeServices.__new__(s.NativeServices)
    runtime.control=0
    runtime.memory=SimpleNamespace(write_u32=lambda a,v:data.__setitem__(a,v),tick_count=lambda:12345)
    runtime.gate=SimpleNamespace(update=lambda maps:permissions.append(maps) or {})
    runtime.objective_hook=runtime.traps=runtime.bonuses=SimpleNamespace(update=lambda:None)
    runtime.launcher=SimpleNamespace(update=lambda client:None)
    runtime.weapons=runtime.discs=SimpleNamespace(update=lambda client:None)
    c=SimpleNamespace(ready=False,capture_ready=True,capture_maps=('M13','M01'),deathlink=SimpleNamespace(enabled=True))
    runtime.update(c)
    assert data[s.ENABLED]==1 and data[s.ALLOWED]==3 and permissions==[('M13','M01')]
    assert data[s.ONLINE]==data[s.DEATH_ENABLED]==data[s.DEATH_REQUEST]==0


@pytest.mark.parametrize('kind,offset',[(8,t.CONCRETE_END),(9,t.DISCO_END),(10,t.MOON_END),
                                    (11,t.BULLET_END),(12,t.VAMPIRE_END),(13,t.HOLOGRAM_END)])
def test_every_timed_effect_adds_duration_and_freezes_when_paused(kind,offset):
    e=BonusEngine();e.start(kind);e.advance(5)
    duration=t.DURATIONS[t.TIMERS[offset]]
    e.start(kind);assert e.value(REWARD+offset)==duration*2-5
    e.put(OBJECT+4,1);e.advance(60);assert e.value(REWARD+offset)==duration*2-5
    e.put(OBJECT+4,0);e.advance(duration*2-5);assert e.value(REWARD+offset)==0


def test_repeated_butterfingers_adds_reload_cycles_without_resetting_current_cycle():
    e=BonusEngine();e.start(7);e.frame()
    weapon=int.from_bytes(e.machine.mem_read(REWARD+t.RELOAD_WEAPON,4),'little')
    assert weapon
    e.start(7)
    assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOADS,4),'little')==10
    assert int.from_bytes(e.machine.mem_read(REWARD+t.RELOAD_WEAPON,4),'little')==weapon
