"""Disposable local AP room for TT core validation; never forces completion.

Observe a real victory or completed objectives from a save. All maps start
available for this capture test. The ordinary TT launcher tests mission locks.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.client import RenegadeClient
from renegade.data import MISSIONS, DATA_DISCS, disc_id
from renegade.native.tt import watch
from renegade.native.tt_profile import verify
from renegade.native.memory import ProcessMemory
from tools.live_test import generate


async def exercise(directory, seed, pid, duration, discs=False):
    ap = Path('C:/ProgramData/Archipelago')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    runtime = None
    report = dict(updated=datetime.now(timezone.utc).isoformat(), status='starting',
                  directory=str(directory), server=f'localhost:{port}', simulated=False,
                  target='physical_disc' if discs else 'objective_or_victory')
    disc_ids = {disc_id(m.map_name, oid) for m in MISSIONS for oid in DATA_DISCS[m.map_name]}
    def save():
        report['updated'] = datetime.now(timezone.utc).isoformat()
        path = ROOT/'.research/tt-4.8.4'/('disc-test-status.json' if discs else 'core-test-status.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        temporary.replace(path)
    def attached(value):
        nonlocal runtime
        runtime = value
        report.update(status='observing', pid=value.memory.pid, before=value.snapshot())
        save()
        logging.info('TT observer attached. %s',
            'Load a mission-start save and physically collect a data disc. All maps are available.'
            if discs else 'Load a campaign save with completed objectives, or play a mission.')
    with (directory/'server.log').open('w', encoding='utf-8') as server_log:
        server = subprocess.Popen([str(ap/'ArchipelagoServer.exe'), str(seed), '--host', '127.0.0.1',
            '--port', str(port), '--disable_save', '--password', ''], stdin=subprocess.PIPE,
            stdout=server_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        client = RenegadeClient(f'localhost:{port}', 'Havoc', state_dir=directory/'state')
        tasks = [asyncio.create_task(client.network()), asyncio.create_task(watch(client, pid, on_attach=attached))]
        try:
            deadline = asyncio.get_running_loop().time() + duration
            while asyncio.get_running_loop().time() < deadline:
                if server.poll() is not None: raise RuntimeError('Disposable server stopped')
                for task in tasks:
                    if task.done():
                        task.result(); raise RuntimeError('TT test client stopped')
                if client.ready and runtime:
                    report.update(acknowledged=sorted(client.acknowledged), after=runtime.snapshot())
                    report['disc_checks_acknowledged'] = sorted(disc_ids.intersection(client.acknowledged))
                    save()
                    if (report['disc_checks_acknowledged'] if discs else client.acknowledged):
                        report['status'] = 'checks_acknowledged'
                        logging.info('PASS: AP server acknowledged %d real TT checks.', len(client.acknowledged))
                        break
                await asyncio.sleep(.25)
            else:
                report['status'] = 'no_check_observed'
        except BaseException as error:
            report.update(status='error', error=str(error))
            raise
        finally:
            for task in tasks: task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [str(e) for e in results if isinstance(e, Exception)]
            if failures: report['cleanup_errors'] = failures
            try:
                if runtime:
                    memory = ProcessMemory(runtime.memory.pid)
                    try:
                        verify(memory, gate=True)
                        report['callbacks_restored'] = memory.read(runtime.discs.site, 5) == runtime.discs.original
                    finally:
                        memory.close()
            except (OSError, ValueError) as error:
                report['restore_error'] = str(error)
            if server.poll() is None:
                try:
                    server.stdin.write(b'/exit\n'); server.stdin.flush()
                    server.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    server.terminate(); server.wait(timeout=5)
            save()
            (directory/'result.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--seconds', type=int, default=900)
    parser.add_argument('--discs', action='store_true', help='Enable disc checks and wait for a physical disc check')
    args = parser.parse_args()
    prefix = 'disc-test-' if args.discs else 'core-test-'
    directory = ROOT/'.research/tt-4.8.4'/(prefix+datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(), logging.FileHandler(directory/'client.log', encoding='utf-8')])
    seed = generate(Path('C:/ProgramData/Archipelago'), directory, starting_missions=12,
                    primary_objectives=True, secondary_objectives=True, checks_per_mission=5,
                    data_discs=args.discs)
    try:
        result = asyncio.run(exercise(directory, seed, args.pid, args.seconds, args.discs))
        print('Result:', result['status'], '- saved to', directory/'result.json')
    except KeyboardInterrupt:
        print('Test stopped; cleanup result saved.')


if __name__ == '__main__':
    main()
