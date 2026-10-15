"""Disposable TT effects, notification and DeathLink live tests.

Close AP launchers and load a safe mission save. Except hologram mode, tests
temporarily deplete health/ammo; incoming/outgoing also kill the player.
Hologram mode writes a separate disposable save, with existing saves backed up.
No mode sends AP checks. Reload a save after a death test. Pause mode
waits for Escape, samples five paused seconds, then waits for manual resume.
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
from renegade.data import ITEM_IDS, SUPPLIES, ARMOUR_TRAP, CONCRETE_BOOTS, MOON_BOOTS, DISCO_HAVOC
from renegade.data import BUTTERFINGERS, BULLET_HOSE, VAMPIRIC_ROUNDS, HOLOGRAM_HAVOC
from renegade.native import tt_weapon_effects as we
from renegade.native import tt_vampiric as va
from renegade.native import tt_hologram as ho, bonuses as bo
from renegade.native.savefiles import inspect_save
from renegade.native import tt, rewards as r, services as s, traps as tr
from renegade.native.tt_campaign import save_directory
from renegade.native.tt_profile import verify
from renegade.native.tt_discs import COMBAT_OBJECT
from renegade.native.notifications import REQUEST
from tools.live_test import generate
from tools.tt_inventory_test import publish_status


def applied_supply_receipts(progress,name,before):
    """A reserved item is spent but still journaled until its native receipt."""
    return {index for index in progress.supply_spent-before
            if progress.received[index].item==ITEM_IDS[name] and str(index) not in progress.activations}


async def exercise(directory,seed,pid,seconds,direction,visual_seconds=45,hotkeys_from=2):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ap=Path('C:/ProgramData/Archipelago')
    report=dict(status='connecting',direction=direction,directory=str(directory),phases=[],forced_checks=False)
    clients=[RenegadeClient(f'localhost:{port}',name,state_dir=directory/'state') for name in ('Havoc','Sakura')]
    havoc,sakura=clients
    server=runtime=hotkeys=None;tasks=[];originals={};save_dir=None;test_save=None
    deadline=asyncio.get_running_loop().time()+seconds
    status_path=ROOT/'.research/tt-4.8.4/effect-services-test-status.json'
    def publish(status=None):
        if status:report['status']=status;logging.info('%s',status)
        report['updated']=datetime.now(timezone.utc).isoformat()
        publish_status(status_path,report)
    def phase(name):report['phases'].append(name);publish(name)
    async def tick():
        if (directory/'STOP').exists():raise RuntimeError('Diagnostic stop requested')
        if asyncio.get_running_loop().time()>deadline:raise TimeoutError('Waiting for focused playable TT frames timed out')
        if server.poll() is not None:raise RuntimeError('Test server exited')
        for task in tasks:
            if task.done():task.result();raise RuntimeError('Test client stopped')
        if runtime:
            runtime.update(havoc.capture_maps,client=havoc)
            mem,ctrl=runtime.memory,runtime.control;rc=runtime.inventory.rewards.control
            report['sample']=dict(playable=mem.u32(ctrl+s.PLAYABLE),alive=mem.u32(ctrl+s.ALIVE),
                combat_state=mem.u32(mem.u32(ctrl+COMBAT_OBJECT)+4) if mem.u32(ctrl+COMBAT_OBJECT) else None,
                frames=mem.u32(ctrl+tt.FRAMES),deaths=mem.u32(ctrl+s.DEATH_COUNT),
                applied=mem.u32(ctrl+s.DEATH_APPLIED),failed=mem.u32(ctrl+s.DEATH_FAILED),
                received=havoc.deathlink.received,sent=havoc.deathlink.sent,
                concrete=struct.unpack('<f',mem.read(rc+tr.CONCRETE_END,4))[0],
                moon=struct.unpack('<f',mem.read(rc+tr.MOON_END,4))[0],
                disco=struct.unpack('<f',mem.read(rc+tr.DISCO_END,4))[0],
                bullet=struct.unpack('<f',mem.read(rc+tr.BULLET_END,4))[0],
                vampire=struct.unpack('<f',mem.read(rc+tr.VAMPIRE_END,4))[0],
                vampire_hits=mem.u32(rc+va.HITS),vampire_nonlethal=mem.u32(rc+va.NONLETHAL),
                vampire_heal=struct.unpack('<4f',mem.read(rc+va.HP_BEFORE,16)),
                hologram=struct.unpack('<f',mem.read(rc+tr.HOLOGRAM_END,4))[0],
                hologram_id=mem.u32(rc+bo.HOLO_ID),hologram_ptr=mem.u32(rc+bo.HOLO_PTR),
                hologram_calls=dict(zip(('saves','protected','named','renders'),struct.unpack('<4I',mem.read(rc+ho.SAVES,16)))),
                reloads=mem.u32(rc+tr.RELOADS),
                weapon_calls=dict(zip(('updates','blocked','rate','loaded','ammo','reload_starts'),
                    struct.unpack('<6I',mem.read(rc+we.UPDATES,24)))),
                stats=struct.unpack('<4f',mem.read(rc+r.STATS,16)),
                notification=mem.u32(runtime.inventory.notifications.control+REQUEST))
            if hotkeys:
                hotkeys.update(havoc,bool(report['sample']['playable'] and report['sample']['alive']))
            publish()
        await asyncio.sleep(.02 if hotkeys else .1)
    async def until(test):
        while not test():await tick()
    with (directory/'server.log').open('w',encoding='utf-8') as log:
        try:
            server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1',
                '--port',str(port),'--disable_save','--password',''],stdin=subprocess.PIPE,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            tasks=[asyncio.create_task(c.network()) for c in clients]
            await until(lambda:all(c.ready and c.deathlink.enabled for c in clients))
            runtime=tt.attach(pid,testing=True)  # No checkpoint identity; no save writer.
            if runtime is None:raise RuntimeError('No supported TT game is running')
            save_dir=save_directory(runtime.memory,runtime.addresses)
            originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in save_dir.iterdir() if p.is_file()}
            shutil.copytree(save_dir,directory/'saves-before')
            report.update(pid=runtime.memory.pid,backed_up_files=len(originals))
            await until(lambda:report.get('sample',{}).get('playable') and report['sample']['alive'])
            rc=runtime.inventory.rewards.control
            if direction=='hotkeys':
                from renegade.native.tt_hotkeys import NativeHotkeys
                hotkeys=NativeHotkeys(runtime.memory)
                assert not hotkeys.disabled, f'Supply shortcut conflicts: {hotkeys.disabled}'
                runtime.memory.write_u32(rc+r.TEST_ACTION,2)
                await until(lambda:runtime.memory.u32(rc+r.TEST_ACTION)==0 and report['sample']['stats'][2]==25)
                report['hotkey_receipts']=[]
                report['hotkeys_from']=hotkeys_from
                for key,name in list(zip(('F2','F3','F4'),SUPPLIES))[hotkeys_from-2:]:
                    before=set(havoc.progress.supply_spent)
                    phase(f'press_{key}_for_{name.replace(" ","_")}')
                    # The recovery journal reserves the item BEFORE dispatch.
                    # Wait for the native result and journal resolution, not the
                    # reservation, before judging the refill or advancing a key.
                    await until(lambda:bool(applied_supply_receipts(havoc.progress,name,before))
                                and runtime.inventory.rewards.pending is None
                                and havoc.supply_request is None)
                    used=applied_supply_receipts(havoc.progress,name,before)
                    assert len(used)==1
                    assert str(next(iter(used))) not in havoc.progress.activations
                    assert runtime.memory.u32(rc+r.RESULT)==1
                    samples=runtime.memory.u32(rc+r.SAMPLES)
                    await until(lambda:runtime.memory.u32(rc+r.SAMPLES)>samples)
                    if key=='F2':assert report['sample']['stats'][0]==report['sample']['stats'][1]
                    if key=='F3':assert report['sample']['stats'][2]==report['sample']['stats'][3]
                    report['hotkey_receipts'].append(dict(key=key,item=name,receipt=next(iter(used))))
                    if key=='F4':assert runtime.memory.u32(rc+r.NOT_FULL)==0
                phase('requested_real_keypress_supply_receipts_passed')
            elif direction=='hologram':
                server.stdin.write(f'/send Havoc {HOLOGRAM_HAVOC}\n'.encode());server.stdin.flush()
                await until(lambda:report['sample']['hologram_ptr'] and report['sample']['hologram']>50)
                report['spawn']=dict(id=report['sample']['hologram_id'],ptr=report['sample']['hologram_ptr'],
                    position=struct.unpack('<3f',runtime.memory.read(rc+bo.SPAWN_POSITION,12)))
                phase('hologram_active_full_minute_follow_combat_and_visual_review')
                runtime.memory.write_u32(rc+ho.TEST_ACTION,2)
                await until(lambda:runtime.memory.u32(rc+ho.TEST_RESULT)==1)
                hp0,hp1=struct.unpack('<2f',runtime.memory.read(rc+ho.TEST_HP_BEFORE,8))
                protected=runtime.memory.u32(rc+ho.PROTECTED)
                report['invincibility']=dict(health_before=hp0,health_after=hp1,damage_attempted=100000,protected=protected)
                assert hp0>0 and hp0==hp1 and protected>0, report['invincibility']
                phase('hologram_native_damage_protection_passed')
                test_save=save_dir/('ap_tt_hologram_'+directory.name.rsplit('-',1)[-1]+'.sav')
                assert not test_save.exists(), 'Disposable test filename already exists'
                runtime.memory.write(rc+ho.TEST_FILENAME,('save\\'+test_save.name).encode('ascii')+b'\0')
                runtime.memory.write_u32(rc+ho.TEST_ACTION,1)
                await until(lambda:runtime.memory.u32(rc+ho.TEST_RESULT)==2)
                assert runtime.memory.u32(rc+ho.SAVES)>0, 'Native save did not exclude the hologram'
                report['test_save']=inspect_save(test_save.read_bytes())
                shutil.copy2(test_save,directory/test_save.name)
                phase('hologram_excluded_from_valid_native_save')
                await until(lambda:report['sample']['hologram']==0 and not report['sample']['hologram_ptr'])
                assert report['sample']['hologram_calls']['renders']>0, 'Hologram render callback never ran'
                phase('hologram_expired_and_actor_removed')
            else:
                await common_supply_test(runtime,havoc,server,report,until,tick,phase)
            if direction in ('hologram','hotkeys'):
                pass
            elif direction=='incoming':
                assert sakura.player_died('M01')
                await until(lambda:report['sample']['applied']==1)
                assert havoc.deathlink.received==1 and report['sample']['deaths']==0
                for _ in range(10):await tick()
                assert havoc.deathlink.sent==0 and sakura.deathlink.received==0
                phase('incoming_native_death_without_echo_passed')
            elif direction=='outgoing':
                runtime.memory.write_u32(runtime.control+s.TEST_ACTION,1)
                await until(lambda:report['sample']['deaths']==1 and havoc.deathlink.sent==1 and sakura.deathlink.received==1)
                assert report['sample']['applied']==0
                phase('outgoing_native_death_and_room_delivery_passed')
            elif direction=='vampire':
                runtime.memory.write_u32(rc+r.TEST_ACTION,2)
                await until(lambda:runtime.memory.u32(rc+r.TEST_ACTION)==0 and report['sample']['stats'][2]==25)
                assert havoc.request_supply(SUPPLIES[2])
                await until(lambda:havoc.supply_request is None)
                server.stdin.write(f'/send Havoc {VAMPIRIC_ROUNDS}\n'.encode());server.stdin.flush()
                await until(lambda:report['sample']['vampire']>50)
                phase('vampiric_rounds_active_shoot_a_live_enemy')
                await until(lambda:report['sample']['vampire_nonlethal']>0)
                hp0,hp1,ar0,ar1=report['sample']['vampire_heal']
                assert hp1>hp0 or ar1>ar0, 'Hit recorded, but no missing health/armour was restored'
                assert 0<=hp1-hp0<=10.01 and 0<=ar1-ar0<=10.01
                report['first_confirmed_heal']=dict(health_before=hp0,health_after=hp1,armour_before=ar0,armour_after=ar1)
                phase('nonlethal_enemy_hit_healing_passed_full_duration_review')
                await until(lambda:report['sample']['vampire']==0)
                phase('vampiric_rounds_expired_naturally')
            elif direction=='weapon':
                server.stdin.write(f'/send Havoc {BULLET_HOSE}\n/send Havoc {BULLET_HOSE}\n'.encode());server.stdin.flush()
                await until(lambda:report['sample']['bullet']>110 and report['sample']['weapon_calls']['rate']>0)
                phase('bullet_stacked_and_active_tt_weapon_update_reached')
                server.stdin.write(f'/send Havoc {BUTTERFINGERS}\n'.encode());server.stdin.flush()
                await until(lambda:report['sample']['reloads']>0)
                await until(lambda:report['sample']['reloads']==0)
                assert report['sample']['weapon_calls']['reload_starts']==5
                assert report['sample']['weapon_calls']['blocked']>0
                phase('five_native_reloads_and_firing_lock_passed')
                # The actual trigger/shot checks are interactive: aim somewhere
                # empty and hold fire with a firearm. No input is synthesized.
                phase('waiting_for_bullet_hose_shots_aim_at_empty_ground')
                await until(lambda:report['sample']['weapon_calls']['ammo']>=10)
                assert report['sample']['weapon_calls']['loaded']>0
                phase('native_shots_use_bullet_hose_ammo_and_loaded_hooks')
                # Preserve the full stacked duration for the user's manual test.
                # Passing the automatic shot check must not end their effect.
                phase('manual_weapon_review_full_remaining_duration')
                await until(lambda:report['sample']['bullet']==0)
                phase('bullet_hose_expired')
            else:
                names=(CONCRETE_BOOTS,MOON_BOOTS,CONCRETE_BOOTS,MOON_BOOTS)
                if direction in ('presentation','pause'):names+=(DISCO_HAVOC,DISCO_HAVOC)
                for name in names:
                    server.stdin.write(f'/send Havoc {name}\n'.encode());server.stdin.flush()
                await until(lambda:report['sample']['concrete']>50 and report['sample']['moon']>110
                            and len(havoc.progress.supply_spent)==2+len(names))
                phase('movement_effects_stacked_to_60_and_120_seconds')
                if direction=='pause':
                    phase('waiting_for_escape_pause')
                    await until(lambda:report['sample']['combat_state']==3 and not report['sample']['playable'])
                    before={key:report['sample'][key] for key in ('concrete','moon','disco')}
                    start=asyncio.get_running_loop().time()
                    while asyncio.get_running_loop().time()-start<5:
                        await tick()
                        assert report['sample']['combat_state']==3, 'Resume only after the pause sample finishes'
                    deltas={key:before[key]-report['sample'][key] for key in before}
                    report['pause_sample']=dict(wall_seconds=asyncio.get_running_loop().time()-start,
                        playable_seconds=deltas,remaining=before)
                    assert all(abs(value)<.01 for value in deltas.values()),deltas
                    phase('paused_timers_frozen_resume_when_ready')
                    await until(lambda:report['sample']['playable'])
                if direction=='presentation':
                    assert report['sample']['disco']>110
                    phase(f'visual_review_hud_disco_and_pause_for_{visual_seconds}_seconds')
                    start=asyncio.get_running_loop().time()
                    while asyncio.get_running_loop().time()-start<visual_seconds:await tick()
                    await until(lambda:report['sample']['playable'])
                def clock_baseline():
                    values={key:report['sample'][key] for key in ('concrete','moon','disco') if report['sample'][key]>10}
                    assert values, 'No active timer remains for the clock sample; use a shorter visual hold'
                    return values,asyncio.get_running_loop().time()
                before,start=clock_baseline()
                while asyncio.get_running_loop().time()-start<5:
                    previous=report['sample']['frames']
                    await tick()
                    # Unfocused TT can stop Think while PLAYABLE retains its last
                    # sample. Wall time then cannot measure the engine clock.
                    if not report['sample']['playable'] or report['sample']['frames']==previous:
                        before,start=clock_baseline()
                elapsed=asyncio.get_running_loop().time()-start
                deltas={key:before[key]-report['sample'][key] for key in before}
                report['clock_sample']=dict(wall_seconds=elapsed,playable_seconds=deltas)
                assert all(elapsed*.6<value<elapsed*1.4 for value in deltas.values()),deltas
                phase('native_play_time_clock_passed')
                # Shorten only this disposable test's counters to exercise expiry.
                for offset in (tr.CONCRETE_END,tr.MOON_END,tr.DISCO_END):
                    runtime.memory.write(rc+offset,struct.pack('<f',.25))
                await until(lambda:report['sample']['concrete']==report['sample']['moon']==report['sample']['disco']==0)
                phase('movement_timers_expired')
            report['deathlink_totals']=dict(sent=havoc.deathlink.sent,received=havoc.deathlink.received,
                peer_received=sakura.deathlink.received)
            assert not havoc.progress.completed and not havoc.acknowledged
            phase('no_checks_sent')
            publish('passed')
        except BaseException as error:
            report['error']=str(error);publish('error');raise
        finally:
            for client in clients:client.stop.set()
            if runtime:
                try:
                    runtime.close();runtime.inventory.rewards.update(havoc,0,-1,False)
                    verify(runtime.memory,gate=True)
                    assert all(runtime.memory.u32(field)==original for field,original,code in runtime.inventory.traps.entries)
                    assert all(runtime.memory.read(site,5)==original for site,original,code in runtime.inventory.weapon_effects.entries)
                    vamp=runtime.inventory.vampiric
                    assert runtime.memory.read(vamp.site,5)==vamp.original
                    assert not runtime.inventory.hologram.pending_cleanup, 'Return to TT to finish bodyguard cleanup'
                    assert all(runtime.memory.read(site,5)==original for site,original,code in runtime.inventory.hologram.entries)
                    report['callbacks_restored']=True
                except (OSError,AssertionError) as error:report.update(status='error',cleanup_error=str(error))
                finally:runtime.memory.close()
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if server and server.poll() is None:
                try:server.communicate(b'/exit\n',timeout=5)
                except subprocess.TimeoutExpired:server.terminate();server.wait(timeout=5)
            if save_dir:
                if test_save and test_save.exists() and test_save.name not in originals:
                    # Only this run's newly created file; all pre-existing saves
                    # must still match the original hashes below.
                    if not (directory/test_save.name).exists():shutil.copy2(test_save,directory/test_save.name)
                    test_save.unlink()
                after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in save_dir.iterdir() if p.is_file()}
                report['saves_unchanged']=after==originals
                if after!=originals:report.update(status='error',save_error='Save directory changed during diagnostic')
            publish();(directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


async def common_supply_test(runtime,havoc,server,report,until,tick,phase):
    rc=runtime.inventory.rewards.control
    runtime.memory.write_u32(rc+r.TEST_ACTION,2)
    await until(lambda:runtime.memory.u32(rc+r.TEST_ACTION)==0 and report['sample']['stats'][2]==25)
    phase('test_health_armour_and_ammo_depleted')
    server.stdin.write(f'/send Havoc {ARMOUR_TRAP}\n'.encode());server.stdin.flush()
    await until(lambda:report['sample']['stats'][2]==0 and len(havoc.progress.supply_spent)==1)
    phase('armour_loss_native_receipt_passed')
    assert havoc.request_supply(SUPPLIES[1])
    await until(lambda:havoc.supply_request is None)
    assert report['sample']['stats'][2]==report['sample']['stats'][3]
    phase('armour_supply_restored')
    await until(lambda:not havoc.notifications.queue and not report['sample']['notification'])
    havoc.notifications.notice('TT notification test: Armour Loss and DeathLink ready.',0)
    await tick()
    await until(lambda:not havoc.notifications.queue and not report['sample']['notification'])
    phase('local_message_window_acknowledged_visual_confirmation_needed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int);parser.add_argument('--seconds',type=int,default=240)
    parser.add_argument('--direction',choices=['incoming','outgoing','movement','presentation','pause','weapon','vampire','hologram','hotkeys'],default='incoming')
    parser.add_argument('--visual-seconds',type=int,default=45)
    parser.add_argument('--hotkeys-from',type=int,choices=(2,3,4),default=2,help='Resume shortcut verification from this function key')
    args=parser.parse_args()
    directory=ROOT/'.research/tt-4.8.4'/('effect-services-'+args.direction+'-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    seed=generate(Path('C:/ProgramData/Archipelago'),directory,multiworld=True,starting_missions=12,
        death_link=True,start_inventory={n:2 for n in SUPPLIES})
    report=asyncio.run(exercise(directory,seed,args.pid,args.seconds,args.direction,args.visual_seconds,args.hotkeys_from))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
