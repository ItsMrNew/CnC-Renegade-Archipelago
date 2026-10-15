"""Atomic checkpoint publication with two older, room-specific recovery saves."""
from functools import lru_cache
import os
import re
import time

from ..data import mission_for_map
from .savefiles import inspect_save, relabel, saved_difficulty


def checkpoint_name(token, map_name):
    mission_for_map(map_name)
    if not re.fullmatch(r'[A-Za-z0-9]+', token):
        raise ValueError('Invalid checkpoint room token')
    return f'ap_checkpoint_{token}_{map_name}'


def _paths(save_dir, token, map_name):
    stem = checkpoint_name(token, map_name)
    latest = save_dir / (stem + '.sav')
    archives = [p for p in save_dir.glob(stem + '_h*.sav')
                if re.fullmatch(re.escape(stem) + r'_h[0-9]+\.sav', p.name)]
    archives.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    return [p for p in [latest, *archives] if p.is_file() and p.resolve().parent == save_dir.resolve()]


@lru_cache(maxsize=128)
def _info(path, modified, size):
    raw = path.read_bytes()
    info = inspect_save(raw)
    return dict(name=path.name, fingerprint=info['sha256'], map=info['map_name'],
                difficulty=saved_difficulty(raw), modified=modified / 1_000_000_000)


def checkpoint_history(save_dir, token, map_name):
    """Newest first; never offer corrupt or foreign saves to the mission loader."""
    choices, seen = [], set()
    for path in _paths(save_dir, token, map_name):
        try:
            stat = path.stat()
            info = _info(path, stat.st_mtime_ns, stat.st_size)
            if info['map'] != map_name or info['fingerprint'] in seen:
                continue
            choices.append(dict(info))
            seen.add(info['fingerprint'])
        except (OSError, ValueError, KeyError):
            continue
        if len(choices) == 3:
            break
    return choices


def publish_checkpoint(save_dir, token, map_name, raw):
    mission = mission_for_map(map_name)
    if inspect_save(raw)['map_name'] != map_name:
        raise ValueError('Checkpoint saved the wrong map')
    saved_difficulty(raw)  # Reject malformed saves before touching the last good one.
    payload = relabel(raw, 'AP Checkpoint ' + mission.label)
    stem = checkpoint_name(token, map_name)
    destination = save_dir / (stem + '.sav')
    if destination.resolve().parent != save_dir.resolve():
        raise ValueError('Checkpoint must be inside Renegade Data/save')
    if destination.is_file():
        previous = destination.read_bytes()
        if previous == payload:
            return destination
        # Archive first. If publication is interrupted, the previous latest still
        # exists, and no history is pruned until the new save is safely in place.
        if inspect_save(previous)['map_name'] != map_name:
            raise ValueError('Existing checkpoint contains a different mission')
        modified = destination.stat().st_mtime_ns
        archive = save_dir / f'{stem}_h{time.time_ns()}.sav'
        with archive.open('xb') as handle:
            handle.write(previous)
        os.utime(archive, ns=(modified, modified))
    temp = destination.with_suffix('.tmp')
    temp.write_bytes(payload)
    temp.replace(destination)
    # Keep exactly three distinct snapshots, scoped to this room and mission.
    keep = {choice['name'] for choice in checkpoint_history(save_dir, token, map_name)}
    for path in _paths(save_dir, token, map_name):
        if path != destination and path.name not in keep:
            path.unlink()
    return destination
