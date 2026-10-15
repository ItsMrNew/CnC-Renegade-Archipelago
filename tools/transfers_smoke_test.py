"""Exercise live room-wide notifications without attaching to Renegade."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import HEALTH_SUPPLY, ARMOUR_TRAP
from tools.live_test import generate


async def main():
    folder = ROOT/'.research'/('transfers-smoke-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    ap = Path('C:/ProgramData/Archipelago')
    seed = generate(ap, folder, multiworld=True, secondary_objectives=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    clients = [RenegadeClient(f'localhost:{port}', name, state_dir=folder/'state') for name in ('Havoc', 'Sakura')]
    with (folder/'server.log').open('w') as log:
        server = subprocess.Popen([str(ap/'ArchipelagoServer.exe'), str(seed), '--host', '127.0.0.1',
            '--port', str(port), '--disable_save'], stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW)
        tasks = [asyncio.create_task(c.network()) for c in clients]
        async def until(condition):
            async def wait():
                while not condition():
                    for task in tasks:
                        if task.done():
                            task.result()
                            raise RuntimeError('Client stopped')
                    await asyncio.sleep(.05)
            await asyncio.wait_for(wait(), 40)
        try:
            await until(lambda: all(c.ready for c in clients))
            havoc, sakura = clients
            for name in sakura.unlocked_maps:
                sakura.mission_finished(name, True)
            await until(lambda: len(havoc.notifications.seen) >= 12)
            await asyncio.sleep(.25)  # Include the server's automatic goal collection notices.
            events = list(havoc.notifications.queue)
            own = havoc.identity[2]
            uninvolved = [e for e in events if own not in e[:2]]
            assert uninvolved, 'Expected other-player transfers in the room-wide feed'
            assert all('Item ' not in havoc.notifications.text(e) for e in events)
            assert len(havoc.notifications.seen) == len(sakura.notifications.seen)
            server.stdin.write(f'/send Havoc {HEALTH_SUPPLY}\n/send Sakura {ARMOUR_TRAP}\n'.encode())
            server.stdin.flush()
            await until(lambda: len([e for e in havoc.notifications.queue if e[0] == 0]) == 2)
            cheats = [e for e in havoc.notifications.queue if e[0] == 0]
            assert cheats[0][4] == 0 and cheats[1][4] == 4
            havoc.notifications.configure({'scope': 'you'})
            assert all(own in e[:2] for e in havoc.notifications.queue)
            report = dict(stage='passed', native_hooks_used=False, room_transfers=len(events),
                          other_player_transfers=len(uninvolved), server_send_notices=len(cheats),
                          your_transfers_filter=True,
                          examples=[havoc.notifications.text(e) for e in events[:4]])
            (folder/'result.json').write_text(json.dumps(report, indent=2)+'\n')
            print(json.dumps(report, indent=2))
            print(folder)
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.to_thread(server.communicate, b'/exit\n', timeout=5)
            except subprocess.TimeoutExpired:
                server.terminate()
                await asyncio.to_thread(server.wait, timeout=5)


if __name__ == '__main__':
    asyncio.run(main())
