"""Disposable TT menu/cutscene/effect cleanup and deferred DeathLink test.

Start at the main menu with AP launchers closed. Uses native Start Mission,
real server-sent items and a peer DeathLink. No objective/disc/victory is forced.
The final incoming death kills the test player. Existing saves are backed up.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import TRAPS, BUFFS, MOON_BOOTS, ARMOUR_TRAP
from renegade.native import tt, services as s, traps as t, bonuses as b
from renegade.native.tt_campaign import save_directory
from renegade.native.tt_discs import CAMERA
from renegade.native.tt_profile import verify, telemetry
from tools.live_test import generate
from tools.tt_inventory_test import publish_status


async def exercise(directory,seed,pid):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ap=Path('C:/ProgramData/Archipelago')
    clients=[RenegadeClient(f'localhost:{port}',name,state_dir=directory/'state') for name in ('Havoc','Sakura')]
    client,peer=clients
    runtime=server=None;tasks=[];originals={};saves=None
    report=dict(status='connecting',phases=[],forced_checks=False,directory=str(directory))
    status_path=ROOT/'.research/tt-4.8.4/lifecycle-test-status.json'
    def publish():
        report['updated']=datetime.now(timezone.utc).isoformat()
        publish_status(status_path,report)
    def phase(name):
        report['phases'].append(name);report['status']=name;publish();logging.info(name)
    def timed():
        rc=runtime.inventory.rewards.control
        return {name:struct.unpack('<f',runtime.memory.read(rc+offset,4))[0] for offset,name in t.TIMERS.items()}
    async def tick():
        if (directory/'STOP').exists():raise RuntimeError('Diagnostic stop requested')
        if server.poll() is not None:raise RuntimeError('Disposable server stopped')
        for task in tasks:
            if task.done():task.result();raise RuntimeError('AP client stopped')
        if runtime:
            runtime.update(client.capture_maps,client=client)
            mem,ctrl,rc=runtime.memory,runtime.control,runtime.inventory.rewards.control
            camera=mem.u32(CAMERA)
            report['sample']={**telemetry(mem,runtime.addresses),
                'menu':runtime.campaign.launcher.at_menu(),'frames':mem.u32(ctrl+tt.FRAMES),
                'playable':mem.u32(ctrl+s.PLAYABLE),'alive':mem.u32(ctrl+s.ALIVE),
                'camera_host':mem.u32(camera+s.CAMERA_HOST) if camera else None,
                'applied_deaths':mem.u32(ctrl+s.DEATH_APPLIED),'sent_deaths':client.deathlink.sent,
                'received_deaths':client.deathlink.received,'hologram':mem.u32(rc+b.HOLO_PTR),
                'timers':timed(),'spent':len(client.progress.supply_spent)}
            if runtime.campaign.error:raise RuntimeError(runtime.campaign.error)
            publish()
        await asyncio.sleep(.05)
    async def until(predicate,seconds=150):
        end=asyncio.get_running_loop().time()+seconds
        while not predicate():
            if asyncio.get_running_loop().time()>end:raise TimeoutError(report['status'])
            await tick()
    async def hold(seconds,check):
        end=asyncio.get_running_loop().time()+seconds
        while asyncio.get_running_loop().time()<end:
            await tick();check()
    def send_items(names):
        server.stdin.write(''.join(f'/send Havoc {name}\n' for name in names).encode());server.stdin.flush()
    def restored(obj):
        mem=obj.memory
        verify(mem,gate=True)
        assert mem.u32(obj.campaign.launcher.menu_slot)==obj.campaign.launcher.menu_think
        for field,original,code in obj.inventory.traps.entries:assert mem.u32(field)==original
        for helper in (obj.inventory.weapon_effects,obj.inventory.hologram):
            for site,original,code in helper.entries:assert mem.read(site,5)==original
        vamp=obj.inventory.vampiric;assert mem.read(vamp.site,5)==vamp.original
        assert not obj.inventory.hologram.pending_cleanup
    def close_runtime():
        nonlocal runtime
        obj=runtime
        if obj:
            try:
                obj.close();obj.inventory.rewards.update(client,0,-1,False);restored(obj)
            finally:obj.memory.close();runtime=None
    with (directory/'server.log').open('w',encoding='utf-8') as log:
        try:
            server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1',
                '--port',str(port),'--disable_save','--password',''],stdin=subprocess.PIPE,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            tasks=[asyncio.create_task(c.network()) for c in clients]
            await until(lambda:all(c.ready and c.deathlink.enabled for c in clients))
            runtime=tt.attach(pid,identity=client.identity,testing=True)
            if not runtime:raise RuntimeError('No supported TT process')
            saves=save_directory(runtime.memory,runtime.addresses)
            originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in saves.iterdir() if p.is_file()}
            shutil.copytree(saves,directory/'saves-before')
            report['backed_up_files']=len(originals)
            await tick();phase('waiting_at_main_menu')
            await until(lambda:report['sample']['menu'])
            names=tuple(TRAPS)+tuple(BUFFS)
            initial=len(client.inventory.items);send_items(names)
            await until(lambda:len(client.inventory.items)==initial+len(names))
            def menu_held():
                assert report['sample']['menu'] and not any(timed().values())
                assert not client.progress.supply_spent and all(client.effect_indexes(n) for n in names)
            await hold(3,menu_held)
            phase('all_traps_and_bonuses_held_unspent_at_menu')
            client.mission_request=('M01',1,False)
            await until(lambda:report['sample']['playable'] and report['sample']['hologram'] and
                        all(value>0 for value in timed().values()) and not any(client.effect_indexes(n) for n in names)
                        and runtime.inventory.rewards.pending is None)
            phase('queued_items_applied_after_entering_playable_mission')
            report['active_before_disconnect']=dict(report['sample'])
            spent=set(client.progress.supply_spent)
            close_runtime()
            phase('disconnect_removed_companion_and_restored_all_effect_hooks')
            runtime=tt.attach(pid,identity=client.identity,testing=True)
            await tick()
            await until(lambda:report['sample']['playable'])
            def no_replay():
                assert not any(timed().values()) and not report['sample']['hologram']
                assert set(client.progress.supply_spent)==spent
            await hold(3,no_replay)
            phase('reattach_did_not_replay_spent_effects')
            client.mission_request=('M13',0,False)
            phase('loading_scorpion_hunters_opening_cutscene')
            await until(lambda:report['sample']['map_name']=='M13.lsd' and report['sample']['camera_host']
                        and not report['sample']['playable'])
            before=len(client.inventory.items);send_items((MOON_BOOTS,ARMOUR_TRAP))
            assert peer.player_died('M01')
            await until(lambda:len(client.inventory.items)==before+2 and client.deathlink.received==1)
            def cinematic_held():
                assert report['sample']['camera_host'] and not report['sample']['playable']
                assert not report['sample']['applied_deaths'] and len(client.deathlink.incoming)==1
                assert set(client.progress.supply_spent)==spent and not any(timed().values())
            await hold(5,cinematic_held)
            phase('cutscene_held_items_and_deathlink_until_player_control')
            await until(lambda:report['sample']['applied_deaths']==1)
            await until(lambda:not report['sample']['alive'])
            def dead_held():
                assert not any(timed().values()) and not report['sample']['hologram']
                assert client.deathlink.sent==0 and peer.deathlink.received==0
            await hold(3,dead_held)
            phase('incoming_death_applied_once_after_cutscene_without_echo')
            assert not client.progress.completed and not client.acknowledged
            report['no_checks_sent']=True
            phase('passed')
        except BaseException as error:
            report.update(status='error',error=str(error));publish();raise
        finally:
            for c in clients:c.stop.set()
            try:
                close_runtime();report['callbacks_restored']=True
            except (OSError,AssertionError) as error:report.update(status='error',cleanup_error=str(error))
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if server and server.poll() is None:
                try:server.communicate(b'/exit\n',timeout=5)
                except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
            if saves:
                changed=[]
                for name,digest in originals.items():
                    path=saves/name
                    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                        if path.exists():
                            (directory/'native-save-changes').mkdir(exist_ok=True)
                            shutil.copy2(path,directory/'native-save-changes'/name)
                        shutil.copy2(directory/'saves-before'/name,path);changed.append(name)
                report['preexisting_files_restored']=changed
                report['original_files_preserved']=all(hashlib.sha256((saves/n).read_bytes()).hexdigest()==d for n,d in originals.items())
            publish();(directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--pid',type=int);args=parser.parse_args()
    directory=ROOT/'.research/tt-4.8.4'/('lifecycle-test-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    seed=generate(Path('C:/ProgramData/Archipelago'),directory,multiworld=True,starting_missions=12,death_link=True)
    result=asyncio.run(exercise(directory,seed,args.pid))
    if result['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
