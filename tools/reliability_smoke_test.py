"""Real AP-server recovery/YAML checks, with a disposable TCP connection only.

Synthetic client capture events exercise transport and durable state. This does
not attach to either game or claim natural objective/disc/DeathLink coverage.
"""
import asyncio
from datetime import datetime
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import OBJECTIVES,DATA_DISCS,HEALTH_SUPPLY,ARMOUR_SUPPLY
from renegade.state import Progress
from tools.live_test import generate


class Relay:
    """Drop only this test client's socket; the AP server stays running."""
    def __init__(self,port):
        self.port=port;self.online=True;self.writers=set();self.tasks=set()

    async def accept(self,reader,writer):
        task=asyncio.current_task();self.tasks.add(task);pair=[writer]
        pumps=[]
        try:
            if not self.online:return
            upstream,other=await asyncio.open_connection('127.0.0.1',self.port)
            pair.append(other);self.writers.update(pair)
            async def pump(source,target):
                while data:=await source.read(65536):
                    target.write(data);await target.drain()
            pumps=[asyncio.create_task(pump(reader,other)),asyncio.create_task(pump(upstream,writer))]
            await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
        except (OSError,ConnectionError):pass
        finally:
            for pump in pumps:pump.cancel()
            await asyncio.gather(*pumps,return_exceptions=True)
            for output in pair:output.close();self.writers.discard(output)
            await asyncio.gather(*(output.wait_closed() for output in pair),return_exceptions=True)
            self.tasks.discard(task)

    def drop(self):
        self.online=False
        for writer in tuple(self.writers):writer.close()

    async def close(self):
        self.drop()
        await asyncio.gather(*tuple(self.tasks),return_exceptions=True)


async def exercise(folder,seed,mode,discs):
    ap=Path('C:/ProgramData/Archipelago')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    relay=Relay(port);listener=await asyncio.start_server(relay.accept,'127.0.0.1',0)
    endpoint=f'localhost:{listener.sockets[0].getsockname()[1]}'
    client=None;network=None
    report=dict(mission_unlocks=mode,data_discs=discs,native_game_access=False,synthetic_checks=True,phases=[])
    def phase(name):report['phases'].append(name);logging.info('%s/%s: %s',mode,discs,name)
    with (folder/'server.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1',
            '--port',str(port),'--disable_save','--password',''],stdin=subprocess.PIPE,
            stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        async def until(predicate,seconds=35):
            async def wait():
                while not predicate():
                    if server.poll() is not None:raise RuntimeError('Disposable AP server exited')
                    if network and network.done():network.result();raise RuntimeError('Client stopped')
                    await asyncio.sleep(.05)
            await asyncio.wait_for(wait(),seconds)
        async def start():
            nonlocal client,network
            client=RenegadeClient(endpoint,'Havoc',state_dir=folder/'state')
            assert not client.capture_ready,'Cached progress cannot authorize a fresh client'
            network=asyncio.create_task(client.network())
            await until(lambda:client.ready)
        async def stop():
            client.stop.set();await asyncio.wait_for(network,15)
        try:
            await start()
            assert client.config['mission_unlocks']==mode and client.config['data_discs']==discs
            assert client.unlocked_maps==('M13','M01')
            assert len(client.locations)==(190 if discs else 167)
            initial_items=len(client.inventory.items)
            supply_index=client.supply_indexes(HEALTH_SUPPLY)[0]
            client.progress.mark_supply(supply_index) # Durable receipt replay test, no native consumption.
            relay.drop();await until(lambda:not client.ready)
            assert client.capture_maps==('M13','M01') and not client.player_died('M01')
            assert client.objective_finished('M01',109)
            assert client.mission_finished('M01',True)
            disc=DATA_DISCS['M01'][0]
            assert client.disc_collected('M01',disc)==discs
            assert not client.mission_finished('M02',True)
            assert not client.mission_finished('M01',True)
            expected=set(client.progress.completed)
            stored=Progress(folder/'state',*client.identity,client.locations,client.config)
            assert stored.completed==expected and supply_index in stored.supply_spent
            phase('offline_checks_persisted_and_locked_checks_rejected')
            server.stdin.write(f'/send Havoc {ARMOUR_SUPPLY}\n'.encode());server.stdin.flush()
            relay.online=True
            await until(lambda:client.ready and client.acknowledged==expected)
            await until(lambda:len(client.inventory.items)>=initial_items+len(expected)+1)
            assert len(client.inventory.items)==initial_items+len(expected)+1
            assert supply_index in client.progress.supply_spent
            snapshot=tuple(client.inventory.items)
            phase('reconnect_flushed_once_and_item_history_preserved')
            # Another outage followed by an actual new client instance proves
            # recovery comes from durable progress, not the old in-memory set.
            relay.drop();await until(lambda:not client.ready)
            assert client.objective_finished('M01',102)
            expected=set(client.progress.completed)
            await stop();relay.online=True;await start()
            await until(lambda:client.acknowledged==expected)
            await until(lambda:len(client.inventory.items)>len(snapshot))
            assert tuple(client.inventory.items[:len(snapshot)])==snapshot
            assert supply_index in client.progress.supply_spent
            phase('client_restart_recovered_outbox_without_replaying_spent_supply')
            # Complete the generated logic, in reverse available-map order for
            # individual mode. These are explicitly transport-level events.
            while len(client.acknowledged)<len(client.locations):
                for name in reversed(client.unlocked_maps):
                    client.mission_finished(name,True)
                    for oid in OBJECTIVES[name]:client.objective_finished(name,oid)
                    for oid in DATA_DISCS[name]:client.disc_collected(name,oid)
                before=len(client.acknowledged)
                if before<len(client.locations):await until(lambda:len(client.acknowledged)>before)
            await until(lambda:len(client.unlocked_maps)==12)
            phase('generated_progression_and_check_catalogue_completed')
            report.update(status='passed',checks=len(client.acknowledged),maps=len(client.unlocked_maps),
                received=len(client.inventory.items),offline_checks=len(expected),spent_supply_preserved=True)
        except BaseException as error:
            report.update(status='error',error=repr(error));raise
        finally:
            if client:client.stop.set()
            if network:
                network.cancel();await asyncio.gather(network,return_exceptions=True)
            listener.close();await listener.wait_closed();await relay.close()
            if server.poll() is None:
                try:await asyncio.to_thread(server.communicate,b'/exit\n',timeout=5)
                except subprocess.TimeoutExpired:server.terminate();await asyncio.to_thread(server.wait,timeout=5)
            (folder/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


async def main():
    root=ROOT/'.research'/('reliability-'+datetime.now().strftime('%Y%m%d-%H%M%S'));root.mkdir()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(root/'client.log',encoding='utf-8')])
    results=[]
    for mode in ('progressive','individual'):
        for discs in (False,True):
            folder=root/f'{mode}-discs-{discs}';folder.mkdir()
            seed=await asyncio.to_thread(generate,Path('C:/ProgramData/Archipelago'),folder,
                starting_missions=2,mission_unlocks=mode,data_discs=discs,weapon_unlocks=True,
                primary_objectives=True,secondary_objectives=True,checks_per_mission=5,death_link=True,
                start_inventory={HEALTH_SUPPLY:2})
            results.append(await exercise(folder,seed,mode,discs))
    (root/'result.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    print(root)


if __name__=='__main__':asyncio.run(main())
