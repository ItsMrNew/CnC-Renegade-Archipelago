"""Automated native secondary/checkpoint and two-way DeathLink test.

Only a disposable room is used. M01 objective 104 and test deaths are forced.
No ordinary client exposes these test-only game-thread jobs.
"""
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import MISSIONS, secondary_id, UPGRADE_AMOUNT, UPGRADE_LIMIT
from renegade.native.services import (watch_services, TEST_ACTION, TEST_RESULT, ALIVE, ACTIVE,
    CAPTURED, SAVES, DEATH_APPLIED, DEATH_COUNT, BUSY)
from renegade.native.savebuilder import attach_builder, BASE, RELOAD, EXIT, FILENAME, MENU_SLOT, MENU_THINK
from renegade.native.gate import verify_gate
from renegade.native.savefiles import inspect_save
from tools.completion_test import native_call, validate_saves
from tools.create_save_pack import digest
from tools.live_test import generate

LOG = logging.getLogger('FeatureTest')


def status(directory, **values):
    values.update(directory=str(directory), updated=datetime.now(timezone.utc).isoformat())
    path=ROOT/'.research/features-status.json'
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(values,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


async def exercise(directory, seed, manifest, rewards_mode=False):
    ap=Path('C:/ProgramData/Archipelago')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    backup=directory/'original-saves';backup.mkdir()
    original={};save_dir=None;pid=None;loaded=False;service_task=None;runtime=None
    clients=[RenegadeClient(f'localhost:{port}',name,state_dir=directory/'state') for name in ('Havoc','Sakura')]
    havoc,sakura=clients
    report={}
    with (directory/'server.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1','--port',str(port),
            '--disable_save','--password',''],stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        tasks=[asyncio.create_task(c.network()) for c in clients]

        async def until(condition, timeout=45):
            deadline=asyncio.get_running_loop().time()+timeout
            while True:
                if server.poll() is not None:raise RuntimeError('Test server stopped')
                for task in [*tasks,service_task]:
                    if task and task.done():
                        task.result();raise RuntimeError('Client or native service stopped')
                value=condition()
                if value:return value
                if asyncio.get_running_loop().time()>deadline:raise TimeoutError('Feature test step timed out')
                await asyncio.sleep(.05)

        async def stop_services():
            nonlocal service_task,runtime
            if service_task:
                if runtime:
                    await until(lambda:not runtime.memory.u32(runtime.control+BUSY))
                service_task.cancel()
                try:await service_task
                except asyncio.CancelledError:pass
                service_task=None;runtime=None

        async def load(filename=None):
            nonlocal save_dir,pid,loaded,original
            await stop_services()
            builder=attach_builder(pid)
            memory=builder.memory
            try:
                pid=memory.pid
                if save_dir is None:
                    save_dir=memory.modules[0].path.parent/'Data'/'save'
                    for path in save_dir.glob('*.sav'):
                        shutil.copy2(path,backup/path.name);original[path.name]=digest(path)
                    (directory/'backup-manifest.json').write_text(json.dumps(original,indent=2),encoding='utf-8')
                    names=validate_saves(manifest,save_dir,MISSIONS[1:2])
                    filename=names['M01']
                if loaded:
                    await native_call(builder.job,EXIT);loaded=False
                builder.prepare(MISSIONS[1],'save\\'+next(x['filename'] for x in manifest['missions'] if x['map_name']=='M01' and x.get('difficulty',1)==1))
                if not filename or '/' in filename or '\\' in filename or not filename.startswith(('ap_start_','ap_checkpoint_')):
                    raise ValueError('Unexpected feature-test save filename')
                memory.write(builder.control+FILENAME,('save\\'+filename+'\0').encode())
                loaded=True
                await native_call(builder.job,RELOAD)
                await native_call(builder.settle)
            finally:
                builder.close();memory.close()

        async def attach():
            nonlocal service_task,runtime
            def attached(value):
                nonlocal runtime
                runtime=value
            service_task=asyncio.create_task(watch_services(havoc,pid,testing=True,on_attach=attached))
            await until(lambda:runtime and runtime.memory.u32(runtime.control+ALIVE) and runtime.memory.u32(runtime.control+ACTIVE))

        async def command(action):
            runtime.memory.write_u32(runtime.control+TEST_RESULT,0)
            runtime.memory.write_u32(runtime.control+TEST_ACTION,action)
            await until(lambda:runtime.memory.u32(runtime.control+TEST_RESULT))
            if runtime.memory.u32(runtime.control+TEST_RESULT)!=action:
                raise RuntimeError('Native test action rejected')

        try:
            await until(lambda:all(c.ready and (rewards_mode or c.deathlink.enabled) for c in clients))
            if havoc.acknowledged or len(havoc.unlocked_maps)!=12:raise RuntimeError('Unexpected test room state')
            status(directory,stage='loading_M01')
            await load();await attach()
            if rewards_mode:
                import struct
                from renegade.data import SUPPLIES, ARMOUR_TRAP, ITEM_IDS, UPGRADES
                from renegade.native import rewards as reward
                def stats():
                    return struct.unpack('<4f',runtime.memory.read(runtime.rewards.control+reward.STATS,16))
                def expected_caps():
                    return tuple(100+UPGRADE_AMOUNT*min(UPGRADE_LIMIT,havoc.inventory.count(ITEM_IDS[n])) for n in UPGRADES)
                async def sampled():
                    serial=runtime.memory.u32(runtime.rewards.control+reward.SAMPLES)
                    await until(lambda:runtime.memory.u32(runtime.rewards.control+reward.SAMPLES)>serial)
                await until(lambda:not havoc.effect_indexes(ARMOUR_TRAP) and runtime.rewards.pending is None and stats()[1]==0)
                assert stats()[0]==100 and stats()[2:]==expected_caps(), stats()
                report['armour_trap_health_unchanged']=True
                report['trap_stats']=stats()
                LOG.info('PASS: received Armour Loss Trap sets armour to zero; health and capacities remain intact.')
                mem,ctrl=runtime.memory,runtime.rewards.control
                mem.write_u32(ctrl+reward.TEST_ACTION,1)
                await until(lambda:mem.u32(ctrl+reward.TEST_RESULT)==1)
                await sampled()
                assert stats()[:2]==(25,25), stats()
                assert mem.u32(ctrl+reward.WEAPONS)>=2 and mem.u32(ctrl+reward.NOT_FULL)>0
                for name in SUPPLIES:
                    before=len(havoc.supply_indexes(name))
                    assert havoc.request_supply(name)
                    await until(lambda:havoc.supply_request is None and runtime.rewards.pending is None
                                and len(havoc.supply_indexes(name))==before-1)
                    await sampled()
                assert stats()[:2]==stats()[2:]==expected_caps(), stats()
                assert mem.u32(ctrl+reward.NOT_FULL)==0
                report.update(refills=stats(),all_owned_weapons_full=True,owned_weapons=mem.u32(ctrl+reward.WEAPONS))
                LOG.info('PASS: Health/Armour supplies refill to actual maxima; every owned weapon reports ammo full.')
                # Use the already-tested objective checkpoint path to save these upgrades.
                await command(1);await command(2)
                checkpoint=runtime.save_dir/runtime.filename('M01')
                check=secondary_id('M01',104)
                await until(lambda:checkpoint.is_file() and check in havoc.acknowledged and runtime.saved[1])
                await asyncio.sleep(.5)
                spent=set(havoc.progress.supply_spent)
                await load(checkpoint.name);await attach();await sampled()
                await until(lambda:stats()[2:]==expected_caps())
                assert stats()[1]>0 and havoc.progress.supply_spent==spent
                report.update(upgrade_reload_no_stacking=True,trap_not_replayed_after_load=True)
                # Reconnect the actual transport, forcing authoritative index-zero item sync.
                tasks[0].cancel();await asyncio.gather(tasks[0],return_exceptions=True)
                tasks[0]=asyncio.create_task(havoc.network())
                await until(lambda:havoc.ready)
                await asyncio.sleep(.5);await sampled()
                assert havoc.progress.supply_spent==spent and stats()[1]>0 and stats()[2:]==expected_caps()
                report.update(reconnect_no_duplicate_effects=True,stage='passed',local_server_stopped=True)
                LOG.info('PASS: capacities survive checkpoint load and reconnect without duplicate consumables or traps.')
                return report
            LOG.info('M01 loaded. Creating and completing secondary objective 104 automatically.')
            await command(1)
            await asyncio.sleep(.2)
            await command(2)
            check=secondary_id('M01',104)
            checkpoint=runtime.save_dir/runtime.filename('M01')
            await until(lambda:check in havoc.acknowledged and runtime.saved[1]>0 and checkpoint.is_file())
            await until(lambda:any(i.location==check and i.player==havoc.identity[2] for c in clients for i in c.inventory.items))
            if havoc.acknowledged!={check}:raise RuntimeError('Unexpected additional checks')
            checkpoint_info=inspect_save(checkpoint.read_bytes())
            report.update(secondary_check=check,checkpoint=str(checkpoint),checkpoint_info=checkpoint_info)
            status(directory,stage='checkpoint_reload',**report)
            LOG.info('PASS: secondary check acknowledged, reward delivered, native checkpoint written. Reloading it.')
            await load(checkpoint.name);await attach()
            bit=1<<3  # M01 catalogue objective 104.
            await until(lambda:runtime.memory.u32(runtime.control+CAPTURED+4)&bit)
            await asyncio.sleep(.5)
            if havoc.acknowledged!={check} or runtime.memory.u32(runtime.control+SAVES+4):
                raise RuntimeError('Checkpoint reload duplicated a check or rewrote the checkpoint')
            report['checkpoint_reload_verified']=True
            LOG.info('PASS: checkpoint reload retains objective completion without duplicate checks or another autosave.')
            status(directory,stage='incoming_deathlink',**report)
            sakura.player_died('M13')
            await until(lambda:runtime.memory.u32(runtime.control+DEATH_APPLIED)==1)
            await asyncio.sleep(.5)
            if runtime.memory.u32(runtime.control+DEATH_COUNT) or havoc.deathlink.sent or sakura.deathlink.received:
                raise RuntimeError('Received DeathLink echoed back')
            report['incoming_death_no_echo']=True
            LOG.info('PASS: Sakura DeathLink killed Havoc; no reflected death.')
            status(directory,stage='outgoing_deathlink',**report)
            await load(checkpoint.name);await attach()
            await command(3)
            await until(lambda:sakura.deathlink.received==1 and havoc.deathlink.sent==1)
            if runtime.memory.u32(runtime.control+DEATH_COUNT)!=1:raise RuntimeError('Wrong local death count')
            report.update(outgoing_death_verified=True,deathlink_sent=havoc.deathlink.sent,deathlink_received=havoc.deathlink.received)
            LOG.info('PASS: local Havoc death reached Sakura exactly once.')
        finally:
            try:
                await stop_services()
                if loaded:
                    builder=attach_builder(pid)
                    try:
                        await native_call(builder.job,EXIT)
                        builder.close()
                        verify_gate(builder.memory,return_to_menu=True)
                        if builder.memory.u32(BASE+MENU_SLOT)!=BASE+MENU_THINK:raise RuntimeError('Menu callback not restored')
                        report['callbacks_restored']=True
                    finally:builder.memory.close()
                for name,sha in original.items():
                    path=save_dir/name
                    if not path.exists() or digest(path)!=sha:shutil.copy2(backup/name,path)
                    if digest(path)!=sha:raise RuntimeError('Failed to preserve '+name)
                report['original_saves_preserved']=True
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
                if server.poll() is None:
                    try:await asyncio.to_thread(server.communicate,b'/exit\n',timeout=5)
                    except subprocess.TimeoutExpired:
                        server.terminate();await asyncio.to_thread(server.wait,timeout=5)
    report.update(stage='passed',forced_secondary_and_deaths=True,local_server_stopped=True)
    return report


def main():
    directory=ROOT/'.research'/('features-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s: %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    try:
        status(directory,stage='generating')
        pack=Path(json.loads((ROOT/'.research/savepack-status.json').read_text(encoding='utf-8'))['pack'])
        manifest=json.loads((pack/'manifest.json').read_text(encoding='utf-8'))
        LOG.info('Creating a disposable two-player feature test. Leave Renegade at its main menu; close all AP clients.')
        seed=generate(Path('C:/ProgramData/Archipelago'),directory,multiworld=True,starting_missions=12,
                      secondary_objectives=True,death_link=True)
        report=asyncio.run(exercise(directory,seed,manifest))
        (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        status(directory,**report)
        LOG.info('PASS: secondary check, checkpoint reload, two-way DeathLink, no echo, original saves preserved.')
    except (Exception,KeyboardInterrupt) as error:
        status(directory,stage='failed',error=str(error))
        LOG.exception('Feature test stopped: %s',error)
        raise


if __name__=='__main__':main()
