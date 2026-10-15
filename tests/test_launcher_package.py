import json
from pathlib import Path
import zipfile

import pytest

from renegade import app_paths, start_saves
from renegade.native.mission_launch import select_save


def test_frozen_state_survives_resource_extraction_and_keeps_adapters_separate(tmp_path, monkeypatch):
    app = tmp_path / 'download'
    app.mkdir()
    legacy = app / '.state-tt'
    legacy.mkdir()
    (legacy / 'room.json').write_text('{"completed":[42]}')
    monkeypatch.setattr(app_paths.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_paths.sys, 'executable', str(app / 'Renegade Archipelago.exe'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'user'))
    tt = app_paths.state_directory(True)
    steam = app_paths.state_directory(False)
    assert tt != steam and not (steam / 'room.json').exists()
    assert json.loads((tt / 'room.json').read_text())['completed'] == [42]
    (tt / 'room.json').write_text('{"completed":[42,43]}')
    app_paths.state_directory(True)
    assert json.loads((tt / 'room.json').read_text())['completed'] == [42,43]
    app_paths.remember_build(True)
    assert app_paths.preferred_build()
    app_paths.remember_build(False)
    assert not app_paths.preferred_build()


def test_bundled_start_does_not_replace_existing_save_or_checkpoint(tmp_path):
    first = select_save(tmp_path, 'room', 'M01', 1)
    original = first.read_bytes()
    checkpoint = tmp_path / 'ap_checkpoint_room_M01.sav'
    checkpoint.write_bytes(original)
    first.write_bytes(b'user file, do not replace')
    with pytest.raises(ValueError):
        select_save(tmp_path, 'room', 'M01', 1)
    assert first.read_bytes() == b'user file, do not replace'
    assert checkpoint.read_bytes() == original
    assert not list(tmp_path.glob('*.tmp'))


def test_damaged_bundled_save_is_rejected_before_writing(tmp_path, monkeypatch):
    real = app_paths.resource_root() / 'assets' / 'mission-starts.zip'
    assets = tmp_path / 'assets'
    assets.mkdir()
    with zipfile.ZipFile(real) as source, zipfile.ZipFile(assets / 'mission-starts.zip', 'w') as target:
        manifest = json.loads(source.read('manifest.json'))
        target.writestr('manifest.json', json.dumps(manifest))
        entry = next(e for e in manifest['missions'] if e['map_name']=='M13' and e['difficulty']==0)
        target.writestr('saves/' + entry['filename'], b'damaged')
    monkeypatch.setattr(start_saves, 'resource_root', lambda: tmp_path)
    with pytest.raises(ValueError, match='failed validation'):
        start_saves.install_start_save(tmp_path / 'game', 'M13', 0)
    assert not (tmp_path / 'game').exists()
