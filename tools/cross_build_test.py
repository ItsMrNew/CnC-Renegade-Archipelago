"""Disposable Steam/TT room integration test; run with both games' privileges.

Both games may start at the main menu or in a mission.
Follow the focus requests in the console. Both players are killed twice.
Two AP checks are synthetic transport probes, not gameplay-completion evidence.
No objectives/discs/victories are forced in either game. Saves are backed up.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import (ITEM_IDS, SUPPLIES, UPGRADES, HEALTH_SUPPLY, MOON_BOOTS,
                           UPGRADE_AMOUNT, UPGRADE_LIMIT)
from renegade.native import services as s, tt, rewards as r, traps as tr
from renegade.native.memory import ProcessMemory, candidates
from renegade.native.inspect import verify as verify_steam, telemetry as steam_state
from renegade.native.gate import verify_gate
from renegade.native.tt_profile import verify as verify_tt, telemetry as tt_state
from renegade.native.tt_campaign import save_directory
from renegade.native.mission_launch import select_save
from renegade.native.savebuilder import attach_builder, EXIT
from tools.completion_test import native_call
from tools.live_test import generate
from tools.tt_inventory_test import publish_status

LOG = logging.getLogger('CrossBuildTest')


class ScoutClient(RenegadeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scouted = []

    async def handle(self, websocket, packet):
        await super().handle(websocket, packet)
        if packet.get('cmd') == 'Connected':
            await self.send(websocket, {'cmd': 'LocationScouts',
                'locations': sorted(self.locations), 'create_as_hint': 0})
        elif packet.get('cmd') == 'LocationInfo':
            self.scouted = packet['locations']


def cross_reward(scouted, receiver, allowed):
    """Choose a real cross-player placement without triggering random effects."""
    safe = {ITEM_IDS[name] for name in (*SUPPLIES, *UPGRADES)}
    options = [item for item in scouted if item['player'] == receiver
               and item['location'] in allowed and item['item'] in safe]
    if not options:
        raise ValueError('Seed has no suitable cross-player placement for this probe')
    return min(options, key=lambda item: item['location'])


def launcher_ready(client, kind):
    state = client.mission_launch
    return bool(client.ready and state and not state.get('busy')
                and (state.get('menu') or state.get('can_queue')))


def discover(steam_pid=None, tt_pid=None):
    found, errors = {}, []
    for pid in set(candidates() if steam_pid is None or tt_pid is None else (steam_pid, tt_pid)):
        memory = None
        try:
            memory = ProcessMemory(pid)
            if memory.modules[0].name.lower() == 'game2.exe':
                if tt_pid is not None and pid != tt_pid:
                    continue
                addresses = verify_tt(memory, gate=True)
                kind, saves = 'tt', save_directory(memory, addresses)
            else:
                if steam_pid is not None and pid != steam_pid:
                    continue
                verify_steam(memory); verify_gate(memory)
                if steam_state(memory)['mode'] not in (0, 1):
                    raise ValueError('Steam must be at the menu or in single player')
                kind, saves = 'steam', memory.modules[0].path.parent / 'Data' / 'save'
            if kind in found:
                raise RuntimeError(f'Multiple supported {kind} processes; specify --{kind}-pid')
            select_save(saves, '', 'M01', 1)
            found[kind] = (pid, saves)
        except (OSError, ValueError) as error:
            errors.append(f'{pid}: {error}')
        finally:
            if memory:
                memory.close()
    if set(found) != {'steam', 'tt'}:
        advice = []
        if 'steam' not in found:
            advice.append("Stock Steam engine not detected. Close the stock Game.exe and launch "
                          "Renegade using Steam's Play button (supported process: Game.dat).")
        if 'tt' not in found:
            advice.append('TT engine not detected. Start game.exe from the separate TT installation '
                          '(supported process: game2.exe).')
        raise RuntimeError(' '.join(advice) + '\nDiagnostics: ' + '; '.join(errors))
    if found['steam'][1].resolve() == found['tt'][1].resolve():
        raise RuntimeError('Steam and TT must use separate save directories')
    return found


async def exercise(directory, seed, games, network_only=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    clients = {kind: ScoutClient(f'localhost:{port}', name, state_dir=directory/'state')
               for kind, name in (('steam', 'Havoc'), ('tt', 'Sakura'))}
    runtimes, tasks, originals = {}, [], {}
    server = steam_task = None
    report = dict(status='connecting', directory=str(directory), phases=[],
                  synthetic_ap_checks=True, forced_native_checks=False, native_hooks_used=False,
                  processes={kind: pid for kind, (pid, _) in games.items()})

    def publish():
        report['updated'] = datetime.now(timezone.utc).isoformat()
        publish_status(ROOT/'.research/cross-build-status.json', report)

    def phase(name, focus=None):
        report.update(status=name, focus=focus); report['phases'].append(name); publish()
        LOG.info('%s%s', name, f' — focus {focus.upper()} Renegade now' if focus else '')

    def mem(kind, offset):
        obj = runtimes[kind]
        return obj.memory.u32(obj.control + offset)

    def rewards(kind):
        obj = runtimes[kind]
        return obj.rewards if kind == 'steam' else obj.inventory.rewards

    def stats(kind):
        obj = rewards(kind)
        values = struct.unpack('<4f', obj.memory.read(obj.control+r.STATS, 16))
        # Both samplers expose native getters, in different orders.
        return values if kind == 'steam' else (values[0], values[2], values[1], values[3])

    async def tick():
        if (directory/'STOP').exists():
            raise RuntimeError('Diagnostic stop requested')
        if server.poll() is not None:
            raise RuntimeError('Disposable server exited')
        for task in tasks + ([steam_task] if steam_task else []):
            if task.done():
                task.result(); raise RuntimeError('Client or native service stopped')
        if 'tt' in runtimes:
            obj = runtimes['tt']; obj.update(clients['tt'].capture_maps, client=clients['tt'])
            if obj.campaign.error:
                raise RuntimeError(obj.campaign.error)
        report['samples'] = {}
        for kind, obj in runtimes.items():
            state = steam_state(obj.memory) if kind == 'steam' else tt_state(obj.memory, obj.addresses)
            report['samples'][kind] = {**state, 'playable': mem(kind, s.PLAYABLE),
                'alive': mem(kind, s.ALIVE), 'generation': mem(kind, s.GENERATION),
                'deaths': mem(kind, s.DEATH_COUNT), 'applied': mem(kind, s.DEATH_APPLIED),
                'sent': clients[kind].deathlink.sent, 'received': clients[kind].deathlink.received,
                'stats': stats(kind)}
        publish(); await asyncio.sleep(.05)

    async def until(predicate, seconds=300):
        deadline = asyncio.get_running_loop().time()+seconds
        while not predicate():
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f'{report["status"]}: waiting for {report.get("focus")} game frames')
            await tick()

    async def hold(seconds):
        end = asyncio.get_running_loop().time()+seconds
        while asyncio.get_running_loop().time() < end:
            await tick()

    async def start_steam_services():
        nonlocal steam_task
        steam_task = asyncio.create_task(s.watch_services(clients['steam'], games['steam'][0],
            testing=True, on_attach=lambda obj: runtimes.__setitem__('steam', obj)))
        await until(lambda: 'steam' in runtimes)

    async def stop_steam_services():
        nonlocal steam_task
        if steam_task:
            steam_task.cancel()
            try:
                await steam_task
            except asyncio.CancelledError:
                pass
            steam_task = None; runtimes.pop('steam', None)
            probe = ProcessMemory(games['steam'][0])
            try:
                verify_steam(probe); verify_gate(probe)
            finally:
                probe.close()

    def send_item(kind, name):
        server.stdin.write(f'/send {clients[kind].slot} {name}\n'.encode()); server.stdin.flush()

    async def start(kind):
        phase('loading_'+kind+'_M01', kind)
        if (kind == 'steam' and not runtimes[kind].launcher.can_queue
                and steam_state(runtimes[kind].memory)['mode'] != 0):
            # Compatibility with the older menu-only Steam loader.
            await stop_steam_services()
            builder = attach_builder(games['steam'][0])
            try:
                await native_call(builder.job, EXIT)
            finally:
                builder.close(); builder.memory.close()
            await start_steam_services()
        # End_Game returns before the engine finishes changing game modes.
        # Wait for the same readiness snapshot the launcher buttons use.
        await until(lambda: launcher_ready(clients[kind], kind))
        generation = mem(kind, s.GENERATION)
        clients[kind].mission_request = ('M01', 1, False)
        await until(lambda: mem(kind, s.GENERATION)>generation and mem(kind, s.PLAYABLE)
                    and mem(kind, s.ALIVE) and mem(kind, s.MAP_INDEX)==1)
        await hold(1)

    async def refill_and_effect(kind):
        phase('native_supply_and_effect_'+kind, kind)
        obj, client = rewards(kind), clients[kind]
        expected = tuple(100+UPGRADE_AMOUNT*min(UPGRADE_LIMIT, client.inventory.count(ITEM_IDS[name]))
                         for name in UPGRADES)  # M01 Soldier base health/armour.
        await until(lambda: stats(kind)[2:] == expected)
        report.setdefault('native_upgrade_caps', {})[kind] = expected
        action = 1 if kind == 'steam' else 2
        serial = obj.memory.u32(obj.control+r.SAMPLES)
        obj.memory.write_u32(obj.control+r.TEST_RESULT, 0)
        obj.memory.write_u32(obj.control+r.TEST_ACTION, action)
        await until(lambda: obj.memory.u32(obj.control+r.TEST_ACTION)==0
                    and obj.memory.u32(obj.control+r.SAMPLES)>serial and stats(kind)[0]==25)
        send_item(kind, HEALTH_SUPPLY)
        await until(lambda: client.supply_indexes(HEALTH_SUPPLY))
        before = set(client.progress.supply_spent)
        assert client.request_supply(HEALTH_SUPPLY)
        await until(lambda: client.supply_request is None and obj.pending is None
                    and len(set(client.progress.supply_spent)-before)==1 and stats(kind)[0]==stats(kind)[2])
        send_item(kind, MOON_BOOTS)
        await until(lambda: struct.unpack('<f', obj.memory.read(obj.control+tr.MOON_END, 4))[0]>50
                    and obj.pending is None)
        phase('native_supply_and_effect_passed_'+kind)

    async def death(source, target):
        phase('native_local_death_'+source, source)
        source_client, target_client = clients[source], clients[target]
        source_sent, target_received = source_client.deathlink.sent, target_client.deathlink.received
        target_sent = target_client.deathlink.sent
        applied = mem(target, s.DEATH_APPLIED)
        obj = runtimes[source]
        action = 3 if source == 'steam' else 1
        obj.memory.write_u32(obj.control+s.TEST_RESULT, 0)
        obj.memory.write_u32(obj.control+s.TEST_ACTION, action)
        await until(lambda: obj.memory.u32(obj.control+s.TEST_RESULT)==action
                    and source_client.deathlink.sent==source_sent+1 and target_client.deathlink.received==target_received+1)
        phase('incoming_death_'+target, target)
        await until(lambda: mem(target, s.DEATH_APPLIED)==applied+1 and not mem(target, s.ALIVE))
        await hold(2)
        assert target_client.deathlink.sent==target_sent, 'Incoming DeathLink echoed'
        assert mem(target, s.DEATH_APPLIED)==applied+1, 'Incoming DeathLink applied twice'
        phase('deathlink_passed_'+source+'_to_'+target)

    with (directory/'server.log').open('w', encoding='utf-8') as log:
        try:
            for kind, (_, saves) in games.items():
                digests = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in saves.iterdir() if p.is_file()}
                shutil.copytree(saves, directory/(kind+'-saves-before'))
                originals[kind] = digests
            server = subprocess.Popen(['C:/ProgramData/Archipelago/ArchipelagoServer.exe', str(seed),
                '--host', '127.0.0.1', '--port', str(port), '--disable_save', '--password', ''],
                stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            tasks = [asyncio.create_task(client.network()) for client in clients.values()]
            await until(lambda: all(client.ready and client.scouted for client in clients.values()))
            report['cross_transfers'] = []
            for source, target in (('steam', 'tt'), ('tt', 'steam')):
                client, receiver = clients[source], clients[target]
                placement = cross_reward(client.scouted, receiver.identity[2], client.locations)
                # Explicit synthetic AP event, isolated to this disposable room.
                client.progress.merge({placement['location']}); client.changed.set()
                await until(lambda: placement['location'] in client.acknowledged
                    and any(item.location==placement['location'] and item.player==client.identity[2]
                            and item.item==placement['item'] for item in receiver.inventory.items))
                report['cross_transfers'].append(dict(source=source, target=target, **placement))
            phase('real_cross_player_item_transfers_from_synthetic_checks')
            if network_only:
                phase('passed')
                return report
            await start_steam_services()
            report['native_hooks_used'] = True
            runtimes['tt'] = tt.attach(games['tt'][0], identity=clients['tt'].identity, testing=True)
            if not runtimes['tt']:
                raise RuntimeError('TT disappeared before attachment')
            await start('tt'); await refill_and_effect('tt')
            await start('steam'); await refill_and_effect('steam')
            await death('steam', 'tt')
            await start('steam')
            await start('tt')
            await death('tt', 'steam')
            phase('passed')
        except BaseException as error:
            report.update(status='error', error=repr(error)); publish(); raise
        finally:
            for client in clients.values():
                client.stop.set()
            errors = []
            try:
                await stop_steam_services()
            except BaseException as error:
                errors.append('Steam: '+repr(error))
            obj = runtimes.pop('tt', None)
            if obj:
                try:
                    obj.close(); obj.inventory.rewards.update(clients['tt'], 0, -1, False)
                    verify_tt(obj.memory, gate=True)
                except BaseException as error:
                    errors.append('TT: '+repr(error))
                finally:
                    obj.memory.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if server and server.poll() is None:
                try:
                    server.communicate(b'/exit\n', timeout=5)
                except subprocess.TimeoutExpired:
                    server.terminate(); server.wait(timeout=5)
            report['save_restoration'] = {}
            for kind, files in originals.items():
                saves = games[kind][1]; changed = []
                for name, digest in files.items():
                    path = saves/name
                    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                        if path.exists():
                            output = directory/(kind+'-native-save-changes'); output.mkdir(exist_ok=True)
                            shutil.copy2(path, output/name)
                        shutil.copy2(directory/(kind+'-saves-before')/name, path); changed.append(name)
                report['save_restoration'][kind] = dict(count=len(files), restored=changed,
                    preserved=all(hashlib.sha256((saves/name).read_bytes()).hexdigest()==digest for name,digest in files.items()))
            if errors:
                report.update(status='error', cleanup_errors=errors)
            else:
                report['callbacks_restored'] = True if report['native_hooks_used'] else None
            publish(); (directory/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steam-pid', type=int); parser.add_argument('--tt-pid', type=int)
    parser.add_argument('--network-only', action='store_true', help='Validate the room/scout probe without game hooks')
    args = parser.parse_args()
    directory = ROOT/'.research'/('cross-build-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(), logging.FileHandler(directory/'client.log', encoding='utf-8')])
    preflight = dict(status='preflight', directory=str(directory), native_hooks_used=False,
                     updated=datetime.now(timezone.utc).isoformat())
    publish_status(ROOT/'.research/cross-build-status.json', preflight)
    try:
        # Read-only preflight before generating or attaching. Persist a failure
        # too, so the status file cannot misleadingly show an earlier pass.
        games = {} if args.network_only else discover(args.steam_pid, args.tt_pid)
        seed = generate(Path('C:/ProgramData/Archipelago'), directory, multiworld=True,
                        starting_missions=12, death_link=True, checks_per_mission=1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        preflight.update(status='error', error=str(error), updated=datetime.now(timezone.utc).isoformat())
        publish_status(ROOT/'.research/cross-build-status.json', preflight)
        (directory/'result.json').write_text(json.dumps(preflight, indent=2)+'\n', encoding='utf-8')
        LOG.error('%s\nNo game hooks were installed. Result: %s', error, directory/'result.json')
        raise SystemExit(1) from None
    result = asyncio.run(exercise(directory, seed, games, args.network_only))
    if result['status']!='passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
