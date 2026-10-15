import struct
from pathlib import Path

import pytest

from renegade.native import checkpoints as c, mission_launch as launch
from renegade.native.savefiles import INFO, DATA, chunks, inspect_save, relabel
from test_mission_launch import saved, launcher
from test_services import CONTROL


def save(marker=0, difficulty=1, map_name='M01'):
    parts = list(chunks(saved(map_name, difficulty)))
    title = 'Test checkpoint\0'.encode('utf-16-le')
    metadata = parts[0][2] + bytes((3, len(title))) + title
    # An extra valid world chunk makes each test snapshot distinct.
    world = parts[1][2] + struct.pack('<III', 1234, 4, marker)
    return struct.pack('<II', INFO, len(metadata)) + metadata + struct.pack('<II', DATA, len(world)) + world


def test_keeps_three_snapshots_without_touching_other_rooms_manual_or_start_saves(tmp_path):
    untouched = ['manual.sav', 'ap_start_pack_Soldier_02_M01.sav',
                 'ap_checkpoint_other_M01.sav', 'ap_checkpoint_room_M02.sav',
                 'ap_checkpoint_room_M01_hnotours.sav']
    for name in untouched:
        (tmp_path / name).write_bytes(b'untouched')
    expected = []
    for i in range(5):
        raw = save(i, i % 3)
        path = c.publish_checkpoint(tmp_path, 'room', 'M01', raw)
        assert inspect_save(path.read_bytes())['world_sha256'] == inspect_save(raw)['world_sha256']
        expected.insert(0, inspect_save(path.read_bytes())['sha256'])
        assert [v['fingerprint'] for v in c.checkpoint_history(tmp_path, 'room', 'M01')] == expected[:3]
    assert len(list(tmp_path.glob('ap_checkpoint_room_M01_h[0-9]*.sav'))) == 2
    assert all((tmp_path / name).read_bytes() == b'untouched' for name in untouched)
    for choice in c.checkpoint_history(tmp_path, 'room', 'M01'):
        selected = dict(name=choice['name'], fingerprint=choice['fingerprint'])
        assert launch.select_save(tmp_path, 'room', 'M01', choice['difficulty'], True, selected).name == choice['name']


def test_duplicate_save_does_not_evict_history(tmp_path):
    for i in range(3):
        c.publish_checkpoint(tmp_path, 'room', 'M01', save(i))
    before = c.checkpoint_history(tmp_path, 'room', 'M01')
    c.publish_checkpoint(tmp_path, 'room', 'M01', save(2))
    assert c.checkpoint_history(tmp_path, 'room', 'M01') == before


@pytest.mark.parametrize('bad', [b'broken', save(map_name='M02')])
def test_bad_checkpoint_preserves_previous_saves(tmp_path, bad):
    path = c.publish_checkpoint(tmp_path, 'room', 'M01', save())
    previous = path.read_bytes()
    with pytest.raises(ValueError):
        c.publish_checkpoint(tmp_path, 'room', 'M01', bad)
    assert path.read_bytes() == previous
    assert len(c.checkpoint_history(tmp_path, 'room', 'M01')) == 1


def test_interrupted_publication_preserves_latest_and_older_history(tmp_path, monkeypatch):
    for i in range(3):
        path = c.publish_checkpoint(tmp_path, 'room', 'M01', save(i))
    before = [v['fingerprint'] for v in c.checkpoint_history(tmp_path, 'room', 'M01')]
    replace = Path.replace
    def fail(self, target):
        raise OSError('simulated interruption')
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError):
        c.publish_checkpoint(tmp_path, 'room', 'M01', save(3))
    assert [v['fingerprint'] for v in c.checkpoint_history(tmp_path, 'room', 'M01')] == before
    monkeypatch.setattr(Path, 'replace', replace)
    c.publish_checkpoint(tmp_path, 'room', 'M01', save(3))
    assert len(list(tmp_path.glob('*.sav'))) == 3


def test_stale_or_foreign_selection_never_loads(tmp_path):
    c.publish_checkpoint(tmp_path, 'room', 'M01', save())
    choice = c.checkpoint_history(tmp_path, 'room', 'M01')[0]
    selection = dict(name=choice['name'], fingerprint=choice['fingerprint'])
    c.publish_checkpoint(tmp_path, 'room', 'M01', save(1))
    for selected in (selection, dict(name='../manual.sav', fingerprint='x'),
                     dict(name='ap_checkpoint_other_M01.sav', fingerprint='x')):
        with pytest.raises(ValueError, match='changed or expired'):
            launch.select_save(tmp_path, 'room', 'M01', 1, True, selected)


def test_older_checkpoint_difficulty_confirmation_binds_to_selected_snapshot(tmp_path):
    runtime, client, data, writes, latest = launcher(tmp_path)
    c.publish_checkpoint(tmp_path, 'room', 'M01', save(1, 0))
    c.publish_checkpoint(tmp_path, 'room', 'M01', save(2, 1))
    choice = c.checkpoint_history(tmp_path, 'room', 'M01')[1]
    selection = dict(name=choice['name'], fingerprint=choice['fingerprint'])
    client.mission_request = ('M01', 2, True, None, selection)
    runtime.update(client)
    offer = client.mission_confirmation
    assert offer['saved_difficulty'] == 0 and offer['checkpoint'] == selection
    assert not writes
    client.mission_request = ('M01', 2, True, offer['id'], selection)
    runtime.update(client)
    assert data[CONTROL + launch.REQUEST] == 1
    assert writes[CONTROL + launch.FILENAME] == b'save\\ap_load_room.sav\0'
    assert launch.saved_difficulty((tmp_path/'ap_load_room.sav').read_bytes()) == 2
