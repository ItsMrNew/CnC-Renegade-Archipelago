"""Disposable stock-game reward test; run elevated from the main menu."""
from datetime import datetime, timezone
import asyncio
import json
import logging
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import features_test
from tools.live_test import generate
from renegade.data import SUPPLIES, UPGRADES, ARMOUR_TRAP


def main():
    directory=ROOT/'.research'/('rewards-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s: %(message)s',
        handlers=[logging.StreamHandler(),logging.FileHandler(directory/'client.log',encoding='utf-8')])
    def status(directory,**values):
        values.update(directory=str(directory),updated=datetime.now(timezone.utc).isoformat())
        path=ROOT/'.research/rewards-status.json';temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(values,indent=2)+'\n',encoding='utf-8');temp.replace(path)
    features_test.status=status
    try:
        status(directory,stage='generating')
        pack=Path(json.loads((ROOT/'.research/savepack-status.json').read_text())['pack'])
        manifest=json.loads((pack/'manifest.json').read_text())
        items={n:2 for n in SUPPLIES};items.update({n:1 for n in UPGRADES});items[ARMOUR_TRAP]=1
        seed=generate(Path('C:/ProgramData/Archipelago'),directory,multiworld=True,starting_missions=12,
                      secondary_objectives=True,start_inventory=items)
        report=asyncio.run(features_test.exercise(directory,seed,manifest,rewards_mode=True))
        (directory/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        status(directory,**report)
        logging.info('PASS: item effects, full ammo, checkpoint reload, reconnect and original saves preserved.')
    except (Exception,KeyboardInterrupt) as error:
        status(directory,stage='failed',error=str(error))
        logging.exception('Reward test stopped: %s',error)
        raise


if __name__=='__main__':main()
