"""Brief TT core attachment test. Allows every map and restores AP callbacks.

Does not connect to AP, grant items, change objectives or force victories.
The current mission's EVA map is revealed when playable; disc capture is disabled.
The independent TT HUD and campaign save-load guards remain until game exit.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from renegade.data import MISSIONS
from renegade.native.tt import attach
from renegade.native.tt_profile import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--output', type=Path, default=ROOT/'.research/tt-4.8.4/core-probe.json')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error('--seconds must be between 1 and 60')
    report = dict(updated=datetime.now(timezone.utc).isoformat(), status='starting')
    runtime = None
    try:
        runtime = attach(args.pid)
        if runtime is None:
            raise RuntimeError('Start the TT test installation first')
        report['before'] = runtime.snapshot()
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            runtime.update(tuple(m.map_name for m in MISSIONS))
            time.sleep(.05)
        report['after'] = runtime.snapshot()
        report['status'] = 'attached_and_sampled'
    finally:
        try:
            if runtime:
                runtime.close()
                verify(runtime.memory, gate=True)
                report['callbacks_restored'] = runtime.memory.read(runtime.discs.site, 5) == runtime.discs.original
        finally:
            if runtime: runtime.memory.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
