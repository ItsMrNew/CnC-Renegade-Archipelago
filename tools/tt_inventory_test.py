"""Disposable TT inventory test. Load a safe mission start and leave it focused.

Changes this attempt's weapons, health and armour; never writes a game save or
forces objective/victory events. Reload your save afterwards to discard the test
loadout. All AP items and receipts belong to an isolated local room.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import socket
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import SUPPLIES, UPGRADES, WEAPON_ITEMS, TRAPS, BUFFS
from renegade.native import tt, tt_inventory as t, weapons as w, rewards as r, services as s
from renegade.native.tt_profile import verify
from tools.live_test import generate


class MaskOverride:
    """Exercise pistol-only and full inventory using one disposable AP stream."""
    def __init__(self,client):self.client=client;self.weapon_mask=1
    def __getattr__(self,name):return getattr(self.client,name)
    def __setattr__(self,name,value):
        if name in ('client','weapon_mask'):
            object.__setattr__(self,name,value)
        else:
            setattr(self.client,name,value)


def publish_status(path,report):
    """A Windows reader may briefly deny rename; retry on the next sample.

    Diagnostic publication must not tear down an otherwise healthy native test.
    The per-run result is written separately after callback restoration.
    """
    temporary=path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        temporary.replace(path)
    except PermissionError:
        return False
    return True


async def exercise(directory,seed,pid,seconds):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ap=Path('C:/ProgramData/Archipelago')
    report=dict(status='connecting',directory=str(directory),phases=[],pid=pid,
                saves_written=False,forced_completion=False)
    status_path=ROOT/'.research/tt-4.8.4/inventory-test-status.json'
    def publish(status=None):
        if status: report['status']=status;logging.info('%s',status)
        report['updated']=datetime.now(timezone.utc).isoformat()
        publish_status(status_path,report)
    runtime=None;server=None;task=None
    client=RenegadeClient(f'localhost:{port}','Havoc',state_dir=directory/'state')
    proxy=MaskOverride(client)
    deadline=asyncio.get_running_loop().time()+seconds
    async def tick():
        if asyncio.get_running_loop().time()>deadline:raise TimeoutError('Timed out waiting for playable TT frames')
        if server.poll() is not None:raise RuntimeError('Disposable server stopped')
        if task.done():task.result();raise RuntimeError('AP client stopped')
        if runtime:
            runtime.update(client.capture_maps,client=proxy)
            m=runtime.memory;rc=runtime.inventory.rewards.control;wc=runtime.inventory.weapons.control
            report['sample']=dict(playable=m.u32(runtime.control+s.PLAYABLE),alive=m.u32(runtime.control+s.ALIVE),
                frames=m.u32(runtime.control+tt.FRAMES),samples=m.u32(rc+r.SAMPLES),
                stats=struct.unpack('<4f',m.read(rc+r.STATS,16)),weapons=m.u32(rc+r.WEAPONS),
                not_full=m.u32(rc+r.NOT_FULL),present=m.u32(wc+w.PRESENT),errors=m.u32(wc+w.ERRORS),
                receipt=m.u32(rc+r.RESULT),action=m.u32(rc+r.TEST_ACTION))
            if report['sample']['errors']:raise RuntimeError('TT weapon reconciliation reported an error')
            publish()
        await asyncio.sleep(.1)
    async def until(predicate):
        while not predicate():await tick()
    def phase(name):report['phases'].append(name);publish(name)
    publish()
    with (directory/'server.log').open('w',encoding='utf-8') as log:
        try:
            server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1',
                '--port',str(port),'--disable_save','--password',''],stdin=subprocess.PIPE,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            task=asyncio.create_task(client.network())
            await until(lambda:client.ready)
            runtime=tt.attach(pid,testing=True)
            if runtime is None:raise RuntimeError('No supported TT game is running')
            report['pid']=runtime.memory.pid
            rc=runtime.inventory.rewards.control
            report['native_controls']=dict(service=runtime.control,rewards=rc,weapons=runtime.inventory.weapons.control)
            publish('waiting_for_playable_mission')
            await until(lambda:report.get('sample',{}).get('weapons')==1 and report['sample']['present']==1)
            phase('pistol_only_passed')
            proxy.weapon_mask=client.weapon_mask
            assert proxy.weapon_mask==w.ALL_WEAPONS
            await until(lambda:report['sample']['present']==w.ALL_WEAPONS and report['sample']['weapons']==23)
            base=(200,100,75)[runtime.memory.u32(t.DIFFICULTY)]
            await until(lambda:report['sample']['stats'][1]>=base+250 and report['sample']['stats'][3]>=base+250)
            phase('all_22_unlocks_and_upgrade_floors_passed')
            runtime.memory.write_u32(rc+r.TEST_ACTION,2)
            await tick()
            await until(lambda:report['sample']['action']==0 and report['sample']['not_full']>0
                        and report['sample']['stats'][0]<=25 and report['sample']['stats'][2]<=25)
            phase('native_health_armour_and_ammo_depleted')
            for name in SUPPLIES:
                before=len(client.progress.supply_spent)
                assert client.request_supply(name)
                await until(lambda:client.supply_request is None)
                assert len(client.progress.supply_spent)==before+1, f'{name} was not applied'
                phase(name+'_applied')
            await until(lambda:report['sample']['not_full']==0)
            assert report['sample']['stats'][0]==report['sample']['stats'][1]
            assert report['sample']['stats'][2]==report['sample']['stats'][3]
            for name in SUPPLIES:
                before=len(client.progress.supply_spent)
                assert client.request_supply(name)
                await until(lambda:client.supply_request is None)
                assert len(client.progress.supply_spent)==before,f'{name} was wasted while full'
                assert report['sample']['receipt']==3
            phase('full_supplies_kept_in_inventory')
            assert all(not client.effect_indexes(name) for name in t.NativeRewards.automatic_items)
            assert all(client.effect_indexes(name) for name in (*TRAPS,*BUFFS)
                       if name not in t.NativeRewards.automatic_items)
            phase('unported_items_remain_unspent')
            report['final_snapshot']=runtime.snapshot()
            report['receipts_spent']=sorted(client.progress.supply_spent)
            publish('passed')
        except BaseException as error:
            report.update(error=str(error));publish('error');raise
        finally:
            client.stop.set()
            if runtime:
                try:
                    runtime.close();runtime.inventory.rewards.update(client,0,-1,False)
                    verify(runtime.memory,gate=True)
                    report['callbacks_restored']=True
                except OSError as error:report['cleanup_error']=str(error)
                finally:runtime.memory.close()
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            if server and server.poll() is None:
                try:server.communicate(b'/exit\n',timeout=5)
                except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
            publish();(directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int);parser.add_argument('--seconds',type=int,default=600)
    args=parser.parse_args()
    directory=ROOT/'.research/tt-4.8.4'/('inventory-test-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    items={**{n:1 for n in (*WEAPON_ITEMS,*TRAPS,*BUFFS)},**{n:5 for n in UPGRADES},**{n:2 for n in SUPPLIES}}
    seed=generate(Path('C:/ProgramData/Archipelago'),directory,starting_missions=12,
        start_inventory=items,weapon_unlocks=True,primary_objectives=True,secondary_objectives=True,checks_per_mission=5)
    asyncio.run(exercise(directory,seed,args.pid,args.seconds))


if __name__=='__main__':main()
