"""Generate and collect a disposable reward pool without accessing the game."""
import asyncio
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import ITEM_IDS, ITEM_FLAGS, SUPPLIES, UPGRADES, UPGRADE_LIMIT, TRAPS, BUFFS, REPORT, CLEARANCE, OBJECTIVES, MISSIONS, WEAPON_ITEMS, location_ids
from tools.live_test import generate


async def main(mode='progressive', weights='default', weapons=False, primary=False, checks=3):
    folder=ROOT/'.research'/('rewards-smoke-'+mode+'-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    ap=Path('C:/ProgramData/Archipelago')
    keys=('armour_loss','butterfingers','concrete_boots','disco_havoc','moon_boots','bullet_hose','vampiric_rounds','hologram_havoc')
    effect_weights=None if weights=='default' else {k:100 if weights=='selected' and k in ('moon_boots','bullet_hose') else 0 for k in keys}
    seed=generate(ap,folder,secondary_objectives=True,mission_unlocks=mode,effect_weights=effect_weights,weapon_unlocks=weapons,
                  primary_objectives=primary,checks_per_mission=checks)
    total=len(location_ids(checks,True,primary))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    c=RenegadeClient(f'localhost:{port}','Havoc',state_dir=folder/'state')
    with (folder/'server.log').open('w') as log:
        server=subprocess.Popen([str(ap/'ArchipelagoServer.exe'),str(seed),'--host','127.0.0.1','--port',str(port),'--disable_save'],
            stdin=subprocess.PIPE,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        task=asyncio.create_task(c.network())
        try:
            completion_order=[]
            async def collect():
                while not (c.goal_sent and len(c.acknowledged)==total and len(c.inventory.items)==total+1):
                    if task.done():task.result();raise RuntimeError('Client stopped')
                    # Prefer later unlocked maps to exercise non-campaign order.
                    for map_name in reversed(c.unlocked_maps):
                        if c.mission_finished(map_name,True):completion_order.append(map_name)
                        for oid in OBJECTIVES[map_name]:c.objective_finished(map_name,oid)
                    await asyncio.sleep(.1)
            await asyncio.wait_for(collect(),45)
            counts=Counter(i.item for i in c.inventory.items)
            assert counts[ITEM_IDS[REPORT]]==0
            if mode=='progressive':
                assert counts[ITEM_IDS[CLEARANCE]]==12
                assert all(counts[ITEM_IDS[m.unlock_item]]==0 for m in MISSIONS)
            else:
                assert counts[ITEM_IDS[CLEARANCE]]==0
                assert all(counts[ITEM_IDS[m.unlock_item]]==1 for m in MISSIONS)
                assert completion_order != [m.map_name for m in MISSIONS]
            assert all(counts[ITEM_IDS[n]]==UPGRADE_LIMIT for n in UPGRADES)
            remaining=total-11-2*UPGRADE_LIMIT-(len(WEAPON_ITEMS) if weapons else 0)
            traps=0 if weights=='disabled' else remaining//10
            buffs=0 if weights=='disabled' else (remaining-traps)//5
            assert sum(counts[ITEM_IDS[n]] for n in TRAPS)==traps
            assert sum(counts[ITEM_IDS[n]] for n in SUPPLIES)==remaining-traps-buffs
            assert sum(counts[ITEM_IDS[n]] for n in BUFFS)==buffs
            assert all(counts[ITEM_IDS[n]]==int(weapons) for n in WEAPON_ITEMS)
            assert c.weapon_mask==((1<<23)-1 if weapons else 0)
            if weights=='selected':
                assert counts[ITEM_IDS['Moon Boots']]==traps and counts[ITEM_IDS['Bullet Hose']]==buffs
                assert all(counts[ITEM_IDS[n]]==0 for n in (*TRAPS,*BUFFS) if n not in ('Moon Boots','Bullet Hose'))
            assert c.config['protocol_version']==10 and c.config['mission_unlocks']==mode and c.config['weapon_unlocks']==weapons
            assert c.config['primary_objectives'] is primary
            for item in c.inventory.items:
                name=next(n for n,i in ITEM_IDS.items() if i==item.item)
                # AP reports starting inventory with flags=0; placed items must
                # retain their actual world classification. The UI uses our catalogue.
                if item.location >= 0:
                    assert item.flags==ITEM_FLAGS[name],(name,item.flags)
            report=dict(stage='passed',checks=len(c.acknowledged),goal=c.goal_sent,native_hooks_used=False,
                        counts={name:counts[i] for name,i in ITEM_IDS.items()},all_classifications_verified=True,
                        mission_unlocks=mode,weights=weights,weapon_unlocks=weapons,primary_objectives=primary,
                        checks_per_mission=checks,completion_order=completion_order)
            (folder/'result.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report,indent=2))
            print(folder)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            try:await asyncio.to_thread(server.communicate,b'/exit\n',timeout=5)
            except subprocess.TimeoutExpired:
                server.terminate();await asyncio.to_thread(server.wait,timeout=5)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mission-unlocks',choices=('progressive','individual'),default='progressive')
    parser.add_argument('--weights',choices=('default','selected','disabled'),default='default')
    parser.add_argument('--weapon-unlocks',action='store_true')
    parser.add_argument('--primary-objectives',action='store_true')
    parser.add_argument('--checks-per-mission',type=int,choices=range(1,6),default=3)
    args=parser.parse_args()
    asyncio.run(main(args.mission_unlocks,args.weights,args.weapon_unlocks,args.primary_objectives,args.checks_per_mission))
