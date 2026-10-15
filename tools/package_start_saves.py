"""Validate an existing mission-start pack and package its 36 saves for the EXE."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from renegade.data import MISSIONS
from renegade.native.savefiles import inspect_save, saved_difficulty


def package(source, output):
    manifest = json.loads((source / 'manifest.json').read_text())
    entries = manifest['missions']
    expected = {(m.map_name, d) for m in MISSIONS for d in range(3)}
    if len(entries) != 36 or {(e['map_name'],e['difficulty']) for e in entries} != expected:
        raise ValueError('Expected every mission at all three difficulties')
    records, saves = [], {}
    for entry in entries:
        name = entry['filename']
        if Path(name).name != name or name in saves:
            raise ValueError('Invalid or duplicate save filename')
        raw = (source / 'saves' / name).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != entry['sha256']
                or inspect_save(raw)['map_name'] != entry['map_name']
                or saved_difficulty(raw) != entry['difficulty']):
            raise ValueError(f'Invalid save: {name}')
        records.append({k:entry[k] for k in ('filename','map_name','difficulty','sha256')})
        saves[name] = raw
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps({'missions':records}, indent=2))
        for name, raw in saves.items():
            archive.writestr('saves/' + name, raw)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('assets/mission-starts.zip'))
    args = parser.parse_args()
    print(package(args.source, args.output))
