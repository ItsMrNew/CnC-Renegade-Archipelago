"""Bundled resources and durable per-user launcher data."""
import json
import os
from pathlib import Path
import shutil
import sys


def resource_root():
    return Path(__file__).resolve().parents[1]


def data_root():
    if not getattr(sys, 'frozen', False):
        return resource_root()
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'RenegadeArchipelago'


def state_directory(tt=False):
    legacy = '.state-tt' if tt else '.state'
    target = data_root() / (('tt' if tt else 'steam') if getattr(sys, 'frozen', False) else legacy)
    target.mkdir(parents=True, exist_ok=True)
    if getattr(sys, 'frozen', False):
        # Preserve durable checks/item receipts when moving from the source
        # launcher to an EXE placed beside it or in its dist directory.
        base = Path(sys.executable).resolve().parent
        source = base / legacy
        if not source.is_dir() and (base.parent / 'RenegadeDesktop.py').is_file():
            source = base.parent / legacy
        if source.is_dir() and not (target / '.migrated').exists():
            for path in source.glob('*.json'):
                destination = target / path.name
                if not destination.exists():
                    shutil.copy2(path, destination)
            (target / '.migrated').touch()
    return target


def preferred_build():
    try:
        return json.loads((data_root() / 'launcher.json').read_text())['build'] == 'tt'
    except (OSError, ValueError, KeyError, TypeError):
        return False


def remember_build(tt):
    path = data_root() / 'launcher.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'build': 'tt' if tt else 'steam'}), encoding='utf-8')
    temp.replace(path)
