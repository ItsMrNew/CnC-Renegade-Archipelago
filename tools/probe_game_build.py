"""Inventory a Renegade installation and compare it with Steam adapter anchors.

Read-only, including --pid: no hooks, allocations, calls or process-memory writes.
Matching anchors are evidence to investigate, never permission to attach to an
unknown build. Runtime detours and object layouts need separate verification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pefile

from renegade.native.profile import STEAM


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def is_pe_candidate(path):
    if not path.is_file() or path.suffix.lower() not in ('.exe', '.dll', '.dat'):
        return False
    with path.open('rb') as handle:
        return handle.read(2) == b'MZ'


def steam_anchors():
    groups = dict(observer=STEAM.anchors, gate=STEAM.gate_anchors, menu=STEAM.menu_anchors)
    for name in ('savebuilder', 'services', 'rewards', 'weapons', 'traps', 'bonuses', 'effects_hud', 'discs'):
        groups[name] = importlib.import_module('renegade.native.' + name).ANCHORS
    return groups


def compare_anchors(read, groups):
    result = {}
    for name, anchors in groups.items():
        rows = []
        for address, expected in anchors.items():
            row = dict(rva=hex(address), expected=expected.hex())
            try:
                actual = read(address, len(expected))
                row.update(actual=actual.hex(), matches=actual == expected)
            except (OSError, ValueError, pefile.PEFormatError) as error:
                row.update(matches=False, error=str(error))
            rows.append(row)
        result[name] = dict(matched=sum(row['matches'] for row in rows), total=len(rows), anchors=rows)
    return result


def image_info(path, anchors=False):
    result = dict(name=path.name, bytes=path.stat().st_size, sha256=digest(path))
    try:
        with pefile.PE(str(path), fast_load=False) as pe:
            result.update(machine=hex(pe.FILE_HEADER.Machine),
                          image_base=hex(pe.OPTIONAL_HEADER.ImageBase),
                          image_size=pe.OPTIONAL_HEADER.SizeOfImage,
                          relocations=bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[5].Size))
            versions = {}
            for group in getattr(pe, 'FileInfo', []):
                for entry in group:
                    for table in getattr(entry, 'StringTable', []):
                        for key, value in table.entries.items():
                            if key in (b'FileVersion', b'ProductVersion', b'ProductName', b'OriginalFilename'):
                                versions[key.decode()] = value.decode(errors='replace')
            result['version'] = versions
            result['exports'] = [symbol.name.decode(errors='replace') for symbol in
                                 getattr(getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None), 'symbols', [])
                                 if symbol.name]
            if anchors:
                result['steam_anchors_on_disk'] = compare_anchors(pe.get_data, steam_anchors())
    except pefile.PEFormatError as error:
        result['error'] = str(error)
    return result


def installation(path):
    path = Path(path).resolve(strict=True)
    images = [image_info(p, p.name.lower() in ('game.dat', 'game.exe', 'game2.exe'))
              for p in sorted(path.iterdir()) if is_pe_candidate(p)]
    by_name = {row['name'].lower(): row for row in images}
    stock = (by_name.get('game.dat', {}).get('sha256') == STEAM.game_sha256
             and by_name.get('scripts.dll', {}).get('sha256') == STEAM.scripts_sha256)
    data = {}
    for p in sorted((path / 'Data').glob('*')):
        if p.is_file() and (p.suffix.lower() in ('.mix', '.dbs', '.dat') or p.name.lower() == 'paths.ini'):
            data[p.name] = dict(bytes=p.stat().st_size, sha256=digest(p))
    return dict(path=str(path), stock_fingerprints_match=stock, images=images, data=data,
                status='stock-files-recognized-live-validation-required' if stock else 'unsupported-build-no-hooks')


def live_inventory(pid, expected_root):
    from renegade.native.memory import ProcessMemory
    from renegade.native.inspect import verify
    from tools.probe_tt_runtime import inspect as inspect_tt
    memory = None
    try:
        memory = ProcessMemory(pid)  # writable=False is intentional.
        game = memory.modules[0]
        if game.path.resolve().parent != expected_root.resolve():
            raise ValueError('Selected process is outside the requested installation')
        modules = [dict(name=m.name, path=str(m.path), base=hex(m.base), size=m.size,
                        sha256=digest(m.path)) for m in memory.modules
                   if m.path.resolve().is_relative_to(expected_root.resolve())]
        report = dict(pid=pid, modules=modules, writable=memory.writable,
                      steam_anchors_in_memory=compare_anchors(
                          lambda rva, size: memory.read(game.base + rva, size), steam_anchors()))
        try:
            verify(memory)
            report['stock_adapter_validation'] = dict(passed=True)
        except (OSError, ValueError) as error:
            report['stock_adapter_validation'] = dict(passed=False, reason=str(error))
        report['tt_diagnostic'] = inspect_tt(memory, digest)
        return report
    finally:
        if memory:
            memory.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path, required=True)
    parser.add_argument('--compare-dir', type=Path)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = dict(read_only=True, hook_installed=False,
                  created=datetime.now(timezone.utc).isoformat(), installation=installation(args.game_dir))
    if args.compare_dir:
        other = installation(args.compare_dir)
        report['comparison'] = other
        left, right = report['installation']['data'], other['data']
        report['data_differences'] = [name for name in sorted(left.keys() | right.keys()) if left.get(name) != right.get(name)]
    if args.pid is not None:
        try:
            report['live'] = live_inventory(args.pid, args.game_dir)
        except (OSError, ValueError) as error:
            report['live'] = dict(pid=args.pid, error=str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(report=str(args.output), status=report['installation']['status'],
                          image_count=len(report['installation']['images']),
                          live_error=report.get('live', {}).get('error'))))


if __name__ == '__main__':
    main()
