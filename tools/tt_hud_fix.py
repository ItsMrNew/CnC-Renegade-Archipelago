"""Apply the pinned TT HUD and campaign save-load guards without connecting to AP.

Start TT through game.exe and leave it at the main menu first. The guard stays
in the process until game exit. No saves, game files or AP checks are changed.
The TT Archipelago launcher also installs this guard on native attachment.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.native.memory import ProcessMemory, candidates
from renegade.native.tt_profile import verify
from renegade.native.tt_hud import NativeHUDGuard
from renegade.native.tt_load import NativeLoadGuard


def select_pid(game_dir, pid=None):
    matches = []
    for candidate in [pid] if pid is not None else candidates():
        memory = ProcessMemory(candidate)
        try:
            game = memory.modules[0]
            if game.name.lower() == 'game2.exe' and game.path.resolve().parent == game_dir.resolve():
                addresses = verify(memory, gate=True)
                NativeHUDGuard(memory, addresses)  # Verify before writable access.
                NativeLoadGuard(memory, addresses)
                matches.append(candidate)
        finally:
            memory.close()
    if len(matches) != 1:
        raise RuntimeError('Start exactly one TT game from the selected test folder, '
                           'wait for the main menu, and close the AP client before using this helper.')
    return matches[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--game-dir', type=Path, default=Path('D:/Renegade AP Testing/TT-4.8.4'))
    parser.add_argument('--seconds', type=float, default=0,
                        help='Optionally sample guard counters for up to 60 seconds')
    parser.add_argument('--output', type=Path, default=ROOT/'.research/tt-4.8.4/hud-fix-status.json')
    args = parser.parse_args(argv)
    if not 0 <= args.seconds <= 60:
        parser.error('--seconds must be between 0 and 60')
    report = dict(updated=datetime.now(timezone.utc).isoformat(), status='starting')
    memory = None
    try:
        pid = select_pid(args.game_dir, args.pid)
        memory = ProcessMemory(pid, writable=True)
        addresses = verify(memory, gate=True)
        guard = NativeHUDGuard(memory, addresses)
        load_guard = NativeLoadGuard(memory, addresses)
        load_installed = load_guard.install()
        installed = guard.install()
        report.update(pid=pid, newly_installed=installed, before=guard.snapshot(),
                      load_newly_installed=load_installed, load_before=load_guard.snapshot())
        print('TT HUD and campaign save-load guards active until Renegade closes.', flush=True)
        print('No AP connection or check hooks are active. You can now test repeated save loads.', flush=True)
        until = time.monotonic()+args.seconds
        while time.monotonic() < until:
            time.sleep(min(.25, max(0, until-time.monotonic())))
        report.update(status='installed_and_verified', after=guard.snapshot(), load_after=load_guard.snapshot())
        print(f"Render frames: {report['after']['render_frames']}; "
              f"missing-text frames guarded: {report['after']['guarded_frames']}")
    except (OSError, ValueError, RuntimeError) as error:
        report.update(status='error', error=str(error))
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    finally:
        if memory: memory.close()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        print(f'Report: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
