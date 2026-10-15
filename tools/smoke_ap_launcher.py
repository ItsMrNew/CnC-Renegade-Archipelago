"""Test the bundled client via an isolated copy of installed Archipelago on Windows.

No real custom_worlds, room settings, saves or games are modified. Only processes
under this test's private executable cache are stopped at the end of each launch.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def client_processes(cache):
    # Read only this executable name, then restrict ownership by resolved path.
    command = ("Get-CimInstance Win32_Process -Filter \"Name = 'Renegade Archipelago.exe'\" | "
               "Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress")
    raw = subprocess.check_output(['powershell', '-NoProfile', '-Command', command], text=True)
    data = json.loads(raw) if raw.strip() else []
    if isinstance(data, dict):
        data = [data]
    return [row for row in data if row.get('ExecutablePath') and
            Path(row['ExecutablePath']).resolve().is_relative_to(cache.resolve())]


def run(apworld, installed, destination):
    if destination.exists():
        raise ValueError('Choose a new audit directory; previous results are preserved.')
    destination.mkdir(parents=True)
    sandbox = destination / 'Archipelago'
    sandbox.mkdir()
    for name in ('lib', 'data', 'share'):
        shutil.copytree(installed / name, sandbox / name)
    for name in ('ArchipelagoLauncher.exe', 'python3.dll', 'python313.dll'):
        shutil.copy2(installed / name, sandbox / name)
    custom = sandbox / 'custom_worlds'
    custom.mkdir()
    shutil.copy2(apworld, custom / 'renegade.apworld')
    env = os.environ.copy()
    env.update(LOCALAPPDATA=str(destination / 'LocalAppData'),
               APPDATA=str(destination / 'AppData'), KIVY_HOME=str(destination / 'kivy'))
    cache = Path(env['LOCALAPPDATA']) / 'RenegadeArchipelagoLauncher'
    profile = Path(env['LOCALAPPDATA']) / 'RenegadeArchipelago'
    results = []
    for attempt in (1, 2):
        log = destination / f'launch-{attempt}.txt'
        try:
            with log.open('wb') as output:
                launcher = subprocess.Popen([str(sandbox / 'ArchipelagoLauncher.exe'), 'Renegade Client'],
                    cwd=sandbox, env=env, stdout=output, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                launcher.wait(timeout=45)
            assert launcher.returncode == 0, log.read_text(errors='replace')
            text = log.read_text(errors='replace')
            assert re.search(r'Launched Renegade client \(PID \d+\)', text), text
            assert 'Invalid or missing manifest' not in text, text
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                processes = client_processes(cache)
                if len(processes) >= 2 and (profile / 'logs/desktop.log').is_file():
                    break
                time.sleep(.5)
            else:
                raise AssertionError('Client did not initialize from the private APWorld cache.')
            time.sleep(2)
            assert len(client_processes(cache)) >= 2, 'Client exited during startup'
            assert 'ERROR' not in (profile / 'logs/desktop.log').read_text(errors='replace')
            executable = Path(processes[0]['ExecutablePath'])
            results.append(dict(attempt=attempt, launcher_exit=launcher.returncode,
                executable=str(executable), mtime=executable.stat().st_mtime_ns,
                sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                profile=str(profile)))
        finally:
            # These are disconnected throwaway clients, never the user's launcher.
            for process in client_processes(cache):
                subprocess.run(['taskkill', '/PID', str(process['ProcessId']), '/T', '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(1)
    assert results[0]['executable'] == results[1]['executable']
    assert results[0]['mtime'] == results[1]['mtime'], 'The second launch should reuse its cache.'
    # Also exercise both native adapters and the bundled saves in that extracted EXE.
    report = destination / 'exe-self-test.json'
    subprocess.run([results[0]['executable'], '--self-test', str(report)], env=env, check=True, timeout=45)
    result = dict(launches=results, packaging_check=json.loads(report.read_text()),
                  game_access=False, real_install_modified=False)
    assert result['packaging_check']['passed']
    (destination / 'results.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apworld', type=Path, required=True)
    parser.add_argument('--installed', type=Path, default=Path('C:/ProgramData/Archipelago'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.apworld.resolve(), args.installed.resolve(), args.output.resolve()), indent=2))
