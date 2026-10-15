"""Backed-up, disposable TT checkpoint and Start/Continue smoke test.

Leave a safe mission loaded and focused. Test checkpoint events are injected
into the save latch only: no objectives, pickups or victories are simulated.
Start/Continue requests are submitted during gameplay. The production loader
returns to the menu and loads automatically, without a mission-lock notice.
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
from renegade.data import MISSIONS, WEAPON_ITEMS, UPGRADES, SUPPLIES
from renegade.native import tt, tt_campaign as c, services as s
from renegade.native import rewards as r, traps as tr, weapons as w, bonuses as bo
from renegade.native.tt_discs import CLOUD_VECTOR
from renegade.native.tt_profile import verify, telemetry
from renegade.native.savefiles import inspect_save, saved_difficulty
from renegade.native.mission_launch import select_save
from tools.live_test import generate
from tools.tt_inventory_test import publish_status


async def exercise(directory,seed,pid,seconds,matrix=False,cases=None,effect_override=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ap=Path('C:/ProgramData/Archipelago')
    report=dict(status='connecting',directory=str(directory),phases=[],forced_checks=False)
    status_path=ROOT/'.research/tt-4.8.4/campaign-test-status.json'
    def publish(status=None):
        if status:report['status']=status;logging.info('%s',status)
        report['updated']=datetime.now(timezone.utc).isoformat()
        publish_status(status_path,report)
    runtime=server=task=None;save_dir=None;originals={}
    client=RenegadeClient(f'localhost:{port}','Havoc',state_dir=directory/'state')
    maps=[m.map_name for m in MISSIONS]
    deadline=asyncio.get_running_loop().time()+seconds
    async def tick():
        if asyncio.get_running_loop().time()>deadline:raise TimeoutError('Waiting for focused, playable TT frames timed out')
        if (directory/'STOP').exists():raise RuntimeError('Test stopped by diagnostic STOP file')
        if server.poll() is not None:raise RuntimeError('Disposable server stopped')
        if task.done():task.result();raise RuntimeError('AP client stopped')
        if runtime:
            runtime.update(maps,client=client)
            mem,ctrl=runtime.memory,runtime.campaign.control
            report['sample']={**telemetry(mem,runtime.addresses),
                'playable':mem.u32(runtime.control+s.PLAYABLE),'alive':mem.u32(runtime.control+s.ALIVE),
                'baseline':mem.u32(ctrl+c.BASELINED),'generation':mem.u32(ctrl+c.GENERATION),
                'live_generation':mem.u32(runtime.control+s.GENERATION),
                'frames':mem.u32(runtime.control+tt.FRAMES),
                'serial':mem.u32(ctrl+c.SERIAL),'ready':mem.u32(ctrl+c.READY),
                'published':list(runtime.campaign.published),'launch':client.mission_launch}
            if runtime.campaign.error:raise RuntimeError(runtime.campaign.error)
            publish()
        await asyncio.sleep(.1)
    async def until(predicate,timeout=150):
        limit=asyncio.get_running_loop().time()+timeout
        while not predicate():
            if asyncio.get_running_loop().time()>limit:
                raise TimeoutError(f'{report["status"]}: native progress stopped or TT needs focus (last sample in result.json)')
            await tick()
    def phase(name):report['phases'].append(name);publish(name)
    async def load(name,difficulty,continuing=False):
        select_save(save_dir,runtime.campaign.token,name,difficulty,continuing)
        phase(('requesting_from_menu_' if runtime.campaign.launcher.at_menu() else 'requesting_from_combat_')+name)
        old_generation=runtime.memory.u32(runtime.control+s.GENERATION)
        client.mission_request=(name,difficulty,continuing)
        await tick()
        await until(lambda:runtime.campaign.launcher.pending is None
            and report['sample']['mode']==1 and report['sample']['map_name']==name+'.lsd'
            and report['sample']['source_name'].replace('\\','/').split('/')[-1]==f'ap_load_{runtime.campaign.token}.sav'
            and report['sample']['alive'] and report['sample']['generation']>old_generation)
        assert runtime.memory.u32(0x80F458)==difficulty
        # A loaded map name/ALIVE flag can survive a crash dialog. Require native
        # frames to keep advancing after physics resumes before reporting success.
        frames=runtime.memory.u32(runtime.control+tt.FRAMES)
        started=asyncio.get_running_loop().time()
        await until(lambda:report['sample']['frames']>=frames+30 and
                    asyncio.get_running_loop().time()-started>=2)
        phase(('continued_' if continuing else 'started_')+name+'_'+str(difficulty))
    publish()
    with (directory/'server.log').open('w',encoding='utf-8') as log:
        try:
            server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1',
                '--port',str(port),'--disable_save','--password',''],stdin=subprocess.PIPE,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            task=asyncio.create_task(client.network())
            await until(lambda:client.ready)
            # Validate and back up before attaching anything that can save.
            probe=tt.attach(pid)
            if probe is None:raise RuntimeError('No supported TT game')
            try:
                pid=probe.memory.pid
                save_dir=c.save_directory(probe.memory,probe.addresses)
            finally:
                probe.close();probe.memory.close()
            for path in save_dir.iterdir():
                if path.is_file():originals[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
            shutil.copytree(save_dir,directory/'saves-before')
            report.update(pid=pid,save_dir=str(save_dir),backed_up_files=len(originals))
            runtime=tt.attach(pid,identity=client.identity,testing=matrix)
            if matrix:
                # Preflight every input before starting the destructive-to-current-
                # attempt sequence. All existing on-disk saves are already backed up.
                for mission in MISSIONS:
                    for difficulty in range(3):select_save(save_dir,runtime.campaign.token,mission.map_name,difficulty)
                report['matrix']=[]
                # Alternate difficulty and jump between maps to stress teardown,
                # including the previously troublesome 08 -> 02 sequence.
                order=(7,1,0,10,2,8,3,11,4,9,5,6)
                plan=cases or [(MISSIONS[index].map_name,(cycle+index)%3)
                               for cycle in range(3) for index in order]
                effects=list(tr.TIMERS.items())
                rc=runtime.inventory.rewards.control;wc=runtime.inventory.weapons.control
                def timers():return {name:struct.unpack('<f',runtime.memory.read(rc+offset,4))[0] for offset,name in effects}
                for mission_name,difficulty in plan:
                    mission=next(m for m in MISSIONS if m.map_name==mission_name)
                    report['current_case']=dict(map=mission_name,difficulty=difficulty,stage='start')
                    await load(mission.map_name,difficulty)
                    await until(lambda:report['sample']['playable'] and
                        runtime.memory.u32(wc+w.PRESENT)==w.ALL_WEAPONS and runtime.memory.u32(rc+r.WEAPONS)==23)
                    await until(lambda:runtime.memory.u32(runtime.campaign.control+c.GENERATION)==runtime.memory.u32(runtime.control+s.GENERATION))
                    assert not any(timers().values()) and not runtime.memory.u32(rc+bo.HOLO_PTR)
                    base=(200,100,75)[difficulty]
                    stats=struct.unpack('<4f',runtime.memory.read(rc+r.STATS,16))
                    assert stats[1]>=base+250 and stats[3]>=base+250,(mission.map_name,difficulty,stats)
                    assert not runtime.memory.u32(wc+w.ERRORS)
                    assert runtime.memory.read(CLOUD_VECTOR,52)==bytes(52)
                    # Start one real AP effect, save while active, then use the
                    # production Continue path. No AP completion is fabricated.
                    offset,effect=effects[len(report['matrix'])%len(effects)]
                    if effect_override:offset,effect=next(pair for pair in effects if pair[1]==effect_override)
                    report['current_case'].update(effect=effect,stage='effect')
                    server.stdin.write(f'/send Havoc {effect}\n'.encode());server.stdin.flush()
                    await until(lambda:timers()[effect]>0 and not client.effect_indexes(effect))
                    old_holo=runtime.memory.u32(rc+bo.HOLO_ID)
                    if old_holo:
                        actor=runtime.memory.u32(rc+bo.HOLO_PTR)
                        physics=runtime.memory.u32(actor+tr.PLAYER_PHYSICS)
                        assert runtime.memory.u32(physics+0x38)&bo.PHYS_DONT_SAVE
                    serial=runtime.memory.u32(runtime.campaign.control+c.SERIAL)
                    runtime.memory.write_u32(runtime.campaign.control+c.PENDING,1)
                    await until(lambda:runtime.memory.u32(runtime.campaign.control+c.SERIAL)>serial
                        and runtime.memory.u32(runtime.campaign.control+c.READY)==0)
                    checkpoint=select_save(save_dir,runtime.campaign.token,mission.map_name,difficulty,True)
                    assert saved_difficulty(checkpoint.read_bytes())==difficulty
                    report['current_case']['stage']='continue'
                    await load(mission.map_name,difficulty,True)
                    await until(lambda:report['sample']['playable'] and runtime.memory.u32(wc+w.PRESENT)==w.ALL_WEAPONS)
                    assert not any(timers().values()) and not runtime.memory.u32(rc+bo.HOLO_PTR)
                    if old_holo:
                        node=runtime.memory.u32(0x856FFC);seen=set()
                        while node:
                            assert node not in seen and len(seen)<65536
                            seen.add(node);actor=runtime.memory.u32(node+4)
                            assert not actor or runtime.memory.u32(actor+12)!=old_holo,'Hologram survived in checkpoint'
                            node=runtime.memory.u32(node)
                    row=dict(map=mission.map_name,difficulty=difficulty,effect=effect,
                        weapons=23,stats=stats,map_revealed=True,checkpoint_reloaded=True,effect_cleared=True)
                    report['matrix'].append(row)
                    phase(f'matrix_{len(report["matrix"])}_of_{len(plan)}_passed')
                assert len(report['matrix'])==len(plan)
                assert not client.progress.completed and not client.acknowledged
                report['all_36_passed']=len({(x['map'],x['difficulty']) for x in report['matrix']})==36
                publish('passed')
                return report
            phase('waiting_for_playable_baseline')
            await until(lambda:report.get('sample',{}).get('baseline')==1)
            assert report['sample']['serial']==0
            phase('loaded_objectives_did_not_overwrite_checkpoint')
            name=report['sample']['map_name'].split('.')[0]
            difficulty=runtime.memory.u32(0x80F458)
            # One unsent save event, separate from the AP capture masks.
            runtime.memory.write_u32(runtime.campaign.control+c.PENDING,1)
            await until(lambda:len(runtime.campaign.published)==1)
            path=select_save(save_dir,runtime.campaign.token,name,difficulty,True)
            report['checkpoint']=inspect_save(path.read_bytes())
            assert saved_difficulty(path.read_bytes())==difficulty
            phase('checkpoint_saved_and_validated')
            await load(name,difficulty,True)
            serial=report['sample']['serial']
            for _ in range(8):await tick()
            assert report['sample']['serial']==serial
            phase('continue_baseline_did_not_resave')
            # Exercise the same deferred latch used by a real physical disc.
            runtime.memory.write_u32(runtime.control+s.DISC_GENERATION,report['sample']['live_generation'])
            runtime.memory.write_u32(runtime.control+s.DISC_EVENT,1)
            await until(lambda:len(runtime.campaign.published)==2)
            phase('disc_checkpoint_published')
            # Previously troublesome 02 -> 08 -> 02 sequence, also switching difficulty.
            await load('M07',2)
            await load('M01',0)
            before_cancel=report['sample']['live_generation']
            other=(difficulty+1)%3
            client.mission_request=(name,other,True);await tick()
            assert client.mission_confirmation and runtime.campaign.launcher.pending is None
            phase('difficulty_change_waits_for_confirmation')
            client.mission_confirmation=None
            await tick()
            assert report['sample']['live_generation']==before_cancel and not runtime.campaign.launcher.at_menu()
            phase('cancelled_restart_kept_checkpoint')
            await load(name,difficulty,True)
            assert report['sample']['serial']==serial+1
            report['final_snapshot']=runtime.snapshot()
            publish('passed')
        except BaseException as error:
            report['error']=str(error);publish('error');raise
        finally:
            client.stop.set()
            if runtime:
                try:
                    runtime.close();verify(runtime.memory,gate=True)
                    assert runtime.memory.u32(runtime.campaign.launcher.menu_slot)==runtime.campaign.launcher.menu_think
                    report['callbacks_restored']=True
                except (OSError,AssertionError) as error:report.update(status='error',cleanup_error=str(error))
                finally:runtime.memory.close()
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            if server and server.poll() is None:
                try:server.communicate(b'/exit\n',timeout=5)
                except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
            # Native loads may update autosave.sav. Restore changed pre-test files;
            # retain our unique room's checkpoints and a copy of native changes.
            if save_dir and (directory/'saves-before').is_dir():
                changed=[]
                for name,digest in originals.items():
                    path=save_dir/name
                    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                        if path.exists():
                            (directory/'native-save-changes').mkdir(exist_ok=True)
                            shutil.copy2(path,directory/'native-save-changes'/name)
                        shutil.copy2(directory/'saves-before'/name,path);changed.append(name)
                report['preexisting_files_restored']=changed
                report['original_files_preserved']=all(hashlib.sha256((save_dir/n).read_bytes()).hexdigest()==d
                                                       for n,d in originals.items())
                if not report['original_files_preserved']:report['status']='error'
            publish();(directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int);parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--matrix',action='store_true',help='All 36 starts/checkpoints, inventory and active-effect reloads; allow 1800 seconds')
    parser.add_argument('--case',action='append',help='Matrix subset, e.g. M08:2 (map and difficulty 0/1/2); repeatable')
    parser.add_argument('--effect',choices=list(tr.TIMERS.values()),help='Use this effect for every selected matrix case')
    args=parser.parse_args()
    if (args.case or args.effect) and not args.matrix:parser.error('--case and --effect require --matrix')
    cases=[]
    for value in args.case or []:
        try:
            name,raw=value.split(':');difficulty=int(raw)
            if name not in {m.map_name for m in MISSIONS} or difficulty not in range(3):raise ValueError()
        except ValueError:parser.error('--case must be a campaign map and difficulty, e.g. M08:2')
        cases.append((name,difficulty))
    directory=ROOT/'.research/tt-4.8.4'/('campaign-test-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    seed=generate(Path('C:/ProgramData/Archipelago'),directory,starting_missions=12,
        primary_objectives=True,secondary_objectives=True,checks_per_mission=5,
        weapon_unlocks=args.matrix,data_discs=args.matrix,
        start_inventory=({**{n:1 for n in WEAPON_ITEMS},**{n:5 for n in UPGRADES},**{n:3 for n in SUPPLIES}} if args.matrix else None))
    report=asyncio.run(exercise(directory,seed,args.pid,args.seconds,args.matrix,cases,args.effect))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
